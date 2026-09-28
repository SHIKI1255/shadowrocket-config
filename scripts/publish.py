"""Publish a validated, complete tree with one fast-forward release ref update."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request

from build import API, REPOSITORY, BuildError, digest, require


def verified_files(directory: Path) -> dict[str, bytes]:
    require(not directory.is_symlink(), "Symlink output is not publishable")
    sums = (directory / "SHA256SUMS").read_bytes()
    files = {}
    for line in sums.decode().splitlines():
        require(re.fullmatch(r"[0-9a-f]{64}  [a-zA-Z0-9_./-]+", line), "Malformed checksum entry")
        expected, path = line.split("  ", 1)
        require(not path.startswith("/") and ".." not in path.split("/") and path not in files,
                "Unsafe/duplicate release path")
        require(not any(p.is_symlink() for p in (directory / path, *(directory / path).parents)),
                "Symlink artifact")
        data = (directory / path).read_bytes()
        require(digest(data) == expected, f"Release hash mismatch: {path}")
        files[path] = data
    files["SHA256SUMS"] = sums
    actual = {p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file()}
    require(actual == set(files), "Unexpected or incomplete release files")
    require({"shadowrocket.conf", "manifest.json", "sources.lock.json", "source_stats.json", "THIRD_PARTY.md"}
            <= set(files), "Missing release metadata")
    require(json.loads(files["manifest.json"])["config_sha256"] == digest(files["shadowrocket.conf"]),
            "Config/manifest mismatch")
    return files


def api(path: str, payload=None, method=None, missing_ok=False):
    token = os.environ.get("GITHUB_TOKEN")
    require(bool(token), "GITHUB_TOKEN is required for publishing")
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(API + "repos/" + REPOSITORY + "/" + path, data=data,
          headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
                   "Content-Type": "application/json", "User-Agent": "shadowrocket-config-publisher/1",
                   "X-GitHub-Api-Version": "2022-11-28"}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if missing_ok and exc.code == 404:
            return None
        raise BuildError(f"GitHub publish API failed: HTTP {exc.code} ({path})") from None


def publish(directory: Path) -> dict:
    require(os.environ.get("GITHUB_REPOSITORY") == REPOSITORY, "Unexpected publishing repository")
    require(os.environ.get("GITHUB_REF") == "refs/heads/main", "Only main can publish")
    require(os.environ.get("GITHUB_EVENT_NAME") in {"push", "schedule", "workflow_dispatch"},
            "This event cannot publish")
    sha = os.environ.get("GITHUB_SHA", "")
    require(re.fullmatch(r"[0-9a-f]{40}", sha), "Missing source revision")
    files = verified_files(directory)
    manifest = json.loads(files["manifest.json"])
    root = Path(__file__).resolve().parents[1]
    for path, expected in manifest["source_files_sha256"].items():
        require(digest((root / path).read_bytes()) == expected, f"Builder/source drift: {path}")
    require(api("git/ref/heads/main")["object"]["sha"] == sha, "Stale build: main advanced")
    ref = api("git/ref/heads/release", missing_ok=True)
    parent = ref["object"]["sha"] if ref else None
    tree = api("git/trees", {"tree": [{"path": path, "mode": "100644", "type": "blob", "content": data.decode("utf-8")}
                                   for path, data in sorted(files.items())]})
    if parent and api("git/commits/" + parent)["tree"]["sha"] == tree["sha"]:
        return {"status": "unchanged", "commit": parent, "config_sha256": manifest["config_sha256"]}
    commit = api("git/commits", {"message": "Publish validated configuration from " + sha,
                                 "tree": tree["sha"], "parents": [parent] if parent else []})
    # Recheck source immediately before the only operation that changes the public branch.
    require(api("git/ref/heads/main")["object"]["sha"] == sha, "Stale build: main advanced")
    if parent:
        api("git/refs/heads/release", {"sha": commit["sha"], "force": False}, method="PATCH")
    else:
        api("git/refs", {"ref": "refs/heads/release", "sha": commit["sha"]})
    require(api("git/ref/heads/release")["object"]["sha"] == commit["sha"], "Release readback mismatch")
    return {"status": "published", "commit": commit["sha"], "config_sha256": manifest["config_sha256"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(publish(args.directory.resolve())))
    except (BuildError, OSError, ValueError, KeyError) as exc:
        print(f"PUBLISH STOPPED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

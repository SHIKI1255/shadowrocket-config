"""Local-only Hosts synchronizer. Private YAML and URLs never leave memory.

Run from PowerShell with --check or --publish and an explicit external --state-dir.
GitHub writes are limited to the two public module files, through a checked PR.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from datetime import datetime
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from hosts_core import (ALLOWED_FILES, HostsError, MODULE, README, REPOSITORY, RAW_ROOT,
                        SHA, BEIJING, check_quantity, differences, digest, normalize_hosts,
                        format_timestamp, module_timestamp,
                        ordered_hosts, parse_module, render_module, require,
                        update_readme, validate_readme)

ROOT = Path(__file__).resolve().parents[1]
PROFILE_UID = "RJxK8goR5EX6"
SOURCE_HOST = "api.geekspeed.org"
MAX_BYTES = 10 * 1024 * 1024
BRANCH_PREFIX = "automation/geekspeed-hosts-"
WORKFLOW = ".github/workflows/hosts.yml"
RUNTIME_FILES = ("scripts/hosts_core.py", "scripts/sync_hosts.py", "scripts/validate_hosts.py",
                 "requirements-hosts.txt", WORKFLOW)


def load_yaml(content: bytes):
    try:
        import yaml
        require(yaml.__version__ == "6.0.3", "yaml_version")
        text = content.decode("utf-8-sig")
        # Reject aliases, anchors and explicit tags before construction. No YAML code runs.
        for token in yaml.scan(text):
            require(not isinstance(token, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken,
                                           yaml.tokens.TagToken)), "yaml_alias_or_tag")

        class UniqueLoader(yaml.SafeLoader):
            pass

        def mapping(loader, node, deep=False):
            result = {}
            for key_node, value_node in node.value:
                key = loader.construct_object(key_node, deep=deep)
                require(type(key) in (str, int, float, bool) and key not in result,
                        "yaml_duplicate_or_complex_key")
                result[key] = loader.construct_object(value_node, deep=deep)
            return result

        UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
        return yaml.load(text, Loader=UniqueLoader)
    except HostsError:
        raise
    except ImportError:
        raise HostsError("yaml_unavailable") from None
    except Exception:
        raise HostsError("yaml_invalid") from None


def source_url():
    try:
        path = (Path.home() / "AppData/Roaming/io.github.clash-verge-rev.clash-verge-rev"
                / "profiles.yaml")
        require(path.stat().st_size <= MAX_BYTES, "source_index_size")
        index = load_yaml(path.read_bytes())
        require(isinstance(index, dict) and isinstance(index.get("items"), list), "source_index")
        matches = [p for p in index["items"] if isinstance(p, dict) and p.get("uid") == PROFILE_UID]
        require(len(matches) == 1 and matches[0].get("type") == "remote", "source_identity")
        url = matches[0].get("url")
        require(type(url) is str, "source_url")
        parsed = urllib.parse.urlsplit(url)
        require(parsed.scheme == "https" and parsed.hostname == SOURCE_HOST
                and parsed.port in (None, 443) and not parsed.username and not parsed.password
                and parsed.path == "/api/v1/client/subscribe" and not parsed.fragment
                and bool(urllib.parse.parse_qs(parsed.query).get("token")), "source_origin")
        return url
    except HostsError:
        raise
    except Exception:
        raise HostsError("source_read_failed") from None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HostsError("redirect_rejected")


def download(url: str, *, private=False, sleeper=time.sleep, opener=None) -> bytes:
    opener = opener or urllib.request.build_opener(NoRedirect())
    headers = {"User-Agent": "Clash.Meta" if private else "Hosts-module-validator",
               "Accept": "text/yaml,application/yaml,text/plain" if private else "text/plain",
               "Cache-Control": "no-cache"}
    for attempt in range(3):
        try:
            with opener.open(urllib.request.Request(url, headers=headers), timeout=25) as response:
                require(response.status == 200, "http_status")
                data = response.read(MAX_BYTES + 1)
                require(0 < len(data) <= MAX_BYTES, "response_size")
                return data
        except HostsError:
            raise
        except urllib.error.HTTPError as exc:
            if exc.code not in (408, 429, 500, 502, 503, 504):
                raise HostsError("http_rejected") from None
        except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError):
            pass
        except Exception:
            raise HostsError("download_failed") from None
        if attempt < 2:
            sleeper((1, 3)[attempt])
    raise HostsError("download_exhausted")


def subscription(data: bytes):
    parsed = load_yaml(data)
    require(isinstance(parsed, dict), "subscription_shape")
    hosts = normalize_hosts(parsed.get("hosts"))
    proxies = parsed.get("proxies")
    require(isinstance(proxies, list) and 0 < len(proxies) <= 10000, "subscription_nodes")
    references = dict.fromkeys(hosts, 0)
    for proxy in proxies:
        require(isinstance(proxy, dict) and type(proxy.get("server")) is str, "subscription_nodes")
        name = proxy["server"].lower().removesuffix(".")
        if name in references:
            references[name] += 1
    # Do not return proxy objects, arbitrary fields or raw YAML across this boundary.
    return {"hosts": hosts, "references": references, "node_count": len(proxies)}


def candidate(previous, readme, base_sha, fetch, sleeper=time.sleep, now=None):
    first = subscription(fetch())
    if first["hosts"] == previous:
        return None
    sleeper(10)
    second = subscription(fetch())
    require(first["hosts"] == second["hosts"], "source_changed_between_reads")
    check_quantity(previous, second["hosts"])
    hosts = ordered_hosts(previous, second["hosts"])
    checked_at = format_timestamp(now if now is not None else datetime.now(BEIJING))
    files = {
        MODULE: render_module(hosts, checked_at),
        README: update_readme(readme, hosts, second["references"], second["node_count"],
                              checked_at, base_sha).encode("utf-8"),
    }
    require(parse_module(files[MODULE]) == second["hosts"], "roundtrip_mismatch")
    validate_readme(files[README].decode("utf-8"), hosts, module_timestamp(files[MODULE]))
    return {"files": files, "diff": differences(previous, hosts), "hosts_count": len(hosts),
            "checked_at": checked_at}


class GitHub:
    def api(self, path, payload=None, method=None):
        args = ["gh", "api", "--hostname", "github.com", "--method",
                method or ("POST" if payload is not None else "GET"),
                f"repos/{REPOSITORY}/{path}"]
        data = None
        if payload is not None:
            args += ["--input", "-"]
            data = json.dumps(payload, ensure_ascii=False)
        env = dict(os.environ, GH_PROMPT_DISABLED="1", NO_COLOR="1")
        for key in ("GH_DEBUG", "GH_FORCE_TTY", "GH_PAGER", "PAGER"):
            env.pop(key, None)
        try:
            result = subprocess.run(args, input=data, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", env=env, timeout=60)
            require(result.returncode == 0, "github_request_failed")
            return json.loads(result.stdout)
        except HostsError:
            raise
        except Exception:
            raise HostsError("github_unavailable") from None

    def head(self):
        sha = self.api("git/ref/heads/main")["object"]["sha"]
        require(SHA.fullmatch(sha), "invalid_revision")
        return sha

    def file(self, path, sha):
        require(SHA.fullmatch(sha), "invalid_revision")
        result = self.api(f"contents/{path}?ref={sha}")
        require(result.get("type") == "file" and result.get("path") == path
                and result.get("encoding") == "base64", "github_file_shape")
        return base64.b64decode(result["content"])

    def open_sync_prs(self):
        rows = self.api("pulls?state=open&per_page=100")
        require(isinstance(rows, list) and len(rows) < 100, "too_many_open_prs")
        return [p for p in rows if p.get("head", {}).get("ref", "").startswith(BRANCH_PREFIX)]

    def check_runner(self, base):
        for path in RUNTIME_FILES:
            local = (ROOT / path).read_bytes().replace(b"\r\n", b"\n")
            remote = self.file(path, base).replace(b"\r\n", b"\n")
            require(local == remote, "runner_version_mismatch")

    def create_pr(self, base, files):
        require(set(files) == set(ALLOWED_FILES), "publication_scope")
        require(self.head() == base and not self.open_sync_prs(), "publication_concurrent")
        parent = self.api(f"git/commits/{base}")
        tree = self.api("git/trees", {"base_tree": parent["tree"]["sha"], "tree": [
            {"path": path, "mode": "100644", "type": "blob", "content": files[path].decode("utf-8")}
            for path in ALLOWED_FILES]})
        commit = self.api("git/commits", {"message": "Synchronize complete GeekSpeed Hosts",
                                          "tree": tree["sha"], "parents": [base]})
        head = commit["sha"]
        require(SHA.fullmatch(head), "invalid_revision")
        branch = BRANCH_PREFIX + digest(files[MODULE])[:12] + "-" + base[:8]
        self.api("git/refs", {"ref": "refs/heads/" + branch, "sha": head})
        pr = self.api("pulls", {"title": "Synchronize complete GeekSpeed Hosts",
            "head": branch, "base": "main", "body":
            "Update the complete optional Hosts module from two matching local provider reads. "
            "Only the module and its documentation change. The private subscription stays in memory.\n\n"
            "Validation: strict YAML and module round-trip, quantity gate, matching content timestamps, "
            "reference counts and scoped PR CI. The source must still match immediately before merge. "
            "Mobile connectivity requires separate verification.\n\n"
            f"Previous fixed module: {RAW_ROOT}/{base}/{MODULE}\n"})
        return {"base": base, "head": head, "number": pr["number"],
                "hashes": {path: digest(files[path]) for path in ALLOWED_FILES}}

    def review(self, pending):
        number, head, base = pending["number"], pending["head"], pending["base"]
        pr = self.api(f"pulls/{number}")
        require(pr.get("state") == "open" and not pr.get("draft")
                and pr["head"]["sha"] == head and pr["base"]["ref"] == "main"
                and pr["base"]["sha"] == base and pr.get("commits") == 1
                and pr["head"]["repo"]["full_name"] == REPOSITORY, "pr_identity_changed")
        changed = self.api(f"pulls/{number}/files?per_page=100")
        require(len(changed) == 2 and {f["filename"] for f in changed} == set(ALLOWED_FILES)
                and all(f["status"] == "modified" for f in changed), "publication_scope")
        for path in ALLOWED_FILES:
            require(digest(self.file(path, head)) == pending["hashes"][path], "candidate_changed")
        require(self.head() == base, "main_advanced")

    def wait_ci(self, pending, sleeper=time.sleep, attempts=40):
        for attempt in range(attempts):
            self.review(pending)
            runs = self.api(f"actions/runs?event=pull_request&head_sha={pending['head']}&per_page=100")
            rows = runs.get("workflow_runs", [])
            require(len(rows) < 100, "ci_ambiguous")
            latest = {}
            for run in sorted(rows, key=lambda r: r["id"], reverse=True):
                if run.get("head_sha") == pending["head"]:
                    latest.setdefault(run["path"], run)
            if WORKFLOW in latest:
                require(all(r.get("conclusion") in (None, "success", "skipped")
                            for r in latest.values()), "ci_failed")
                if all(r["status"] == "completed" for r in latest.values()):
                    require(latest[WORKFLOW]["conclusion"] == "success", "ci_failed")
                    return
            if attempt < attempts - 1:
                sleeper(15)
        raise HostsError("ci_pending")

    def merge(self, pending):
        self.review(pending)
        head_commit = self.api(f"git/commits/{pending['head']}")
        require([p["sha"] for p in head_commit["parents"]] == [pending["base"]], "pr_identity_changed")
        # A non-forced fast-forward of this merge commit is an atomic concurrency guard:
        # any independent advance of main is not its ancestor and GitHub rejects it.
        merged = self.api("git/commits", {
            "message": f"Merge pull request #{pending['number']}: synchronize GeekSpeed Hosts",
            "tree": head_commit["tree"]["sha"], "parents": [pending["base"], pending["head"]]})
        sha = merged["sha"]
        require(SHA.fullmatch(sha) and self.head() == pending["base"], "main_advanced")
        self.api("git/refs/heads/main", {"sha": sha, "force": False}, method="PATCH")
        require(self.head() == sha, "merge_readback")
        return sha


@contextmanager
def runtime_lock(directory: Path):
    require(directory.is_absolute() and ROOT not in directory.resolve().parents
            and directory.resolve() != ROOT, "state_location")
    directory.mkdir(parents=True, exist_ok=True)
    handle = (directory / "run.lock").open("a+b")
    locked = False
    try:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError:
            raise HostsError("already_running") from None
        yield
    finally:
        if locked:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def save_state(directory: Path, value):
    temporary = directory / "publication.next.json"
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, directory / "publication.json")


def verify_published_once(gh, pending, downloader=download):
    pr = gh.api(f"pulls/{pending['number']}")
    require(pr.get("merged") and SHA.fullmatch(pr.get("merge_commit_sha") or ""), "merge_pending")
    sha = pr["merge_commit_sha"]
    for path, expected in pending["hashes"].items():
        require(digest(gh.file(path, sha)) == expected, "published_hash_mismatch")
        require(digest(downloader(f"{RAW_ROOT}/{sha}/{path}")) == expected, "fixed_raw_pending")
        require(digest(downloader(f"{RAW_ROOT}/main/{path}")) == expected, "rolling_raw_pending")
    return sha


def verify_published(gh, pending, downloader=download, sleeper=time.sleep):
    # GitHub may report an open PR briefly after an atomic merge; Raw can lag too.
    # Retry readback only, never repeat a commit, ref update or merge.
    for attempt in range(3):
        try:
            return verify_published_once(gh, pending, downloader)
        except HostsError as exc:
            if exc.code not in {"merge_pending", "fixed_raw_pending", "rolling_raw_pending"} or attempt == 2:
                raise
            sleeper((5, 10)[attempt])


def run(directory: Path, publish=False, gh=None, sleeper=time.sleep, downloader=download):
    gh = gh or GitHub()
    with runtime_lock(directory):
        state_path = directory / "publication.json"
        if state_path.exists():
            pending = json.loads(state_path.read_text(encoding="utf-8"))
            if pending and not pending.get("verified"):
                # Never create a second PR, merge an old candidate or reread private data here.
                sha = verify_published(gh, pending, downloader, sleeper)
                pending.update(verified=True, merged_sha=sha)
                save_state(directory, pending)
                return {"status": "published", "commit": sha, "pr": pending["number"],
                        "module_sha256": pending["hashes"][MODULE], "verification_only": True}
        require(not gh.open_sync_prs(), "existing_sync_pr")
        base = gh.head()
        gh.check_runner(base)
        previous_module = gh.file(MODULE, base)
        previous = parse_module(previous_module)
        readme = gh.file(README, base).decode("utf-8")
        validate_readme(readme, previous, module_timestamp(previous_module))
        url = source_url()
        result = candidate(previous, readme, base,
                           lambda: downloader(url, private=True), sleeper=sleeper)
        if result is None:
            return {"status": "unchanged", "commit": base, "hosts_count": len(previous)}
        summary = {"hosts_count": result["hosts_count"], "diff": result["diff"],
                   "checked_at": result["checked_at"]}
        if not publish:
            return {"status": "candidate", "base": base, **summary}
        pending = gh.create_pr(base, result["files"])
        save_state(directory, pending)
        # This public URL can be attached to the scheduled task while CI is running.
        print(json.dumps({"status": "pr_created", "pr": pending["number"],
                          "url": f"https://github.com/{REPOSITORY}/pull/{pending['number']}"}), flush=True)
        gh.wait_ci(pending, sleeper=sleeper)
        # CI may take minutes. A stale candidate must never advance main.
        latest = subscription(downloader(url, private=True))
        require(latest["hosts"] == parse_module(result["files"][MODULE]), "source_changed_before_merge")
        sha = gh.merge(pending)
        pending["merged_sha"] = sha
        save_state(directory, pending)
        verified_sha = verify_published(gh, pending, downloader, sleeper)
        require(verified_sha == sha, "merge_readback")
        pending["verified"] = True
        save_state(directory, pending)
        return {"status": "published", "commit": sha, "pr": pending["number"],
                "module_sha256": pending["hashes"][MODULE],
                "rollback": f"{RAW_ROOT}/{base}/{MODULE}", **summary}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--publish", action="store_true")
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = run(args.state_dir, publish=args.publish)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 0
    except HostsError as exc:
        print(json.dumps({"status": "needs_attention", "reason": exc.code}), flush=True)
    except KeyboardInterrupt:
        print(json.dumps({"status": "needs_attention", "reason": "interrupted"}), flush=True)
    except Exception:
        print(json.dumps({"status": "failed", "reason": "internal_error"}), flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())

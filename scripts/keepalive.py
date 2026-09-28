"""Write a truthful maintenance record at most once every 30 days, after release."""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import json
import os
import re
import sys

from build import REPOSITORY, BuildError, json_bytes, require
from publish import api

RECORD = ".github/maintenance.json"
INTERVAL = timedelta(days=30)
RESULTS = {"success", "failure", "cancelled", "skipped"}


def due(record, now):
    require(now.tzinfo is not None, "UTC-aware maintenance time required")
    if record is None:
        return True
    require(record.get("schema_version") == 1, "Unknown maintenance record")
    previous = datetime.fromisoformat(record["checked_at_utc"].replace("Z", "+00:00"))
    require(previous.tzinfo is not None and previous <= now, "Invalid maintenance timestamp")
    return now - previous >= INTERVAL


def maintain(now=None):
    require(os.environ.get("GITHUB_REPOSITORY") == REPOSITORY, "Unexpected maintenance repository")
    require(os.environ.get("GITHUB_REF") == "refs/heads/main", "Only main can maintain")
    require(os.environ.get("GITHUB_EVENT_NAME") in {"push", "schedule", "workflow_dispatch"},
            "This event cannot maintain")
    sha = os.environ.get("GITHUB_SHA", "")
    run = os.environ.get("GITHUB_RUN_ID", "")
    require(re.fullmatch(r"[0-9a-f]{40}", sha) and run.isdecimal(), "Invalid workflow identity")
    validation = os.environ.get("VALIDATE_RESULT", "")
    publication = os.environ.get("PUBLISH_RESULT", "")
    require(validation in RESULTS and publication in RESULTS, "Missing actual job results")
    now = now or datetime.now(timezone.utc)
    if api("git/ref/heads/main")["object"]["sha"] != sha:
        return {"status": "skipped_main_advanced"}
    previous = api("contents/" + RECORD + "?ref=" + sha, missing_ok=True)
    record = None
    if previous:
        require(previous.get("encoding") == "base64" and previous.get("type") == "file",
                "Invalid maintenance file")
        record = json.loads(base64.b64decode(previous["content"]))
    if not due(record, now):
        return {"status": "not_due", "last_checked_at_utc": record["checked_at_utc"]}
    ref = api("git/ref/heads/release", missing_ok=True)
    release = ref["object"]["sha"] if ref else None
    if release:
        require(re.fullmatch(r"[0-9a-f]{40}", release), "Invalid release revision")
    current = {
        "schema_version": 1,
        "checked_at_utc": now.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "run_url": "https://github.com/" + REPOSITORY + "/actions/runs/" + run,
        "source_commit": sha,
        "validation_result": validation,
        "publication_result": publication,
        "published_release_commit": release,
        "note": "Maintenance activity only; failed validation never authorizes a release.",
    }
    parent = api("git/commits/" + sha)
    tree = api("git/trees", {"base_tree": parent["tree"]["sha"], "tree": [
        {"path": RECORD, "mode": "100644", "type": "blob", "content": json_bytes(current).decode()}
    ]})
    commit = api("git/commits", {"message": "Record scheduled maintenance and publication status",
                                "tree": tree["sha"], "parents": [sha]})
    if api("git/ref/heads/main")["object"]["sha"] != sha:
        return {"status": "skipped_main_advanced"}
    api("git/refs/heads/main", {"sha": commit["sha"], "force": False}, method="PATCH")
    require(api("git/ref/heads/main")["object"]["sha"] == commit["sha"], "Maintenance ref readback mismatch")
    saved = api("contents/" + RECORD + "?ref=" + commit["sha"])
    require(json.loads(base64.b64decode(saved["content"])) == current, "Maintenance content readback mismatch")
    return {"status": "recorded", "commit": commit["sha"], **current}


def main():
    try:
        print(json.dumps(maintain(), ensure_ascii=False))
    except (BuildError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"MAINTENANCE STOPPED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

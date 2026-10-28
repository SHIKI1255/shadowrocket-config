"""Repository-local failure notifications and anonymous publication readback."""
import argparse
import json
import os
from pathlib import Path
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from build import REPOSITORY, ROOT
from shared_data import RAW, fetch, digest, json_bytes, require, release_sha
from publish import api, verified_files

MARKER = "<!-- release-watch:v1 -->"


def incidents():
    found = []
    for page in range(1, 11):
        rows = api("issues?state=open&per_page=100&page=" + str(page))
        found += [r for r in rows if "pull_request" not in r and
                  r.get("user", {}).get("login") == "github-actions[bot]" and
                  r.get("body", "").startswith(MARKER)]
        if len(rows) < 100:
            return found
    raise ValueError("Issue inventory incomplete")


def identity():
    require(os.environ.get("GITHUB_REPOSITORY") == REPOSITORY and
            os.environ.get("GITHUB_REF") == "refs/heads/main" and
            os.environ.get("GITHUB_EVENT_NAME") in {"push", "schedule", "workflow_dispatch"},
            "Only trusted main runs may write monitoring state")
    sha, run = os.environ.get("GITHUB_SHA", ""), os.environ.get("GITHUB_RUN_ID", "")
    require(bool(re.fullmatch("[a-f0-9]{40}", sha)) and run.isdecimal(), "Invalid run identity")
    require(api("git/ref/heads/main")["object"]["sha"] == sha, "Main advanced; do not write stale state")
    return sha, "https://github.com/" + REPOSITORY + "/actions/runs/" + run


def notify(results, report=None, candidate=""):
    _, url = identity()
    require(set(results) >= {"validate", "publish"}, "Missing job outcomes")
    require(all(v in {"success", "failure", "cancelled", "skipped"} for v in results.values()),
            "Invalid job outcome")
    existing = incidents()
    require(len(existing) <= 1, "Multiple incident records require review")
    failed = sorted(k for k, v in results.items() if v in {"failure", "cancelled"})
    if not failed:
        # A no-change probe is not a new validation/publication acceptance.
        if results["validate"] != "success" or results["publish"] != "success":
            return {"status": "unchanged"}
        if existing:
            api("issues/" + str(existing[0]["number"]),
                {"state": "closed", "body": existing[0]["body"] +
                 "\n\n恢复：完整验证、发布及匿名哈希读回通过。\n" + url}, method="PATCH")
            return {"status": "recovered"}
        return {"status": "healthy"}
    category = (report or {}).get("category", "workflow_failure")
    fp = digest(json_bytes({"jobs": failed, "category": category,
                            "evidence": (report or {}).get("fingerprint", ""),
                            "source": "" if report else os.environ.get("GITHUB_SHA", "")}))
    token = "<!-- fingerprint:" + fp + " -->"
    if existing and token in existing[0]["body"]:
        return {"status": "deduplicated"}
    body = MARKER + "\n" + token + "\n\n自动发布需要检查；本次失败不等于设备连接故障。\n\n"
    body += "阶段：" + ", ".join(failed) + "\n类别：" + category + "\n运行：" + url
    if candidate:
        body += "\n审核候选：" + candidate
    body += "\n\n未通过检查不会发布候选。发布步骤已完成而读回失败时，保留现场并检查缓存，不自动回滚。"
    if existing:
        api("issues/" + str(existing[0]["number"]), {"body": body}, method="PATCH")
        return {"status": "updated"}
    api("issues", {"title": "自动发布检查需要处理", "body": body})
    return {"status": "opened"}


def public_readback(directory):
    files = verified_files(directory)
    sha = release_sha(REPOSITORY)
    # No token is attached by shared_data.fetch to raw.githubusercontent.com.
    def check(item):
        path, data = item
        expected = digest(data)
        for revision in (sha, "release"):
            for attempt in range(3):
                actual = fetch(RAW + REPOSITORY + "/" + revision + "/" + path)
                if digest(actual) == expected:
                    break
                if revision != "release" or attempt == 2:
                    raise ValueError("Public download hash mismatch: " + path)
                time.sleep((5, 15)[attempt])
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(check, files.items()))
    require(release_sha(REPOSITORY) == sha, "Release advanced during readback")
    return {"status": "public_hashes_verified", "commit": sha, "files": len(files)}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--verify", type=Path)
    p.add_argument("--evidence", type=Path)
    a = p.parse_args()
    try:
        if a.verify:
            result = public_readback(a.verify)
        else:
            report, candidate, candidate_error = None, "", False
            results = json.loads(os.environ["JOB_RESULTS"])
            if a.evidence and (a.evidence/"report.json").exists():
                # Only the producer has an evidence verifier/candidate writer.
                from review_failure import verified_evidence, propose
                try:
                    report, files = verified_evidence(a.evidence)
                    if results["validate"] == "failure" and report["candidate_eligible"]:
                        candidate = propose(report, files)
                except (ValueError, OSError, KeyError, TypeError):
                    candidate_error = True
                    results["candidate"] = "failure"
            result = notify(results, report, candidate)
            if candidate_error:
                print(json.dumps(result)); return 1
        print(json.dumps(result))
        return 0
    except (ValueError, OSError, KeyError, TypeError):
        print("MONITOR STOPPED: checks or API operation failed; inspect workflow stage", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())

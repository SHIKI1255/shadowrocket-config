"""Read-only three-repository health assessment; optional issue notification only."""
import argparse
from datetime import datetime, timedelta, timezone
import http.client
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

from shared_data import RAW, API, fetch, verify, digest, json_bytes, require
from public_http import request_bytes
from publish import api as issue_api
from release_health import identity

ROOT = Path(__file__).resolve().parents[1]
PRODUCER = "SHIKI1255/shadowrocket-config"
REPOSITORIES = (PRODUCER, "SHIKI1255/quantumultx-config", "SHIKI1255/clash-config")
LIMITS = {"producer_hours": 36, "consumer_hours": 24, "lag_hours": 24}
MARKER = "<!-- repository-health:v1 -->"
MAX_HISTORY = 96
EVENTS = {"push", "schedule", "workflow_dispatch"}
READBACK = "Verify anonymous fixed and rolling download hashes"


class HealthError(ValueError):
    pass


def utc(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(result.tzinfo is not None, "Timezone required")
    return result.astimezone(timezone.utc)


def stamp(value):
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha(value):
    require(isinstance(value, str) and re.fullmatch("[a-f0-9]{40}", value), "Invalid revision")
    return value


def read_api(repo, path):
    require(repo in REPOSITORIES and not path.startswith("/") and ".." not in path.split("/") and
            re.fullmatch(r"[a-zA-Z0-9_./?=&-]+", path), "Unapproved API path")
    headers = {"User-Agent": "SHIKI1255-Repository-Health/1", "Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        data = request_bytes(urllib.request.Request(API+"repos/"+repo+"/"+path, headers=headers),
                             limit=8*1024*1024, expected_host="api.github.com")
        require(0 < len(data) <= 8*1024*1024, "Invalid API response size")
        result = json.loads(data)
        require(isinstance(result, dict), "Invalid API object")
        return result
    except (OSError, ValueError, http.client.HTTPException):
        raise HealthError("public_api_unavailable") from None


def release_ref(repo):
    return sha(read_api(repo, "git/ref/heads/release")["object"]["sha"])


def manifest_at(commit):
    m = json.loads(fetch(RAW+PRODUCER+"/"+sha(commit)+"/shared/manifest.json"))
    require(m.get("schema_version") == 1, "Unknown shared schema")
    require(re.fullmatch("[a-f0-9]{64}", m["bundle_sha256"]), "Invalid bundle hash")
    require(m["shared_version"] == digest(json_bytes({k:v for k,v in m.items() if k!="shared_version"})),
            "Invalid shared version")
    return m


def release_data(repo, commit):
    prefix = RAW+repo+"/"+sha(commit)+"/"
    shared, _ = verify(fetch(prefix+"shared/manifest.json"), fetch(prefix+"shared/bundle.json"))
    client = json.loads(fetch(prefix+"manifest.json"))
    require(client.get("schema_version") == 2 and client["shared_version"] == shared["shared_version"],
            "Client/shared manifest mismatch")
    consumed = commit
    if repo != PRODUCER:
        lock = json.loads(fetch(prefix+"shared-consumer.lock.json"))
        require(lock.get("schema_version") == 1 and lock["shared_version"] == shared["shared_version"]
                and lock["bundle_sha256"] == shared["bundle_sha256"], "Consumer lock mismatch")
        consumed = sha(lock["shared_release"])
    return {"release": commit, "consumed_release": consumed,
            "shared_version": shared["shared_version"], "bundle_sha256": shared["bundle_sha256"]}


def qualifying_time(repo, run, jobs):
    # PR checks and partial unit-only successes never refresh the health clock.
    if run.get("event") not in EVENTS or run.get("head_branch") != "main" or run.get("status") != "completed":
        return None
    require(len({j["name"] for j in jobs}) == len(jobs), "Ambiguous latest job inventory")
    by_name = {j["name"]: j for j in jobs}
    if repo == PRODUCER:
        good = all(by_name.get(n, {}).get("conclusion") == "success" for n in ("validate", "publish"))
        good = good and any(s["name"] == READBACK and s.get("conclusion") == "success"
                            for s in by_name.get("publish", {}).get("steps", []))
        return by_name["publish"]["completed_at"] if good else None
    # A successful no-change probe is activity even when validation/publication are skipped.
    job = by_name.get("probe")
    if job and job.get("conclusion") == "success":
        return job["completed_at"]
    return None


def last_verified(repo, now=None):
    fixed_now = now
    now = now or datetime.now(timezone.utc)
    hours = LIMITS["producer_hours"] if repo == PRODUCER else LIMITS["consumer_hours"]
    cutoff = now - timedelta(hours=hours)
    for page in range(1, 4):
        data = read_api(repo, "actions/workflows/update.yml/runs?branch=main&per_page=100&page="+str(page))
        runs = data["workflow_runs"]
        for run in runs:
            if run.get("event") not in EVENTS or run.get("head_branch") != "main":
                continue
            if run.get("status") != "completed":
                continue
            jobs = read_api(repo, "actions/runs/"+str(int(run["id"]))+"/jobs?filter=latest&per_page=100")
            require(jobs["total_count"] == len(jobs["jobs"]), "Incomplete job inventory")
            value = qualifying_time(repo, run, jobs["jobs"])
            if value:
                require(utc(value) <= (fixed_now or datetime.now(timezone.utc)), "Activity timestamp is in the future")
                return {"at": stamp(utc(value)), "run_id": run["id"]}
        if len(runs) < 100 or (runs and utc(runs[-1]["created_at"]) < cutoff):
            return None
    raise HealthError("activity_history_incomplete")


def continuous_lag(commits, consumed, current, bundle_hash, now, get_manifest=manifest_at):
    """Replay bounded immutable release history; later differing versions do not reset age."""
    require(0 < len(commits) <= MAX_HISTORY, "Incomplete release history")
    parent, since, last_time = consumed, None, None
    for commit in commits:
        current_sha = sha(commit["sha"])
        require([p["sha"] for p in commit["parents"]] == [parent], "Nonlinear release history")
        when = utc(commit["commit"]["committer"]["date"])
        require(when <= now and (last_time is None or when >= last_time), "Invalid publication time")
        m = get_manifest(current_sha)
        if m["bundle_sha256"] == bundle_hash:
            since = None
        elif since is None:
            since = when
        parent, last_time = current_sha, when
    require(parent == current and since is not None, "History/current bundle disagreement")
    return stamp(since)


def lag_since(consumer, producer, now):
    old, current = consumer["consumed_release"], producer["release"]
    require(old != current, "Same source revision has different contents")
    # Verify the original consumed data identity before using its ancestry.
    prior = manifest_at(old)
    require(prior["shared_version"] == consumer["shared_version"] and
            prior["bundle_sha256"] == consumer["bundle_sha256"], "Consumed source identity mismatch")
    comparison = read_api(PRODUCER, "compare/"+old+"..."+current+"?per_page=100&page=1")
    require(comparison["status"] == "ahead" and comparison["merge_base_commit"]["sha"] == old and
            comparison["total_commits"] == len(comparison["commits"]), "Release ancestry unavailable")
    return continuous_lag(comparison["commits"], old, current, consumer["bundle_sha256"], now)


def assess(observations, errors, now):
    problems = []
    for repo in REPOSITORIES:
        if repo in errors:
            problems.append({"repository": repo, "kind": "inspection_unavailable"})
            continue
        row = observations[repo]
        if row["workflow_state"] != "active":
            problems.append({"repository": repo, "kind": "workflow_disabled"})
        hours = LIMITS["producer_hours"] if repo == PRODUCER else LIMITS["consumer_hours"]
        last = row["last_verified"]
        if last is None or now - utc(last["at"]) > timedelta(hours=hours):
            problems.append({"repository": repo, "kind": "activity_overdue"})
        if row.get("sync_status") == "content_pending":
            if now - utc(row["lag_since"]) > timedelta(hours=LIMITS["lag_hours"]):
                problems.append({"repository": repo, "kind": "content_lag"})
    return problems


def collect(now=None):
    fixed_now = now
    now = now or datetime.now(timezone.utc)
    observations, errors = {}, {}
    for repo in REPOSITORIES:
        try:
            workflow = read_api(repo, "actions/workflows/update.yml")
            require(workflow["path"] == ".github/workflows/update.yml", "Unexpected workflow")
            current = release_data(repo, release_ref(repo))
            observations[repo] = {**current, "workflow_state": workflow["state"],
                                  "last_verified": last_verified(repo, fixed_now)}
        except (ValueError, OSError, KeyError, TypeError, AttributeError, IndexError):
            errors[repo] = "public_inspection_failed"
    if PRODUCER not in errors:
        producer = observations[PRODUCER]
        producer["sync_status"] = "producer"
        for repo in REPOSITORIES[1:]:
            if repo in errors:
                continue
            row = observations[repo]
            if row["shared_version"] == producer["shared_version"]:
                row["sync_status"] = "current"
            elif row["bundle_sha256"] == producer["bundle_sha256"]:
                row["sync_status"] = "metadata_pending"
            else:
                try:
                    row["lag_since"] = lag_since(row, producer, fixed_now or datetime.now(timezone.utc))
                    row["sync_status"] = "content_pending"
                except (ValueError, OSError, KeyError, TypeError, AttributeError, IndexError):
                    errors[repo] = "sync_history_unavailable"
    else:
        for repo in REPOSITORIES[1:]:
            errors.setdefault(repo, "producer_inspection_unavailable")
    now = fixed_now or datetime.now(timezone.utc)
    return {"schema_version": 1, "checked_at": stamp(now), "limits": LIMITS,
            "source_commit": os.environ.get("GITHUB_SHA", ""),
            "run_id": os.environ.get("GITHUB_RUN_ID", ""),
            "observations": observations, "errors": errors,
            "problems": assess(observations, errors, now)}


def health_issues():
    found = []
    for page in range(1, 11):
        rows = issue_api("issues?state=open&per_page=100&page="+str(page))
        found += [r for r in rows if "pull_request" not in r and
                  r.get("user", {}).get("login") == "github-actions[bot]" and
                  (r.get("body") or "").startswith(MARKER)]
        if len(rows) < 100:
            return found
    raise HealthError("issue_inventory_incomplete")


def notify(report, now=None):
    now = now or datetime.now(timezone.utc)
    source, url = identity()  # Own repository/main/event only; no cross-repository writes.
    require(report.get("schema_version") == 1 and report["limits"] == LIMITS, "Unknown health report")
    require(report["source_commit"] == source and report["run_id"] == os.environ["GITHUB_RUN_ID"],
            "Health report/run mismatch")
    require(timedelta(0) <= now - utc(report["checked_at"]) <= timedelta(minutes=30), "Stale health report")
    require(set(report["observations"]) | set(report["errors"]) == set(REPOSITORIES), "Incomplete assessment")
    require(report["problems"] == assess(report["observations"], report["errors"], utc(report["checked_at"])),
            "Health verdict mismatch")
    # Legitimate publication during inspection must not produce a stale alert or close an old one.
    for repo, row in report["observations"].items():
        if release_ref(repo) != row["release"]:
            return {"status": "deferred_release_advanced"}
    existing = health_issues()
    require(len(existing) <= 1, "Multiple health incidents require review")
    problems = report["problems"]
    if not problems:
        if existing:
            identity()  # Recheck source immediately before the mutation.
            saved = issue_api("issues/"+str(existing[0]["number"]),
                {"state":"closed","body":existing[0]["body"]+"\n\n恢复：检查未发现过期或实际内容滞后。\n"+url},
                method="PATCH")
            require(saved["state"] == "closed", "Issue close readback mismatch")
            return {"status":"recovered"}
        return {"status":"healthy"}
    # Ages, run IDs, subsequent upstream versions and metadata-only drift do not create duplicate notices.
    fingerprint = "<!-- fingerprint:"+digest(json_bytes(problems))+" -->"
    if existing and fingerprint in existing[0]["body"]:
        return {"status":"deduplicated"}
    labels = {"workflow_disabled":"发布工作流未启用", "activity_overdue":"成功验证/检查已过期",
              "content_lag":"实际规则内容持续超过 24 小时未同步", "inspection_unavailable":"公开状态无法核实"}
    body = MARKER+"\n"+fingerprint+"\n\n三端更新健康检查需要处理；不代表设备连接已失效。\n\n"
    body += "\n".join("- "+p["repository"]+"："+labels[p["kind"]] for p in problems)
    body += "\n\n本次通知采样时间："+report["checked_at"]+"\n证据运行："+url
    body += "\n\n完整状态见该运行 health-report 附件。仅元数据变化不作为规则落后；未获取到证据时不会宣布恢复。"
    payload = {"body":body}
    if existing:
        path, method = "issues/"+str(existing[0]["number"]), "PATCH"
    else:
        path, method = "issues", "POST"
        payload["title"] = "三端更新健康检查：过期或同步状态需处理"
    identity()
    saved = issue_api(path, payload, method=method)
    require(saved["body"] == body and saved["state"] == "open", "Issue readback mismatch")
    return {"status":"updated" if existing else "opened", "issue":saved["number"]}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path)
    p.add_argument("--notify", type=Path)
    args = p.parse_args()
    try:
        require(not (args.output and args.notify), "Choose inspect or notify")
        if args.notify:
            require(not args.notify.is_symlink() and args.notify.stat().st_size <= 1024*1024, "Invalid report")
            result = notify(json.loads(args.notify.read_bytes()))
        else:
            result = collect()
            if args.output:
                target = args.output.resolve()
                require(not target.is_relative_to(ROOT) and not target.exists(), "Output must be new and outside source")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(json_bytes(result))
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError, AttributeError, IndexError):
        print("HEALTH STOPPED: could not verify health or notification; no release was changed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

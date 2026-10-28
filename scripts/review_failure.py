"""Create review-only draft PRs. Never edits an active baseline or merges a PR."""
import json
import os
from pathlib import Path
import re
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared import core, engine as e
from diagnostics import analyze
from shared_data import RAW, fetch, release_sha
from build import REPOSITORY
from publish import api
from release_health import identity


def verified_evidence(directory):
    e.require(not directory.is_symlink(), "Symlink evidence")
    sums = (directory/"EVIDENCE_SHA256SUMS").read_text(encoding="utf-8")
    files = {}
    for line in sums.splitlines():
        e.require(bool(re.fullmatch(r"[a-f0-9]{64}  [a-zA-Z0-9_./-]+", line)), "Invalid evidence checksum")
        sha, name = line.split("  ", 1)
        e.require(not name.startswith("/") and ".." not in name.split("/") and name not in files,
                  "Unsafe evidence path")
        p = directory/name
        e.require(not any(x.is_symlink() for x in (p, *p.parents)), "Symlink evidence")
        e.require(p.stat().st_size <= 16*1024*1024, "Oversized evidence")
        data = p.read_bytes()
        e.require(e.digest(data) == sha, "Evidence hash mismatch")
        files[name] = data
    actual = {p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file()}
    e.require(actual == set(files) | {"EVIDENCE_SHA256SUMS"}, "Evidence inventory mismatch")
    conf = core.read_json("sources.yaml")
    allowed = {"report.json", "proposed-baseline.json", "source-states.json", "snapshot/sources.lock.json"}
    allowed |= {"snapshot/snapshots/" + c + "/" + s["id"] + ".txt"
                for c in ("shadowrocket", "clash") for s in conf["sources"]}
    allowed |= {"snapshot/licenses/" + n + ".txt" for n in conf["repositories"]}
    e.require(set(files) <= allowed, "Unapproved evidence files")
    report = json.loads(files["report.json"])
    e.require(report.get("schema_version") == 1 and report.get("publishable") is False,
              "Not review-only evidence")
    e.require(report.get("source_commit") == os.environ.get("GITHUB_SHA") and
              report.get("run_id") == os.environ.get("GITHUB_RUN_ID"), "Evidence/run mismatch")
    return report, files


def validate_proposal(report, files):
    e.require(report["status"] == "failure" and report["category"] == "quantity_review" and
              report["candidate_eligible"] and report["complete_snapshot"], "Not a quantity candidate")
    prior_sha = report["previous_release"]
    e.require(bool(re.fullmatch("[a-f0-9]{40}", prior_sha)), "Invalid baseline release")
    e.require(release_sha(REPOSITORY) == prior_sha, "Release advanced; candidate is stale")
    previous = json.loads(fetch(RAW + REPOSITORY + "/" + prior_sha + "/source_stats.json"))
    conf = core.read_json("sources.yaml")
    lock = json.loads(files["snapshot/sources.lock.json"])
    inputs = {p.removeprefix("snapshot/"):d for p,d in files.items()
              if p.startswith(("snapshot/snapshots/","snapshot/licenses/"))}
    expected = {"snapshots/"+c+"/"+s["id"]+".txt"
                for c in ("shadowrocket","clash") for s in conf["sources"]}
    expected |= {"licenses/"+n+".txt" for n in conf["repositories"]}
    e.require(set(inputs) == set(lock["files"]) == expected, "Incomplete source snapshot")
    e.require(lock["schema_version"] == 2 and set(lock["repositories"]) == set(conf["repositories"]),
              "Unexpected source lock")
    # Bind each archived file to the configured source, not an arbitrary artifact URL.
    for n, spec in conf["repositories"].items():
        row = lock["repositories"][n]
        e.require(row["repo"] == spec["repo"] and re.fullmatch("[a-f0-9]{40}", row["commit"]),
                  "Upstream identity mismatch")
    urls = {}
    for c in ("shadowrocket","clash"):
        for s in core.source_specs(conf, c):
            upstream = lock["repositories"].get(s.get("repository"))
            urls["snapshots/"+c+"/"+s["id"]+".txt"] = (
                RAW+upstream["repo"]+"/"+upstream["commit"]+"/"+s["path"] if upstream else s["url"])
    for n, spec in conf["repositories"].items():
        row = lock["repositories"][n]
        urls["licenses/"+n+".txt"] = RAW+row["repo"]+"/"+row["commit"]+"/"+spec["license_path"]
    e.require(all(lock["files"][p] == {"url":urls[p],"sha256":e.digest(d)} for p,d in inputs.items()),
              "Source identity/hash mismatch")
    changes, states, proposed, gates = analyze(conf, inputs, previous, core.read_json("baseline.json"))
    identity_data = {"category":"quantity_review","gates":gates,"sources":{}}
    e.require(bool(gates) and report["fingerprint"] == e.digest(e.json_bytes(identity_data)) and
              report["clients"] == changes and report["gates"] == gates, "Report cannot be reproduced")
    e.require(files["proposed-baseline.json"] == e.json_bytes(proposed) and
              files["source-states.json"] == e.json_bytes(states), "Proposal cannot be reproduced")
    return proposed


def propose(report, files):
    sha, url = identity()
    validate_proposal(report, files)
    fp = report["fingerprint"]
    branch = "review/upstream-" + fp[:24]
    existing = api("pulls?state=all&head=SHIKI1255:" + branch + "&per_page=100")
    if existing:
        e.require(len(existing) == 1, "Ambiguous candidate history")
        return existing[0]["html_url"]
    ref = api("git/ref/heads/" + branch, missing_ok=True)
    e.require(ref is None, "Existing candidate branch requires manual review")
    prefix = "reviews/upstream/" + fp + "/"
    docs = {p:files[p].decode("utf-8") for p in ("report.json","proposed-baseline.json","source-states.json")}
    docs["README.md"] = (
        "# 上游数量变化待人工确认\n\n此 PR 只保存审核材料，不修改生效基线。不要把合并本报告当成放行。\n\n"
        "确认新增、删除、顺序和覆盖范围合理后，另行应用 proposed-baseline.json 中对应的精确迁移，"
        "保留现有阈值，补充测试，再通过完整联网构建、离线复现和发布检查。\n\n"
        "运行及完整公开源快照（public-evidence，保留 7 天）：" + url + "\n\n"
        "原始源码：" + sha + "\n前一发布：" + report["previous_release"] +
        "\n\n若源码、上游或前一发布已变化，必须重新生成审核，不能套用旧候选。\n")
    parent = api("git/commits/" + sha)
    tree = api("git/trees", {"base_tree":parent["tree"]["sha"],"tree":[
        {"path":prefix+p,"mode":"100644","type":"blob","content":data} for p,data in sorted(docs.items())]})
    commit = api("git/commits", {"message":"Prepare upstream quantity review "+fp[:12],
                                 "tree":tree["sha"],"parents":[sha]})
    identity()
    e.require(release_sha(REPOSITORY) == report["previous_release"], "Release advanced")
    api("git/refs", {"ref":"refs/heads/"+branch,"sha":commit["sha"]})
    result = api("pulls", {"title":"待确认：上游数量变化 "+fp[:12], "head":branch,"base":"main","draft":True,
        "body":"数量检查仍阻止发布。本 PR 仅提供差异和拟议精确迁移，必须由使用者确认后另行应用并完成全部验证。\n\n"+url})
    e.require(result.get("draft") is True and result["head"]["sha"] == commit["sha"], "PR readback mismatch")
    return result["html_url"]

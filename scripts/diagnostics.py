"""Public-only failure evidence and non-publishable review proposals."""
import copy
import difflib
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared import core, engine as e
from shared_data import write_files
from shared_data import fetch, RAW

QUANTITY = ("Abnormal ", "Too many added rules:", "Too many removed rules:",
            "Unreviewed source transition:")


def analyze(conf, inputs, previous, accepted):
    reports, states, all_stats, proposed = {}, {}, {}, copy.deepcopy(accepted)
    changed_gates = []
    for client in ("shadowrocket", "clash"):
        rules, stats, conflicts, duplicates, _ = core.assemble(conf, inputs, client)
        e.check_conflicts(conflicts, accepted[client]["allowed_conflicts"])
        core.evaluate(rules, client)
        all_stats[client] = stats
        prior = (previous or {}).get(client, {})
        if set(prior) != set(stats):
            raise e.BuildError("Previous source inventory unavailable")
        changes = []
        for name, current in stats.items():
            old = prior[name]
            before, after = set(old["rules"]), set(current["rules"])
            errors = []
            for check in (
                lambda: e.check_limits({name: current}, {name: accepted[client]["sources"][name]}, conf["limits"]),
                lambda: core.check_previous_limits({name: current}, {name: old},
                           {name: accepted[client]["sources"][name]}, conf["limits"])):
                try: check()
                except e.BuildError as exc:
                    if not str(exc).startswith(QUANTITY): raise
                    errors.append(str(exc))
            if old != current or errors:
                row = {"source": name, "before_count": old["raw_count"],
                       "after_count": current["raw_count"],
                       "added": sorted(after-before), "removed": sorted(before-after),
                       "order_changed": old["rules"] != current["rules"],
                       "order_only": before == after and old["rules"] != current["rules"],
                       "from_sha256": core.source_state_sha256(old),
                       "to_sha256": core.source_state_sha256(current),
                       "gate_errors": sorted(set(errors))}
                changes.append(row)
            if errors:
                changed_gates.append({"client": client, "source": name,
                    "from": core.source_state_sha256(old), "to": core.source_state_sha256(current),
                    "errors": sorted(set(errors))})
                proposed[client]["sources"][name] = {
                    "raw_count": current["raw_count"], "retained_count": current["retained_count"],
                    "reviewed_transition": {"from_sha256": core.source_state_sha256(old),
                                            "to_sha256": core.source_state_sha256(current)}}
                states[client + "/" + name] = {"before": old, "after": current}
        e.check_limits(stats, proposed[client]["sources"], conf["limits"])
        core.check_previous_limits(stats, prior, proposed[client]["sources"], conf["limits"])
        reports[client] = {"changes": changes, "rules": len(rules),
                           "conflicts": len(conflicts), "duplicates": duplicates}
    core.check_source_parity(all_stats, conf)
    qrules, _, conflicts, _, _ = core.assemble(conf, inputs, "quantumultx")
    core.evaluate(qrules, "quantumultx")
    core.q.check_native_conflicts([core.q.convert(r) for r in qrules], conflicts)
    for client in core.CLIENTS:
        rules, *_ = core.assemble(conf, inputs, client)
        voice = [r for r in rules if r.source == "voice"]
        if not voice or any(e.route(rules, ip=r.value.split("/")[0])["policy"] != "PROXY"
                            for r in voice):
            raise e.BuildError("Voice regression")
    return reports, states, proposed, changed_gates


def evidence(root, directory, context, error=None):
    """A separate directory never receives a config, release manifest or publisher input."""
    conf, lock, inputs = context.get("conf"), context.get("lock"), context.get("inputs", {})
    files = {}
    report = {"schema_version": 1, "publishable": False,
              "status": "failure" if error else "success",
              "source_commit": os.environ.get("GITHUB_SHA", ""),
              "previous_release": context.get("previous_release", ""),
              "run_id": os.environ.get("GITHUB_RUN_ID", ""),
              "category": "build_failure" if error else "success", "candidate_eligible": False}
    if conf and lock:
        expected = {"snapshots/" + client + "/" + s["id"] + ".txt"
                    for client in ("shadowrocket", "clash") for s in conf["sources"]}
        expected |= {"licenses/" + name + ".txt" for name in conf["repositories"]}
        e.require(set(inputs) <= expected and set(lock["files"]) == set(inputs),
                  "Unapproved evidence inventory")
        for name, data in inputs.items():
            e.require(e.digest(data) == lock["files"][name]["sha256"], "Evidence hash mismatch")
            files["snapshot/" + name] = data
        files["snapshot/sources.lock.json"] = e.json_bytes(lock)
        report["input_hashes"] = {p: e.digest(d) for p,d in inputs.items()}
        report["complete_snapshot"] = set(inputs) == expected
        if error and report["previous_release"]:
            # Raw text differences remain available even if the new syntax cannot parse.
            # This secondary download is diagnostic only and never supplies build data.
            try:
                prefix = RAW + "SHIKI1255/shadowrocket-config/" + report["previous_release"] + "/"
                prior_lock = json.loads(fetch(prefix + "sources.lock.json"))
                e.require(set(prior_lock["files"]) == expected, "Prior snapshot inventory mismatch")
                raw_changes = {}
                for name, data in inputs.items():
                    old_hash = prior_lock["files"][name]["sha256"]
                    if old_hash == e.digest(data): continue
                    old = fetch(prefix + name)
                    e.require(e.digest(old) == old_hash, "Prior snapshot hash mismatch")
                    diff = list(difflib.unified_diff(old.decode("utf-8").splitlines(),
                                data.decode("utf-8").splitlines(), fromfile="previous", tofile="candidate"))
                    raw_changes[name] = {"from_sha256": old_hash, "to_sha256": e.digest(data),
                                         "diff": diff[:2000], "diff_truncated": len(diff) > 2000}
                report["raw_changes"] = raw_changes
            except (ValueError, KeyError, TypeError, OSError):
                report["raw_diff_status"] = "unavailable; archived candidate snapshot is retained"
        if report["complete_snapshot"] and context.get("previous"):
            try:
                changes, states, proposed, gates = analyze(
                    conf, inputs, context["previous"], core.read_json("baseline.json"))
                report["clients"] = changes
                report["category"] = "quantity_review" if gates else ("build_failure" if error else "success")
                report["candidate_eligible"] = bool(error and gates)
                report["gates"] = gates
                if report["candidate_eligible"]:
                    files["proposed-baseline.json"] = e.json_bytes(proposed)
                    files["source-states.json"] = e.json_bytes(states)
            except (ValueError, KeyError, TypeError, OSError) as exc:
                # Never include raw exception text, response snippets or environment.
                report["category"] = "source_or_routing_review"
                report["analysis_error_type"] = type(exc).__name__
    if error and str(error).startswith(("Download failed", "Public download failed")):
        report["category"] = "download_failure"
    identity = {"category": report["category"], "gates": report.get("gates", []),
                "sources": {} if report.get("gates") else report.get("input_hashes", {})}
    report["fingerprint"] = e.digest(e.json_bytes(identity))
    files["report.json"] = e.json_bytes(report)
    files["EVIDENCE_SHA256SUMS"] = "".join(
        e.digest(data) + "  " + p + "\n" for p,data in sorted(files.items())).encode()
    write_files(root, directory.resolve(), files)
    return report

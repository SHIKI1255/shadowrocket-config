"""One upstream snapshot, three native outputs. Never accepts private subscriptions."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import io
import ipaddress
import json
from pathlib import Path
import re
import tarfile
import urllib.parse

from shared.engine import (
    Rule, RAW, API, CODELOAD, REPOSITORY, fetch, decode, digest, json_bytes,
    require, records, parse_rule, parse_source, merge_rules, check_bounds,
    check_limits, check_conflicts, route, check_case_result,
)
from shared import quantumult as q

ROOT = Path(__file__).resolve().parents[1]
CLIENTS = ("shadowrocket", "quantumultx", "clash")
SUPPLEMENTS = {"telegram_ip": "telegram", "proxy_v2fly": "proxy"}
RETIRED_CLASH_RESOURCES = ("apple_domains", "proxy_domains", "china_domains", "copilot")
SOURCE_STATS_SCHEMA = 2
LOCK_SCHEMA = 3


def read_json(name):
    data = json.loads((ROOT / "shared" / name).read_text(encoding="utf-8"))
    if name == "sources.yaml":
        require(data.get("schema_version") == 2, "Unknown source schema")
        ids = [s["id"] for s in data["sources"]]
        require(len(ids) == len(set(ids)) and all(re.fullmatch(r"[a-z][a-z0-9_]*", i) for i in ids),
                "Invalid or duplicate source identifier")
    return data


def source_specs(conf, client):
    for original in conf["sources"]:
        spec = dict(original)
        if client == "clash":
            if "clash_path" in spec:
                spec["path"], spec["format"] = spec["clash_path"], "clash"
            spec.pop("regex_overrides", None)
        yield spec


def _archive_repos(conf) -> set[str]:
    return {name for name, spec in conf["repositories"].items() if spec.get("archive") == "tarball"}


def expected_snapshot_files(conf) -> set[str]:
    archive_repos = _archive_repos(conf)
    expected = {"snapshots/" + name + "/data.json" for name in archive_repos}
    for s in conf["sources"]:
        if s.get("repository") in archive_repos:
            continue
        expected.add("snapshots/sources/" + s["id"] + ".txt")
    expected |= {"licenses/" + name + ".txt" for name in conf["repositories"]}
    return expected


def expected_snapshot_urls(conf, lock) -> dict[str, str]:
    archive_repos = _archive_repos(conf)
    urls: dict[str, str] = {}
    for name in archive_repos:
        row = lock["repositories"][name]
        urls["snapshots/" + name + "/data.json"] = CODELOAD + row["repo"] + "/tar.gz/" + row["commit"]
    for s in conf["sources"]:
        repo_name = s.get("repository")
        if repo_name in archive_repos:
            continue
        row = lock["repositories"].get(repo_name)
        urls["snapshots/sources/" + s["id"] + ".txt"] = (
            RAW + row["repo"] + "/" + row["commit"] + "/" + s["path"] if row else s["url"]
        )
    for name, spec in conf["repositories"].items():
        row = lock["repositories"][name]
        lic_commit = row.get("license_commit") or row["commit"]
        urls["licenses/" + name + ".txt"] = RAW + row["repo"] + "/" + lic_commit + "/" + spec["license_path"]
    return urls


def _resolve_ref(repo: str, ref: str) -> str:
    if re.fullmatch(r"[a-f0-9]{40}", ref):
        return ref
    sha = json.loads(fetch(
        API + "repos/" + repo + "/git/ref/heads/" + urllib.parse.quote(ref, safe="")
    ))["object"]["sha"]
    require(re.fullmatch(r"[a-f0-9]{40}", sha), "Invalid upstream SHA")
    return sha


def _extract_v2fly_tarball(tar_bytes: bytes, conf: dict, repo_name: str) -> tuple[bytes, bytes]:
    """Safely extract LICENSE and only transitively reachable data/* files from a v2fly tarball."""
    all_data: dict[str, str] = {}
    license_bytes: bytes | None = None
    license_path = conf["repositories"][repo_name]["license_path"]
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as tf:
        for member in tf.getmembers():
            if member.isdir():
                continue
            require(member.isfile() and not member.issym() and not member.islnk(),
                    f"Unsafe tarball member in {repo_name}: {member.name}")
            parts = member.name.split("/", 1)
            if len(parts) != 2:
                continue
            rel = parts[1]
            require(".." not in rel.split("/") and not rel.startswith("/"),
                    f"Unsafe tarball path: {rel}")
            if rel == license_path:
                require(member.size <= 512 * 1024, "Oversized license file")
                extracted = tf.extractfile(member)
                require(extracted is not None, "Missing license stream")
                license_bytes = extracted.read()
                decode(license_bytes)
            elif rel.startswith("data/") and "/" not in rel[5:]:
                require(member.size <= 2 * 1024 * 1024, f"Oversized v2fly file: {rel}")
                extracted = tf.extractfile(member)
                require(extracted is not None, f"Missing v2fly stream: {rel}")
                all_data[rel[5:]] = decode(extracted.read())
    require(license_bytes is not None, f"Missing {license_path} in {repo_name} tarball")

    # Transitively collect only files reachable from configured sources for this repo.
    reachable: dict[str, str] = {}

    def visit(name: str, stack: tuple[str, ...] = ()):
        require(name not in stack, f"v2fly include cycle: {' -> '.join((*stack, name))}")
        require(name in all_data, f"Missing v2fly data file: {name}")
        if name in reachable:
            return
        text = all_data[name]
        reachable[name] = text
        for _, raw_line in records(text):
            content = raw_line.split("#", 1)[0].strip()
            if not content:
                continue
            token = content.split()[0]
            if token.startswith("include:"):
                sub = token.split(":", 1)[1]
                require(re.fullmatch(r"[a-zA-Z0-9_!.-]+", sub) is not None and ".." not in sub,
                        f"Invalid v2fly include target: {sub}")
                visit(sub, (*stack, name))

    for s in conf["sources"]:
        if s.get("repository") != repo_name:
            continue
        paths = s.get("paths") or [s["path"]]
        for path in paths:
            visit(path.removeprefix("data/"))

    require(bool(reachable), f"No reachable data files in {repo_name}")
    return json_bytes(reachable), license_bytes


def collect(conf, replay_dir=None, replay_commit="", capture=None):
    expected = expected_snapshot_files(conf)
    if replay_dir is not None or replay_commit:
        require(replay_dir is not None or re.fullmatch(r"[a-f0-9]{40}", replay_commit),
                "Full replay SHA required")

        def read(path):
            require(re.fullmatch(r"[a-zA-Z0-9_./-]+", path) and ".." not in path.split("/")
                    and not path.startswith("/"), "Unsafe snapshot path")
            return (replay_dir / path).read_bytes() if replay_dir else fetch(
                RAW + REPOSITORY + "/" + replay_commit + "/" + path
            )

        lock = json.loads(read("sources.lock.json"))
        require(lock.get("schema_version") == LOCK_SCHEMA,
                "Use an archived pre-migration configuration for old schema releases")
        require(set(lock["files"]) == expected, "Replay source inventory mismatch")
        files = {p: read(p) for p in lock["files"]}
        for p, data in files.items():
            require(digest(data) == lock["files"][p]["sha256"], "Snapshot hash mismatch: " + p)
        if capture is not None:
            capture(lock, files)
        return lock, files

    revisions = {}
    for name, spec in conf["repositories"].items():
        sha = _resolve_ref(spec["repo"], spec["ref"])
        rev = {"repo": spec["repo"], "commit": sha, "ref": spec["ref"], "license": spec["license"]}
        if spec.get("license_ref"):
            rev["license_ref"] = spec["license_ref"]
            rev["license_commit"] = _resolve_ref(spec["repo"], spec["license_ref"])
        revisions[name] = rev

    archive_repos = _archive_repos(conf)
    raw_requests: dict[str, str] = {}
    tar_requests: dict[str, str] = {}
    for name in archive_repos:
        r = revisions[name]
        tar_requests[name] = CODELOAD + r["repo"] + "/tar.gz/" + r["commit"]
    for s in conf["sources"]:
        repo_name = s.get("repository")
        if repo_name in archive_repos:
            continue
        r = revisions.get(repo_name)
        url = RAW + r["repo"] + "/" + r["commit"] + "/" + s["path"] if r else s["url"]
        raw_requests["snapshots/sources/" + s["id"] + ".txt"] = url
    for name, r in revisions.items():
        if name in archive_repos:
            continue
        lic_commit = r.get("license_commit") or r["commit"]
        raw_requests["licenses/" + name + ".txt"] = (
            RAW + r["repo"] + "/" + lic_commit + "/" + conf["repositories"][name]["license_path"]
        )

    all_urls = list(dict.fromkeys([*tar_requests.values(), *raw_requests.values()]))
    downloaded, failures = {}, []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {url: pool.submit(fetch, url) for url in all_urls}
        for url, future in futures.items():
            try:
                downloaded[url] = future.result()
            except (ValueError, OSError) as exc:
                failures.append(exc)

    files: dict[str, bytes] = {}
    file_urls: dict[str, str] = {}
    for path, url in raw_requests.items():
        if url in downloaded:
            files[path] = downloaded[url]
            file_urls[path] = url
    for name, url in tar_requests.items():
        if url in downloaded:
            tar_bytes = downloaded[url]
            revisions[name]["tarball_sha256"] = digest(tar_bytes)
            if not failures:
                bundle_bytes, lic_bytes = _extract_v2fly_tarball(tar_bytes, conf, name)
                files["snapshots/" + name + "/data.json"] = bundle_bytes
                file_urls["snapshots/" + name + "/data.json"] = url
                files["licenses/" + name + ".txt"] = lic_bytes
                file_urls["licenses/" + name + ".txt"] = (
                    RAW + revisions[name]["repo"] + "/" + revisions[name]["commit"] + "/"
                    + conf["repositories"][name]["license_path"]
                )

    lock = {
        "schema_version": LOCK_SCHEMA,
        "repositories": revisions,
        "files": {p: {"url": file_urls[p], "sha256": digest(data)} for p, data in files.items()},
    }
    if failures:
        if capture is not None:
            capture(lock, files)
        raise failures[0]

    require(set(files) == expected, "Collected snapshot inventory mismatch")
    if capture is not None:
        capture(lock, files)
    for data in files.values():
        decode(data)
    print(json.dumps({
        "event": "public_source_snapshot",
        "repositories": revisions,
        "files_sha256": {p: row["sha256"] for p, row in lock["files"].items()},
    }, sort_keys=True))
    return lock, files


def assemble(conf, inputs, client):
    platform = read_json("platforms.json")
    target = "clash" if client == "clash" else "shadowrocket"
    bootstrap = [parse_rule(x, source="local_direct") for x in platform[target]["bootstrap"]]
    custom = [
        parse_rule(x, source="personal_" + x.split(",")[2].lower())
        for _, x in records((ROOT / "shared/custom.list").read_text(encoding="utf-8"))
    ]
    require(all(r.kind.startswith("DOMAIN") for r in custom), "Only shared domain selectors belong in custom.list")
    require([r.policy for r in custom] == sorted(r.policy for r in custom), "Personal DIRECT must precede PROXY")
    direct = [r for r in custom if r.policy == "DIRECT"]
    proxy = [r for r in custom if r.policy == "PROXY"]
    if client == "clash":
        processes = [
            parse_rule(x, source="personal_" + x.split(",")[2].lower())
            for x in platform["clash"]["process_rules"]
        ]
        require(all(r.kind == "PROCESS-NAME" for r in processes), "Invalid process extension")
        direct += [r for r in processes if r.policy == "DIRECT"]
        proxy = [r for r in processes if r.policy == "PROXY"] + proxy
    rules = bootstrap + direct + proxy

    archive_repos = _archive_repos(conf)
    v2fly_bundles = {
        name: json.loads(decode(inputs["snapshots/" + name + "/data.json"]))
        for name in archive_repos
    }
    stats, origins = {}, {}
    for spec in source_specs(conf, target):
        repo_name = spec.get("repository")
        if repo_name in archive_repos:
            parsed, stats[spec["id"]] = parse_source(spec, None, v2fly_data=v2fly_bundles[repo_name])
        else:
            parsed, stats[spec["id"]] = parse_source(spec, inputs["snapshots/sources/" + spec["id"] + ".txt"])
        require(not any(r.kind in {"FINAL", "MATCH", "GEOIP"} for r in parsed), "Unexpected upstream fallback")
        for r in parsed:
            origins.setdefault(r.line, []).append(spec["id"])
        rules += [replace(r, source=SUPPLEMENTS.get(r.source, r.source)) for r in parsed]

    rules += [
        parse_rule("GEOIP,CN,DIRECT", source="geoip_cn"),
        parse_rule(("MATCH" if client == "clash" else "FINAL") + ",PROXY", source="final_proxy"),
    ]
    for r in bootstrap + direct + proxy:
        origins.setdefault(r.line, []).insert(0, r.source)
    merged, conflicts, duplicates = merge_rules(rules)
    provenance = [
        {"rule": r.line, "category": r.source, "origins": list(dict.fromkeys(origins.get(r.line, [r.source])))}
        for r in merged
    ]
    return merged, stats, conflicts, duplicates, provenance


def case_rows(client):
    return [
        {"name": c["name"], "input": c["input"], **c["expect"][client]}
        for c in read_json("routing_cases.json") if client in c["expect"]
    ]


def evaluate(rules, client):
    native = [q.convert(r) for r in rules] if client == "quantumultx" else None
    results = []
    for c in case_rows(client):
        actual = q.route_native(native, **c["input"]) if native is not None else route(rules, **c["input"])
        check_case_result(c, actual, client)
        if "expected_category" in c:
            require(actual["source"] == c["expected_category"], "Category regression: " + c["name"])
        results.append({**c, "actual": actual})
    return results


def render_lines(rules, labels):
    lines, previous = [], None
    for r in rules:
        if previous != r.source:
            lines += ["", "# " + r.source + " | " + labels[r.source]]
            previous = r.source
        lines.append(r.line)
    return "\n".join(lines).strip() + "\n"


def check_source_parity(stats, conf):
    """Client syntax cannot silently create different common upstream coverage."""
    for spec in conf["sources"]:
        category = spec["id"]
        if category in SUPPLEMENTS:
            continue
        comparable = []
        for client in ("shadowrocket", "clash"):
            names = [category] + [k for k, v in SUPPLEMENTS.items() if v == category]
            lines = []
            for name in names:
                lines += stats[client][name]["rules"]
            selected = set()
            for line in lines:
                r = parse_rule(line)
                if r.kind in {"USER-AGENT", "PROCESS-NAME"}:
                    continue
                if r.kind == "DOMAIN-REGEX":
                    mapped = spec.get("regex_overrides", {}).get(r.value)
                    require(mapped is not None, "Unreviewed client regex difference")
                    r = parse_rule(mapped, r.policy)
                selected.add(r.line)
            comparable.append(selected)
        require(comparable[0] == comparable[1], "Client upstream coverage diverged: " + category)


def overlaps(rules):
    """Report provable earlier suffix coverage, never remove it automatically."""
    suffixes, result = {}, []
    for r in rules:
        if r.kind not in {"DOMAIN", "DOMAIN-SUFFIX"}:
            continue
        parts = r.value.lstrip(".").split(".")
        for i in range(len(parts)):
            prior = suffixes.get(".".join(parts[i:]))
            if prior is not None and prior.policy == r.policy and prior.line != r.line:
                result.append({"rule": r.line, "earlier": prior.line, "category": r.source})
                break
        if r.kind == "DOMAIN-SUFFIX" and not r.value.startswith("."):
            suffixes.setdefault(r.value, r)
    return result


def source_state_sha256(row):
    """Identify the complete ordered rule state and its counts, not just a size."""
    return digest(json_bytes({k: row[k] for k in ("raw_count", "retained_count", "rules")}))


def check_previous_limits(stats, previous, accepted, limits, *, sources_conf=None):
    """Apply only a reviewed exact transition; all other deltas keep their tiered gates."""
    require(set(stats) == set(previous), "Source inventory changed: review baseline")
    comparison = dict(previous)
    for name, current in stats.items():
        transition = accepted[name].get("reviewed_transition")
        if transition is None:
            continue
        require(
            isinstance(transition, dict)
            and set(transition) == {"from_sha256", "to_sha256"}
            and all(isinstance(v, str) and re.fullmatch(r"[a-f0-9]{64}", v) for v in transition.values()),
            "Malformed reviewed transition: " + name,
        )
        if source_state_sha256(previous[name]) == transition["from_sha256"]:
            require(
                source_state_sha256(current) == transition["to_sha256"],
                "Unreviewed source transition: " + name,
            )
            comparison[name] = current
    check_limits(stats, comparison, limits, sources_conf=sources_conf)


def build(conf, lock, inputs, previous=None, audit=False):
    labels = read_json("categories.json")
    accepted = read_json("baseline.json")
    sources_conf = {s["id"]: s for s in conf["sources"]}
    allowed_categories = accepted.get("allowed_category_conflicts", [])
    payload = {
        "schema_version": 1,
        "categories": labels,
        "platforms": {},
        "licenses": {p: decode(d) for p, d in inputs.items() if p.startswith("licenses/")},
    }
    baselines, all_stats, provenance, reports, coverage = {}, {}, {}, {}, {}
    prev_schema_ok = previous is not None and previous.get("schema_version") == SOURCE_STATS_SCHEMA
    if previous is not None and not prev_schema_ok:
        print(json.dumps({
            "event": "previous_release_schema_upgrade",
            "from_schema": previous.get("schema_version", 1),
            "to_schema": SOURCE_STATS_SCHEMA,
        }, sort_keys=True))

    for client in CLIENTS:
        rules, stats, conflicts, duplicates, provenance[client] = assemble(conf, inputs, client)
        coverage[client] = overlaps(rules)
        source_client = "clash" if client == "clash" else "shadowrocket"
        all_stats[source_client] = stats
        baselines[source_client] = {
            "sources": {k: {f: v[f] for f in ("raw_count", "retained_count")} for k, v in stats.items()},
            "allowed_conflicts": [],
        }
        require(not any(r.kind == "IP-ASN" and r.value in {"14061", "20473"} for r in rules),
                "AI cloud ASN returned")
        if not audit:
            require(set(stats) == set(accepted[source_client]["sources"]),
                    "Source inventory changed: review baseline")
            check_bounds(stats, sources_conf, conf["limits"])
            check_conflicts(
                conflicts,
                accepted[source_client].get("allowed_conflicts", []),
                allowed_categories,
            )
            if prev_schema_ok:
                old = previous.get(source_client)
                if old:
                    check_previous_limits(
                        stats, old, accepted[source_client]["sources"],
                        conf["limits"], sources_conf=sources_conf,
                    )
        cases = evaluate(rules, client)
        voice = [r for r in rules if r.source == "voice"]
        require(
            voice and all(
                route(rules, ip=str(ipaddress.ip_network(r.value).network_address))["policy"] == "PROXY"
                for r in voice
            ),
            "Voice regression",
        )
        telegram_ips = [r for r in rules if r.source == "telegram" and r.kind == "IP-CIDR"]
        require(
            telegram_ips and all(
                route(rules, ip=str(ipaddress.ip_network(r.value).network_address))["policy"] == "PROXY"
                for r in telegram_ips
            ),
            "Telegram IP regression",
        )
        report = {
            "rules": len(rules),
            "removed_exact_duplicates": duplicates,
            "conflicts": conflicts,
            "routing_cases": cases,
            "voice_ip_cases": len(voice),
            "telegram_ip_cases": len(telegram_ips),
        }
        data = {"report": report}
        if client == "shadowrocket":
            data["rules_text"] = render_lines(rules, labels)
        elif client == "quantumultx":
            native, seen = [], set()
            for r in rules:
                n = q.convert(r)
                if n.line not in seen:
                    seen.add(n.line)
                    native.append(n)
            q.check_native_conflicts(native, conflicts)
            boot = set(read_json("platforms.json")["quantumultx"]["bootstrap_values"])
            local = [r for r in native if r.source == "local_direct" and r.value in boot]
            remote = [r for r in native if r not in local and r.kind != "final"]
            require(remote[-1].line == "geoip, cn, direct", "Wrong Quantumult X tail")
            data.update({"rules_text": render_lines(remote, labels), "local_rules_text": render_lines(local, labels)})
            report.update({
                "native_rules": len(native),
                "local_rules": len(local) + 1,
                "remote_rules": len(remote),
                "removed_native_duplicates": len(rules) - len(native),
                "no_resolve_removed": sum(bool(r.options) for r in rules),
                "user_agent_priority_changes": sum(r.kind == "USER-AGENT" for r in rules),
            })
        else:
            desc = [{"id": k, "policy": "DIRECT" if k == "personal_direct" else "PROXY"}
                    for k in ("personal_direct", "personal_proxy")]
            desc += [{"id": s["id"], "policy": s["policy"]} for s in conf["sources"] if s["id"] not in SUPPLEMENTS]
            data["providers"] = desc
            data["rules"] = {}
            for d in desc:
                selected = [r for r in rules if r.source == d["id"]]
                require(selected and all(r.policy == d["policy"] for r in selected),
                        "Empty or mixed-policy category")
                data["rules"][d["id"]] = (
                    "# " + d["id"] + " | " + labels[d["id"]] + "\n"
                    + "".join(r.selector + "\n" for r in selected)
                )
            # Retired URLs remain available as empty compatibility placeholders so existing installs never 404.
            for old in RETIRED_CLASH_RESOURCES:
                data["rules"][old] = "# Compatibility resource; retired or merged in v2\n"
            bootstrap = read_json("platforms.json")["clash"]["bootstrap"]
            data["guard"] = {"rules": bootstrap + ["MATCH,REJECT"]}
            report["providers"] = {d["id"]: len(list(records(data["rules"][d["id"]]))) for d in desc}
        reports[client] = report
        payload["platforms"][client] = data

    check_source_parity(all_stats, conf)
    all_stats_doc = {"schema_version": SOURCE_STATS_SCHEMA, **all_stats}
    files = {**inputs, "sources.lock.json": json_bytes(lock), "source_stats.json": json_bytes(all_stats_doc)}
    if audit:
        baselines["schema_version"] = 2
        baselines["allowed_category_conflicts"] = allowed_categories
        files.update({"candidate_baseline.json": json_bytes(baselines), "candidate_report.json": json_bytes(reports)})
        return files

    bundle = json_bytes(payload)
    source_hashes = {
        p.relative_to(ROOT).as_posix(): digest(p.read_bytes())
        for p in sorted((ROOT / "shared").rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }
    basis = {
        "schema_version": 1,
        "bundle_sha256": digest(bundle),
        "source_files_sha256": source_hashes,
        "upstreams": lock["repositories"],
        "provenance_sha256": digest(json_bytes(provenance)),
    }
    manifest = {**basis, "shared_version": digest(json_bytes(basis))}
    files.update({
        "shared/bundle.json": bundle,
        "shared/manifest.json": json_bytes(manifest),
        "shared/provenance.json": json_bytes(provenance),
        "shared/overlaps.json": json_bytes(coverage),
    })
    return files

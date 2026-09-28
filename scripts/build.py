"""Deterministic Shadowrocket builder. Python 3.12, no third-party dependencies."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import fnmatch
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "SHIKI1255/shadowrocket-config"
RAW = "https://raw.githubusercontent.com/"
API = "https://api.github.com/"
MAX_BYTES = 8 * 1024 * 1024
KINDS = {"DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD", "DOMAIN-WILDCARD",
         "IP-CIDR", "IP-ASN", "USER-AGENT", "GEOIP", "FINAL"}


class BuildError(ValueError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def require(condition, message):
    if not condition:
        raise BuildError(message)


def fetch(url: str, *, missing_ok=False) -> bytes | None:
    parsed = urllib.parse.urlparse(url)
    require(parsed.scheme == "https" and parsed.hostname in
            {"raw.githubusercontent.com", "api.github.com", "openai.com"},
            "Unapproved source URL")
    require(not parsed.username and not parsed.password and not parsed.query,
            "Credentials and query strings are not source URLs")
    headers = {"User-Agent": "shadowrocket-config-builder/1", "Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if parsed.hostname == "api.github.com" and token:
        headers["Authorization"] = "Bearer " + token
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as response:
            require(urllib.parse.urlparse(response.url).hostname == parsed.hostname,
                    "Unexpected cross-host redirect")
            data = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if missing_ok and exc.code == 404:
            return None
        raise BuildError(f"Download failed: HTTP {exc.code}: {url}") from None
    except (OSError, urllib.error.URLError) as exc:
        raise BuildError(f"Download failed: {url}: {type(exc).__name__}") from None
    require(0 < len(data) <= MAX_BYTES, f"Empty or oversized source: {url}")
    return data


def decode(data: bytes) -> str:
    try:
        value = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise BuildError("Source is not UTF-8") from exc
    require("\x00" not in value and not value.lstrip().startswith(("<!DOCTYPE", "<html")),
            "Unexpected binary/HTML source")
    return value


@dataclass(frozen=True)
class Rule:
    kind: str
    value: str
    policy: str
    options: tuple[str, ...] = ()
    source: str = "custom"

    @property
    def selector(self):
        # no-resolve changes DNS behavior, so preserve it as part of the selector.
        return ",".join((self.kind, self.value, *self.options))

    @property
    def line(self):
        if self.kind == "FINAL":
            return "FINAL," + self.policy
        return ",".join((self.kind, self.value, self.policy, *self.options))


def parse_rule(line: str, policy: str | None = None, source="custom") -> Rule:
    parts = [part.strip() for part in line.split(",")]
    kind = parts[0]
    require(kind in KINDS, f"Unknown rule type in {source}: {kind}")
    if kind == "FINAL":
        require(policy is None and len(parts) == 2, "Invalid FINAL rule")
        value, effective, options = "", parts[1], ()
    else:
        require(len(parts) >= (2 if policy else 3), f"Incomplete rule: {line}")
        value = parts[1]
        effective = policy or parts[2]
        options = tuple(parts[2:] if policy else parts[3:])
    require(effective in {"DIRECT", "PROXY"}, f"Unapproved policy: {effective}")
    require(options in {(), ("no-resolve",)}, f"Unknown options in {source}: {options}")
    require(not options or kind in {"IP-CIDR", "IP-ASN", "GEOIP"}, "Invalid no-resolve")
    if kind.startswith("DOMAIN"):
        require(value == value.lower() and bool(value) and len(value) <= 253,
                f"Invalid domain: {value}")
        pattern = r"[a-z0-9_.?*\-]+" if kind == "DOMAIN-WILDCARD" else r"[a-z0-9_.\-]+"
        require(re.fullmatch(pattern, value) is not None and
                (not value.startswith(".") or kind in {"DOMAIN-SUFFIX", "DOMAIN-KEYWORD"}),
                f"Invalid domain selector: {value}")
        require(".." not in value and not value.endswith("."), f"Invalid domain: {value}")
    elif kind == "IP-CIDR":
        try:
            require("/" in value and str(ipaddress.ip_network(value, strict=True)) == value,
                    "CIDR must be canonical")
        except ValueError as exc:
            raise BuildError(f"Invalid CIDR: {value}") from exc
    elif kind == "IP-ASN":
        require(value.isdecimal() and 0 < int(value) < 2**32, "Invalid ASN")
    elif kind == "GEOIP":
        require(value == "CN", "Only the explicit CN fallback is supported")
    elif kind == "USER-AGENT":
        require(bool(value) and not any(c in value for c in "\r\n\x00"), "Invalid user agent")
    return Rule(kind, value, effective, options, source)


def records(text: str):
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if line and not line.startswith(("#", "//")):
            yield number, line


def parse_source(spec: dict, data: bytes) -> tuple[list[Rule], dict]:
    source, policy, fmt = spec["id"], spec["policy"], spec["format"]
    text = decode(data)
    result, skipped, raw_count = [], [], 0
    if fmt == "voice":
        obj = json.loads(text)
        require(set(obj) == {"creationTime", "prefixes"} and isinstance(obj["prefixes"], list),
                "Unknown voice JSON schema")
        lines = []
        for prefix in obj["prefixes"]:
            require(isinstance(prefix, dict) and len(prefix) == 1 and
                    next(iter(prefix)) in {"ipv4Prefix", "ipv6Prefix"}, "Unknown voice prefix")
            network = ipaddress.ip_network(next(iter(prefix.values())), strict=True)
            require(network.version == (4 if "ipv4Prefix" in prefix else 6) and network.is_global,
                    "Invalid/private voice network")
            require(network.prefixlen >= (24 if network.version == 4 else 48), "Voice range too broad")
            lines.append(f"IP-CIDR,{network},no-resolve")
        lines = list(enumerate(lines, 1))
    else:
        lines = list(records(text))
    for number, line in lines:
        raw_count += 1
        try:
            if fmt in {"shadowrocket", "voice"}:
                rule = parse_rule(line, policy, source)
            elif fmt == "domain_set":
                require("," not in line and " " not in line, "Unknown domain-set syntax")
                prefix = "DOMAIN-SUFFIX" if line.startswith(".") else "DOMAIN"
                rule = parse_rule(f"{prefix},{line.lstrip('.')}", policy, source)
            elif fmt == "v2fly":
                fields = line.split("#", 1)[0].split()
                require(bool(fields) and all(re.fullmatch(r"@!?[a-z0-9_-]+", x) for x in fields[1:]),
                        "Unknown v2fly attributes")
                entry, tags = fields[0], fields[1:]
                require(set(tags) <= {"@ads", "@!cn"}, "Unknown v2fly attribute semantics")
                prefix, value = entry.split(":", 1) if ":" in entry else ("domain", entry)
                if prefix == "regexp":
                    mapped = spec.get("regex_overrides", {}).get(value)
                    require(mapped is not None, "Unknown regexp; manual semantic review required")
                    rule = parse_rule(mapped, policy, source)
                else:
                    mapping = {"domain": "DOMAIN-SUFFIX", "full": "DOMAIN"}
                    require(prefix in mapping, f"Unknown v2fly syntax: {prefix}")
                    rule = parse_rule(f"{mapping[prefix]},{value}", policy, source)
                if "@ads" in tags:
                    skipped.append({"line": number, "rule": rule.line, "reason": "ads attribute"})
                    continue
            else:
                raise BuildError(f"Unknown source format: {fmt}")
            if rule.value in spec.get("exclude_domains", []):
                skipped.append({"line": number, "rule": rule.line, "reason": "domestic compatibility"})
                continue
            allow = spec.get("allow_rules")
            if allow is not None and rule.selector not in allow:
                skipped.append({"line": number, "rule": rule.line, "reason": "Copilot ownership filter"})
                continue
            result.append(rule)
        except (ValueError, TypeError) as exc:
            raise BuildError(f"{source}:{number}: {exc}") from exc
    require(raw_count > 0 and result, f"Empty rules: {source}")
    stats = {"raw_count": raw_count, "retained_count": len(result),
             "rules": [r.line for r in result], "skipped": skipped}
    return result, stats


def merge_rules(rules: list[Rule]) -> tuple[list[Rule], list[dict], int]:
    seen, first, conflicts, unique, duplicates = set(), {}, {}, [], 0
    for rule in rules:
        if rule.line in seen:
            duplicates += 1
            continue
        seen.add(rule.line)
        prior = first.get(rule.selector)
        if prior is not None and prior.policy != rule.policy:
            key = (rule.selector, prior.policy, prior.source, rule.policy, rule.source)
            conflicts[key] = {"selector": rule.selector, "winner": prior.policy,
                              "winner_source": prior.source, "later": rule.policy,
                              "later_source": rule.source}
        else:
            first.setdefault(rule.selector, rule)
        unique.append(rule)
    return unique, sorted(conflicts.values(), key=lambda x: json.dumps(x, sort_keys=True)), duplicates


def check_limits(stats: dict, baseline: dict, limits: dict):
    require(set(stats) == set(baseline), "Source inventory changed: review baseline")
    for name, current in stats.items():
        old = baseline[name]
        for key in ("raw_count", "retained_count"):
            require(old[key] > 0, f"Invalid baseline: {name}")
            ratio = current[key] / old[key]
            require(limits["min_ratio"] <= ratio <= limits["max_ratio"],
                    f"Abnormal {name} {key}: {old[key]} -> {current[key]}")
        if "rules" in old:
            before, after = set(old["rules"]), set(current["rules"])
            require(len(before - after) / len(before) <= limits["max_removed_ratio"],
                    f"Too many removed rules: {name}")
            require(len(after - before) / len(before) <= limits["max_added_ratio"],
                    f"Too many added rules: {name}")


def check_conflicts(conflicts: list[dict], allowed: list[dict]):
    approved = {json.dumps(row, sort_keys=True) for row in allowed}
    unknown = [row for row in conflicts if json.dumps(row, sort_keys=True) not in approved]
    require(not unknown, "Unregistered policy conflicts: " + json.dumps(unknown, ensure_ascii=False))


def route(rules: list[Rule], *, domain="", ip="", country="", asn="", user_agent="") -> dict:
    """Conservative static matcher, not the Shadowrocket runtime.

    Without IP/geolocation, DNS-dependent IP/GEOIP rules are unresolved; FINAL is
    never presented as a proven domain result. no-resolve rules don't trigger DNS.
    """
    host = domain.lower().rstrip(".")
    address = ipaddress.ip_address(ip) if ip else None
    unresolved = False
    for rule in rules:
        kind, value = rule.kind, rule.value
        matches = False
        if kind == "DOMAIN":
            matches = bool(host) and host == value
        elif kind == "DOMAIN-SUFFIX":
            # Preserve upstream's literal leading-dot suffix; do not broaden it to an apex.
            matches = bool(host) and (host.endswith(value) if value.startswith(".") else
                                     (host == value or host.endswith("." + value)))
        elif kind == "DOMAIN-KEYWORD":
            matches = bool(host) and value in host
        elif kind == "DOMAIN-WILDCARD":
            matches = bool(host) and fnmatch.fnmatchcase(host, value)
        elif kind == "USER-AGENT":
            matches = bool(user_agent) and fnmatch.fnmatchcase(user_agent, value)
        elif kind == "IP-CIDR":
            matches = address is not None and address in ipaddress.ip_network(value)
            if address is None and not rule.options:
                unresolved = True
        elif kind == "IP-ASN":
            matches = bool(asn) and str(asn) == value
            if not asn and not rule.options:
                unresolved = True
        elif kind == "GEOIP":
            matches = bool(country) and country == value
            if not country:
                unresolved = True
        elif kind == "FINAL":
            matches = True
        if matches:
            return {"policy": "UNRESOLVED" if unresolved else rule.policy,
                    "rule": rule.line, "source": rule.source}
    return {"policy": "UNRESOLVED", "rule": "", "source": ""}


def check_cases(rules: list[Rule], cases: list[dict]) -> list[dict]:
    results = []
    for case in cases:
        actual = route(rules, **case["input"])
        require(actual["policy"] == case["policy"], f"Routing regression {case['name']}: {actual}")
        results.append({**case, "actual": actual})
    return results


def load_config(root: Path) -> dict:
    # JSON is a strict subset of YAML 1.2. No general YAML parser or executable tags.
    conf = json.loads((root / "config/sources.yaml").read_text(encoding="utf-8"))
    require(conf["schema_version"] == 1, "Unsupported source config")
    ids = [s["id"] for s in conf["sources"]]
    require(len(ids) == len(set(ids)) and all(re.fullmatch(r"[a-z0-9_]+", i) for i in ids),
            "Invalid/duplicate source id")
    return conf


def collect(conf: dict) -> tuple[dict, dict[str, bytes]]:
    revisions = {}
    for name, repo in conf["repositories"].items():
        ref = repo["ref"]
        if re.fullmatch(r"[0-9a-f]{40}", ref):
            sha = ref
        else:
            url = API + "repos/" + repo["repo"] + "/commits/" + urllib.parse.quote(ref, safe="")
            sha = json.loads(fetch(url))["sha"]
        require(re.fullmatch(r"[0-9a-f]{40}", sha) is not None, "Invalid upstream commit")
        revisions[name] = {"repo": repo["repo"], "commit": sha, "ref": ref, "license": repo["license"]}
    requests = []
    for spec in conf["sources"]:
        if "repository" in spec:
            repo = revisions[spec["repository"]]
            url = RAW + repo["repo"] + "/" + repo["commit"] + "/" + spec["path"]
        else:
            url = spec["url"]
        requests.append(("snapshots/" + spec["id"] + ".txt", url, spec["id"]))
    for name, repo in revisions.items():
        path = conf["repositories"][name]["license_path"]
        requests.append(("licenses/" + name + ".txt", RAW + repo["repo"] + "/" + repo["commit"] + "/" + path, None))
    files, entries = {}, {}
    # Resolve each repo once, then fetch only immutable URLs. Any failure aborts.
    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(lambda item: fetch(item[1]), requests))
    for (path, url, source_id), data in zip(requests, responses):
        decode(data)
        files[path] = data
        entries[path] = {"url": url, "sha256": digest(data), "source_id": source_id}
    return {"schema_version": 1, "repositories": revisions, "files": entries}, files


def replay(conf: dict, directory: Path | None = None, commit="") -> tuple[dict, dict]:
    require(directory is not None or re.fullmatch(r"[0-9a-f]{40}", commit), "Replay requires full commit SHA")
    def read(path):
        require(not path.startswith("/") and ".." not in Path(path).parts and "\\" not in path,
                "Unsafe snapshot path")
        return (directory / path).read_bytes() if directory else fetch(RAW + REPOSITORY + "/" + commit + "/" + path)
    lock = json.loads(read("sources.lock.json"))
    require(lock["schema_version"] == 1, "Unknown lock version")
    expected = {"snapshots/" + s["id"] + ".txt" for s in conf["sources"]}
    expected |= {"licenses/" + r + ".txt" for r in conf["repositories"]}
    require(set(lock["files"]) == expected, "Replay inventory does not match config")
    files = {}
    for path, entry in lock["files"].items():
        data = read(path)
        require(digest(data) == entry["sha256"], f"Snapshot hash mismatch: {path}")
        files[path] = data
    return lock, files


def previous_stats() -> dict | None:
    ref = fetch(API + "repos/" + REPOSITORY + "/git/ref/heads/release", missing_ok=True)
    if ref is None:
        return None
    sha = json.loads(ref)["object"]["sha"]
    require(re.fullmatch(r"[0-9a-f]{40}", sha), "Invalid release revision")
    return json.loads(fetch(RAW + REPOSITORY + "/" + sha + "/source_stats.json"))


def assemble(root: Path, conf: dict, files: dict) -> tuple[list[Rule], dict, list[dict], int]:
    rules = [parse_rule(line) for _, line in records((root / "rules/custom.list").read_text(encoding="utf-8"))]
    require(not any(r.kind in {"FINAL", "GEOIP"} for r in rules), "Fallbacks must remain last")
    stats = {}
    for spec in conf["sources"]:
        parsed, stats[spec["id"]] = parse_source(spec, files["snapshots/" + spec["id"] + ".txt"])
        require(not any(r.kind in {"FINAL", "GEOIP"} for r in parsed), "Unexpected source fallback")
        rules.extend(parsed)
    rules.extend([parse_rule("GEOIP,CN,DIRECT", source="fallback"), parse_rule("FINAL,PROXY", source="fallback")])
    merged, conflicts, duplicates = merge_rules(rules)
    return merged, stats, conflicts, duplicates


def render(base: str, rules: list[Rule]) -> bytes:
    require(base.count("{{RULES}}") == 1 and "RULE-SET," not in base and "DOMAIN-SET," not in base,
            "Invalid base template")
    require(re.findall(r"^\[([^]]+)\]$", base, re.M) == ["General", "Rule", "Host", "URL Rewrite"],
            "Unexpected base sections")
    require("update-url = " + RAW + REPOSITORY + "/release/shadowrocket.conf" in base,
            "Missing stable update URL")
    lines, prior = [], None
    for rule in rules:
        if rule.source != prior:
            lines.extend(["", "# " + rule.source])
            prior = rule.source
        lines.append(rule.line)
    result = base.replace("{{RULES}}", "\n".join(lines).strip()).replace("\r\n", "\n")
    return (result.rstrip() + "\n").encode()


def checksum_files(files: dict[str, bytes]) -> bytes:
    return "".join(f"{digest(data)}  {path}\n" for path, data in sorted(files.items())).encode()


def build(root: Path, conf: dict, lock: dict, inputs: dict, previous=None, audit=False) -> dict[str, bytes]:
    rules, stats, conflicts, duplicates = assemble(root, conf, inputs)
    cases = json.loads((root / "tests/routing_cases.json").read_text(encoding="utf-8"))
    results = check_cases(rules, cases)
    voice = [r for r in rules if r.source == "voice"]
    require(all(route(rules, ip=str(ipaddress.ip_network(r.value).network_address))["policy"] == "PROXY"
                for r in voice), "Voice IP routing regression")
    # Other general providers may keep their ASNs; old OpenAI/Copilot cloud-wide rules must disappear.
    require(not any(r.kind == "IP-ASN" and r.value in {"14061", "20473"} for r in rules),
            "AI cloud-wide ASN reintroduced")
    base = (root / "config/base.conf").read_text(encoding="utf-8")
    output = render(base, rules)
    baseline = {"sources": {name: {k: v[k] for k in ("raw_count", "retained_count")} for name, v in stats.items()},
                "allowed_conflicts": conflicts}
    if not audit:
        accepted = json.loads((root / "tests/source_baseline.json").read_text(encoding="utf-8"))
        check_limits(stats, accepted["sources"], conf["limits"])
        check_conflicts(conflicts, accepted["allowed_conflicts"])
        if previous is not None:
            # Inventory edits need the committed baseline to change above. Compare
            # only common providers with the previous release so reviewed additions
            # and removals do not require deleting historical release metadata.
            common = set(stats) & set(previous)
            check_limits({k: stats[k] for k in common}, {k: previous[k] for k in common}, conf["limits"])
    source_paths = ["config/base.conf", "config/sources.yaml", "rules/custom.list", "tests/routing_cases.json",
                    "scripts/build.py", "docs/third_party.md"]
    if not audit:
        source_paths.append("tests/source_baseline.json")
    source_hashes = {p: digest((root / p).read_bytes()) for p in source_paths}
    metadata = {"schema_version": 1, "config_sha256": digest(output), "rules": len(rules),
                "removed_exact_duplicates": duplicates, "conflicts": conflicts, "routing_cases": results,
                "voice_ip_cases": len(voice), "source_files_sha256": source_hashes,
                "validation": "static only; Shadowrocket iOS compilation and live access unverified"}
    files = {**inputs, "sources.lock.json": json_bytes(lock), "source_stats.json": json_bytes(stats)}
    if audit:
        files["candidate_baseline.json"] = json_bytes(baseline)
        files["candidate_report.json"] = json_bytes(metadata)
        return files
    files.update({"shadowrocket.conf": output, "manifest.json": json_bytes(metadata),
                  "THIRD_PARTY.md": (root / "docs/third_party.md").read_bytes()})
    files["SHA256SUMS"] = checksum_files(files)
    return files


def write_files(output: Path, files: dict[str, bytes]):
    require(not output.is_relative_to(ROOT), "Generated outputs must be outside the source checkout")
    # A directory must be empty: a failed run cannot overwrite a previous successful build.
    require(not output.exists() or not any(output.iterdir()), "Output directory must be empty")
    output.mkdir(parents=True, exist_ok=True)
    for name, data in sorted(files.items()):
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--replay", type=Path)
    parser.add_argument("--replay-commit", default="")
    parser.add_argument("--previous-release", action="store_true")
    parser.add_argument("--audit", action="store_true", help="Candidate baseline only; never produces publishable config")
    args = parser.parse_args()
    try:
        require(not (args.replay and args.replay_commit), "Choose one replay method")
        require(not (args.audit and args.previous_release), "Audit is not a publishing operation")
        conf = load_config(ROOT)
        lock, inputs = replay(conf, args.replay, args.replay_commit) if args.replay or args.replay_commit else collect(conf)
        old = previous_stats() if args.previous_release else None
        files = build(ROOT, conf, lock, inputs, old, args.audit)
        write_files(args.output.resolve(), files)
        print(json.dumps({"status": "audit_candidate" if args.audit else "validated", "files": len(files),
                          "config_sha256": digest(files["shadowrocket.conf"]) if "shadowrocket.conf" in files else None}))
    except (BuildError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"BUILD STOPPED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

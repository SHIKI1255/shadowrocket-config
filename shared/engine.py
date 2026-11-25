"""Deterministic routing rule engine. Python 3.12+ standard library only."""
from __future__ import annotations

from dataclasses import dataclass
import fnmatch
from functools import lru_cache
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "SHIKI1255/shadowrocket-config"
RAW = "https://raw.githubusercontent.com/"
API = "https://api.github.com/"
CODELOAD = "https://codeload.github.com/"
MAX_BYTES = 8 * 1024 * 1024
APPROVED_HOSTS = {
    "raw.githubusercontent.com",
    "api.github.com",
    "codeload.github.com",
    "openai.com",
    "core.telegram.org",
}
KINDS = {
    "DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD", "DOMAIN-WILDCARD", "DOMAIN-REGEX",
    "IP-CIDR", "IP-ASN", "PROCESS-NAME", "USER-AGENT", "GEOIP", "MATCH", "FINAL",
}
V2FLY_KNOWN_ATTRS = {"@ads", "@telemetry", "@cn", "@!cn"}


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
    from scripts.public_http import request_bytes
    parsed = urllib.parse.urlparse(url)
    require(parsed.scheme == "https" and parsed.hostname in APPROVED_HOSTS,
            "Unapproved source URL")
    require(not parsed.username and not parsed.password and not parsed.query,
            "Credentials and query strings are not source URLs")
    headers = {"User-Agent": "SHIKI1255-Rules/2"}
    if parsed.hostname == "api.github.com":
        headers["Accept"] = "application/vnd.github+json"
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = "Bearer " + token
    try:
        data = request_bytes(urllib.request.Request(url, headers=headers),
                             limit=MAX_BYTES, expected_host=parsed.hostname)
    except urllib.error.HTTPError as exc:
        if missing_ok and exc.code == 404:
            return None
        raise BuildError(f"Download failed: HTTP {exc.code}: {url}") from None
    except (OSError, urllib.error.URLError, http.client.HTTPException) as exc:
        raise BuildError(f"Download failed: {url}: {type(exc).__name__}") from None
    except ValueError as exc:
        raise BuildError(str(exc)) from None
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
        if self.kind in {"MATCH", "FINAL"}:
            return self.kind + "," + self.policy
        return ",".join((self.kind, self.value, self.policy, *self.options))


def parse_rule(line: str, policy: str | None = None, source="custom") -> Rule:
    parts = [part.strip() for part in line.split(",")]
    kind = "IP-CIDR" if parts[0] == "IP-CIDR6" else parts[0]
    require(kind in KINDS, f"Unknown rule type in {source}: {kind}")
    if kind in {"MATCH", "FINAL"}:
        require(policy is None and len(parts) == 2, "Invalid MATCH rule")
        value, effective, options = "", parts[1], ()
    else:
        require(len(parts) >= (2 if policy else 3), f"Incomplete rule: {line}")
        value = parts[1]
        effective = policy or parts[2]
        options = tuple(parts[2:] if policy else parts[3:])
    require(effective in {"DIRECT", "PROXY"}, f"Unapproved policy: {effective}")
    require(options in {(), ("no-resolve",)}, f"Unknown options in {source}: {options}")
    require(not options or kind in {"IP-CIDR", "IP-ASN", "GEOIP"}, "Invalid no-resolve")
    if kind == "DOMAIN-REGEX":
        require(bool(value) and len(value) < 2048 and not any(c in value for c in "\r\n\x00,"), "Invalid regex")
        re.compile(value)
        require(not re.search(r"\(\?[=!<]|\\[1-9]", value), "Regex unsupported by RE2")
    elif kind.startswith("DOMAIN"):
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
    elif kind in {"PROCESS-NAME", "USER-AGENT"}:
        require(bool(value) and not any(c in value for c in "\r\n\x00"), "Invalid process name")
    return Rule(kind, value, effective, options, source)


def records(text: str):
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if line and not line.startswith(("#", "//")):
            yield number, line


def _parse_include_filters(tags: list[str]) -> tuple[set[str], set[str]]:
    pos, neg = set(), set()
    for tag in tags:
        require(re.fullmatch(r"@-?!?[a-z0-9_-]+", tag) is not None,
                "Unknown v2fly include attribute: " + tag)
        if tag.startswith("@-"):
            target = "@" + tag[2:]
            require(target in V2FLY_KNOWN_ATTRS,
                    "Unknown v2fly attribute semantics: " + tag)
            neg.add(target)
        else:
            require(tag in V2FLY_KNOWN_ATTRS,
                    "Unknown v2fly attribute semantics: " + tag)
            pos.add(tag)
    return pos, neg


def expand_v2fly_entries(
    entry_name: str,
    v2fly_data: dict[str, str],
    *,
    stack: tuple[str, ...] = (),
) -> list[tuple[str, int, str, set[str]]]:
    """Recursively expand v2fly data files with cycle detection and attribute filtering."""
    require(entry_name not in stack, f"v2fly include cycle: {' -> '.join((*stack, entry_name))}")
    require(entry_name in v2fly_data, f"Missing v2fly data file: {entry_name}")
    expanded: list[tuple[str, int, str, set[str]]] = []
    for number, raw_line in records(v2fly_data[entry_name]):
        content = raw_line.split("#", 1)[0].strip()
        if not content:
            continue
        fields = content.split()
        entry, tags = fields[0], fields[1:]
        if entry.startswith("include:"):
            sub = entry.split(":", 1)[1]
            require(re.fullmatch(r"[a-zA-Z0-9_!.-]+", sub) is not None and ".." not in sub,
                    f"{entry_name}:{number}: Invalid v2fly include target: {sub}")
            try:
                pos, neg = _parse_include_filters(tags)
            except BuildError as exc:
                raise BuildError(f"{entry_name}:{number}: {exc}") from exc
            for src_file, src_line, sub_entry, sub_tags in expand_v2fly_entries(
                sub, v2fly_data, stack=(*stack, entry_name)
            ):
                if pos and not pos.issubset(sub_tags):
                    continue
                if neg and (neg & sub_tags):
                    continue
                expanded.append((src_file, src_line, sub_entry, set(sub_tags)))
        else:
            require(all(re.fullmatch(r"@!?[a-z0-9_-]+", x) for x in tags),
                    f"{entry_name}:{number}: Unknown v2fly attributes")
            expanded.append((entry_name, number, entry, set(tags)))
    return expanded


def _v2fly_name(path: str) -> str:
    return path.removeprefix("data/")


def parse_source(
    spec: dict,
    data: bytes | None,
    *,
    v2fly_data: dict[str, str] | None = None,
) -> tuple[list[Rule], dict]:
    source, policy, fmt = spec["id"], spec["policy"], spec["format"]
    result, skipped, raw_count = [], [], 0
    seen_rules: set[str] = set()

    if fmt == "v2fly" and v2fly_data is not None:
        raw_paths = spec.get("paths") or [spec["path"]]
        entries: list[tuple[str, int, str, set[str]]] = []
        for path in raw_paths:
            entries.extend(expand_v2fly_entries(_v2fly_name(path), v2fly_data))
        require_attrs = set(spec.get("require_attrs", []))
        exclude_attrs = set(spec.get("exclude_attrs", []))
        exclude_domains = set(spec.get("exclude_domains", []))
        allow = spec.get("allow_rules")
        skip_regex = bool(spec.get("skip_regex", False))
        regex_overrides = spec.get("regex_overrides")

        for origin_file, number, entry, tag_set in entries:
            raw_count += 1
            loc = f"{source}({origin_file}):{number}"
            try:
                unknown = tag_set - V2FLY_KNOWN_ATTRS
                require(not unknown, "Unknown v2fly attribute semantics: " + " ".join(sorted(unknown)))
                prefix, value = entry.split(":", 1) if ":" in entry else ("domain", entry)
                if prefix == "regexp":
                    # Always validate regex syntax before any override or exclusion.
                    parse_rule("DOMAIN-REGEX," + value, policy, source)
                    if regex_overrides is not None:
                        mapped = regex_overrides.get(value)
                        if mapped is not None:
                            rule = parse_rule(mapped, policy, source)
                        elif skip_regex:
                            skipped.append({"line": number, "file": origin_file, "rule": f"DOMAIN-REGEX,{value},{policy}", "reason": "regex skipped"})
                            continue
                        else:
                            raise BuildError("Unknown regexp; manual semantic review required")
                    elif skip_regex:
                        skipped.append({"line": number, "file": origin_file, "rule": f"DOMAIN-REGEX,{value},{policy}", "reason": "regex skipped"})
                        continue
                    else:
                        rule = parse_rule("DOMAIN-REGEX," + value, policy, source)
                else:
                    mapping = {"domain": "DOMAIN-SUFFIX", "full": "DOMAIN"}
                    require(prefix in mapping, f"Unknown v2fly syntax: {prefix}")
                    rule = parse_rule(f"{mapping[prefix]},{value}", policy, source)
                if "@ads" in tag_set:
                    skipped.append({"line": number, "file": origin_file, "rule": rule.line, "reason": "ads attribute"})
                    continue
                if "@telemetry" in tag_set:
                    skipped.append({"line": number, "file": origin_file, "rule": rule.line, "reason": "telemetry attribute"})
                    continue
                if require_attrs and not require_attrs.issubset(tag_set):
                    skipped.append({"line": number, "file": origin_file, "rule": rule.line, "reason": "attribute filter"})
                    continue
                if exclude_attrs and (exclude_attrs & tag_set):
                    skipped.append({"line": number, "file": origin_file, "rule": rule.line, "reason": "attribute filter"})
                    continue
                if rule.value in exclude_domains:
                    skipped.append({"line": number, "file": origin_file, "rule": rule.line, "reason": "domestic compatibility"})
                    continue
                if allow is not None and rule.selector not in allow:
                    skipped.append({"line": number, "file": origin_file, "rule": rule.line, "reason": "allowlist filter"})
                    continue
                if rule.line not in seen_rules:
                    seen_rules.add(rule.line)
                    result.append(rule)
            except (ValueError, TypeError) as exc:
                raise BuildError(f"{loc}: {exc}") from exc

        for extra_domain in spec.get("extra_domains", []):
            rule = parse_rule(f"DOMAIN-SUFFIX,{extra_domain}", policy, source)
            raw_count += 1
            if rule.line not in seen_rules:
                seen_rules.add(rule.line)
                result.append(rule)

        require(raw_count > 0 and result, f"Empty rules: {source}")
        stats = {"raw_count": raw_count, "retained_count": len(result),
                 "rules": [r.line for r in result], "skipped": skipped}
        return result, stats

    require(data is not None, f"Missing raw data for source: {source}")
    text = decode(data)
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
    elif fmt == "cidr_list":
        min_v4 = int(spec.get("min_ipv4_prefix", 19))
        min_v6 = int(spec.get("min_ipv6_prefix", 32))
        lines = []
        for number, line in records(text):
            try:
                network = ipaddress.ip_network(line, strict=True)
            except ValueError as exc:
                raise BuildError(f"{source}:{number}: Invalid CIDR: {line}") from exc
            require(network.is_global, f"{source}:{number}: Invalid/private network: {line}")
            limit = min_v4 if network.version == 4 else min_v6
            require(network.prefixlen >= limit, f"{source}:{number}: CIDR range too broad: {line}")
            lines.append((number, f"IP-CIDR,{network},no-resolve"))
    else:
        lines = list(records(text))

    require_attrs = set(spec.get("require_attrs", []))
    exclude_attrs = set(spec.get("exclude_attrs", []))
    skip_regex = bool(spec.get("skip_regex", False))

    for number, line in lines:
        raw_count += 1
        try:
            if fmt in {"clash", "shadowrocket", "voice", "cidr_list"}:
                rule = parse_rule(line, policy, source)
                require(fmt != "shadowrocket" or rule.kind not in {"DOMAIN-REGEX", "PROCESS-NAME"},
                        "Unsupported Shadowrocket source syntax")
            elif fmt == "domain_set":
                require("," not in line and " " not in line, "Unknown domain-set syntax")
                prefix = "DOMAIN-SUFFIX" if line.startswith((".", "+.")) else "DOMAIN"
                value = line[2:] if line.startswith("+.") else line.lstrip(".")
                rule = parse_rule(f"{prefix},{value}", policy, source)
            elif fmt == "v2fly":
                fields = line.split("#", 1)[0].split()
                require(bool(fields) and all(re.fullmatch(r"@!?[a-z0-9_-]+", x) for x in fields[1:]),
                        "Unknown v2fly attributes")
                entry, tags = fields[0], fields[1:]
                tag_set = set(tags)
                unknown = tag_set - V2FLY_KNOWN_ATTRS
                require(not unknown, "Unknown v2fly attribute semantics: " + " ".join(sorted(unknown)))
                prefix, value = entry.split(":", 1) if ":" in entry else ("domain", entry)
                if prefix == "regexp":
                    parse_rule("DOMAIN-REGEX," + value, policy, source)
                    if "regex_overrides" in spec:
                        mapped = spec["regex_overrides"].get(value)
                        if mapped is not None:
                            rule = parse_rule(mapped, policy, source)
                        elif skip_regex:
                            skipped.append({"line": number, "rule": f"DOMAIN-REGEX,{value},{policy}", "reason": "regex skipped"})
                            continue
                        else:
                            raise BuildError("Unknown regexp; manual semantic review required")
                    elif skip_regex:
                        skipped.append({"line": number, "rule": f"DOMAIN-REGEX,{value},{policy}", "reason": "regex skipped"})
                        continue
                    else:
                        rule = parse_rule("DOMAIN-REGEX," + value, policy, source)
                else:
                    mapping = {"domain": "DOMAIN-SUFFIX", "full": "DOMAIN"}
                    require(prefix in mapping, f"Unknown v2fly syntax: {prefix}")
                    rule = parse_rule(f"{mapping[prefix]},{value}", policy, source)
                if "@ads" in tag_set:
                    skipped.append({"line": number, "rule": rule.line, "reason": "ads attribute"})
                    continue
                if "@telemetry" in tag_set:
                    skipped.append({"line": number, "rule": rule.line, "reason": "telemetry attribute"})
                    continue
                if require_attrs and not require_attrs.issubset(tag_set):
                    skipped.append({"line": number, "rule": rule.line, "reason": "attribute filter"})
                    continue
                if exclude_attrs and (exclude_attrs & tag_set):
                    skipped.append({"line": number, "rule": rule.line, "reason": "attribute filter"})
                    continue
            else:
                raise BuildError(f"Unknown source format: {fmt}")
            if rule.value in spec.get("exclude_domains", []):
                skipped.append({"line": number, "rule": rule.line, "reason": "domestic compatibility"})
                continue
            allow = spec.get("allow_rules")
            if allow is not None and rule.selector not in allow:
                skipped.append({"line": number, "rule": rule.line, "reason": "allowlist filter"})
                continue
            if rule.line not in seen_rules:
                seen_rules.add(rule.line)
                result.append(rule)
        except (ValueError, TypeError) as exc:
            raise BuildError(f"{source}:{number}: {exc}") from exc

    for extra_domain in spec.get("extra_domains", []):
        rule = parse_rule(f"DOMAIN-SUFFIX,{extra_domain}", policy, source)
        raw_count += 1
        if rule.line not in seen_rules:
            seen_rules.add(rule.line)
            result.append(rule)

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


def check_bounds(stats: dict, sources_conf: dict[str, dict], limits: dict):
    """Static absolute sanity bounds per source (prevents collapse/explosion without freezing history)."""
    default_min = int(limits.get("min_absolute_rules", 1))
    for name, current in stats.items():
        spec = sources_conf.get(name, {})
        min_rules = int(spec.get("min_rules", default_min))
        max_rules = spec.get("max_rules")
        require(current["retained_count"] >= min_rules and current["raw_count"] >= min_rules,
                f"Abnormal {name} retained_count: below min_rules {min_rules} ({current['retained_count']})")
        if max_rules is not None:
            require(current["retained_count"] <= int(max_rules),
                    f"Abnormal {name} retained_count: above max_rules {max_rules} ({current['retained_count']})")


def check_limits(stats: dict, baseline: dict, limits: dict, *, sources_conf: dict[str, dict] | None = None):
    require(set(stats) == set(baseline), "Source inventory changed: review baseline")
    small_threshold = int(limits.get("small_source_threshold", 0))
    small_delta = int(limits.get("small_source_delta", 0))
    default_min = int(limits.get("min_absolute_rules", 1))

    for name, current in stats.items():
        old = baseline[name]
        spec = (sources_conf or {}).get(name, {})
        min_rules = int(spec.get("min_rules", old.get("min_rules", default_min)))
        max_rules = spec.get("max_rules", old.get("max_rules"))

        if spec.get("dynamic_ip") or old.get("dynamic_ip"):
            require(current["retained_count"] >= min_rules and current["raw_count"] >= min_rules,
                    f"Abnormal {name} retained_count: {old['retained_count']} -> {current['retained_count']}")
            if max_rules is not None:
                require(current["retained_count"] <= int(max_rules),
                        f"Abnormal {name} retained_count: {old['retained_count']} -> {current['retained_count']}")
            continue

        is_small = small_threshold > 0 and old["retained_count"] < small_threshold
        for key in ("raw_count", "retained_count"):
            require(old[key] > 0, f"Invalid baseline: {name}")
            if max_rules is not None and key == "retained_count":
                require(current[key] <= int(max_rules),
                        f"Abnormal {name} {key}: {old[key]} -> {current[key]}")
            if key == "retained_count":
                require(current[key] >= min_rules,
                        f"Abnormal {name} {key}: {old[key]} -> {current[key]}")
            ratio = current[key] / old[key]
            within_ratio = limits["min_ratio"] <= ratio <= limits["max_ratio"]
            within_small = is_small and abs(current[key] - old[key]) <= small_delta
            require(within_ratio or within_small,
                    f"Abnormal {name} {key}: {old[key]} -> {current[key]}")

        if "rules" in old:
            before, after = set(old["rules"]), set(current["rules"])
            removed = len(before - after)
            added = len(after - before)
            if is_small:
                require(not before or bool(before & after),
                        f"Too many removed rules: {name}")
                max_rem = max(int(len(before) * limits["max_removed_ratio"]), small_delta)
                max_add = max(int(len(before) * limits["max_added_ratio"]), small_delta)
                require(removed <= max_rem, f"Too many removed rules: {name}")
                require(added <= max_add, f"Too many added rules: {name}")
            else:
                require(removed / len(before) <= limits["max_removed_ratio"],
                        f"Too many removed rules: {name}")
                require(added / len(before) <= limits["max_added_ratio"],
                        f"Too many added rules: {name}")


def check_conflicts(conflicts: list[dict], allowed: list[dict], allowed_categories: list[dict] | None = None):
    approved = {json.dumps(row, sort_keys=True) for row in allowed}
    approved_cats = {
        (row["winner"], row["winner_source"], row["later"], row["later_source"])
        for row in (allowed_categories or [])
    }
    unknown = [
        row for row in conflicts
        if json.dumps(row, sort_keys=True) not in approved
        and (row["winner"], row["winner_source"], row["later"], row["later_source"]) not in approved_cats
    ]
    require(not unknown, "Unregistered policy conflicts: " + json.dumps(unknown, ensure_ascii=False))


@lru_cache(maxsize=None)
def _ip_network(cidr: str):
    return ipaddress.ip_network(cidr)


def route(rules: list[Rule], *, domain="", ip="", country="", asn="", process="", user_agent="") -> dict:
    """Conservative static matcher; not a substitute for the Mihomo runtime.

    Without IP/geolocation, DNS-dependent IP/GEOIP rules are unresolved; MATCH is
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
        elif kind == "DOMAIN-REGEX":
            matches = bool(host) and re.search(value, host) is not None
        elif kind == "USER-AGENT":
            matches = bool(user_agent) and fnmatch.fnmatchcase(user_agent, value)
        elif kind == "PROCESS-NAME":
            matches = bool(process) and process == value
        elif kind == "IP-CIDR":
            matches = address is not None and address in _ip_network(value)
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
        elif kind in {"MATCH", "FINAL"}:
            matches = True
        if matches:
            return {"policy": "UNRESOLVED" if unresolved else rule.policy,
                    "rule": rule.line, "source": rule.source}
    return {"policy": "UNRESOLVED", "rule": "", "source": ""}


def check_case_result(case: dict, actual: dict, context="Routing"):
    require(actual["policy"] == case["policy"], f"{context} regression {case['name']}: {actual}")
    if "expected_rule" in case:
        require(actual["rule"] == case["expected_rule"],
                f"{context} rule regression {case['name']}: expected {case['expected_rule']!r}, got {actual}")


def check_cases(rules: list[Rule], cases: list[dict]) -> list[dict]:
    results = []
    for case in cases:
        actual = route(rules, **case["input"])
        check_case_result(case, actual)
        results.append({**case, "actual": actual})
    return results

"""Deterministic Mihomo rule resources. Python 3.12 standard library only."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import fnmatch
import hashlib
import http.client
import ipaddress
import io
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "SHIKI1255/shadowrocket-config"
RAW = "https://raw.githubusercontent.com/"
API = "https://api.github.com/"
MAX_BYTES = 8 * 1024 * 1024
KINDS = {"DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD", "DOMAIN-WILDCARD", "DOMAIN-REGEX",
         "IP-CIDR", "IP-ASN", "PROCESS-NAME", "USER-AGENT", "GEOIP", "MATCH", "FINAL"}


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
    headers = {"User-Agent": "clash-config-builder/1", "Accept": "application/vnd.github+json"}
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
    except (OSError, urllib.error.URLError, http.client.HTTPException) as exc:
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
            if fmt in {"clash", "shadowrocket", "voice"}:
                rule = parse_rule(line, policy, source)
                require(fmt != "shadowrocket" or rule.kind not in {"DOMAIN-REGEX", "PROCESS-NAME"}, "Unsupported Shadowrocket source syntax")
            elif fmt == "domain_set":
                require("," not in line and " " not in line, "Unknown domain-set syntax")
                prefix = "DOMAIN-SUFFIX" if line.startswith((".", "+.")) else "DOMAIN"
                value = line[2:] if line.startswith("+.") else line.lstrip('.')
                rule = parse_rule(f"{prefix},{value}", policy, source)
            elif fmt == "v2fly":
                fields = line.split("#", 1)[0].split()
                require(bool(fields) and all(re.fullmatch(r"@!?[a-z0-9_-]+", x) for x in fields[1:]),
                        "Unknown v2fly attributes")
                entry, tags = fields[0], fields[1:]
                require(set(tags) <= {"@ads", "@!cn"}, "Unknown v2fly attribute semantics")
                prefix, value = entry.split(":", 1) if ":" in entry else ("domain", entry)
                if prefix == "regexp":
                    if "regex_overrides" in spec:
                        mapped = spec["regex_overrides"].get(value)
                        require(mapped is not None, "Unknown regexp; manual semantic review required")
                        rule = parse_rule(mapped, policy, source)
                    else:
                        rule = parse_rule("DOMAIN-REGEX," + value, policy, source)
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



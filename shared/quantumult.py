"""Explicit native Quantumult X conversion; static checks are not the iOS engine."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import ipaddress
import re

from shared.engine import Rule, require, parse_rule, records, route

MAPPING = {
    'DOMAIN': 'host', 'DOMAIN-SUFFIX': 'host-suffix', 'DOMAIN-KEYWORD': 'host-keyword',
    'DOMAIN-WILDCARD': 'host-wildcard', 'USER-AGENT': 'user-agent',
    'IP-CIDR': 'ip-cidr', 'IP-ASN': 'ip-asn', 'GEOIP': 'geoip', 'FINAL': 'final',
}
REVERSE = {v: k for k, v in MAPPING.items()} | {'ip6-cidr': 'IP-CIDR'}
BOOTSTRAP = {
    'localhost', 'lan', 'local', 'home.arpa', 'captive.apple.com',
    'dns.alidns.com', 'doh.pub', 'dns.pub',
    '10.0.0.0/8', '127.0.0.0/8', '169.254.0.0/16', '172.16.0.0/12',
    '192.168.0.0/16', '224.0.0.0/4', '255.255.255.255/32',
    '::1/128', 'fc00::/7', 'fe80::/10', 'ff00::/8',
}


@dataclass(frozen=True)
class NativeRule:
    kind: str
    value: str
    policy: str
    source: str = 'custom'

    @property
    def selector(self):
        return ','.join((self.kind, self.value))

    @property
    def line(self):
        return ', '.join((self.kind, self.policy) if self.kind == 'final'
                         else (self.kind, self.value, self.policy))


def convert(rule: Rule) -> NativeRule:
    require(rule.kind in MAPPING, 'Unknown canonical rule type')
    require(rule.options in {(), ('no-resolve',)}, 'Unknown canonical options')
    kind = MAPPING[rule.kind]
    if rule.kind == 'IP-CIDR' and ipaddress.ip_network(rule.value).version == 6:
        kind = 'ip6-cidr'
    return NativeRule(kind, rule.value.lower() if rule.kind == 'GEOIP' else rule.value,
                      rule.policy.lower(), rule.source)


def parse_native(line: str, source='native') -> NativeRule:
    fields = [p.strip() for p in line.split(',')]
    require(fields[0] in REVERSE, 'Unknown Quantumult X rule type')
    kind = fields[0]
    require(len(fields) == (2 if kind == 'final' else 3), 'Invalid native fields/options')
    require(fields[-1] in {'direct', 'proxy'}, 'Unknown native policy')
    value = '' if kind == 'final' else fields[1]
    canonical_value = value.upper() if kind == 'geoip' else value
    canonical_line = ','.join((REVERSE[kind], fields[-1].upper()) if kind == 'final'
                              else (REVERSE[kind], canonical_value, fields[-1].upper()))
    canonical = parse_rule(canonical_line, source=source)
    require(convert(canonical).kind == kind, 'Wrong IPv4/IPv6 rule family')
    return convert(canonical)


def native_conflicts(rules):
    first, conflicts = {}, set()
    for r in rules:
        prev = first.setdefault(r.selector, r)
        if prev.policy != r.policy:
            conflicts.add((r.selector, prev.policy, prev.source, r.policy, r.source))
    return conflicts


def check_native_conflicts(rules, allowed):
    approved = set()
    for item in allowed:
        fields = item['selector'].split(',')
        line = ','.join([*fields[:2], item['winner'], *fields[2:]])
        r = convert(parse_rule(line))
        approved.add((r.selector, item['winner'].lower(), item['winner_source'],
                      item['later'].lower(), item['later_source']))
    require(native_conflicts(rules) <= approved, 'Unregistered native policy conflict after conversion')


def route_native(rules, **inputs):
    """Validate explicit selectors, host-before-UA, and supplied IP/GeoIP facts.

    Does not emulate DNS, remote insertion, GeoIP databases or tunnel routing.
    Host selectors precede user-agent as documented. Network cases require their
    evidence in inputs; missing evidence remains UNRESOLVED rather than a pass.
    """
    ordered = [r for r in rules if r.kind.startswith('host')]
    ordered += [r for r in rules if r.kind == 'user-agent']
    ordered += [r for r in rules if not r.kind.startswith('host') and r.kind != 'user-agent']
    canonical = []
    for r in ordered:
        canonical.append(Rule(REVERSE[r.kind], r.value.upper() if r.kind == 'geoip' else r.value,
                              r.policy.upper(), (), r.source))
    # A known literal destination needs no domain DNS lookup; ASN/GeoIP are still
    # unknown when not supplied. Exact CIDR matches earlier in order can be proved.
    return route(canonical, **inputs)


def render_lines(rules):
    lines, prior = [], None
    for r in rules:
        if r.source != prior:
            lines.extend(['', '# ' + r.source])
            prior = r.source
        lines.append(r.line)
    return '\n'.join(lines).strip() + '\n'



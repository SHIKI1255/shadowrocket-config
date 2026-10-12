"""One upstream snapshot, three native outputs. Never accepts private subscriptions."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import ipaddress
import json
from pathlib import Path
import re
import urllib.parse

from shared.engine import (Rule, RAW, API, REPOSITORY, fetch, decode, digest, json_bytes,
    require, records, parse_rule, parse_source, merge_rules, check_limits, check_conflicts,
    route, check_case_result)
from shared import quantumult as q

ROOT = Path(__file__).resolve().parents[1]
CLIENTS = ('shadowrocket', 'quantumultx', 'clash')
SUPPLEMENTS = {'apple_domains': 'apple', 'proxy_domains': 'proxy', 'china_domains': 'china'}


def read_json(name):
    return json.loads((ROOT / 'shared' / name).read_text(encoding='utf-8'))


def source_specs(conf, client):
    for original in conf['sources']:
        spec = dict(original)
        if client == 'clash':
            if 'clash_path' in spec:
                spec['path'], spec['format'] = spec['clash_path'], 'clash'
            spec.pop('regex_overrides', None)
        yield spec


def collect(conf, replay_dir=None, replay_commit=''):
    if replay_dir is not None or replay_commit:
        require(replay_dir is not None or re.fullmatch(r'[a-f0-9]{40}', replay_commit), 'Full replay SHA required')
        def read(path):
            require(re.fullmatch(r'[a-zA-Z0-9_./-]+', path) and '..' not in path.split('/')
                    and not path.startswith('/'), 'Unsafe snapshot path')
            return (replay_dir / path).read_bytes() if replay_dir else fetch(RAW + REPOSITORY + '/' + replay_commit + '/' + path)
        lock = json.loads(read('sources.lock.json'))
        require(lock.get('schema_version') == 2, 'Use an archived pre-migration configuration for old schema releases')
        expected = {'snapshots/' + client + '/' + s['id'] + '.txt'
                    for client in ('shadowrocket', 'clash') for s in conf['sources']}
        expected |= {'licenses/' + name + '.txt' for name in conf['repositories']}
        require(set(lock['files']) == expected, 'Replay source inventory mismatch')
        files = {p: read(p) for p in lock['files']}
        for p, data in files.items():
            require(digest(data) == lock['files'][p]['sha256'], 'Snapshot hash mismatch: ' + p)
        return lock, files
    revisions = {}
    for name, spec in conf['repositories'].items():
        ref = spec['ref']
        sha = ref if re.fullmatch(r'[a-f0-9]{40}', ref) else json.loads(fetch(
            API + 'repos/' + spec['repo'] + '/git/ref/heads/' + urllib.parse.quote(ref, safe='')))['object']['sha']
        require(re.fullmatch(r'[a-f0-9]{40}', sha), 'Invalid upstream SHA')
        revisions[name] = {'repo': spec['repo'], 'commit': sha, 'ref': ref, 'license': spec['license']}
    requests = {}
    for client in ('shadowrocket', 'clash'):
        for s in source_specs(conf, client):
            r = revisions.get(s.get('repository'))
            url = RAW + r['repo'] + '/' + r['commit'] + '/' + s['path'] if r else s['url']
            requests['snapshots/' + client + '/' + s['id'] + '.txt'] = url
    for name, r in revisions.items():
        requests['licenses/' + name + '.txt'] = RAW + r['repo'] + '/' + r['commit'] + '/' + conf['repositories'][name]['license_path']
    # Fetch each URL once, including the mutable official voice JSON.
    urls = list(dict.fromkeys(requests.values()))
    with ThreadPoolExecutor(max_workers=6) as pool:
        downloaded = dict(zip(urls, pool.map(fetch, urls)))
    files = {p: downloaded[url] for p, url in requests.items()}
    for data in files.values(): decode(data)
    lock = {'schema_version': 2, 'repositories': revisions,
            'files': {p: {'url': requests[p], 'sha256': digest(data)} for p, data in files.items()}}
    return lock, files


def assemble(conf, inputs, client):
    platform = read_json('platforms.json')
    target = 'clash' if client == 'clash' else 'shadowrocket'
    bootstrap = [parse_rule(x, source='local_direct') for x in platform[target]['bootstrap']]
    custom = [parse_rule(x, source='personal_' + x.split(',')[2].lower())
              for _, x in records((ROOT / 'shared/custom.list').read_text(encoding='utf-8'))]
    require(all(r.kind.startswith('DOMAIN') for r in custom), 'Only shared domain selectors belong in custom.list')
    require([r.policy for r in custom] == sorted(r.policy for r in custom), 'Personal DIRECT must precede PROXY')
    direct = [r for r in custom if r.policy == 'DIRECT']
    proxy = [r for r in custom if r.policy == 'PROXY']
    if client == 'clash':
        processes = [parse_rule(x, source='personal_' + x.split(',')[2].lower()) for x in platform['clash']['process_rules']]
        require(all(r.kind == 'PROCESS-NAME' for r in processes), 'Invalid process extension')
        direct += [r for r in processes if r.policy == 'DIRECT']
        proxy = [r for r in processes if r.policy == 'PROXY'] + proxy
    rules = bootstrap + direct + proxy
    stats, origins = {}, {}
    for spec in source_specs(conf, target):
        parsed, stats[spec['id']] = parse_source(spec, inputs['snapshots/' + target + '/' + spec['id'] + '.txt'])
        require(not any(r.kind in {'FINAL', 'MATCH', 'GEOIP'} for r in parsed), 'Unexpected upstream fallback')
        for r in parsed: origins.setdefault(r.line, []).append(spec['id'])
        rules += [replace(r, source=SUPPLEMENTS.get(r.source, r.source)) for r in parsed]
    rules += [parse_rule('GEOIP,CN,DIRECT', source='geoip_cn'),
              parse_rule(('MATCH' if client == 'clash' else 'FINAL') + ',PROXY', source='final_proxy')]
    for r in bootstrap + direct + proxy: origins.setdefault(r.line, []).insert(0, r.source)
    merged, conflicts, duplicates = merge_rules(rules)
    provenance = [{'rule': r.line, 'category': r.source,
                   'origins': list(dict.fromkeys(origins.get(r.line, [r.source])))} for r in merged]
    return merged, stats, conflicts, duplicates, provenance


def case_rows(client):
    return [{'name': c['name'], 'input': c['input'], **c['expect'][client]}
            for c in read_json('routing_cases.json') if client in c['expect']]


def evaluate(rules, client):
    native = [q.convert(r) for r in rules] if client == 'quantumultx' else None
    results = []
    for c in case_rows(client):
        actual = q.route_native(native, **c['input']) if native is not None else route(rules, **c['input'])
        check_case_result(c, actual, client)
        if 'expected_category' in c:
            require(actual['source'] == c['expected_category'], 'Category regression: ' + c['name'])
        results.append({**c, 'actual': actual})
    return results


def render_lines(rules, labels):
    lines, previous = [], None
    for r in rules:
        if previous != r.source:
            lines += ['', '# ' + r.source + ' | ' + labels[r.source]]
            previous = r.source
        lines.append(r.line)
    return '\n'.join(lines).strip() + '\n'


def check_source_parity(stats, conf):
    """Client syntax cannot silently create different common upstream coverage."""
    for spec in conf['sources']:
        category = spec['id']
        if category in SUPPLEMENTS: continue
        comparable = []
        for client in ('shadowrocket', 'clash'):
            names = [category] + [k for k,v in SUPPLEMENTS.items() if v == category]
            lines = []
            for name in names: lines += stats[client][name]['rules']
            selected = set()
            for line in lines:
                r = parse_rule(line)
                if r.kind in {'USER-AGENT','PROCESS-NAME'}: continue
                if r.kind == 'DOMAIN-REGEX':
                    mapped = spec.get('regex_overrides', {}).get(r.value)
                    require(mapped is not None, 'Unreviewed client regex difference')
                    r = parse_rule(mapped, r.policy)
                selected.add(r.line)
            comparable.append(selected)
        require(comparable[0] == comparable[1], 'Client upstream coverage diverged: ' + category)


def overlaps(rules):
    """Report provable earlier suffix coverage, never remove it automatically."""
    suffixes, result = {}, []
    for r in rules:
        if r.kind not in {'DOMAIN','DOMAIN-SUFFIX'}: continue
        parts = r.value.lstrip('.').split('.')
        for i in range(len(parts)):
            prior = suffixes.get('.'.join(parts[i:]))
            if prior is not None and prior.policy == r.policy and prior.line != r.line:
                result.append({'rule': r.line, 'earlier': prior.line, 'category': r.source})
                break
        if r.kind == 'DOMAIN-SUFFIX' and not r.value.startswith('.'):
            suffixes.setdefault(r.value,r)
    return result


def build(conf, lock, inputs, previous=None, audit=False):
    labels = read_json('categories.json')
    accepted = read_json('baseline.json')
    payload = {'schema_version': 1, 'categories': labels, 'platforms': {},
               'licenses': {p: decode(d) for p,d in inputs.items() if p.startswith('licenses/')}}
    baselines, all_stats, provenance, reports, coverage = {}, {}, {}, {}, {}
    for client in CLIENTS:
        rules, stats, conflicts, duplicates, provenance[client] = assemble(conf, inputs, client)
        coverage[client] = overlaps(rules)
        source_client = 'clash' if client == 'clash' else 'shadowrocket'
        all_stats[source_client] = stats
        baselines[source_client] = {'sources': {k: {f: v[f] for f in ('raw_count', 'retained_count')} for k, v in stats.items()},
                                    'allowed_conflicts': conflicts}
        require(not any(r.kind == 'IP-ASN' and r.value in {'14061', '20473'} for r in rules), 'AI cloud ASN returned')
        if not audit:
            check_limits(stats, accepted[source_client]['sources'], conf['limits'])
            check_conflicts(conflicts, accepted[source_client]['allowed_conflicts'])
            old = (previous or {}).get(source_client)
            if old is None and previous and 'openai' in previous and source_client == 'shadowrocket': old = previous
            if old: check_limits(stats, old, conf['limits'])
        cases = evaluate(rules, client)
        voice = [r for r in rules if r.source == 'voice']
        require(voice and all(route(rules, ip=str(ipaddress.ip_network(r.value).network_address))['policy'] == 'PROXY'
                             for r in voice), 'Voice regression')
        report = {'rules': len(rules), 'removed_exact_duplicates': duplicates, 'conflicts': conflicts,
                  'routing_cases': cases, 'voice_ip_cases': len(voice)}
        data = {'report': report}
        if client == 'shadowrocket':
            data['rules_text'] = render_lines(rules, labels)
        elif client == 'quantumultx':
            native, seen = [], set()
            for r in rules:
                n = q.convert(r)
                if n.line not in seen: seen.add(n.line); native.append(n)
            q.check_native_conflicts(native, conflicts)
            boot = set(read_json('platforms.json')['quantumultx']['bootstrap_values'])
            local = [r for r in native if r.source == 'local_direct' and r.value in boot]
            remote = [r for r in native if r not in local and r.kind != 'final']
            require(remote[-1].line == 'geoip, cn, direct', 'Wrong Quantumult X tail')
            data.update({'rules_text': render_lines(remote, labels), 'local_rules_text': render_lines(local, labels)})
            report.update({'native_rules': len(native), 'local_rules': len(local)+1, 'remote_rules': len(remote),
                           'removed_native_duplicates': len(rules)-len(native),
                           'no_resolve_removed': sum(bool(r.options) for r in rules),
                           'user_agent_priority_changes': sum(r.kind == 'USER-AGENT' for r in rules)})
        else:
            desc = [{'id': k, 'policy': 'DIRECT' if k == 'personal_direct' else 'PROXY'} for k in ('personal_direct','personal_proxy')]
            desc += [{'id': s['id'], 'policy': s['policy']} for s in conf['sources'] if s['id'] not in SUPPLEMENTS]
            data['providers'] = desc
            data['rules'] = {}
            for d in desc:
                selected = [r for r in rules if r.source == d['id']]
                require(selected and all(r.policy == d['policy'] for r in selected), 'Empty or mixed-policy category')
                data['rules'][d['id']] = '# ' + d['id'] + ' | ' + labels[d['id']] + '\n' + ''.join(r.selector+'\n' for r in selected)
            # Retired URLs remain available. Parent providers contain all their rules.
            for old, new in SUPPLEMENTS.items(): data['rules'][old] = '# Compatibility resource; merged into ' + new + '.list\n'
            bootstrap = read_json('platforms.json')['clash']['bootstrap']
            data['guard'] = {'rules': bootstrap + ['MATCH,REJECT']}
            report['providers'] = {d['id']: len(list(records(data['rules'][d['id']]))) for d in desc}
        reports[client] = report
        payload['platforms'][client] = data
    check_source_parity(all_stats, conf)
    files = {**inputs, 'sources.lock.json': json_bytes(lock), 'source_stats.json': json_bytes(all_stats)}
    if audit:
        files.update({'candidate_baseline.json': json_bytes(baselines), 'candidate_report.json': json_bytes(reports)})
        return files
    bundle = json_bytes(payload)
    source_hashes = {p.relative_to(ROOT).as_posix(): digest(p.read_bytes()) for p in sorted((ROOT/'shared').rglob('*'))
                     if p.is_file() and '__pycache__' not in p.parts}
    basis = {'schema_version': 1, 'bundle_sha256': digest(bundle), 'source_files_sha256': source_hashes,
             'upstreams': lock['repositories'], 'provenance_sha256': digest(json_bytes(provenance))}
    manifest = {**basis, 'shared_version': digest(json_bytes(basis))}
    files.update({'shared/bundle.json': bundle, 'shared/manifest.json': json_bytes(manifest),
                  'shared/provenance.json': json_bytes(provenance),
                  'shared/overlaps.json': json_bytes(coverage)})
    return files

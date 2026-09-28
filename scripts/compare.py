"""Compare original remote configurations with a built release, without DNS guesses."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import re
import sys

import build as b


def section(text, name):
    match = re.search(r'^\[' + re.escape(name) + r'\]\s*\n(.*?)(?=^\[|\Z)', text, re.M | re.S)
    b.require(match is not None, 'Missing section: ' + name)
    return match.group(1)


def expand_original(path, revisions, downloads):
    text = path.read_text(encoding='utf-8-sig')
    rules = []
    for _, line in b.records(section(text, 'Rule')):
        if line.startswith(('RULE-SET,', 'DOMAIN-SET,')):
            kind, url, policy = line.split(',')
            pieces = url.removeprefix(b.RAW).split('/')
            repo = '/'.join(pieces[:2])
            b.require(repo in revisions and url.startswith(b.RAW), 'Unknown baseline source')
            fixed = b.RAW + repo + '/' + revisions[repo] + '/' + '/'.join(pieces[3:])
            if fixed not in downloads:
                downloads[fixed] = b.fetch(fixed)
            spec = {'id': pieces[-1], 'format': 'domain_set' if kind == 'DOMAIN-SET' else 'shadowrocket', 'policy': policy}
            parsed, _ = b.parse_source(spec, downloads[fixed])
            rules.extend(parsed)
        else:
            rules.append(b.parse_rule(line, source='original_static'))
    return rules, text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original-a', type=Path, required=True)
    parser.add_argument('--original-b', type=Path, required=True)
    parser.add_argument('--release', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    conf = b.load_config(b.ROOT)
    lock, inputs = b.replay(conf, args.release)
    current, _, _, _ = b.assemble(b.ROOT, conf, inputs)
    revisions = {r['repo']: r['commit'] for r in lock['repositories'].values()}
    revisions['ACL4SSR/ACL4SSR'] = json.loads(b.fetch(b.API + 'repos/ACL4SSR/ACL4SSR/commits/master'))['sha']
    downloads = {entry['url']: inputs[path] for path, entry in lock['files'].items()}
    old_a, text_a = expand_original(args.original_a, revisions, downloads)
    old_b, text_b = expand_original(args.original_b, revisions, downloads)
    new_text = (args.release/'shadowrocket.conf').read_text(encoding='utf-8')
    for name in ['General', 'Host']:
        before_a = [line for _, line in b.records(section(text_a, name))]
        before_b = [line for _, line in b.records(section(text_b, name))]
        after = [line for _, line in b.records(section(new_text, name)) if not line.startswith('update-url = ')]
        b.require(before_a == before_b == after, name + ' settings/order drifted')
    cases = json.loads((b.ROOT/'tests/routing_cases.json').read_text())
    for domain in ['auth0.com','login.auth0.com','stripe.com','api.stripe.com','sentry.io','other.ingest.sentry.io',
                   'algolia.net','launchdarkly.com','identrust.com','observeit.net','segment.io',
                   'static.cloudflareinsights.com','browser-intake-datadoghq.com','openaicom.imgix.net',
                   'sub.openaicom.imgix.net','openaiapi-site.azureedge.net','sub.openaiapi-site.azureedge.net',
                   'random-openaicom-api.example','cp4.cloudflare.com','ct.sendgrid.net']:
        cases.append({'name': 'Shared/scope: ' + domain, 'input': {'domain': domain}})
    for ip in ['24.199.123.28', '64.23.132.171']:
        cases.append({'name': 'Removed fixed AI IP: ' + ip, 'input': {'ip': ip}})
    for asn in ['14061', '20473']:
        cases.append({'name': 'Removed cloud ASN: ' + asn, 'input': {'ip': '1.2.3.4', 'asn': asn}})
    rows = []
    for case in cases:
        rows.append({'name': case['name'], 'input': case['input'],
                     'a': b.route(old_a, **case['input']), 'b': b.route(old_b, **case['input']),
                     'new': b.route(current, **case['input'])})
    counts = {}
    for name, rules in [('A',old_a),('B',old_b),('new',current)]:
        merged, conflicts, duplicates = b.merge_rules(rules)
        counts[name] = {'expanded': len(rules), 'unique': len(merged), 'duplicates': duplicates, 'conflicts': len(conflicts)}
    result = {'inputs_sha256': {'original_a': b.digest(args.original_a.read_bytes()), 'original_b': b.digest(args.original_b.read_bytes())},
              'upstream_commits': revisions, 'counts': counts, 'general_and_host_unchanged': True,
              'rows': rows, 'limitation': 'Static model only. UNRESOLVED needs destination IP/geolocation. No live iPhone validation.'}
    md = ['# 初始配置差异报告（2026-09-28）', '',
          'A = shadowrocket-optimized.conf；B = shadowrocket-optimized (2).conf。使用同一固定上游版本展开比较。', '',
          'General 与 Host 的值和顺序逐项相同，仅新增 update-url。URL Rewrite 去除单引号，保留路径、查询参数和片段。', '',
          '这是静态匹配分析；`UNRESOLVED` 表示需要目标 IP / GeoIP 判断，不能直接理解为直连。', '',
          '| 配置 | 展开规则 | 精确去重后 | 同选择器策略冲突 |', '| --- | ---: | ---: | ---: |']
    for name, values in counts.items():
        md.append(f"| {name} | {values['expanded']} | {values['unique']} | {values['conflicts']} |")
    md += ['', '## 分流与规则来源变化', '',
           '| 用例 | A | B | 优化后 | 优化后的首个相关规则 |', '| --- | --- | --- | --- | --- |']
    for row in rows:
        if row['a']['policy'] != row['new']['policy'] or row['b']['policy'] != row['new']['policy'] or row['name'].startswith(('Shared/', 'Removed')):
            md.append(f"| {row['name']} | {row['a']['policy']} | {row['b']['policy']} | {row['new']['policy']} | `{row['new']['rule']}` |")
    md += ['', '## 有意收窄的共享服务', '',
           '- 删除 AI 集合对 auth0.com、stripe.com、sentry.io、algolia.net、launchdarkly.com、identrust.com、observeit.net、segment.io 整个父域的强制代理。部分域名仍命中通用 Proxy，这是保留通用库的结果。',
           '- 保留官方要求的 intercom.io、intercomcdn.com 范围以及 ct.sendgrid.net；Stripe、Sentry、WorkOS、Cloudflare 等使用列明端点。',
           '- Arkose、Statsig、Featuregates 保留为独立兼容规则。humb.apple.com 是共享 Apple 端点，保留 B 的代理选择。',
           '- Copilot 上游 51 条中保留 16 条微软/Bing 专属记录；复制的 OpenAI 规则、共享父域、泛关键词和云 ASN 不再从 Copilot 引入。',
           '- 不再按 AS14061/AS20473 或两个旧固定 IP 认定 AI 流量，改用官方 voice JSON 的 23 条当前网络前缀。',
           '- cp4.cloudflare.com 原先已经代理，本次是显式保障，不是修复漏分流。',
           '- 保留 snssdk.com、bytedapm.com 国内分流；不新增 CapCut、Trae、REJECT 或 MITM。跳过 @ads 记录并不代表广告被拦截，父域仍可能覆盖它们。', '',
           '## 冲突基线', '',
           '登记 42 个现有同选择器异策略组合，保留先匹配规则。例如 Apple/Microsoft 优先直连、TikTok 优先代理。不是将所有冲突自动放行；新增冲突或优先来源变化将阻断发布。域名父子覆盖通过关键回归案例检查，不声称穷尽所有可能流量。', '',
           '## 可复核输入', '',
           *[f'- {repo}: `{sha}`' for repo, sha in revisions.items()], '',
           '完整逐用例结果和原文件 SHA-256 见同目录 initial_comparison.json。手机编译、登录、上传、语音、实际 UDP 支持和后台自动刷新尚待实机验收。', '']
    outputs = {'initial_comparison.json': b.json_bytes(result), 'initial_comparison.md': '\n'.join(md).encode()}
    # Preserve original-only upstream bytes and their provenance outside source.
    legacy_lock = {}
    for url, data in downloads.items():
        name = 'baseline_sources/' + b.digest(url.encode())[:16] + '.txt'
        outputs[name] = data
        legacy_lock[name] = {'url': url, 'sha256': b.digest(data)}
    outputs['baseline_sources.json'] = b.json_bytes(legacy_lock)
    b.write_files(args.output.resolve(), outputs)
    print(json.dumps({'counts': counts, 'cases': len(rows), 'general_and_host_unchanged': True}))


if __name__ == '__main__':
    main()

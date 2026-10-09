import copy
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import build as b
import publish as p


def rules(*lines):
    return [b.parse_rule(line) for line in lines]


class ConversionTests(unittest.TestCase):
    def parse(self, text, fmt='v2fly', **extra):
        return b.parse_source({'id': 'test', 'format': fmt, 'policy': 'PROXY', **extra}, text.encode())

    def test_exact_suffix_and_attributes(self):
        result, stats = self.parse('example.com @!cn\nfull:cdn.example.net\ntracker.example @ads\n')
        self.assertEqual([r.line for r in result], ['DOMAIN-SUFFIX,example.com,PROXY', 'DOMAIN,cdn.example.net,PROXY'])
        self.assertEqual((stats['raw_count'], stats['retained_count']), (3, 2))

    def test_known_regexp_only(self):
        conf = b.load_config(b.ROOT)['sources'][0]
        expression = next(iter(conf['regex_overrides']))
        result, _ = b.parse_source(conf, ('regexp:' + expression).encode())
        self.assertEqual(result[0].kind, 'DOMAIN-WILDCARD')
        with self.assertRaises(b.BuildError):
            b.parse_source(conf, b'regexp:.*azure.com')

    def test_unknown_include_does_not_expand_scope(self):
        with self.assertRaises(b.BuildError):
            self.parse('include:bytedance')

    def test_unknown_attribute_syntax_fails_even_for_ads(self):
        with self.assertRaises(b.BuildError):
            self.parse('bad.example @ads invalid-token')

    def test_new_attribute_semantics_require_review(self):
        with self.assertRaises(b.BuildError):
            self.parse('example.com @new_attribute')

    def test_unknown_rule_fails_before_filter(self):
        with self.assertRaises(b.BuildError):
            self.parse('DOMAIN-REGEX,.*', 'shadowrocket', allow_rules=[])

    def test_no_arbitrary_upstream_policy(self):
        with self.assertRaises(b.BuildError):
            self.parse('DOMAIN,example.com,DIRECT', 'shadowrocket')

    def test_invalid_cidr_and_option(self):
        for value in ['IP-CIDR,10.0.0.1/8', 'IP-CIDR,1.2.3.4/32,unknown', 'DOMAIN,x.test,no-resolve']:
            with self.subTest(value=value), self.assertRaises(b.BuildError):
                self.parse(value, 'shadowrocket')

    def test_ipv6_stays_native_ip_cidr(self):
        result, _ = self.parse('IP-CIDR,2001:4860::/32,no-resolve', 'shadowrocket')
        self.assertEqual(result[0].line, 'IP-CIDR,2001:4860::/32,PROXY,no-resolve')

    def test_literal_keyword_is_preserved(self):
        result, _ = self.parse('DOMAIN-KEYWORD,.tmall.com', 'shadowrocket')
        self.assertEqual(result[0].value, '.tmall.com')
        self.assertEqual(b.route(result, domain='www.tmall.com')['policy'], 'PROXY')
        self.assertEqual(b.route(result, domain='tmall.com')['policy'], 'UNRESOLVED')

    def test_domain_set_dot_and_exact(self):
        result, _ = self.parse('.example.com\nexample.net', 'domain_set')
        self.assertEqual([r.kind for r in result], ['DOMAIN-SUFFIX', 'DOMAIN'])
        self.assertEqual(b.route(result, domain='sub.example.com')['policy'], 'PROXY')
        self.assertEqual(b.route(result, domain='sub.example.net')['policy'], 'UNRESOLVED')

    def test_tiktok_domestic_exclusion(self):
        result, _ = self.parse('bytedapm.com @!cn\ntiktok.com @!cn', exclude_domains=['bytedapm.com'])
        self.assertEqual([r.value for r in result], ['tiktok.com'])

    def test_copilot_whitelist_excludes_shared_and_asn(self):
        result, stats = self.parse('DOMAIN,www.bing.com\nDOMAIN-SUFFIX,stripe.com\nIP-ASN,14061,no-resolve',
                                 'shadowrocket', allow_rules=['DOMAIN,www.bing.com'])
        self.assertEqual([r.value for r in result], ['www.bing.com'])
        self.assertEqual(len(stats['skipped']), 2)

    def test_empty_filtered_set_fails(self):
        with self.assertRaises(b.BuildError):
            self.parse('ad.example @ads')

    def test_html_or_binary_fails(self):
        for data in [b'<html>oops</html>', b'\xff', b'example.com\x00']:
            with self.subTest(data=data), self.assertRaises(b.BuildError):
                b.decode(data)

    def test_voice_prefix_schemas(self):
        obj = {'creationTime': '2026-01-01', 'prefixes': [{'ipv4Prefix': '8.8.8.8/32'}, {'ipv6Prefix': '2001:4860::1/128'}]}
        result, _ = self.parse(json.dumps(obj), 'voice')
        self.assertEqual(len(result), 2)
        self.assertTrue(all(r.options == ('no-resolve',) for r in result))

    def test_voice_rejects_private_broad_wrong_family_and_new_schema(self):
        for entry in [{'ipv4Prefix': '10.0.0.1/32'}, {'ipv4Prefix': '8.0.0.0/8'},
                      {'ipv6Prefix': '8.8.8.8/32'}, {'prefix': '8.8.8.8/32'}]:
            obj = {'creationTime': '2026-01-01', 'prefixes': [entry]}
            with self.subTest(entry=entry), self.assertRaises(b.BuildError):
                self.parse(json.dumps(obj), 'voice')


class RoutingTests(unittest.TestCase):
    def test_first_match_and_exact_dedup(self):
        original = rules('DOMAIN-SUFFIX,example.com,DIRECT', 'DOMAIN-SUFFIX,example.com,PROXY',
                         'DOMAIN-SUFFIX,example.com,DIRECT', 'FINAL,PROXY')
        result, conflicts, duplicates = b.merge_rules(original)
        self.assertEqual(duplicates, 1)
        self.assertEqual(b.route(result, domain='a.example.com')['policy'], 'DIRECT')
        self.assertEqual(len(conflicts), 1)
        with self.assertRaises(b.BuildError):
            b.check_conflicts(conflicts, [])
        b.check_conflicts(conflicts, conflicts)
        changed = copy.deepcopy(conflicts)
        changed[0]['winner_source'] = 'new_source'
        with self.assertRaises(b.BuildError):
            b.check_conflicts(changed, conflicts)

    def test_no_resolve_is_not_deduplicated_away(self):
        result, _, count = b.merge_rules(rules('IP-CIDR,8.8.8.8/32,PROXY,no-resolve', 'IP-CIDR,8.8.8.8/32,PROXY'))
        self.assertEqual((len(result), count), (2, 0))

    def test_suffix_boundary(self):
        rr = rules('DOMAIN-SUFFIX,openai.com,PROXY')
        self.assertEqual(b.route(rr, domain='fakeopenai.com')['policy'], 'UNRESOLVED')
        self.assertEqual(b.route(rr, domain='api.openai.com')['policy'], 'PROXY')

    def test_domain_fallback_requires_geo_evidence(self):
        rr = rules('GEOIP,CN,DIRECT', 'FINAL,PROXY')
        self.assertEqual(b.route(rr, domain='unknown.example')['policy'], 'UNRESOLVED')
        self.assertEqual(b.route(rr, domain='unknown.example', country='CN')['policy'], 'DIRECT')
        self.assertEqual(b.route(rr, domain='unknown.example', country='US')['policy'], 'PROXY')

    def test_critical_regression_fails(self):
        with self.assertRaises(b.BuildError):
            b.check_cases(rules('DOMAIN-SUFFIX,microsoft,DIRECT'),
                          [{'name': 'Copilot', 'input': {'domain': 'copilot.cloud.microsoft'}, 'policy': 'PROXY'}])

    def test_rewrite_preserves_path_query_fragment(self):
        lines = (b.ROOT/'config/base.conf').read_text().split('[URL Rewrite]')[1].strip().splitlines()
        for line in lines:
            expression, replacement, status = line.split()
            target = 'http://www.google.cn/a?q=1#x' if 'google' in expression else 'http://g.cn/a?q=1#x'
            self.assertEqual(re.sub(expression, replacement.replace('$2', r'\g<2>'), target),
                             'https://www.google.com/a?q=1#x')
            self.assertEqual(status, '302')


    def test_expected_rule_accepts_match_and_reports_source(self):
        cases = json.loads((b.ROOT/'tests/routing_cases.json').read_text())
        for case in (c for c in cases if 'expected_rule' in c):
            with self.subTest(case=case['name']):
                rr = [b.parse_rule(case['expected_rule'], source='upstream')]
                result = b.check_cases(rr, [case])[0]
                self.assertEqual(result['actual']['rule'], case['expected_rule'])
                self.assertEqual(result['actual']['source'], 'upstream')

    def test_expected_rule_rejects_missing_broader_and_preempted_matches(self):
        cases = json.loads((b.ROOT/'tests/routing_cases.json').read_text())
        for case in (c for c in cases if 'expected_rule' in c):
            domain = case['input']['domain']
            opposite = 'DIRECT' if case['policy'] == 'PROXY' else 'PROXY'
            variants = {
                'missing': rules('FINAL,' + opposite),
                'broader_same_policy': rules('DOMAIN-KEYWORD,' + domain.rsplit('.', 1)[-1] + ',' + case['policy']),
                'earlier_opposite': rules('DOMAIN,' + domain + ',' + opposite, case['expected_rule']),
            }
            for reason, rr in variants.items():
                with self.subTest(case=case['name'], reason=reason), self.assertRaises(b.BuildError):
                    b.check_cases(rr, [case])

    def test_policy_only_cases_remain_compatible(self):
        case = {'name': 'Legacy policy check', 'input': {'domain': 'sydney.bing.com'}, 'policy': 'PROXY'}
        result = b.check_cases(rules('DOMAIN-SUFFIX,bing.com,PROXY'), [case])
        self.assertEqual(result[0]['actual']['rule'], 'DOMAIN-SUFFIX,bing.com,PROXY')

    def test_front_guards_survive_upstream_duplicates_and_conflicts(self):
        custom = [b.parse_rule(line, source='custom') for _, line in
                  b.records((b.ROOT/'rules/custom.list').read_text())]
        domains = ['githubusercontent.com', 'byteoversea.com', 'ibytedtos.com']
        upstream = [b.parse_rule('DOMAIN-SUFFIX,' + domain + ',' + policy, source='upstream')
                    for domain in domains for policy in ['PROXY', 'DIRECT']]
        merged, conflicts, duplicates = b.merge_rules(custom + upstream)
        self.assertEqual(duplicates, 3)
        self.assertEqual(len(conflicts), 3)
        with self.assertRaises(b.BuildError):
            b.check_conflicts(conflicts, [])
        for domain in domains:
            actual = b.route(merged, domain='cdn.' + domain)
            self.assertEqual(actual, {'policy': 'PROXY',
                'rule': 'DOMAIN-SUFFIX,' + domain + ',PROXY', 'source': 'custom'})


class GateTests(unittest.TestCase):
    limits = {'min_ratio': 0.8, 'max_ratio': 1.5, 'max_removed_ratio': 0.2, 'max_added_ratio': 0.5}

    def test_abnormal_addition_removal_and_inventory_fail(self):
        baseline = {'x': {'raw_count': 10, 'retained_count': 10}}
        for stats in [{'x': {'raw_count': 7, 'retained_count': 7}},
                      {'x': {'raw_count': 16, 'retained_count': 16}}, {}]:
            with self.subTest(stats=stats), self.assertRaises(b.BuildError):
                b.check_limits(stats, baseline, self.limits)
        b.check_limits({'x': {'raw_count': 8, 'retained_count': 8}}, baseline, self.limits)

    def test_same_count_replacement_fails_against_previous_release(self):
        old = {'x': {'raw_count': 2, 'retained_count': 2, 'rules': ['one', 'two']}}
        new = {'x': {'raw_count': 2, 'retained_count': 2, 'rules': ['three', 'four']}}
        with self.assertRaises(b.BuildError):
            b.check_limits(new, old, self.limits)

    def test_http_error_does_not_become_empty_success(self):
        error = urllib.error.HTTPError('url', 503, 'failure', None, None)
        with patch('build.urllib.request.urlopen', side_effect=error), self.assertRaises(b.BuildError):
            b.fetch('https://openai.com/chatgpt-voice.json')

    def test_credentials_in_url_rejected(self):
        for url in ['https://user:password@openai.com/file', 'https://openai.com/file?token=x', 'http://openai.com/file']:
            with self.subTest(url=url), self.assertRaises(b.BuildError):
                b.fetch(url)

    def test_release_read_failure_is_not_first_release(self):
        with patch('build.fetch', side_effect=b.BuildError('HTTP 503')), self.assertRaises(b.BuildError):
            b.previous_stats()

    def test_existing_output_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            (path/'shadowrocket.conf').write_text('last good')
            with self.assertRaises(b.BuildError):
                b.write_files(path, {'shadowrocket.conf': b'bad'})
            self.assertEqual((path/'shadowrocket.conf').read_text(), 'last good')

    def test_replay_hash_tampering_stops_build(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            (path/'snapshots').mkdir()
            (path/'snapshots/x.txt').write_bytes(b'tampered')
            (path/'sources.lock.json').write_bytes(b.json_bytes({'schema_version': 1,
                'files': {'snapshots/x.txt': {'sha256': b.digest(b'correct')}}}))
            with self.assertRaises(b.BuildError):
                b.replay({'sources': [{'id': 'x'}], 'repositories': {}}, path)

    def test_json_yaml_config_inventory_and_no_extra_services(self):
        conf = b.load_config(b.ROOT)
        self.assertEqual(len(conf['sources']), 21)
        self.assertFalse(any(s['id'] in {'capcut', 'trae'} for s in conf['sources']))


class PublishTests(unittest.TestCase):
    def make_release(self, directory):
        files = {'shadowrocket.conf': b'good', 'manifest.json': b.json_bytes({'config_sha256': b.digest(b'good'), 'source_files_sha256': {}}),
                 'sources.lock.json': b'{}', 'source_stats.json': b'{}', 'THIRD_PARTY.md': b'notice'}
        files['SHA256SUMS'] = b.checksum_files(files)
        for name, data in files.items():
            (directory/name).write_bytes(data)
        return files

    def test_tampering_and_unlisted_file_block_publish(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            self.make_release(path)
            p.verified_files(path)
            (path/'extra.txt').write_text('unknown')
            with self.assertRaises(b.BuildError):
                p.verified_files(path)
            (path/'shadowrocket.conf').write_text('changed')
            with self.assertRaises(b.BuildError):
                p.verified_files(path)

    def test_pr_cannot_publish(self):
        with patch.dict(os.environ, {'GITHUB_REPOSITORY': b.REPOSITORY, 'GITHUB_REF': 'refs/heads/main',
                                     'GITHUB_EVENT_NAME': 'pull_request'}), patch('publish.api') as api:
            with self.assertRaises(b.BuildError):
                p.publish(Path('.'))
            api.assert_not_called()

    def test_stale_main_stops_before_any_write(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            self.make_release(path)
            with patch.dict(os.environ, {'GITHUB_REPOSITORY': b.REPOSITORY, 'GITHUB_REF': 'refs/heads/main',
                                         'GITHUB_EVENT_NAME': 'push', 'GITHUB_SHA': 'a'*40}), \
                 patch('publish.api', return_value={'object': {'sha': 'b'*40}}) as api:
                with self.assertRaises(b.BuildError):
                    p.publish(path)
                self.assertEqual(api.call_count, 1)

    def test_failed_fast_forward_keeps_previous_release(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            self.make_release(path)
            writes = []
            def fake(path, payload=None, method=None, missing_ok=False):
                if path == 'git/ref/heads/main': return {'object': {'sha': 'a'*40}}
                if path == 'git/ref/heads/release': return {'object': {'sha': 'b'*40}}
                if path == 'git/trees': return {'sha': 'c'*40}
                if path == 'git/commits/' + 'b'*40: return {'tree': {'sha': 'd'*40}}
                if path == 'git/commits': return {'sha': 'e'*40}
                if path == 'git/refs/heads/release':
                    writes.append(payload)
                    raise b.BuildError('Concurrent update rejected')
                self.fail(path)
            with patch.dict(os.environ, {'GITHUB_REPOSITORY': b.REPOSITORY, 'GITHUB_REF': 'refs/heads/main',
                                         'GITHUB_EVENT_NAME': 'push', 'GITHUB_SHA': 'a'*40}), patch('publish.api', side_effect=fake):
                with self.assertRaises(b.BuildError):
                    p.publish(path)
            self.assertEqual(writes, [{'sha': 'e'*40, 'force': False}])


if __name__ == '__main__':
    unittest.main()

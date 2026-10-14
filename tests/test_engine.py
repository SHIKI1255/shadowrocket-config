import copy,json,os,re,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from shared import engine as b, core, quantumult as q
b.load_config=lambda root:core.read_json('sources.yaml')
def rules(*lines):return [b.parse_rule(line) for line in lines]
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
        lines = (b.ROOT/'config/base.conf').read_text(encoding='utf-8').split('[URL Rewrite]')[1].strip().splitlines()
        for line in lines:
            expression, replacement, status = line.split()
            target = 'http://www.google.cn/a?q=1#x' if 'google' in expression else 'http://g.cn/a?q=1#x'
            self.assertEqual(re.sub(expression, replacement.replace('$2', r'\g<2>'), target),
                             'https://www.google.com/a?q=1#x')
            self.assertEqual(status, '302')


    def test_expected_rule_accepts_match_and_reports_source(self):
        cases = core.case_rows('shadowrocket')
        for case in (c for c in cases if 'expected_rule' in c):
            with self.subTest(case=case['name']):
                rr = [b.parse_rule(case['expected_rule'], source='upstream')]
                result = b.check_cases(rr, [case])[0]
                self.assertEqual(result['actual']['rule'], case['expected_rule'])
                self.assertEqual(result['actual']['source'], 'upstream')

    def test_expected_rule_rejects_missing_broader_and_preempted_matches(self):
        cases = core.case_rows('shadowrocket')
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
                  b.records((b.ROOT/'shared/custom.list').read_text())]
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

class QuantumultConversion(unittest.TestCase):
    def native(self, *lines):
        return [q.convert(b.parse_rule(line)) for line in lines]

    def test_ipv6_wildcard_and_no_resolve(self):
        rules = self.native('IP-CIDR,2001:4860::/32,PROXY,no-resolve',
                            'IP-ASN,15169,PROXY,no-resolve', 'DOMAIN-WILDCARD,chatgpt-*.azure.com,PROXY')
        self.assertEqual([r.line for r in rules], ['ip6-cidr, 2001:4860::/32, proxy',
                          'ip-asn, 15169, proxy', 'host-wildcard, chatgpt-*.azure.com, proxy'])
        for r in rules: self.assertEqual(q.parse_native(r.line).line, r.line)

    def test_native_expected_rule_guards_after_conversion(self):
        cases = core.case_rows('quantumultx')
        for case in (c for c in cases if 'expected_rule' in c):
            domain = case['input']['domain']
            good = self.native(case['expected_rule'])
            b.check_case_result(case, q.route_native(good, **case['input']), context='Native routing')
            opposite = 'DIRECT' if case['policy'] == 'PROXY' else 'PROXY'
            variants = {
                'missing': self.native('FINAL,' + opposite),
                'broader_same_policy': self.native('DOMAIN-KEYWORD,' + domain.rsplit('.', 1)[-1] + ',' + case['policy']),
                'earlier_opposite': self.native('DOMAIN,' + domain + ',' + opposite, case['expected_rule']),
            }
            for reason, rr in variants.items():
                with self.subTest(case=case['name'], reason=reason), self.assertRaises(b.BuildError):
                    b.check_case_result(case, q.route_native(rr, **case['input']), context='Native routing')

    def test_native_unknown_format_family_policy_or_options_fail(self):
        for line in ['ip-cidr, ::1/128, direct', 'ip6-cidr, 1.2.3.4/32, proxy',
                     'host, example.com, proxy, no-resolve', 'host, x.com, secret',
                     'DOMAIN,x.com,PROXY', 'host-regex, .*, proxy']:
            with self.subTest(line=line), self.assertRaises(b.BuildError): q.parse_native(line)

    def test_host_priority_over_user_agent(self):
        rules = self.native('USER-AGENT,Example*,DIRECT', 'DOMAIN-SUFFIX,openai.com,PROXY', 'FINAL,PROXY')
        self.assertEqual(q.route_native(rules, domain='api.openai.com', user_agent='Example/1')['policy'], 'PROXY')
        self.assertEqual(q.route_native(rules, domain='other.test', user_agent='Example/1')['policy'], 'DIRECT')

    def test_dns_dependent_fallback_not_falsely_proven(self):
        rules = self.native('IP-ASN,15169,PROXY,no-resolve', 'GEOIP,CN,DIRECT', 'FINAL,PROXY')
        self.assertEqual(q.route_native(rules, domain='unknown.test')['policy'], 'UNRESOLVED')
        self.assertEqual(q.route_native(rules, ip='1.2.3.4', asn='9999', country='CN')['policy'], 'DIRECT')

    def test_conversion_induced_conflict_stops(self):
        rules = self.native('IP-CIDR,8.8.8.8/32,PROXY,no-resolve', 'IP-CIDR,8.8.8.8/32,DIRECT')
        with self.assertRaises(b.BuildError): q.check_native_conflicts(rules, [])

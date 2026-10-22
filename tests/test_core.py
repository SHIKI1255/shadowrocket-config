import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from shared import core, engine as e


class SharedCore(unittest.TestCase):
    def test_unknown_source_schema_and_duplicate_ids_stop(self):
        for data in [{'schema_version':9,'sources':[]},
                     {'schema_version':1,'sources':[{'id':'x'},{'id':'x'}]}]:
            with patch('pathlib.Path.read_text',return_value=json.dumps(data)),self.assertRaises(e.BuildError):
                core.read_json('sources.yaml')

    def test_all_original_case_coverage_retained(self):
        for client, count in [('shadowrocket',98),('quantumultx',98),('clash',122)]:
            self.assertGreaterEqual(len(core.case_rows(client)),count+50)

    def test_quantity_and_replacement_gates(self):
        limits=core.read_json('sources.yaml')['limits']
        old={'x':{'raw_count':100,'retained_count':100,'rules':['old']}}
        for new in [{}, {'x':{'raw_count':1,'retained_count':1}}, {'x':{'raw_count':151,'retained_count':151}},
                    {'x':{'raw_count':100,'retained_count':100,'rules':['new']}}]:
            with self.assertRaises(e.BuildError):e.check_limits(new,old,limits)

    def test_conflicts_and_priority_are_never_auto_approved(self):
        rr=[e.parse_rule('DOMAIN,example.com,DIRECT'),e.parse_rule('DOMAIN,example.com,PROXY')]
        _,conflicts,_=e.merge_rules(rr)
        with self.assertRaises(e.BuildError):e.check_conflicts(conflicts,[])
        e.check_conflicts(conflicts,conflicts)

    def test_offline_or_private_source_stops(self):
        with patch('urllib.request.urlopen',side_effect=OSError('offline')),self.assertRaises(e.BuildError):
            e.fetch('https://openai.com/chatgpt-voice.json')
        for url in ['https://example.org/subscription','https://openai.com/file?token=x','http://openai.com/file']:
            with self.assertRaises(e.BuildError):e.fetch(url)

    def test_clash_regex_and_process_kept(self):
        rules,_=e.parse_source({'id':'openai','format':'v2fly','policy':'PROXY'},b'regexp:^chatgpt-\\S+-\\d+\\.azure\\.com$\n')
        self.assertEqual(e.route(rules,domain='chatgpt-east-1.azure.com')['policy'],'PROXY')
        self.assertEqual(e.route([e.parse_rule('PROCESS-NAME,Codex.exe,PROXY')],process='Codex.exe')['policy'],'PROXY')

    def test_common_source_drift_stops(self):
        stats={'shadowrocket':{'x':{'rules':['DOMAIN,a.example,PROXY']}},'clash':{'x':{'rules':['DOMAIN,b.example,PROXY']}}}
        with self.assertRaises(e.BuildError):core.check_source_parity(stats,{'sources':[{'id':'x'}]})

    def test_platform_extensions_are_not_equated(self):
        stats={'shadowrocket':{'x':{'rules':['DOMAIN,a.example,PROXY','USER-AGENT,A*,PROXY']}},
               'clash':{'x':{'rules':['DOMAIN,a.example,PROXY','PROCESS-NAME,A.exe,PROXY']}}}
        core.check_source_parity(stats,{'sources':[{'id':'x'}]})

    def test_coverage_is_only_a_report(self):
        rr=[e.parse_rule('DOMAIN-SUFFIX,example.com,PROXY'),e.parse_rule('DOMAIN,sub.example.com,PROXY')]
        self.assertEqual(len(core.overlaps(rr)),1)
        self.assertEqual(len(e.merge_rules(rr)[0]),2)

    def test_source_filters_and_order_still_shared(self):
        conf=core.read_json('sources.yaml')
        self.assertEqual(len(conf['sources']),21)
        for c in ['shadowrocket','clash']:
            specs=list(core.source_specs(conf,c))
            self.assertLess(next(i for i,s in enumerate(specs) if s['id']=='copilot'),next(i for i,s in enumerate(specs) if s['id']=='microsoft'))
            self.assertEqual(next(s for s in specs if s['id']=='tiktok')['exclude_domains'],['bytedapm.com'])
        shared=list(e.records((core.ROOT/'shared/custom.list').read_text(encoding='utf-8')))
        self.assertFalse(any('checkout.mzxnyysm.com' in s for _,s in shared))

    def test_source_order_preserves_exact_options(self):
        rr=[e.parse_rule('IP-CIDR,8.8.8.8/32,PROXY,no-resolve'),e.parse_rule('IP-CIDR,8.8.8.8/32,PROXY')]
        self.assertEqual(len(e.merge_rules(rr)[0]),2)

    def transition_fixture(self):
        def state(n):
            return {'raw_count': n, 'retained_count': n,
                    'rules': ['DOMAIN,host%d.example,PROXY' % i for i in range(n)]}
        before, after = {'x': state(9)}, {'x': state(19)}
        accepted = {'x': {'raw_count': 19, 'retained_count': 19,
                         'reviewed_transition': {
                             'from_sha256': core.source_state_sha256(before['x']),
                             'to_sha256': core.source_state_sha256(after['x'])}}}
        return before, after, accepted, core.read_json('sources.yaml')['limits']

    def test_reviewed_transition_accepts_only_exact_old_and_new_states(self):
        before, after, accepted, limits = self.transition_fixture()
        original = copy.deepcopy(before)
        with self.assertRaises(e.BuildError):
            e.check_limits(after, before, limits)
        core.check_previous_limits(after, before, accepted, limits)
        self.assertEqual(before, original)
        variants = [copy.deepcopy(after) for _ in range(5)]
        variants[0]['x']['rules'][0] = 'DOMAIN,unreviewed.example,PROXY'
        variants[1]['x']['rules'].reverse()
        variants[2]['x']['raw_count'] += 1
        variants[3]['x']['retained_count'] -= 1
        variants[4]['x']['rules'].append('DOMAIN,extra.example,PROXY')
        for variant in variants:
            with self.subTest(variant=variant), self.assertRaises(e.BuildError):
                core.check_previous_limits(variant, before, accepted, limits)
        wrong_before = copy.deepcopy(before)
        wrong_before['x']['rules'][0] = 'DOMAIN,other.example,PROXY'
        with self.assertRaises(e.BuildError):
            core.check_previous_limits(after, wrong_before, accepted, limits)

    def test_reviewed_transition_does_not_exempt_other_sources_or_inventory(self):
        before, after, accepted, limits = self.transition_fixture()
        before['y'] = {'raw_count': 100, 'retained_count': 100, 'rules': ['old']}
        after['y'] = {'raw_count': 151, 'retained_count': 151, 'rules': ['new']}
        accepted['y'] = {'raw_count': 100, 'retained_count': 100}
        with self.assertRaises(e.BuildError):
            core.check_previous_limits(after, before, accepted, limits)
        del after['y']
        with self.assertRaises(e.BuildError):
            core.check_previous_limits(after, before, accepted, limits)

    def test_reviewed_transition_is_not_a_permanent_size_or_replacement_exemption(self):
        _, after, accepted, limits = self.transition_fixture()
        core.check_previous_limits(after, after, accepted, limits)
        changed = copy.deepcopy(after)
        changed['x']['raw_count'] = 100
        with self.assertRaises(e.BuildError):
            core.check_previous_limits(changed, after, accepted, limits)
        changed = copy.deepcopy(after)
        changed['x']['rules'] = ['DOMAIN,replacement.example,PROXY']
        with self.assertRaises(e.BuildError):
            core.check_previous_limits(changed, after, accepted, limits)

    def test_reviewed_transition_rejects_malformed_records(self):
        before, after, accepted, limits = self.transition_fixture()
        for invalid in ({}, 'allow', {'from_sha256': 'x', 'to_sha256': 'y'},
                        {**accepted['x']['reviewed_transition'], 'wildcard': True}):
            bad = copy.deepcopy(accepted)
            bad['x']['reviewed_transition'] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(e.BuildError):
                core.check_previous_limits(after, before, bad, limits)

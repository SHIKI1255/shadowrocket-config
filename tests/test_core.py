import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from shared import core, engine as e


class SharedCore(unittest.TestCase):
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

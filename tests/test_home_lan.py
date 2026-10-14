"""The public LAN endpoint must not open a general private-node escape hatch."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import render_client as renderer
from shared_data import BuildError


class HomeLanTemplate(unittest.TestCase):
    def setUp(self):
        self.base = (ROOT / 'config/base.conf').read_text(encoding='utf-8')
        self.data = {'rules_text': 'DOMAIN,localhost,DIRECT\nDOMAIN,example.com,PROXY\nFINAL,PROXY\n'}

    def test_optional_node_preserves_selected_node_routing(self):
        text = renderer.shadowrocket(self.base, self.data)['shadowrocket.conf'].decode()
        self.assertEqual(text.count(renderer.HOME_LAN_NODE), 1)
        self.assertEqual(text.split('[Rule]\n')[1].split('\n[')[0].strip(), self.data['rules_text'].strip())
        self.assertNotIn('[Proxy Group]', text)
        self.assertIn('udp-policy-not-supported-behaviour = REJECT', text)
        for section in ('General', 'Host', 'URL Rewrite'):
            extract = lambda s: s.split('[' + section + ']\n')[1].split('\n[')[0].strip()
            self.assertEqual(extract(self.base), extract(text))

    def test_other_endpoints_credentials_and_protocol_changes_stop(self):
        node = renderer.HOME_LAN_NODE
        changes = [node + '\n' + node, '', node.replace('192.168.5.4', '203.0.113.1'),
                   node.replace('7897', '1080'), node.replace('socks5,', 'http,'),
                   node.replace(', , ,', ', user, secret,'), node + ', password=secret',
                   node + ', tls=true', node.replace('udp-relay=false', 'udp-relay=true'),
                   node.replace('tfo=false', 'tfo=true'),
                   node + '\nother = socks5, 192.168.5.5, 7897']
        for changed in changes:
            with self.subTest(changed=changed), self.assertRaises(BuildError):
                renderer.shadowrocket(self.base.replace(node, changed), self.data)

    def test_duplicate_or_unapproved_sections_stop(self):
        for changed in [self.base.replace('[Proxy]\n', '[Proxy]\n[Proxy]\n'),
                        self.base + '\n[Proxy Group]\nhome = select, DIRECT\n']:
            with self.assertRaises(BuildError):
                renderer.shadowrocket(changed, self.data)

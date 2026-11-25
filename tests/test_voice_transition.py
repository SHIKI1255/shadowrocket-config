"""Regression coverage for official OpenAI voice snapshots and v2 dynamic IP guardrails."""
import copy
import ipaddress
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared import core, engine as e, quantumult as q

FIXTURES = Path(__file__).parent / "fixtures"
SPEC = {"id": "voice", "format": "voice", "policy": "PROXY"}
OLD_STATE = "4f377c5cc8a14acf0aeac4c48c7c780d8b88d7728570e1046510aaca17a780eb"
NEW_STATE = "eb4a1c6d29cd30bb29ee70afc32f8d75b8a52ad6dc32a325d8ae60718c9adef5"


class ReviewedVoiceTransition(unittest.TestCase):
    old_fixture = "voice-20260326.json"
    new_fixture = "voice-20261025.json"
    old_count, new_count, collapsed_count = 23, 150, 150
    old_state, new_state = OLD_STATE, NEW_STATE

    @classmethod
    def setUpClass(cls):
        cls.old_rules, cls.old = e.parse_source(
            SPEC, (FIXTURES / cls.old_fixture).read_bytes())
        cls.new_json = json.loads((FIXTURES / cls.new_fixture).read_bytes())
        cls.new_rules, cls.new = e.parse_source(
            SPEC, (FIXTURES / cls.new_fixture).read_bytes())
        cls.limits = core.read_json("sources.yaml")["limits"]
        cls.conf = core.read_json("sources.yaml")
        cls.sources_conf = {s["id"]: s for s in cls.conf["sources"]}
        cls.accepted = {
            client: {
                "sources": {
                    "voice": {
                        "raw_count": cls.new_count,
                        "retained_count": cls.new_count,
                        "reviewed_transition": {
                            "from_sha256": cls.old_state,
                            "to_sha256": cls.new_state,
                        },
                    }
                }
            }
            for client in ("shadowrocket", "clash")
        }

    def check_transition(self, current, previous=None, client="shadowrocket"):
        # Exercises the exact reviewed_transition gate without dynamic_ip exemption.
        core.check_previous_limits(
            {"voice": current},
            {"voice": self.old if previous is None else previous},
            {"voice": self.accepted[client]["sources"]["voice"]},
            self.limits,
        )

    def test_reviewed_snapshot_is_exact_public_single_address_expansion(self):
        self.assertEqual((self.old["raw_count"], self.old["retained_count"]),
                         (self.old_count, self.old_count))
        self.assertEqual((self.new["raw_count"], self.new["retained_count"]),
                         (self.new_count, self.new_count))
        self.assertEqual(core.source_state_sha256(self.old), self.old_state)
        self.assertEqual(core.source_state_sha256(self.new), self.new_state)
        self.assertEqual(self.new["skipped"], [])
        old_values = {r.value for r in self.old_rules}
        networks = [ipaddress.ip_network(r.value, strict=True) for r in self.new_rules]
        self.assertEqual(len(set(networks)), self.new_count)
        self.assertTrue(all(n.version == 4 and n.prefixlen == 32 and n.is_global for n in networks))
        self.assertEqual(len(list(ipaddress.collapse_addresses(networks))), self.collapsed_count)
        self.assertEqual([r for r in self.new_rules if r.value in old_values], self.old_rules)
        self.assertEqual(len({r.value for r in self.new_rules} - old_values),
                         self.new_count - self.old_count)

    def test_v2_dynamic_ip_policy_accepts_official_resizes_within_bounds(self):
        voice_spec = self.sources_conf["voice"]
        self.assertTrue(voice_spec["dynamic_ip"])
        self.assertEqual((voice_spec["min_rules"], voice_spec["max_rules"]), (50, 2000))
        # Official resizes within [50, 2000] pass cleanly without false-positive stops.
        e.check_limits({"voice": self.new}, {"voice": self.old}, self.limits, sources_conf=self.sources_conf)
        for out_of_bounds in (49, 2001):
            bad = {"raw_count": out_of_bounds, "retained_count": out_of_bounds, "rules": []}
            with self.subTest(count=out_of_bounds), self.assertRaises(e.BuildError):
                e.check_limits({"voice": bad}, {"voice": self.new}, self.limits, sources_conf=self.sources_conf)

    def test_reviewed_transition_still_enforces_exact_match_when_used(self):
        with self.assertRaises(e.BuildError):
            e.check_limits({"voice": self.new}, {"voice": self.old}, self.limits)
        for client in ("shadowrocket", "clash"):
            with self.subTest(client=client):
                self.check_transition(self.new, client=client)

    def test_first_transition_rejects_changed_members_order_options_and_counts(self):
        variants = {}
        for label in ("member", "order", "options", "missing", "extra", "duplicate", "raw_count"):
            variants[label] = copy.deepcopy(self.new)
        variants["member"]["rules"][0] = "IP-CIDR,8.8.8.8/32,PROXY,no-resolve"
        variants["order"]["rules"].reverse()
        variants["options"]["rules"][0] = variants["options"]["rules"][0].replace(",no-resolve", "")
        variants["missing"]["rules"].pop()
        variants["extra"]["rules"].append("IP-CIDR,8.8.8.8/32,PROXY,no-resolve")
        variants["duplicate"]["rules"][0] = variants["duplicate"]["rules"][1]
        variants["raw_count"]["raw_count"] += 1
        for label, state in variants.items():
            with self.subTest(label=label), self.assertRaises(e.BuildError):
                self.check_transition(state)
        wrong_old = copy.deepcopy(self.old)
        wrong_old["rules"][0] = "IP-CIDR,8.8.8.8/32,PROXY,no-resolve"
        with self.assertRaises(e.BuildError):
            self.check_transition(self.new, previous=wrong_old)

    def test_following_release_keeps_quantity_and_replacement_gates(self):
        self.check_transition(self.new, previous=self.new)
        low = int(self.new_count * self.limits["min_ratio"]) - 1
        high = int(self.new_count * self.limits["max_ratio"]) + 1
        for count in (low, high):
            state = copy.deepcopy(self.new)
            state["raw_count"] = state["retained_count"] = count
            with self.subTest(count=count), self.assertRaises(e.BuildError):
                self.check_transition(state, previous=self.new)
        state = copy.deepcopy(self.new)
        state["rules"] = [
            f"IP-CIDR,{ipaddress.ip_address('8.8.0.0') + i}/32,PROXY,no-resolve"
            for i in range(self.new_count)
        ]
        with self.assertRaises(e.BuildError):
            self.check_transition(state, previous=self.new)

    def test_native_conversion_preserves_each_address_and_policy(self):
        for rule in self.new_rules:
            with self.subTest(network=rule.value):
                self.assertEqual(rule.kind, "IP-CIDR")
                self.assertEqual(rule.policy, "PROXY")
                self.assertEqual(rule.options, ("no-resolve",))
                native = q.convert(rule)
                self.assertEqual(native.line, f"ip-cidr, {rule.value}, proxy")
                self.assertEqual(q.parse_native(native.line, source="voice"), native)
                self.assertEqual(e.parse_rule(rule.selector, "PROXY", "voice"), rule)

    def test_exact_ip_matching_cannot_pass_via_same_policy_fallback(self):
        rules = self.new_rules + [e.parse_rule("FINAL,PROXY", source="final_proxy")]
        native = [q.convert(r) for r in rules]
        values = {r.value for r in self.new_rules}
        for rule in self.new_rules:
            address = ipaddress.ip_network(rule.value).network_address
            with self.subTest(network=rule.value):
                self.assertEqual(e.route(rules, ip=str(address)), {
                    "policy": "PROXY", "rule": rule.line, "source": "voice"})
                self.assertEqual(q.route_native(native, ip=str(address)), {
                    "policy": "PROXY", "rule": f"IP-CIDR,{rule.value},PROXY", "source": "voice"})
                for offset in (-1, 1):
                    adjacent = str(address + offset)
                    if adjacent + "/32" in values:
                        continue
                    self.assertEqual(e.route(rules, ip=adjacent)["source"], "final_proxy")
                    self.assertEqual(q.route_native(native, ip=adjacent)["source"], "final_proxy")

    def test_exact_dedup_retains_first_position_and_preserves_options(self):
        repeated = self.new_rules + [self.new_rules[0]]
        merged, conflicts, duplicates = e.merge_rules(repeated)
        self.assertEqual(merged, self.new_rules)
        self.assertEqual(conflicts, [])
        self.assertEqual(duplicates, 1)
        with_option_difference = e.parse_rule(
            f"IP-CIDR,{self.new_rules[0].value},PROXY", source="voice")
        self.assertEqual(len(e.merge_rules(self.new_rules + [with_option_difference])[0]),
                         self.new_count + 1)

    def test_changed_schema_private_broad_and_wrong_family_still_fail(self):
        for prefix in ({"ipv4Prefix": "10.0.0.1/32"}, {"ipv4Prefix": "8.0.0.0/8"},
                       {"ipv6Prefix": "8.8.8.8/32"}, {"unexpected": "8.8.8.8/32"}):
            data = copy.deepcopy(self.new_json)
            data["prefixes"][0] = prefix
            with self.subTest(prefix=prefix), self.assertRaises(e.BuildError):
                e.parse_source(SPEC, json.dumps(data).encode())
        for data in ({**self.new_json, "extra": True}, {**self.new_json, "prefixes": []}):
            with self.subTest(data_keys=list(data)), self.assertRaises(e.BuildError):
                e.parse_source(SPEC, json.dumps(data).encode())


class ReviewedVoice402Transition(ReviewedVoiceTransition):
    old_fixture = "voice-20261025.json"
    new_fixture = "voice-20261027.json"
    old_count, new_count, collapsed_count = 150, 402, 400
    old_state = NEW_STATE
    new_state = "b6a91feb1ae7cb6c24e01958d87211f94a2380f85ef932e8f08c4d5e0a62b398"

    def test_adjacent_addresses_remain_individual_source_records(self):
        networks = [ipaddress.ip_network(r.value) for r in self.new_rules]
        combined = {str(n) for n in ipaddress.collapse_addresses(networks) if n.prefixlen < 32}
        self.assertEqual(combined, {"9.205.248.20/31", "134.138.50.120/31"})
        self.assertTrue(all(r.value.endswith("/32") for r in self.new_rules))


class ReviewedVoice411Transition(ReviewedVoiceTransition):
    old_fixture = "voice-20261112.json"
    new_fixture = "voice-20261116.json"
    old_count, new_count, collapsed_count = 540, 411, 409
    old_state = "5430bb229a1a721291576f0382127ec7193878f1dbfc818c7aa4892940b3d3f7"
    new_state = "020f85cc14349ac30fd10e28b8e61311def35d9a4b0b1b5e9a595c05a869979d"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # 411/540 = 0.761 passes v2 limits (0.75..1.6); exercise reviewed_transition with strict 0.8/0.2 bounds.
        cls.strict_limits = {"min_ratio": 0.8, "max_ratio": 1.5, "max_removed_ratio": 0.2, "max_added_ratio": 0.5}

    def test_v2_limits_accept_540_to_411_without_manual_transition(self):
        e.check_limits({"voice": self.new}, {"voice": self.old}, self.limits)

    def test_reviewed_transition_still_enforces_exact_match_when_used(self):
        with self.assertRaises(e.BuildError):
            e.check_limits({"voice": self.new}, {"voice": self.old}, self.strict_limits)
        for client in ("shadowrocket", "clash"):
            with self.subTest(client=client):
                core.check_previous_limits(
                    {"voice": self.new}, {"voice": self.old},
                    {"voice": self.accepted[client]["sources"]["voice"]}, self.strict_limits)

    def test_first_transition_rejects_changed_members_order_options_and_counts(self):
        wrong_old = copy.deepcopy(self.old)
        wrong_old["rules"][0] = "IP-CIDR,8.8.8.8/32,PROXY,no-resolve"
        with self.assertRaises(e.BuildError):
            core.check_previous_limits(
                {"voice": self.new}, {"voice": wrong_old},
                {"voice": self.accepted["shadowrocket"]["sources"]["voice"]}, self.strict_limits)

    def test_reviewed_snapshot_is_exact_public_single_address_expansion(self):
        self.assertEqual((self.old["raw_count"], self.old["retained_count"]),
                         (self.old_count, self.old_count))
        self.assertEqual((self.new["raw_count"], self.new["retained_count"]),
                         (self.new_count, self.new_count))
        self.assertEqual(core.source_state_sha256(self.old), self.old_state)
        self.assertEqual(core.source_state_sha256(self.new), self.new_state)
        self.assertEqual(self.new["skipped"], [])
        old_values = {r.value for r in self.old_rules}
        new_values = {r.value for r in self.new_rules}
        self.assertEqual(new_values - old_values, set())
        self.assertEqual(len(old_values - new_values), 129)
        self.assertEqual([r for r in self.new_rules if r.value in old_values],
                         [r for r in self.old_rules if r.value in new_values])

import copy
import io
import json
from pathlib import Path
import sys
import tarfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared import core, engine as e


def _make_tarball(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class SharedCore(unittest.TestCase):
    def test_unknown_source_schema_and_duplicate_ids_stop(self):
        for data in [{"schema_version": 9, "sources": []},
                     {"schema_version": 1, "sources": []},
                     {"schema_version": 2, "sources": [{"id": "x"}, {"id": "x"}]}]:
            with patch("pathlib.Path.read_text", return_value=json.dumps(data)), self.assertRaises(e.BuildError):
                core.read_json("sources.yaml")

    def test_all_original_case_coverage_retained(self):
        for client, count in [("shadowrocket", 98), ("quantumultx", 98), ("clash", 122)]:
            self.assertGreaterEqual(len(core.case_rows(client)), count + 60)

    def test_quantity_and_replacement_gates(self):
        limits = core.read_json("sources.yaml")["limits"]
        old = {"x": {"raw_count": 100, "retained_count": 100,
                     "rules": [f"DOMAIN,h{i}.example,PROXY" for i in range(100)]}}
        for new in [
            {},
            {"x": {"raw_count": 1, "retained_count": 1, "rules": ["DOMAIN,h0.example,PROXY"]}},
            {"x": {"raw_count": 161, "retained_count": 161,
                   "rules": [f"DOMAIN,h{i}.example,PROXY" for i in range(161)]}},
            {"x": {"raw_count": 100, "retained_count": 100,
                   "rules": [f"DOMAIN,new{i}.example,PROXY" for i in range(100)]}},
        ]:
            with self.assertRaises(e.BuildError):
                e.check_limits(new, old, limits)

    def test_small_source_delta_and_dynamic_ip_limits(self):
        limits = core.read_json("sources.yaml")["limits"]
        old_small = {"x": {"raw_count": 10, "retained_count": 10,
                           "rules": [f"DOMAIN,h{i}.example,PROXY" for i in range(10)]}}
        # Small source growth within small_source_delta (+15) passes smoothly.
        good_small = {"x": {"raw_count": 25, "retained_count": 25,
                            "rules": [f"DOMAIN,h{i}.example,PROXY" for i in range(25)]}}
        e.check_limits(good_small, old_small, limits)
        # 100% replacement still fails even for a small source.
        replaced = {"x": {"raw_count": 10, "retained_count": 10,
                          "rules": [f"DOMAIN,other{i}.example,PROXY" for i in range(10)]}}
        with self.assertRaises(e.BuildError):
            e.check_limits(replaced, old_small, limits)
        # Excessive jump (> small_source_delta=40) fails.
        too_big = {"x": {"raw_count": 60, "retained_count": 60,
                         "rules": [f"DOMAIN,h{i}.example,PROXY" for i in range(60)]}}
        with self.assertRaises(e.BuildError):
            e.check_limits(too_big, old_small, limits)
        # Official dynamic IP sources use min_rules..max_rules bounds instead of tight ratios.
        sources_conf = {"voice": {"dynamic_ip": True, "min_rules": 50, "max_rules": 2000}}
        old_v = {"voice": {"raw_count": 150, "retained_count": 150, "rules": []}}
        e.check_limits({"voice": {"raw_count": 444, "retained_count": 444, "rules": []}},
                       old_v, limits, sources_conf=sources_conf)
        for bad in (49, 2001):
            with self.assertRaises(e.BuildError):
                e.check_limits({"voice": {"raw_count": bad, "retained_count": bad, "rules": []}},
                               old_v, limits, sources_conf=sources_conf)

    def test_conflicts_and_category_priority_gates(self):
        rr = [e.parse_rule("DOMAIN,example.com,PROXY", source="github"),
              e.parse_rule("DOMAIN,example.com,DIRECT", source="microsoft")]
        _, conflicts, _ = e.merge_rules(rr)
        with self.assertRaises(e.BuildError):
            e.check_conflicts(conflicts, [])
        e.check_conflicts(conflicts, conflicts)
        allowed_cat = [{"winner": "PROXY", "winner_source": "github",
                        "later": "DIRECT", "later_source": "microsoft"}]
        e.check_conflicts(conflicts, [], allowed_cat)
        # Reversed direction must fail closed.
        reversed_cat = [{"winner": "DIRECT", "winner_source": "microsoft",
                         "later": "PROXY", "later_source": "github"}]
        with self.assertRaises(e.BuildError):
            e.check_conflicts(conflicts, [], reversed_cat)

    def test_offline_or_private_source_stops(self):
        with patch("urllib.request.urlopen", side_effect=OSError("offline")), self.assertRaises(e.BuildError):
            e.fetch("https://openai.com/chatgpt-voice.json")
        for url in ["https://example.org/subscription", "https://openai.com/file?token=x",
                    "http://openai.com/file", "http://core.telegram.org/resources/cidr.txt"]:
            with self.assertRaises(e.BuildError):
                e.fetch(url)

    def test_clash_regex_and_process_kept(self):
        rules, _ = e.parse_source(
            {"id": "openai", "format": "v2fly", "policy": "PROXY"},
            b"regexp:^chatgpt-\\S+-\\d+\\.azure\\.com$\n",
        )
        self.assertEqual(e.route(rules, domain="chatgpt-east-1.azure.com")["policy"], "PROXY")
        self.assertEqual(e.route([e.parse_rule("PROCESS-NAME,Codex.exe,PROXY")], process="Codex.exe")["policy"], "PROXY")

    def test_common_source_drift_stops(self):
        stats = {"shadowrocket": {"x": {"rules": ["DOMAIN,a.example,PROXY"]}},
                 "clash": {"x": {"rules": ["DOMAIN,b.example,PROXY"]}}}
        with self.assertRaises(e.BuildError):
            core.check_source_parity(stats, {"sources": [{"id": "x"}]})

    def test_platform_extensions_are_not_equated(self):
        stats = {"shadowrocket": {"x": {"rules": ["DOMAIN,a.example,PROXY", "USER-AGENT,A*,PROXY"]}},
                 "clash": {"x": {"rules": ["DOMAIN,a.example,PROXY", "PROCESS-NAME,A.exe,PROXY"]}}}
        core.check_source_parity(stats, {"sources": [{"id": "x"}]})

    def test_coverage_is_only_a_report(self):
        rr = [e.parse_rule("DOMAIN-SUFFIX,example.com,PROXY"), e.parse_rule("DOMAIN,sub.example.com,PROXY")]
        self.assertEqual(len(core.overlaps(rr)), 1)
        self.assertEqual(len(e.merge_rules(rr)[0]), 2)

    def test_source_filters_and_order_still_shared(self):
        conf = core.read_json("sources.yaml")
        self.assertEqual(len(conf["sources"]), 20)
        self.assertEqual(set(conf["repositories"]), {"v2fly", "metacubex"})
        for c in ["shadowrocket", "clash"]:
            specs = list(core.source_specs(conf, c))
            ids = [s["id"] for s in specs]
            self.assertNotIn("copilot", ids)
            self.assertIn("xai", ids)
            self.assertIn("telegram_ip", ids)
            self.assertLess(ids.index("github"), ids.index("microsoft"))
            self.assertLess(ids.index("steam_cn"), ids.index("steam"))
            self.assertEqual(next(s for s in specs if s["id"] == "tiktok")["exclude_domains"], ["bytedapm.com"])
        shared = [s for _, s in e.records((core.ROOT / "shared/custom.list").read_text(encoding="utf-8"))]
        self.assertFalse(any("checkout.mzxnyysm.com" in s for s in shared))
        self.assertIn("DOMAIN,sydney.bing.com,PROXY", shared)

    def test_v2fly_tarball_extracts_only_reachable_and_rejects_symlinks(self):
        conf = {
            "repositories": {"v2fly": {"repo": "v2fly/domain-list-community", "ref": "master",
                                       "archive": "tarball", "license": "MIT", "license_path": "LICENSE"}},
            "sources": [{"id": "github", "repository": "v2fly", "path": "data/github", "format": "v2fly", "policy": "PROXY"}],
        }
        good_tar = _make_tarball({
            "repo-abc/LICENSE": b"MIT",
            "repo-abc/data/github": b"include:npmjs\ngithub.com\n",
            "repo-abc/data/npmjs": b"npmjs.org\n",
            "repo-abc/data/unused": b"unused.example\n",
        })
        bundle, lic = core._extract_v2fly_tarball(good_tar, conf, "v2fly")
        self.assertEqual(lic, b"MIT")
        parsed = json.loads(bundle)
        self.assertEqual(set(parsed), {"github", "npmjs"})
        # Reject symlink inside tarball
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            info = tarfile.TarInfo("repo-abc/data/link")
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            tf.addfile(info)
        with self.assertRaises(e.BuildError):
            core._extract_v2fly_tarball(buf.getvalue(), conf, "v2fly")

    def test_source_order_preserves_exact_options(self):
        rr = [e.parse_rule("IP-CIDR,8.8.8.8/32,PROXY,no-resolve"), e.parse_rule("IP-CIDR,8.8.8.8/32,PROXY")]
        self.assertEqual(len(e.merge_rules(rr)[0]), 2)

    def transition_fixture(self):
        def state(n):
            return {"raw_count": n, "retained_count": n,
                    "rules": ["DOMAIN,host%d.example,PROXY" % i for i in range(n)]}
        before, after = {"x": state(100)}, {"x": state(190)}
        accepted = {"x": {"raw_count": 190, "retained_count": 190,
                          "reviewed_transition": {
                              "from_sha256": core.source_state_sha256(before["x"]),
                              "to_sha256": core.source_state_sha256(after["x"])}}}
        return before, after, accepted, core.read_json("sources.yaml")["limits"]

    def test_reviewed_transition_accepts_only_exact_old_and_new_states(self):
        before, after, accepted, limits = self.transition_fixture()
        original = copy.deepcopy(before)
        with self.assertRaises(e.BuildError):
            e.check_limits(after, before, limits)
        core.check_previous_limits(after, before, accepted, limits)
        self.assertEqual(before, original)
        variants = [copy.deepcopy(after) for _ in range(5)]
        variants[0]["x"]["rules"][0] = "DOMAIN,unreviewed.example,PROXY"
        variants[1]["x"]["rules"].reverse()
        variants[2]["x"]["raw_count"] += 1
        variants[3]["x"]["retained_count"] -= 1
        variants[4]["x"]["rules"].append("DOMAIN,extra.example,PROXY")
        for variant in variants:
            with self.subTest(variant=variant), self.assertRaises(e.BuildError):
                core.check_previous_limits(variant, before, accepted, limits)
        wrong_before = copy.deepcopy(before)
        wrong_before["x"]["rules"][0] = "DOMAIN,other.example,PROXY"
        with self.assertRaises(e.BuildError):
            core.check_previous_limits(after, wrong_before, accepted, limits)

    def test_reviewed_transition_does_not_exempt_other_sources_or_inventory(self):
        before, after, accepted, limits = self.transition_fixture()
        before["y"] = {"raw_count": 100, "retained_count": 100, "rules": [f"DOMAIN,o{i}.e,PROXY" for i in range(100)]}
        after["y"] = {"raw_count": 170, "retained_count": 170, "rules": [f"DOMAIN,n{i}.e,PROXY" for i in range(170)]}
        accepted["y"] = {"raw_count": 100, "retained_count": 100}
        with self.assertRaises(e.BuildError):
            core.check_previous_limits(after, before, accepted, limits)
        del after["y"]
        with self.assertRaises(e.BuildError):
            core.check_previous_limits(after, before, accepted, limits)

    def test_reviewed_transition_is_not_a_permanent_size_or_replacement_exemption(self):
        _, after, accepted, limits = self.transition_fixture()
        core.check_previous_limits(after, after, accepted, limits)
        changed = copy.deepcopy(after)
        changed["x"]["raw_count"] = 500
        changed["x"]["retained_count"] = 500
        with self.assertRaises(e.BuildError):
            core.check_previous_limits(changed, after, accepted, limits)
        changed = copy.deepcopy(after)
        changed["x"]["rules"] = [f"DOMAIN,replacement{i}.example,PROXY" for i in range(190)]
        with self.assertRaises(e.BuildError):
            core.check_previous_limits(changed, after, accepted, limits)

    def test_reviewed_transition_rejects_malformed_records(self):
        before, after, accepted, limits = self.transition_fixture()
        for invalid in ({}, "allow", {"from_sha256": "x", "to_sha256": "y"},
                        {**accepted["x"]["reviewed_transition"], "wildcard": True}):
            bad = copy.deepcopy(accepted)
            bad["x"]["reviewed_transition"] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(e.BuildError):
                core.check_previous_limits(after, before, bad, limits)

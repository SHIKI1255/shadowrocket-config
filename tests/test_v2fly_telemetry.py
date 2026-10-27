"""Reviewed v2fly telemetry compatibility; unknown semantics still fail closed."""
import contextlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared import core, engine as e, quantumult as q

FIXTURES = Path(__file__).parent / "fixtures"
OLD_HASH = "ec1043e63c31bbef51efe13a3e2af29a3f66b8b4692f517d75a864cbc4a3d88d"
NEW_HASH = "25d53e9af3e04d926210d6436bd791594ef4b86f2dc3c108510ac8059d76d969"
REVISION = "fa3cb96fbc210b8d6466251a1d71b4a6e63a9c85"


class TelemetryCompatibility(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before = (FIXTURES / "openai-before-telemetry.txt").read_bytes()
        cls.after = (FIXTURES / "openai-after-telemetry.txt").read_bytes()
        cls.spec = next(s for s in core.read_json("sources.yaml")["sources"] if s["id"] == "openai")

    def parse(self, entry):
        return e.parse_source(self.spec, ("example.com\n" + entry + "\n").encode())

    def test_fixed_upstream_snapshots_only_add_reviewed_metadata(self):
        self.assertEqual(e.digest(self.before), OLD_HASH)
        self.assertEqual(e.digest(self.after), NEW_HASH)
        changed = [(a, b) for a, b in zip(self.before.splitlines(), self.after.splitlines()) if a != b]
        self.assertEqual(len(self.before.splitlines()), len(self.after.splitlines()))
        self.assertEqual(len(changed), 3)
        self.assertTrue(all(b == a + b" @telemetry" and a.endswith(b" @ads") for a, b in changed))
        self.assertEqual(self.after.decode().splitlines()[23],
                         "full:browser-intake-datadoghq.com @ads @telemetry")

    def test_old_and_new_ordered_rules_and_exclusions_match_all_clients(self):
        for client in ("shadowrocket", "quantumultx", "clash"):
            with self.subTest(client=client):
                spec = dict(self.spec)
                if client == "clash":
                    spec.pop("regex_overrides")
                old, before = e.parse_source(spec, self.before)
                new, after = e.parse_source(spec, self.after)
                self.assertEqual((after["raw_count"], after["retained_count"]), (25, 21))
                self.assertEqual(old, new)
                self.assertEqual(before, after)
                if client == "quantumultx":
                    self.assertEqual([q.convert(r).line for r in old], [q.convert(r).line for r in new])
                self.assertEqual(len(after["skipped"]), 4)
                self.assertTrue(all(row["reason"] == "ads attribute" for row in after["skipped"]))
                self.assertTrue(all(r.policy == "PROXY" for r in new))

    def test_reviewed_pair_is_order_independent_and_preserves_match_scope(self):
        for tags in ("@ads @telemetry", "@telemetry @ads"):
            for prefix in ("full:", "domain:"):
                with self.subTest(tags=tags, prefix=prefix):
                    rules, stats = self.parse(prefix + "tracker.example " + tags + " # reviewed")
                    self.assertEqual([r.line for r in rules], ["DOMAIN-SUFFIX,example.com,PROXY"])
                    kind = "DOMAIN" if prefix == "full:" else "DOMAIN-SUFFIX"
                    self.assertEqual(stats["skipped"][0]["rule"], kind + ",tracker.example,PROXY")

    def test_standalone_and_unknown_combinations_still_stop(self):
        for tags in ("@telemetry", "@!telemetry", "@ads @new_attribute",
                     "@ads @telemetry @new_attribute", "@ads @telemetry @!cn",
                     "@telemetry @cn", "@ads @telemetry=true"):
            with self.subTest(tags=tags), self.assertRaises(e.BuildError):
                self.parse("full:tracker.example " + tags)

    def test_semantic_error_retains_physical_line_and_unknown_attribute(self):
        with self.assertRaisesRegex(e.BuildError, r"openai:24: Unknown v2fly attribute semantics:.*@new_attribute"):
            e.parse_source(self.spec, self.after.replace(b"@telemetry", b"@new_attribute", 1))

    def test_invalid_rules_and_attributes_cannot_hide_behind_ads(self):
        for entry in ("full:tracker.example @ads @telemetry broken",
                      "include:other @ads @telemetry",
                      "keyword:tracker @ads @telemetry",
                      "full:not/a/domain @ads @telemetry",
                      "regexp:unreviewed @ads @telemetry"):
            with self.subTest(entry=entry), self.assertRaises(e.BuildError):
                self.parse(entry)

    def test_exclusion_is_not_a_reject_rule_and_personal_helpers_survive(self):
        custom = [e.parse_rule(line, source="personal_proxy")
                  for _, line in e.records((core.ROOT / "shared/custom.list").read_text(encoding="utf-8"))]
        parsed, _ = e.parse_source(self.spec, self.after)
        self.assertFalse(any(r.policy == "REJECT" for r in parsed))
        for host in ("o33249.ingest.sentry.io", "rum.browser-intake-datadoghq.com"):
            result = e.route(custom + parsed, domain=host)
            self.assertEqual(result, {"policy": "PROXY", "rule": "DOMAIN," + host + ",PROXY",
                                      "source": "personal_proxy"})

    def test_empty_filtered_sources_and_existing_limits_still_stop(self):
        with self.assertRaises(e.BuildError):
            e.parse_source(self.spec, b"full:tracker.example @ads @telemetry\n")
        limits = core.read_json("sources.yaml")["limits"]
        self.assertEqual(limits, {"min_ratio": 0.8, "max_ratio": 1.5,
                                 "max_removed_ratio": 0.2, "max_added_ratio": 0.5})
        with self.assertRaises(e.BuildError):
            e.check_limits({"x": {"raw_count": 151, "retained_count": 151}},
                           {"x": {"raw_count": 100, "retained_count": 100}}, limits)

    def test_public_snapshot_diagnostics_survive_later_parse_failure(self):
        config = {"repositories": {"v2fly": {"repo": "v2fly/domain-list-community",
                   "ref": "master", "license": "MIT", "license_path": "LICENSE"}},
                  "sources": [self.spec]}
        bad_source = self.after.replace(b"@telemetry", b"@new_attribute", 1)
        raw = e.RAW + "v2fly/domain-list-community/" + REVISION + "/"
        responses = {
            e.API + "repos/v2fly/domain-list-community/git/ref/heads/master":
                json.dumps({"object": {"sha": REVISION}}).encode(),
            raw + "data/openai": bad_source,
            raw + "LICENSE": b"MIT fixture license",
        }
        output = io.StringIO()
        with patch.object(core, "fetch", side_effect=lambda url: responses[url]) as fetch, contextlib.redirect_stdout(output):
            lock, inputs = core.collect(config)
            with self.assertRaises(e.BuildError):
                e.parse_source(self.spec, inputs["snapshots/shadowrocket/openai.txt"])
        event = json.loads(output.getvalue())
        self.assertEqual(event["event"], "public_source_snapshot")
        self.assertEqual(event["repositories"], lock["repositories"])
        self.assertEqual(event["repositories"]["v2fly"]["commit"], REVISION)
        self.assertEqual(event["files_sha256"], {p: e.digest(data) for p, data in inputs.items()})
        self.assertEqual(fetch.call_count, 3)  # Both client inputs share one download.
        self.assertNotIn("browser-intake-datadoghq.com", output.getvalue())
        self.assertNotIn("url", event)
        self.assertEqual(set(event), {"event", "repositories", "files_sha256"})


if __name__ == "__main__":
    unittest.main()

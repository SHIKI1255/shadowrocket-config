"""v2fly unified telemetry compatibility; unknown semantics still fail closed."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tarfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared import core, engine as e, quantumult as q

FIXTURES = Path(__file__).parent / "fixtures"
OLD_HASH = "ec1043e63c31bbef51efe13a3e2af29a3f66b8b4692f517d75a864cbc4a3d88d"
NEW_HASH = "25d53e9af3e04d926210d6436bd791594ef4b86f2dc3c108510ac8059d76d969"
REVISION = "fa3cb96fbc210b8d6466251a1d71b4a6e63a9c85"


def _make_tarball(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


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

    def test_reviewed_pair_and_standalone_telemetry_are_uniformly_skipped(self):
        for tags in ("@ads @telemetry", "@telemetry @ads", "@telemetry", "@telemetry @cn", "@ads @telemetry @!cn"):
            for prefix in ("full:", "domain:"):
                with self.subTest(tags=tags, prefix=prefix):
                    rules, stats = self.parse(prefix + "tracker.example " + tags + " # reviewed")
                    self.assertEqual([r.line for r in rules], ["DOMAIN-SUFFIX,example.com,PROXY"])
                    kind = "DOMAIN" if prefix == "full:" else "DOMAIN-SUFFIX"
                    self.assertEqual(stats["skipped"][0]["rule"], kind + ",tracker.example,PROXY")

    def test_unknown_attributes_still_stop(self):
        for tags in ("@!telemetry", "@ads @new_attribute",
                     "@ads @telemetry @new_attribute", "@ads @telemetry=true"):
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

    def test_empty_filtered_sources_and_v2_limits_still_stop(self):
        with self.assertRaises(e.BuildError):
            e.parse_source(self.spec, b"full:tracker.example @ads @telemetry\n")
        limits = core.read_json("sources.yaml")["limits"]
        self.assertEqual(limits, {
            "min_ratio": 0.75, "max_ratio": 1.6,
            "max_removed_ratio": 0.25, "max_added_ratio": 0.6,
            "small_source_threshold": 100, "small_source_delta": 40,
            "min_absolute_rules": 1,
        })
        with self.assertRaises(e.BuildError):
            e.check_limits({"x": {"raw_count": 161, "retained_count": 161}},
                           {"x": {"raw_count": 100, "retained_count": 100}}, limits)

    def test_public_snapshot_diagnostics_survive_later_parse_failure(self):
        config = {
            "repositories": {
                "v2fly": {
                    "repo": "v2fly/domain-list-community",
                    "ref": "master",
                    "archive": "tarball",
                    "license": "MIT",
                    "license_path": "LICENSE",
                }
            },
            "sources": [self.spec],
        }
        bad_source = self.after.replace(b"@telemetry", b"@new_attribute", 1)
        tarball = _make_tarball({
            f"v2fly-{REVISION}/LICENSE": b"MIT fixture license",
            f"v2fly-{REVISION}/data/openai": bad_source,
        })
        responses = {
            e.API + "repos/v2fly/domain-list-community/git/ref/heads/master":
                json.dumps({"object": {"sha": REVISION}}).encode(),
            e.CODELOAD + "v2fly/domain-list-community/tar.gz/" + REVISION: tarball,
        }
        output = io.StringIO()
        with patch.object(core, "fetch", side_effect=lambda url: responses[url]) as fetch, contextlib.redirect_stdout(output):
            lock, inputs = core.collect(config)
            v2fly_data = json.loads(inputs["snapshots/v2fly/data.json"])
            with self.assertRaises(e.BuildError):
                e.parse_source(self.spec, None, v2fly_data=v2fly_data)
        event = json.loads(output.getvalue())
        self.assertEqual(event["event"], "public_source_snapshot")
        self.assertEqual(event["repositories"], lock["repositories"])
        self.assertEqual(event["repositories"]["v2fly"]["commit"], REVISION)
        self.assertEqual(event["files_sha256"], {p: e.digest(data) for p, data in inputs.items()})
        self.assertEqual(fetch.call_count, 2)  # Ref resolve + single tarball download.
        self.assertNotIn("browser-intake-datadoghq.com", output.getvalue())
        self.assertNotIn("url", event)
        self.assertEqual(set(event), {"event", "repositories", "files_sha256"})


ANTHROPIC_BEFORE = "48597d05d9206b7c841be6872c7ca954685a416d7e61c9316c017ed5f28c7053"
ANTHROPIC_AFTER = "59ad6a25a6d9759995777fe98f2cf30675199fad00546a0ffe027b95ec0776f0"


class AnthropicTelemetryExclusion(unittest.TestCase):
    """v2fly #4268 standalone telemetry is excluded uniformly across v2fly sources."""

    @classmethod
    def setUpClass(cls):
        cls.before = (FIXTURES / "anthropic-before-telemetry.txt").read_bytes()
        cls.after = (FIXTURES / "anthropic-after-telemetry.txt").read_bytes()
        cls.spec = next(s for s in core.read_json("sources.yaml")["sources"] if s["id"] == "claude")

    def parse(self, entry):
        return e.parse_source(self.spec, (entry + "\n").encode())

    def test_fixed_upstream_snapshots_only_add_telemetry_lines(self):
        self.assertEqual(e.digest(self.before), ANTHROPIC_BEFORE)
        self.assertEqual(e.digest(self.after), ANTHROPIC_AFTER)
        before_lines = self.before.splitlines()
        after_lines = self.after.splitlines()
        added = [line for line in after_lines if line not in before_lines]
        self.assertEqual(added, [
            b"# Claude Code telemetry",
            b"full:http-intake.logs.us5.datadoghq.com @telemetry",
            b"full:o1158394.ingest.us.sentry.io @telemetry",
        ])

    def test_standalone_telemetry_is_excluded_for_all_clients(self):
        for client in ("shadowrocket", "quantumultx", "clash"):
            with self.subTest(client=client):
                old, before_stats = e.parse_source(dict(self.spec), self.before)
                new, after_stats = e.parse_source(dict(self.spec), self.after)
                self.assertEqual(before_stats["raw_count"], 19)
                self.assertEqual((after_stats["raw_count"], after_stats["retained_count"]), (21, 19))
                self.assertEqual(old, new)
                skipped = after_stats["skipped"]
                self.assertEqual([row["rule"] for row in skipped], [
                    "DOMAIN,http-intake.logs.us5.datadoghq.com,PROXY",
                    "DOMAIN,o1158394.ingest.us.sentry.io,PROXY",
                ])
                self.assertTrue(all(row["reason"] == "telemetry attribute" for row in skipped))
                self.assertTrue(all(r.policy == "PROXY" for r in new))
                if client == "quantumultx":
                    self.assertEqual([q.convert(r).line for r in old],
                                     [q.convert(r).line for r in new])

    def test_standalone_telemetry_also_works_on_other_v2fly_sources(self):
        github_spec = next(s for s in core.read_json("sources.yaml")["sources"] if s["id"] == "github")
        rules, stats = e.parse_source(github_spec, b"github.com\ncafe.github.com @telemetry\n")
        self.assertEqual([r.line for r in rules], ["DOMAIN-SUFFIX,github.com,PROXY"])
        self.assertEqual(stats["skipped"][0]["reason"], "telemetry attribute")

    def test_unknown_attributes_still_stop_on_claude(self):
        for entry in ("full:tracker.example @telemetry @new_attribute",
                      "full:tracker.example @!telemetry"):
            with self.subTest(entry=entry), self.assertRaises(e.BuildError):
                self.parse(entry)

    def test_telemetry_exclusion_validates_syntax_before_skipping(self):
        for entry in ("full:tracker.example @telemetry broken",
                      "include:other @telemetry",
                      "keyword:tracker @telemetry",
                      "regexp:unreviewed @telemetry"):
            with self.subTest(entry=entry), self.assertRaises(e.BuildError):
                self.parse(entry)

    def test_semantic_error_keeps_claude_physical_line(self):
        with self.assertRaisesRegex(e.BuildError,
                                    r"claude:24: Unknown v2fly attribute semantics:.*@new_attribute"):
            e.parse_source(self.spec, self.after.replace(b"@telemetry", b"@new_attribute", 1))

    def test_claude_source_with_only_telemetry_is_empty_and_stops(self):
        with self.assertRaisesRegex(e.BuildError, r"Empty rules: claude"):
            self.parse("full:tracker.example @telemetry")


if __name__ == "__main__":
    unittest.main()

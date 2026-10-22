"""Failure paths use fabricated subscriptions, never a user's actual profile."""
import contextlib
from datetime import datetime, timezone
from functools import wraps
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import sync_hosts as sync
from hosts_core import (BEGIN, END, ALLOWED_FILES, MODULE, README, HostsError, digest,
                        module_timestamp, parse_module, render_module, update_readme)
from test_hosts_core import hosts

HAS_YAML = importlib.util.find_spec("yaml") is not None
BASE = "a" * 40
HEAD = "b" * 40
MERGED = "c" * 40
SECRET = "CREDENTIAL_CANARY_DO_NOT_PUBLISH"
TEMPLATE = "# Module\n" + BEGIN + "\n" + END + "\n"


def body(entries=None, secret=SECRET):
    # JSON is a YAML subset: deterministic synthetic complete Clash subscription.
    entries = hosts() if entries is None else entries
    return json.dumps({"hosts": entries, "proxies": [{"server": next(iter(entries), "a.example.com"),
                       "password": secret, "name": secret}], "subscription_url": secret}).encode()


def with_subscription_parsers(test):
    """Exercise recovery with JSON fixtures, plus real YAML when installed."""
    @wraps(test)
    def run(self):
        # body() emits JSON. Replace only parsing, retaining subscription
        # validation and every recovery/publication assertion in both modes.
        parsers = [("stdlib_json_fixture", json.loads)]
        if HAS_YAML:
            parsers.append(("pyyaml_integration", sync.load_yaml))
        for name, parser in parsers:
            with self.subTest(parser=name):
                with patch.object(sync, "load_yaml", side_effect=parser) as parse:
                    test(self)
                parse.assert_called()
    return run


def public_files(entries=None):
    entries = hosts() if entries is None else entries
    return {MODULE: render_module(entries), README: update_readme(TEMPLATE, entries,
        dict.fromkeys(entries, 0), 1, "2026-10-13", BASE).encode()}


class FakeGitHub:
    def __init__(self):
        self.files = public_files()
        self.created = []
        self.merged = False
        self.open = []

    def head(self): return BASE
    def check_runner(self, base): pass
    def open_sync_prs(self): return self.open
    def file(self, path, sha): return self.files[path]

    def create_pr(self, base, files):
        self.created.append(files)
        return {"base": BASE, "head": HEAD, "number": 123,
                "hashes": {p: digest(files[p]) for p in ALLOWED_FILES}}

    def wait_ci(self, pending, **kwargs): pass

    def merge(self, pending):
        self.merged = True
        self.files = self.created[-1]
        return MERGED

    def api(self, path):
        return {"merged": self.merged, "merge_commit_sha": MERGED}


@unittest.skipUnless(HAS_YAML, "Local sync requires PyYAML; dedicated Hosts CI installs the pinned version")
class PrivateParsingTests(unittest.TestCase):
    def test_dependency_exact_version(self):
        import yaml
        self.assertEqual(yaml.__version__, "6.0.3")

    def test_real_yaml_shape_and_private_boundary(self):
        text = b"hosts:\n  a.example.com: 1.1.1.1\nproxies:\n  - server: a.example.com\n    password: secret\n"
        parsed = sync.subscription(text)
        self.assertEqual(parsed, {"hosts": {"a.example.com": "1.1.1.1"},
                                 "references": {"a.example.com": 1}, "node_count": 1})
        self.assertNotIn(SECRET, json.dumps(sync.subscription(body())))

    def test_duplicate_keys_aliases_tags_and_bad_documents_stop(self):
        examples = [b"hosts: {}\nhosts: {}", b"hosts:\n  a: 1\n  a: 2", b"a: &a 1\nb: *a",
                    b"!!python/object/apply:os.system [echo bad]", b"a: 1\n---\nb: 2", b"\xff"]
        for content in examples:
            with self.subTest(content=content), self.assertRaises(HostsError):
                sync.load_yaml(content)
        for content in (b"<html>login required</html>", b"{}", b"hosts: {}", body({}),
                        body({"a.example.com": ["1.1.1.1"]})):
            with self.assertRaises(HostsError):
                sync.subscription(content)

    def test_no_change_including_order_and_node_metadata_does_not_read_twice(self):
        fetch = Mock(return_value=body(dict(reversed(list(hosts().items()))), secret="changed"))
        sleeper = Mock()
        self.assertIsNone(sync.candidate(hosts(), TEMPLATE, BASE, fetch, sleeper))
        self.assertEqual(fetch.call_count, 1)
        sleeper.assert_not_called()

    def test_full_replacement_and_twice_verified(self):
        entries = hosts()
        entries.pop("entry0.example.com")
        entries["new.example.com"] = "1.1.1.1"
        entries = {k: "9.9.9.9" for k in entries}
        fetch, sleeper = Mock(return_value=body(entries)), Mock()
        result = sync.candidate(hosts(), TEMPLATE, BASE, fetch, sleeper,
                                datetime(2026, 10, 16, 1, 2, 3, tzinfo=timezone.utc))
        self.assertEqual(fetch.call_count, 2)
        sleeper.assert_called_once_with(10)
        self.assertEqual(parse_module(result["files"][MODULE]), entries)
        self.assertEqual(module_timestamp(result["files"][MODULE]), "2026-10-16 09:02:03 +08:00")
        self.assertIn(result["checked_at"], result["files"][README].decode())
        self.assertEqual(len(result["diff"]["added"]), 1)
        self.assertEqual(len(result["diff"]["removed"]), 1)
        for data in result["files"].values(): self.assertNotIn(SECRET.encode(), data)

    def test_source_changes_and_quantity_gate_stop_before_candidate(self):
        for responses, expected in (([body(hosts(15)), body(hosts(14))], "source_changed_between_reads"),
                                    ([body(hosts(8))] * 2, "quantity_gate"),
                                    ([body(hosts(32))] * 2, "quantity_gate")):
            with self.assertRaisesRegex(HostsError, expected):
                sync.candidate(hosts(), TEMPLATE, BASE, Mock(side_effect=responses), Mock())

    def test_source_identity_must_be_unique_and_https_origin_exact(self):
        profile = {"uid": sync.PROFILE_UID, "type": "remote",
                   "url": f"https://api.geekspeed.org/api/v1/client/subscribe?token={SECRET}"}
        with tempfile.TemporaryDirectory() as temp, patch.object(sync.Path, "home", return_value=Path(temp)):
            path = Path(temp) / "AppData/Roaming/io.github.clash-verge-rev.clash-verge-rev/profiles.yaml"
            path.parent.mkdir(parents=True)
            for rows in ([profile], [profile, profile], [], [dict(profile, url="https://other.example/?token=x")]):
                path.write_text(json.dumps({"items": rows}), encoding="utf-8")
                if rows == [profile]:
                    self.assertEqual(sync.source_url(), profile["url"])
                else:
                    with self.assertRaises(HostsError): sync.source_url()

    def test_no_change_run_never_creates_pr(self):
        gh = FakeGitHub()
        with tempfile.TemporaryDirectory() as temp, patch.object(sync, "source_url", return_value="private"):
            result = sync.run(Path(temp), True, gh, Mock(), Mock(return_value=body()))
        self.assertEqual(result["status"], "unchanged")
        self.assertFalse(gh.created or gh.merged)

    def test_stamped_no_change_does_not_refresh_timestamp_or_readme(self):
        gh = FakeGitHub()
        stamp = "2026-10-16 09:02:03 +08:00"
        gh.files = {MODULE: render_module(hosts(), stamp),
                    README: update_readme(TEMPLATE, hosts(), dict.fromkeys(hosts(), 0), 1, stamp, BASE).encode()}
        original = dict(gh.files)
        # A different order and node count do not cause a date-only commit.
        response = json.loads(body(dict(reversed(list(hosts().items())))))
        response["proxies"].append({"server": "other.example.com"})
        fetch = Mock(return_value=json.dumps(response).encode())
        with tempfile.TemporaryDirectory() as temp, patch.object(sync, "source_url", return_value="private"):
            for _ in range(2):
                self.assertEqual(sync.run(Path(temp), True, gh, Mock(), fetch)["status"], "unchanged")
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(gh.files, original)
        self.assertFalse(gh.created or gh.merged)

    def test_premerge_source_must_equal_candidate_after_ci(self):
        new = hosts(15)
        cases = (body(hosts(14)), body(), b"hosts: {}", urllib.error.URLError(SECRET))
        for final in cases:
            with self.subTest(final_type=type(final).__name__):
                gh = FakeGitHub()
                original = dict(gh.files)
                events = []
                responses = iter([body(new), body(new), final])
                def fetch(url, **kwargs):
                    events.append("source")
                    item = next(responses)
                    if isinstance(item, Exception): raise item
                    return item
                gh.wait_ci = Mock(side_effect=lambda *a, **kw: events.append("ci"))
                with tempfile.TemporaryDirectory() as temp, patch.object(sync, "source_url", return_value="private"), contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaises((HostsError, urllib.error.URLError)):
                        sync.run(Path(temp), True, gh, Mock(), fetch)
                    self.assertTrue((Path(temp) / "publication.json").exists())
                self.assertEqual(events, ["source", "source", "ci", "source"])
                self.assertEqual(len(gh.created), 1)
                self.assertFalse(gh.merged)
                self.assertEqual(gh.files, original)

    def test_premerge_confirmation_does_not_regenerate_candidate(self):
        gh = FakeGitHub()
        new = hosts(15)
        events, snapshots = [], []
        def download(url, **kwargs):
            if kwargs.get("private"):
                events.append("source")
                return body(dict(reversed(list(new.items()))))
            return gh.files[MODULE if url.endswith("sgmodule") else README]
        def ci(*args, **kwargs):
            events.append("ci")
            snapshots.append(dict(gh.created[0]))
        gh.wait_ci = Mock(side_effect=ci)
        with tempfile.TemporaryDirectory() as temp, patch.object(sync, "source_url", return_value="private"), contextlib.redirect_stdout(io.StringIO()):
            result = sync.run(Path(temp), True, gh, Mock(), download)
        self.assertEqual(events, ["source", "source", "ci", "source"])
        self.assertEqual(result["status"], "published")
        self.assertEqual(gh.files, snapshots[0])
        self.assertEqual(parse_module(gh.files[MODULE]), new)
        self.assertEqual(module_timestamp(gh.files[MODULE]), result["checked_at"])

    def test_check_only_and_publish_only_two_files(self):
        for publish in (False, True):
            gh = FakeGitHub()
            new = hosts(15)
            def download(url, **kwargs):
                return body(new) if kwargs.get("private") else gh.files[MODULE if url.endswith("sgmodule") else README]
            with tempfile.TemporaryDirectory() as temp, patch.object(sync, "source_url", return_value="private"), contextlib.redirect_stdout(io.StringIO()):
                result = sync.run(Path(temp), publish, gh, Mock(), download)
            self.assertEqual(result["status"], "published" if publish else "candidate")
            self.assertEqual(len(gh.created), int(publish))
            if publish: self.assertEqual(set(gh.created[0]), set(ALLOWED_FILES))
            self.assertNotIn(SECRET, json.dumps(result))

    def test_ci_failure_leaves_old_files_and_pending_pr_no_duplicate(self):
        gh = FakeGitHub()
        gh.wait_ci = Mock(side_effect=HostsError("ci_failed"))
        with tempfile.TemporaryDirectory() as temp, patch.object(sync, "source_url", return_value="private"), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(HostsError, "ci_failed"):
                sync.run(Path(temp), True, gh, Mock(), Mock(return_value=body(hosts(15))))
            self.assertFalse(gh.merged)
            self.assertEqual(gh.files, public_files())
            with self.assertRaisesRegex(HostsError, "merge_pending"):
                sync.run(Path(temp), True, gh, Mock(), Mock())
            self.assertEqual(len(gh.created), 1)


class PublicationAndTransportTests(unittest.TestCase):
    def test_module_gate_runs_without_optional_yaml_dependency(self):
        result = subprocess.run([sys.executable, "-B", "-S", str(ROOT / "scripts/validate_hosts.py")],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_bounded_retry_auth_redirect_size(self):
        sleeper = Mock()
        opener = MagicMock()
        opener.open.side_effect = urllib.error.URLError(SECRET)
        with self.assertRaisesRegex(HostsError, "download_exhausted"):
            sync.download("https://example.com", opener=opener, sleeper=sleeper)
        self.assertEqual(opener.open.call_count, 3)
        opener.open.reset_mock()
        opener.open.side_effect = urllib.error.HTTPError("private", 403, SECRET, {}, None)
        with self.assertRaisesRegex(HostsError, "http_rejected"):
            sync.download("https://example.com", opener=opener, sleeper=sleeper)
        self.assertEqual(opener.open.call_count, 1)
        with self.assertRaisesRegex(HostsError, "redirect_rejected"):
            sync.NoRedirect().redirect_request(None, None, 302, SECRET, {}, "https://other.example")
        opener.open.side_effect = None
        response = opener.open.return_value.__enter__.return_value
        response.status = 200
        response.read.return_value = b"x" * (sync.MAX_BYTES + 1)
        with self.assertRaisesRegex(HostsError, "response_size"):
            sync.download("https://example.com", opener=opener)

    def test_mutual_exclusion_and_release(self):
        with tempfile.TemporaryDirectory() as temp:
            with sync.runtime_lock(Path(temp)):
                with self.assertRaisesRegex(HostsError, "already_running"):
                    with sync.runtime_lock(Path(temp)): pass
            with sync.runtime_lock(Path(temp)): pass

    def test_existing_pr_blocks_before_private_fetch(self):
        gh = FakeGitHub()
        gh.open = [{"number": 1}]
        with tempfile.TemporaryDirectory() as temp, patch.object(sync, "source_url") as source:
            with self.assertRaisesRegex(HostsError, "existing_sync_pr"):
                sync.run(Path(temp), True, gh)
            source.assert_not_called()

    @with_subscription_parsers
    def test_raw_cache_delay_reverified_without_second_pr(self):
        gh = FakeGitHub()
        stamp = "2026-10-18 09:00:05 +08:00"
        gh.files = {MODULE: render_module(hosts(), stamp),
                    README: update_readme(TEMPLATE, hosts(), dict.fromkeys(hosts(), 0), 1, stamp, BASE).encode()}
        gh.merged = True
        pending = {"number": 123, "head": HEAD, "base": BASE,
                   "hashes": {p: digest(gh.files[p]) for p in ALLOWED_FILES}}
        with tempfile.TemporaryDirectory() as temp:
            sync.save_state(Path(temp), pending)
            with self.assertRaisesRegex(HostsError, "fixed_raw_pending"):
                sync.run(Path(temp), True, gh, sleeper=Mock(), downloader=Mock(return_value=b"old"))
            def raw(url, **kwargs):
                return body() if kwargs.get("private") else gh.files[MODULE if url.endswith("sgmodule") else README]
            out = io.StringIO()
            with patch.object(sync, "source_url", return_value="private"), contextlib.redirect_stdout(out):
                result = sync.run(Path(temp), True, gh, downloader=raw)
            self.assertEqual(result["status"], "unchanged")
            self.assertTrue(result["source_checked"])
            recovery = json.loads(out.getvalue())
            self.assertEqual(recovery["status"], "publication_recovered")
            self.assertFalse(recovery["source_checked"])
            self.assertEqual(module_timestamp(gh.files[MODULE]), stamp)
            self.assertTrue(json.loads((Path(temp) / "publication.json").read_text())["verified"])
            self.assertFalse(gh.created)

    def pending_publication(self, gh):
        return {"number": 123, "head": HEAD, "base": BASE, "merged_sha": MERGED,
                "hashes": {p: digest(gh.files[p]) for p in ALLOWED_FILES}}

    def test_rolling_raw_delay_both_files_and_bounded_exhaustion(self):
        for stale_path in ALLOWED_FILES:
            for clears in (True, False):
                with self.subTest(path=stale_path, clears=clears):
                    gh = FakeGitHub()
                    gh.merged = True
                    gh.api = Mock(wraps=gh.api)
                    gh.merge = Mock(wraps=gh.merge)
                    sleeper, out = Mock(), io.StringIO()
                    urls = []
                    def raw(url):
                        urls.append(url)
                        path = MODULE if url.endswith("sgmodule") else README
                        if url == f"{sync.RAW_ROOT}/main/{stale_path}" and (not clears or gh.api.call_count < 8):
                            return b"old"
                        return gh.files[path]
                    with contextlib.redirect_stdout(out):
                        if clears:
                            self.assertEqual(sync.verify_published(gh, self.pending_publication(gh), raw, sleeper), MERGED)
                        else:
                            with self.assertRaises(sync.PublicationError) as error:
                                sync.verify_published(gh, self.pending_publication(gh), raw, sleeper)
                            self.assertEqual(error.exception.code, "rolling_raw_pending")
                            self.assertEqual(error.exception.details, {
                                "phase": "rolling_raw", "merged": True, "pr": 123,
                                "commit": MERGED, "pending_file": stale_path,
                                "attempt": 8, "retry_count": 7})
                    self.assertEqual(gh.api.call_count, 8)
                    self.assertEqual([c.args[0] for c in sleeper.call_args_list], [5, 10, 20, 40, 60, 60, 60])
                    self.assertEqual(len(out.getvalue().splitlines()), 7)
                    self.assertTrue(all("?" not in url for url in urls))
                    self.assertFalse(gh.created)
                    gh.merge.assert_not_called()

    def test_non_visibility_errors_never_retry(self):
        for code in ("http_rejected", "response_size", "download_exhausted", "published_hash_mismatch"):
            with self.subTest(code=code):
                gh = FakeGitHub()
                gh.merged = True
                pending = self.pending_publication(gh)
                raw, sleeper = Mock(side_effect=HostsError(code)), Mock()
                if code == "published_hash_mismatch":
                    gh.file = Mock(return_value=b"wrong")
                with self.assertRaises(sync.PublicationError) as error:
                    sync.verify_published(gh, pending, raw, sleeper)
                self.assertEqual(error.exception.code, code)
                self.assertEqual(error.exception.details["attempt"], 1)
                sleeper.assert_not_called()

    @with_subscription_parsers
    def test_recovery_then_changed_source_creates_one_fresh_pr(self):
        gh = FakeGitHub()
        gh.merged = True
        original = self.pending_publication(gh)
        events = []
        def raw(url, **kwargs):
            if kwargs.get("private"):
                events.append("source")
                with self.assertRaisesRegex(HostsError, "already_running"):
                    with sync.runtime_lock(directory): pass
                if events.count("source") == 1:
                    self.assertTrue(json.loads((directory / "publication.json").read_text())["verified"])
                return body(hosts(15))
            return gh.files[MODULE if url.endswith("sgmodule") else README]
        gh.head = Mock(side_effect=lambda: events.append("head") or MERGED)
        # Only private reads count when asserting two reads plus the pre-merge check.
        with tempfile.TemporaryDirectory() as temp, patch.object(sync, "source_url", return_value="private"), contextlib.redirect_stdout(io.StringIO()):
            directory = Path(temp)
            sync.save_state(directory, original)
            result = sync.run(directory, True, gh, Mock(), raw)
            self.assertTrue(json.loads((directory / "publication.json").read_text())["verified"])
        self.assertEqual(events, ["head", "source", "source", "source"])
        self.assertEqual(result["status"], "published")
        self.assertEqual(len(gh.created), 1)
        self.assertEqual(parse_module(gh.files[MODULE]), hosts(15))

    @with_subscription_parsers
    def test_recovery_then_source_failure_or_interrupt_is_not_success(self):
        for failure, status, reason in ((HostsError("download_exhausted"), "needs_attention", "download_exhausted"),
                                        (KeyboardInterrupt(), "needs_attention", "interrupted")):
            with self.subTest(reason=reason):
                gh = FakeGitHub()
                gh.merged = True
                def raw(url, **kwargs):
                    if kwargs.get("private"): raise failure
                    return gh.files[MODULE if url.endswith("sgmodule") else README]
                real_run = sync.run
                with tempfile.TemporaryDirectory() as temp, patch.object(sync, "source_url", return_value="private"):
                    directory = Path(temp)
                    sync.save_state(directory, self.pending_publication(gh))
                    out = io.StringIO()
                    with patch.object(sync, "run", side_effect=lambda d, **kw: real_run(d, gh=gh, sleeper=Mock(), downloader=raw, **kw)), contextlib.redirect_stdout(out):
                        self.assertEqual(sync.main(["--publish", "--state-dir", temp]), 1)
                    events = [json.loads(line) for line in out.getvalue().splitlines()]
                    self.assertEqual(events[0]["status"], "publication_recovered")
                    self.assertEqual(events[-1], {"status": status, "reason": reason})
                    self.assertTrue(json.loads((directory / "publication.json").read_text())["verified"])
                    # A later process resumes a fresh check without repeating recovery.
                    def good_raw(url, **kwargs):
                        return body() if kwargs.get("private") else gh.files[MODULE if url.endswith("sgmodule") else README]
                    resumed = io.StringIO()
                    with contextlib.redirect_stdout(resumed):
                        result = real_run(directory, True, gh, Mock(), good_raw)
                    self.assertEqual(result["status"], "unchanged")
                    self.assertEqual(resumed.getvalue(), "")
                    self.assertFalse(gh.created)

    def test_unmerged_recovery_never_reads_source_or_changes_state(self):
        gh, sleeper = FakeGitHub(), Mock()
        with tempfile.TemporaryDirectory() as temp, patch.object(sync, "source_url") as source, contextlib.redirect_stdout(io.StringIO()):
            directory = Path(temp)
            pending = self.pending_publication(gh)
            del pending["merged_sha"]
            sync.save_state(directory, pending)
            before = (directory / "publication.json").read_bytes()
            with self.assertRaisesRegex(HostsError, "merge_pending"):
                sync.run(directory, True, gh, sleeper, Mock())
            self.assertEqual((directory / "publication.json").read_bytes(), before)
            source.assert_not_called()
            self.assertFalse(gh.created or gh.merged)

    @with_subscription_parsers
    def test_interrupt_before_recovery_save_is_safe_to_resume(self):
        gh = FakeGitHub()
        gh.merged = True
        raw = lambda url, **kw: body() if kw.get("private") else gh.files[MODULE if url.endswith("sgmodule") else README]
        with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()):
            directory = Path(temp)
            sync.save_state(directory, self.pending_publication(gh))
            before = (directory / "publication.json").read_bytes()
            with patch.object(sync.os, "replace", side_effect=KeyboardInterrupt()), patch.object(sync, "source_url") as source:
                with self.assertRaises(KeyboardInterrupt):
                    sync.run(directory, True, gh, Mock(), raw)
                source.assert_not_called()
            self.assertEqual((directory / "publication.json").read_bytes(), before)
            with patch.object(sync, "source_url", return_value="private"):
                self.assertEqual(sync.run(directory, True, gh, Mock(), raw)["status"], "unchanged")
            self.assertTrue(json.loads((directory / "publication.json").read_text())["verified"])
            self.assertFalse(gh.created)

    def test_readback_rejects_invalid_state_without_leaking_identifiers(self):
        gh = FakeGitHub()
        for field, value in (("number", SECRET), ("merged_sha", SECRET), ("hashes", {SECRET: "bad"})):
            with self.subTest(field=field):
                pending = self.pending_publication(gh)
                pending[field] = value
                with self.assertRaises(sync.PublicationError) as error:
                    sync.verify_published(gh, pending, Mock(), Mock())
                self.assertNotIn(SECRET, json.dumps(error.exception.details))
                self.assertEqual(error.exception.details["attempt"], 1)

    def test_readback_failure_cli_reports_public_stage_and_nonzero(self):
        gh = FakeGitHub()
        gh.merged = True
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            sync.save_state(directory, self.pending_publication(gh))
            real_run, out = sync.run, io.StringIO()
            with patch.object(sync, "run", side_effect=lambda d, **kw: real_run(d, gh=gh, sleeper=Mock(), downloader=Mock(return_value=SECRET.encode()), **kw)), contextlib.redirect_stdout(out):
                self.assertEqual(sync.main(["--publish", "--state-dir", temp]), 1)
            result = json.loads(out.getvalue().splitlines()[-1])
            self.assertEqual(result["status"], "needs_attention")
            self.assertTrue(result["merged"])
            self.assertEqual(result["commit"], MERGED)
            self.assertEqual(result["phase"], "fixed_raw")
            self.assertEqual(result["attempt"], 8)
            self.assertFalse(json.loads((directory / "publication.json").read_text()).get("verified"))
            self.assertNotIn(SECRET, out.getvalue())

    def test_merge_visibility_delay_retries_only_readback(self):
        gh = FakeGitHub()
        pending = {"number": 123, "head": HEAD, "base": BASE,
                   "hashes": {p: digest(gh.files[p]) for p in ALLOWED_FILES}}
        gh.api = Mock(side_effect=[{"merged": False}, {"merged": True, "merge_commit_sha": MERGED}])
        sleeper = Mock()
        raw = lambda url: gh.files[MODULE if url.endswith("sgmodule") else README]
        self.assertEqual(sync.verify_published(gh, pending, raw, sleeper), MERGED)
        sleeper.assert_called_once_with(5)
        self.assertFalse(gh.created or gh.merged)

    def test_known_and_unknown_errors_never_emit_secret(self):
        for error in (RuntimeError("https://private.example?token=" + SECRET),
                      HostsError("http_rejected"), UnicodeError(SECRET)):
            out, err = io.StringIO(), io.StringIO()
            with patch.object(sync, "run", side_effect=error), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = sync.main(["--check", "--state-dir", str(ROOT.parent / "unused-state")])
            self.assertEqual(code, 1)
            self.assertNotIn(SECRET, out.getvalue() + err.getvalue())
            self.assertNotIn("https://private", out.getvalue() + err.getvalue())

    def test_github_stderr_is_captured_and_sanitized(self):
        gh = sync.GitHub()
        completed = subprocess.CompletedProcess([], 1, "", SECRET)
        with patch.object(sync.subprocess, "run", return_value=completed) as command:
            with self.assertRaisesRegex(HostsError, "github_request_failed"):
                gh.api("pulls")
            self.assertTrue(command.call_args.kwargs["capture_output"])

    def test_merge_uses_validated_tree_nonforced_atomic_forward(self):
        gh = sync.GitHub()
        gh.review = Mock()
        gh.head = Mock(side_effect=[BASE, MERGED])
        gh.api = Mock(side_effect=[{"parents": [{"sha": BASE}], "tree": {"sha": "tree"}},
                                   {"sha": MERGED}, {}])
        pending = {"base": BASE, "head": HEAD, "number": 1}
        self.assertEqual(gh.merge(pending), MERGED)
        self.assertEqual(gh.api.call_args_list[-1].args[1], {"sha": MERGED, "force": False})
        gh.head = Mock(return_value="d" * 40)
        gh.api = Mock(side_effect=[{"parents": [{"sha": BASE}], "tree": {"sha": "tree"}}, {"sha": MERGED}])
        with self.assertRaisesRegex(HostsError, "main_advanced"): gh.merge(pending)
        self.assertEqual(gh.api.call_count, 2)  # Never advances main.

    def test_ci_requires_correct_workflow_and_success(self):
        for conclusion in ("failure", "cancelled", "skipped"):
            gh = sync.GitHub()
            gh.review = Mock()
            gh.api = Mock(return_value={"workflow_runs": [{"id": 1, "path": sync.WORKFLOW,
                "head_sha": HEAD, "status": "completed", "conclusion": conclusion}]})
            with self.assertRaisesRegex(HostsError, "ci_failed"):
                gh.wait_ci({"head": HEAD}, sleeper=Mock(), attempts=1)
        gh.api = Mock(return_value={"workflow_runs": []})
        with self.assertRaisesRegex(HostsError, "ci_pending"):
            gh.wait_ci({"head": HEAD}, sleeper=Mock(), attempts=1)

    def test_review_checks_scope_head_base_and_immutable_file_hashes(self):
        files = public_files()
        pending = {"number": 1, "head": HEAD, "base": BASE,
                   "hashes": {p: digest(files[p]) for p in ALLOWED_FILES}}
        pr = {"state": "open", "draft": False, "commits": 1,
              "head": {"sha": HEAD, "repo": {"full_name": sync.REPOSITORY}},
              "base": {"ref": "main", "sha": BASE}}
        changed = [{"filename": p, "status": "modified"} for p in ALLOWED_FILES]
        gh = sync.GitHub()
        gh.head = Mock(return_value=BASE)
        gh.file = Mock(side_effect=lambda path, sha: files[path])
        gh.api = Mock(side_effect=[pr, changed])
        gh.review(pending)
        for mutation in (changed + [{"filename": "config/base.conf", "status": "modified"}], changed[:1]):
            gh.api = Mock(side_effect=[pr, mutation])
            with self.assertRaisesRegex(HostsError, "publication_scope"): gh.review(pending)
        gh.api = Mock(side_effect=[dict(pr, commits=2)])
        with self.assertRaisesRegex(HostsError, "pr_identity_changed"): gh.review(pending)
        gh.api = Mock(side_effect=[pr, changed])
        gh.file = Mock(return_value=b"tampered")
        with self.assertRaisesRegex(HostsError, "candidate_changed"): gh.review(pending)

    def test_hosts_ci_has_no_private_subscription_access(self):
        dedicated = (ROOT / sync.WORKFLOW).read_text(encoding='utf-8')
        self.assertIn('python -B -S scripts/validate_hosts.py', dedicated)
        self.assertNotIn('scripts/sync_hosts.py --publish', dedicated)
        self.assertNotIn('secrets.', dedicated)


if __name__ == "__main__":
    unittest.main()

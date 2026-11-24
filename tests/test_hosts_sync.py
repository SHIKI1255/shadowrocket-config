"""Failure-path tests for local-only iCloud Hosts sync using fabricated subscriptions."""
from datetime import datetime, timezone
import importlib.util
import json
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
from hosts_core import HostsError, digest, module_timestamp, parse_module, render_module
from test_hosts_core import hosts

HAS_YAML = importlib.util.find_spec("yaml") is not None
SECRET = "CREDENTIAL_CANARY_DO_NOT_PUBLISH"
TEST_UID = "SyntheticProfile01"
TEST_HOST = "api.provider.example"
TEST_PATH = "/api/v1/client/subscribe"


def body(entries=None, secret=SECRET):
    entries = hosts() if entries is None else entries
    return json.dumps({
        "hosts": entries,
        "proxies": [{"server": next(iter(entries), "a.example.com"),
                     "password": secret, "name": secret}],
        "subscription_url": secret,
    }).encode()


def make_env(temp: Path):
    target_dir = temp / "icloud_modules"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "Local-Hosts.sgmodule"
    config_path = temp / "config.json"
    state_dir = temp / "state"
    cfg = {
        "profile_uid": TEST_UID,
        "source_host": TEST_HOST,
        "source_path": TEST_PATH,
        "module_name": "Synthetic Hosts",
        "module_desc": "本地测试模块",
        "target_path": str(target),
    }
    config_path.write_text(json.dumps(cfg), encoding="utf-8")
    return config_path, state_dir, target, cfg


@unittest.skipUnless(HAS_YAML, "Local sync requires PyYAML; dedicated Hosts CI installs the pinned version")
class LocalSyncTests(unittest.TestCase):
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

    def test_source_identity_must_be_unique_and_https_origin_exact_on_both_platforms(self):
        profile = {"uid": TEST_UID, "type": "remote",
                   "url": f"https://{TEST_HOST}{TEST_PATH}?token={SECRET}"}
        cfg = {"profile_uid": TEST_UID, "source_host": TEST_HOST, "source_path": TEST_PATH}
        platforms = (
            ("win32", "AppData/Roaming/io.github.clash-verge-rev.clash-verge-rev/profiles.yaml"),
            ("darwin", "Library/Application Support/io.github.clash-verge-rev.clash-verge-rev/profiles.yaml"),
        )
        for platform_name, rel in platforms:
            with self.subTest(platform=platform_name), tempfile.TemporaryDirectory() as temp, \
                 patch.object(sync.Path, "home", return_value=Path(temp)), \
                 patch.object(sync.sys, "platform", platform_name):
                path = Path(temp) / rel
                path.parent.mkdir(parents=True)
                for rows in ([profile], [profile, profile], [],
                             [dict(profile, url="https://other.example/?token=x")]):
                    path.write_text(json.dumps({"items": rows}), encoding="utf-8")
                    if rows == [profile]:
                        self.assertEqual(sync.source_url(cfg), profile["url"])
                    else:
                        with self.assertRaises(HostsError):
                            sync.source_url(cfg)

    def test_missing_baseline_requires_explicit_init(self):
        with tempfile.TemporaryDirectory() as temp:
            cfg_path, state_dir, target, _ = make_env(Path(temp))
            with patch.object(sync, "source_url", return_value="private"):
                with self.assertRaisesRegex(HostsError, "baseline_missing"):
                    sync.run(cfg_path, state_dir, sync=True, init=False,
                             sleeper=Mock(), downloader=Mock(return_value=body()))
            self.assertFalse(target.exists())

    def test_init_sync_unchanged_and_backup_update_lifecycle(self):
        with tempfile.TemporaryDirectory() as temp:
            cfg_path, state_dir, target, cfg = make_env(Path(temp))
            mod_kw = {"module_name": cfg["module_name"], "module_desc": cfg["module_desc"]}
            sleeper = Mock()
            t0 = datetime(2026, 11, 24, 1, 0, 0, tzinfo=timezone.utc)
            with patch.object(sync, "source_url", return_value="private"):
                # --check with --init does not write target
                chk = sync.run(cfg_path, state_dir, sync=False, init=True,
                               sleeper=sleeper, downloader=Mock(return_value=body()), now=t0)
                self.assertEqual(chk["status"], "candidate")
                self.assertFalse(target.exists())
                # --sync with --init creates initial local module without #!url
                res = sync.run(cfg_path, state_dir, sync=True, init=True,
                               sleeper=sleeper, downloader=Mock(return_value=body()), now=t0)
                self.assertEqual(res["status"], "synced")
                self.assertIsNone(res["backup"])
                self.assertTrue(target.is_file())
                raw = target.read_bytes()
                self.assertNotIn(b"#!url", raw)
                self.assertNotIn(SECRET.encode(), raw)
                self.assertEqual(parse_module(raw, **mod_kw), hosts())
                self.assertEqual(module_timestamp(raw, **mod_kw), "2026-11-24 09:00:00 +08:00")
                # Unchanged order/node-metadata does not rewrite target or sleep
                fetch_same = Mock(return_value=body(dict(reversed(list(hosts().items()))), secret="other"))
                sleeper.reset_mock()
                mtime_before = target.stat().st_mtime_ns
                unchanged = sync.run(cfg_path, state_dir, sync=True, sleeper=sleeper,
                                     downloader=fetch_same, now=t0)
                self.assertEqual(unchanged["status"], "unchanged")
                self.assertEqual(fetch_same.call_count, 1)
                sleeper.assert_not_called()
                self.assertEqual(target.stat().st_mtime_ns, mtime_before)
                # Real IP update creates backup and writes atomically
                updated = dict(hosts())
                updated["entry0.example.com"] = "9.9.9.9"
                t1 = datetime(2026, 11, 25, 1, 0, 5, tzinfo=timezone.utc)
                res2 = sync.run(cfg_path, state_dir, sync=True, sleeper=sleeper,
                                downloader=Mock(return_value=body(updated)), now=t1)
                self.assertEqual(res2["status"], "synced")
                self.assertIsNotNone(res2["backup"])
                self.assertTrue((state_dir / "backups" / res2["backup"]).is_file())
                self.assertEqual(parse_module(target.read_bytes(), **mod_kw), updated)
                self.assertEqual(len(res2["diff"]["changed"]), 1)
                self.assertNotIn(SECRET, json.dumps(res2))

    def test_source_changes_between_reads_and_quantity_gate_stop(self):
        with tempfile.TemporaryDirectory() as temp:
            cfg_path, state_dir, target, cfg = make_env(Path(temp))
            mod_kw = {"module_name": cfg["module_name"], "module_desc": cfg["module_desc"]}
            initial = render_module(hosts(), **mod_kw)
            target.write_bytes(initial)
            with patch.object(sync, "source_url", return_value="private"):
                for responses, expected in (
                    ([body(hosts(15)), body(hosts(14))], "source_changed_between_reads"),
                    ([body(hosts(8))] * 2, "quantity_gate"),
                    ([body(hosts(32))] * 2, "quantity_gate"),
                ):
                    with self.assertRaisesRegex(HostsError, expected):
                        sync.run(cfg_path, state_dir, sync=True, sleeper=Mock(),
                                 downloader=Mock(side_effect=responses))
            self.assertEqual(target.read_bytes(), initial)

    def test_icloud_placeholder_and_remote_url_module_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            cfg_path, state_dir, target, cfg = make_env(Path(temp))
            placeholder = target.parent / f".{target.name}.icloud"
            placeholder.write_bytes(b"stub")
            with self.assertRaisesRegex(HostsError, "icloud_placeholder_present"):
                sync.run(cfg_path, state_dir, sync=True, init=True,
                         sleeper=Mock(), downloader=Mock(return_value=body()))
            placeholder.unlink()
            mod_kw = {"module_name": cfg["module_name"], "module_desc": cfg["module_desc"]}
            target.write_bytes(b"#!url=https://example.invalid/old.sgmodule\n"
                               + render_module(hosts(), **mod_kw))
            with self.assertRaisesRegex(HostsError, "remote_url_module_present"):
                sync.run(cfg_path, state_dir, sync=True,
                         sleeper=Mock(), downloader=Mock(return_value=body()))

    def test_config_and_target_cannot_live_inside_repo_or_be_symlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            cfg_path, state_dir, target, _ = make_env(Path(temp))
            with self.assertRaisesRegex(HostsError, "config_location"):
                sync.load_config(ROOT / "README.md")
            real = Path(temp) / "real.sgmodule"
            real.write_text("x", encoding="utf-8")
            target.symlink_to(real)
            with self.assertRaisesRegex(HostsError, "target_location"):
                sync.load_config(cfg_path)


class TransportAndIsolationTests(unittest.TestCase):
    def test_hosts_core_tests_run_without_site_packages(self):
        result = subprocess.run(
            [sys.executable, "-B", "-S", "-m", "unittest", "discover", "-s",
             str(ROOT / "tests"), "-p", "test_hosts_core.py"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_bounded_retry_auth_redirect_size(self):
        sleeper = Mock()
        opener = MagicMock()
        opener.open.side_effect = urllib.error.URLError(SECRET)
        with self.assertRaisesRegex(HostsError, "download_exhausted"):
            sync.download("https://example.com", opener=opener, sleeper=sleeper)
        self.assertEqual(opener.open.call_count, 3)
        opener.open.reset_mock()
        http_err = urllib.error.HTTPError("private", 403, SECRET, {}, None)
        self.addCleanup(http_err.close)
        opener.open.side_effect = http_err
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
                    with sync.runtime_lock(Path(temp)):
                        pass
            with sync.runtime_lock(Path(temp)):
                pass

    def test_backup_prunes_beyond_retention(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            target = base / "Local-Hosts.sgmodule"
            target.write_bytes(b"v0")
            state = base / "state"
            state.mkdir()
            for i in range(sync.MAX_BACKUPS + 3):
                sync.backup_existing(state, target, datetime(2026, 11, 1, 0, i, 0, tzinfo=timezone.utc))
            self.assertEqual(len(list((state / "backups").glob("*.sgmodule"))), sync.MAX_BACKUPS)

    def test_hosts_workflow_never_invokes_sync_hosts_sync(self):
        workflow = (ROOT / ".github/workflows/hosts.yml").read_text(encoding="utf-8")
        self.assertIn("python -B -S -m unittest discover -s tests -p test_hosts_core.py -v", workflow)
        self.assertNotIn("scripts/sync_hosts.py --sync", workflow)


if __name__ == "__main__":
    unittest.main()

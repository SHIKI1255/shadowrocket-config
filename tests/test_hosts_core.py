"""Standard-library-only validation for local Shadowrocket Hosts modules."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from hosts_core import (DEFAULT_MODULE_DESC, DEFAULT_MODULE_NAME, TIME_MARKER, HostsError,
                        check_quantity, differences, format_timestamp, module_timestamp,
                        normalize_hosts, ordered_hosts, parse_module, render_module)


def hosts(count=16):
    return {f"entry{i}.example.com": f"8.8.4.{i+1}" for i in range(count)}


class HostsCoreTests(unittest.TestCase):
    def test_no_public_provider_module_exists_in_repo(self):
        modules_dir = ROOT / "modules"
        self.assertEqual(sorted(p.name for p in modules_dir.iterdir()), ["README.md"])

    def test_legacy_and_timestamp_headers_preserve_mapping(self):
        legacy = render_module(hosts())
        self.assertIsNone(module_timestamp(legacy))
        self.assertNotIn(b"#!url", legacy)
        stamped = render_module(hosts(), "2026-10-18 09:00:05 +08:00")
        self.assertEqual(module_timestamp(stamped), "2026-10-18 09:00:05 +08:00")
        self.assertEqual(parse_module(stamped), parse_module(legacy))
        self.assertEqual(stamped.decode().count(TIME_MARKER), 1)
        self.assertEqual(parse_module(stamped.replace(b"\n", b"\r\n")), hosts())

    def test_remote_url_directive_is_rejected(self):
        valid = render_module(hosts(2))
        with_url = b"#!url=https://example.invalid/module.sgmodule\n" + valid
        with self.assertRaisesRegex(HostsError, "remote_url_module_present"):
            parse_module(with_url)

    def test_custom_module_name_and_desc_roundtrip(self):
        kw = {"module_name": "Custom Local", "module_desc": "本地测试描述"}
        rendered = render_module(hosts(3), "2026-11-24 09:00:00 +08:00", **kw)
        self.assertEqual(parse_module(rendered, **kw), hosts(3))
        self.assertEqual(module_timestamp(rendered, **kw), "2026-11-24 09:00:00 +08:00")
        with self.assertRaisesRegex(HostsError, "module_metadata"):
            parse_module(rendered)

    def test_invalid_duplicate_and_extra_timestamps_are_rejected(self):
        stamp = "2026-10-18 09:00:05 +08:00"
        valid = render_module(hosts(2), stamp)
        for invalid in ("2026-02-30 09:00:05 +08:00", "2026-10-18 24:00:00 +08:00",
                        "2026-10-18 9:00:05 +08:00", "2026-10-18 09:00:05 +00:00",
                        "2026-10-18", stamp + " extra", stamp + TIME_MARKER + stamp):
            with self.subTest(invalid=invalid), self.assertRaises(HostsError):
                parse_module(valid.replace(stamp.encode(), invalid.encode()))
        for extra in ("\n#!desc=duplicate\n", "\n#" + TIME_MARKER + stamp + "\n"):
            with self.assertRaises(HostsError):
                parse_module(valid + extra.encode())

    def test_timestamp_uses_beijing_across_date_and_timezone_boundaries(self):
        self.assertEqual(format_timestamp(datetime(2026, 12, 31, 16, 0, 1, tzinfo=timezone.utc)),
                         "2027-01-01 00:00:01 +08:00")
        self.assertEqual(format_timestamp(datetime(2026, 10, 18, 23, 45,
                         tzinfo=timezone(timedelta(hours=-7)))), "2026-10-19 14:45:00 +08:00")
        with self.assertRaisesRegex(HostsError, "timestamp_timezone"):
            format_timestamp(datetime(2026, 10, 18))

    def test_full_replacement_preserves_existing_order(self):
        old = hosts(4)
        new = {"new.example.com": "1.1.1.1", **hosts(3)}
        new["entry1.example.com"] = "9.9.9.9"
        ordered = ordered_hosts(old, new)
        self.assertEqual(list(ordered), list(hosts(3)) + ["new.example.com"])
        self.assertEqual(parse_module(render_module(ordered)), new)
        self.assertNotIn("entry3.example.com", ordered)
        diff = differences(old, ordered)
        self.assertEqual(len(diff["added"]), 1)
        self.assertEqual(len(diff["removed"]), 1)
        self.assertEqual(len(diff["changed"]), 1)

    def test_normalization_and_canonical_duplicate(self):
        self.assertEqual(normalize_hosts({"A.Example.COM.": "1.1.1.1"}), {"a.example.com": "1.1.1.1"})
        with self.assertRaises(HostsError):
            normalize_hosts({"a.example.com": "1.1.1.1", "A.EXAMPLE.COM.": "9.9.9.9"})

    def test_illegal_values_and_private_destinations_fail(self):
        bad = [{}, [], {"a.example.com": ["1.1.1.1"]}, {"*.example.com": "1.1.1.1"},
               {"a.example.com": "other.example.com"}, {"a.example.com": "127.0.0.1"},
               {"a.example.com": "192.168.1.1"}, {"a.example.com": "224.0.0.1"},
               {"a.example.com": "1.1.1.1,9.9.9.9"}, {"a.example.com": True},
               {"a.example.com": "fe80::1%eth0"}, {"a.example.com\n[MITM]": "1.1.1.1"}]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(HostsError):
                normalize_hosts(value)

    def test_quantity_boundaries_not_ip_change_percentage(self):
        for size in (9, 16, 31):
            check_quantity(hosts(), hosts(size))
        check_quantity(hosts(), {k: "1.1.1.1" for k in hosts()})
        for size in (0, 8, 32):
            with self.subTest(size=size), self.assertRaisesRegex(HostsError, "quantity_gate"):
                check_quantity(hosts(), hosts(size))
        for size in (9, 17, 33):
            check_quantity(hosts(17), hosts(size))
        for size in (8, 34):
            with self.subTest(size=size), self.assertRaisesRegex(HostsError, "quantity_gate"):
                check_quantity(hosts(17), hosts(size))

    def test_module_rejects_extra_sections_metadata_and_duplicates(self):
        valid = render_module(hosts(2))
        for extra in (b"[Host]\n", b"[DNS]\n", b"[MITM]\n", b"[Rule]\n", b"[Proxy]\n",
                      b"#!script=anything\n", b"entry0.example.com = 1.1.1.1\n",
                      b"ENTRY0.EXAMPLE.COM = 1.1.1.1\n"):
            with self.subTest(extra=extra), self.assertRaises(HostsError):
                parse_module(valid + extra)
        header = f"#!name={DEFAULT_MODULE_NAME}\n#!desc={DEFAULT_MODULE_DESC}\n"
        for value in (valid.replace(b"[Host]", b"[Hosts]"), valid[3:], b"\xff" + valid,
                      (header + "entry.example.com = 1.1.1.1\n[Host]\n").encode()):
            with self.assertRaises(HostsError):
                parse_module(value)


if __name__ == "__main__":
    unittest.main()

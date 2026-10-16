"""Public module validation and lossless complete-set replacement."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from hosts_core import (BEGIN, END, HEADER, MODULE, README, HostsError, check_quantity,
                        normalize_hosts, ordered_hosts, parse_module, render_module,
                        update_readme, validate_readme)


def hosts(count=16):
    return {f"entry{i}.example.com": f"8.8.4.{i+1}" for i in range(count)}


class HostsCoreTests(unittest.TestCase):
    def test_current_module_and_documentation(self):
        current = parse_module((ROOT / MODULE).read_bytes())
        validate_readme((ROOT / README).read_text(encoding="utf-8"), current)

    def test_full_replacement_preserves_existing_order(self):
        old = hosts(4)
        new = {"new.example.com": "1.1.1.1", **hosts(3)}
        new["entry1.example.com"] = "9.9.9.9"
        ordered = ordered_hosts(old, new)
        self.assertEqual(list(ordered), list(hosts(3)) + ["new.example.com"])
        self.assertEqual(parse_module(render_module(ordered)), new)
        self.assertNotIn("entry3.example.com", ordered)

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

    def test_module_rejects_extra_sections_metadata_and_duplicates(self):
        valid = render_module(hosts(2))
        for extra in (b"[Host]\n", b"[DNS]\n", b"[MITM]\n", b"[Rule]\n", b"[Proxy]\n",
                      b"#!script=anything\n", b"entry0.example.com = 1.1.1.1\n",
                      b"ENTRY0.EXAMPLE.COM = 1.1.1.1\n"):
            with self.subTest(extra=extra), self.assertRaises(HostsError):
                parse_module(valid + extra)
        for value in (valid.replace(b"[Host]", b"[Hosts]"), valid[3:], b"\xff" + valid,
                      (HEADER + "entry.example.com = 1.1.1.1\n[Host]\n").encode()):
            with self.assertRaises(HostsError):
                parse_module(value)

    def test_readme_is_limited_to_generated_snapshot_and_matches_module(self):
        template = "Before\n" + BEGIN + "\nold\n" + END + "\nAfter\n"
        current = hosts(2)
        result = update_readme(template, current, dict.fromkeys(current, 1), 2,
                               "2026-10-16", "a" * 40)
        self.assertTrue(result.startswith("Before\n") and result.endswith("\nAfter\n"))
        validate_readme(result, current)
        self.assertIn("2026-10-16", result)
        self.assertIn("待手机实测", result)
        with self.assertRaises(HostsError):
            validate_readme(result, hosts(3))
        with self.assertRaises(HostsError):
            update_readme(template + BEGIN, current, {}, 0, "2026-10-16", "a" * 40)


if __name__ == "__main__":
    unittest.main()

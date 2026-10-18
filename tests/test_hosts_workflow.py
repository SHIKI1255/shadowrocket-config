"""Exercise the real push/PR filters without adding a builder YAML dependency.

This reader deliberately supports only the quoted lists used in these workflows;
an unfamiliar syntax fails the test instead of silently guessing GitHub behavior.
GitHub runs on the real PR remain the integration check for event semantics.
"""
import ast
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
UPDATE = ".github/workflows/update.yml"
HOSTS = ".github/workflows/hosts.yml"


def workflow_runs(text, event, changed):
    on = re.search(r"^on:\n(.*?)(?=^\S|\Z)", text, re.MULTILINE | re.DOTALL)
    if on is None: raise ValueError("unsupported workflow triggers")
    block = re.search(r"^  " + re.escape(event) + r":\n(.*?)(?=^  \S|\Z)",
                      on[1], re.MULTILINE | re.DOTALL)
    if block is None: raise ValueError("missing event")
    filters = re.findall(r"^    (paths|paths-ignore):\n((?:      - '[^'\n]+'\n)+)",
                         block[1], re.MULTILINE)
    if len(filters) != 1: raise ValueError("unsupported path filters")
    mode, lines = filters[0]
    patterns = [ast.literal_eval(line.strip()[2:]) for line in lines.splitlines()]
    for pattern in patterns:
        if not re.fullmatch(r"[A-Za-z0-9_./*\-]+", pattern):
            raise ValueError("unsupported path pattern")
    def matches(path, pattern):
        expression = re.escape(pattern).replace(r"\*\*", ".*").replace(r"\*", "[^/]*")
        return re.fullmatch(expression, path) is not None
    matched = [any(matches(path, pattern) for pattern in patterns) for path in changed]
    return bool(changed) and (any(matched) if mode == "paths" else not all(matched))


class WorkflowTriggerTests(unittest.TestCase):
    def setUp(self):
        self.update = (ROOT / UPDATE).read_text(encoding="utf-8")
        self.hosts = (ROOT / HOSTS).read_text(encoding="utf-8")

    def assert_trigger_matrix(self, update, hosts):
        cases = [
            ([UPDATE], (True, True)),
            ([UPDATE, "tests/test_hosts_workflow.py"], (True, True)),
            (["modules/GeekSpeed-Hosts.sgmodule"], (False, True)),
            (["modules/README.md"], (False, True)),
            (["modules/GeekSpeed-Hosts.sgmodule", "modules/README.md"], (False, True)),
            (["scripts/sync_hosts.py", "tests/test_hosts_sync.py"], (False, True)),
            ([HOSTS], (False, True)),
            (["scripts/build.py"], (True, False)),
            (["scripts/publish.py"], (True, False)),
            (["shared/custom.list"], (True, False)),
            (["config/base.conf"], (True, False)),
            (["modules/README.md", "shared/custom.list"], (True, True)),
        ]
        for event in ("push", "pull_request"):
            for paths, expected in cases:
                actual = (workflow_runs(update, event, paths), workflow_runs(hosts, event, paths))
                self.assertEqual(actual, expected, (event, paths))
        # Keepalive writes are pushes, not PRs. Preserve the existing PR filter.
        self.assertFalse(workflow_runs(update, "push", [".github/maintenance.json"]))
        self.assertFalse(workflow_runs(hosts, "push", [".github/maintenance.json"]))
        self.assertTrue(workflow_runs(update, "pull_request", [".github/maintenance.json"]))

    def test_real_workflow_trigger_matrix(self):
        self.assert_trigger_matrix(self.update, self.hosts)

    def test_self_ignore_regression_is_detected_for_each_event(self):
        for event in ("push", "pull_request"):
            with self.subTest(event=event):
                start = "  " + event + ":\n    branches: [main]\n    paths-ignore:\n"
                self.assertIn(start, self.update)
                mutant = self.update.replace(start, start + "      - '" + UPDATE + "'\n", 1)
                with self.assertRaises(AssertionError):
                    self.assert_trigger_matrix(mutant, self.hosts)

    def test_filter_reader_rejects_unknown_syntax_and_honors_slashes(self):
        simple = "on:\n  push:\n    paths:\n      - 'tests/test_hosts*.py'\n"
        self.assertTrue(workflow_runs(simple, "push", ["tests/test_hosts_core.py"]))
        self.assertFalse(workflow_runs(simple, "push", ["tests/test_hosts/inner.py"]))
        for text in (simple.replace("'tests/test_hosts*.py'", "unquoted"),
                     simple.replace("test_hosts*", "!test_hosts*")):
            with self.assertRaises(ValueError): workflow_runs(text, "push", [UPDATE])


if __name__ == "__main__":
    unittest.main()

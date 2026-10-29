"""Health monitoring uses successful activity and content, never release age alone."""
import copy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"scripts"))
import check_health as h

NOW = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
A, B, C, D = (c*40 for c in "abcd")
HASH, OTHER = "e"*64, "f"*64


def observations():
    return {repo: {"release": A, "consumed_release": A, "shared_version": HASH,
                   "bundle_sha256": HASH, "workflow_state": "active",
                   "last_verified": {"at": h.stamp(NOW), "run_id": 1},
                   "sync_status": "current"} for repo in h.REPOSITORIES}


def report(rows=None, errors=None):
    rows, errors = rows or observations(), errors or {}
    return {"schema_version": 1, "checked_at": h.stamp(NOW), "limits": h.LIMITS,
            "source_commit": A, "run_id": "123", "observations": rows, "errors": errors,
            "problems": h.assess(rows, errors, NOW)}


def commit(sha, parent, hours):
    return {"sha": sha, "parents": [{"sha": parent}],
            "commit": {"committer": {"date": h.stamp(NOW-timedelta(hours=hours))}}}


def run():
    return {"event": "schedule", "head_branch": "main", "status": "completed",
            "id": 42, "created_at": h.stamp(NOW-timedelta(minutes=5))}


class AssessmentTests(unittest.TestCase):
    def test_age_boundaries_and_one_second_over(self):
        for repo, hours in zip(h.REPOSITORIES, (36, 24, 24)):
            rows = observations()
            rows[repo]["last_verified"]["at"] = h.stamp(NOW-timedelta(hours=hours))
            self.assertEqual(h.assess(rows, {}, NOW), [])
            self.assertEqual(h.assess(rows, {}, NOW+timedelta(seconds=1)),
                             [{"repository": repo, "kind": "activity_overdue"}])

    def test_content_lag_boundary_and_metadata_only_never_lag(self):
        rows = observations()
        qx = rows[h.REPOSITORIES[1]]
        qx.update(sync_status="content_pending", lag_since=h.stamp(NOW-timedelta(hours=24)))
        self.assertEqual(h.assess(rows, {}, NOW), [])
        self.assertEqual(h.assess(rows, {}, NOW+timedelta(seconds=1))[0]["kind"], "content_lag")
        qx.update(sync_status="metadata_pending", shared_version=OTHER)
        self.assertEqual(h.assess(rows, {}, NOW+timedelta(seconds=1)), [])

    def test_disabled_missing_activity_and_unknown_are_not_healthy(self):
        rows = observations()
        rows[h.PRODUCER]["workflow_state"] = "disabled_inactivity"
        rows[h.REPOSITORIES[1]]["last_verified"] = None
        result = h.assess(rows, {h.REPOSITORIES[2]: "unavailable"}, NOW)
        self.assertEqual([p["kind"] for p in result],
                         ["workflow_disabled", "activity_overdue", "inspection_unavailable"])

    def test_successful_no_change_probe_counts_even_if_other_jobs_skipped(self):
        jobs = [{"name": "probe", "conclusion": "success", "completed_at": h.stamp(NOW)},
                {"name": "validate", "conclusion": "skipped"},
                {"name": "publish", "conclusion": "skipped"}]
        for repo in h.REPOSITORIES[1:]:
            self.assertEqual(h.qualifying_time(repo, run(), jobs), h.stamp(NOW))
            for change in ({"event": "pull_request"}, {"head_branch": "test"}, {"status": "in_progress"}):
                self.assertIsNone(h.qualifying_time(repo, {**run(), **change}, jobs))
        jobs[0]["conclusion"] = "failure"
        self.assertIsNone(h.qualifying_time(h.REPOSITORIES[1], run(), jobs))

    def test_producer_requires_full_success_and_download_readback(self):
        jobs = [{"name": "validate", "conclusion": "success"},
                {"name": "publish", "conclusion": "success", "completed_at": h.stamp(NOW),
                 "steps": [{"name": h.READBACK, "conclusion": "success"}]}]
        self.assertEqual(h.qualifying_time(h.PRODUCER, run(), jobs), h.stamp(NOW))
        for field, value in (("steps", []), ("conclusion", "skipped")):
            changed = copy.deepcopy(jobs); changed[1][field] = value
            self.assertIsNone(h.qualifying_time(h.PRODUCER, run(), changed))
        self.assertIsNone(h.qualifying_time(h.PRODUCER, run(), jobs[:1]))
        with self.assertRaises(ValueError):
            h.qualifying_time(h.PRODUCER, run(), jobs+jobs)

    def test_activity_api_job_inventory_and_timestamp_checks(self):
        jobs = [{"name": "probe", "conclusion": "success", "completed_at": h.stamp(NOW)}]
        with patch.object(h, "read_api", side_effect=[{"workflow_runs": [run()]}, {"total_count": 1, "jobs": jobs}]):
            self.assertEqual(h.last_verified(h.REPOSITORIES[1], NOW)["run_id"], 42)
        for response in ({"total_count": 2, "jobs": jobs},
                         {"total_count": 1, "jobs": [{**jobs[0], "completed_at": h.stamp(NOW+timedelta(seconds=1))}]}):
            with patch.object(h, "read_api", side_effect=[{"workflow_runs": [run()]}, response]), self.assertRaises(ValueError):
                h.last_verified(h.REPOSITORIES[1], NOW)
        with patch.object(h, "read_api", return_value={"workflow_runs": []}):
            self.assertIsNone(h.last_verified(h.PRODUCER, NOW))

    def test_activity_pagination_is_bounded(self):
        # Non-main runs cannot manufacture successful activity.
        rows = [{**run(), "head_branch": "foreign"} for _ in range(100)]
        with patch.object(h, "read_api", return_value={"workflow_runs": rows}) as api, self.assertRaises(h.HealthError):
            h.last_verified(h.PRODUCER, NOW)
        self.assertEqual(api.call_count, 3)

    def test_collect_metadata_only_without_history_or_writes(self):
        values = observations()
        values[h.REPOSITORIES[1]]["shared_version"] = OTHER
        values[h.REPOSITORIES[2]]["shared_version"] = OTHER
        with patch.object(h, "read_api", return_value={"path": ".github/workflows/update.yml", "state": "active"}), \
             patch.object(h, "release_ref", return_value=A), \
             patch.object(h, "release_data", side_effect=lambda repo, _: values[repo]), \
             patch.object(h, "last_verified", return_value={"at": h.stamp(NOW), "run_id": 1}), \
             patch.object(h, "lag_since") as history, patch.object(h, "issue_api") as write:
            result = h.collect(NOW)
        self.assertEqual(result["problems"], [])
        self.assertEqual(result["observations"][h.REPOSITORIES[1]]["sync_status"], "metadata_pending")
        history.assert_not_called(); write.assert_not_called()

    def test_collect_actual_difference_and_unknown_history(self):
        values = observations(); values[h.REPOSITORIES[1]].update(shared_version=OTHER, bundle_sha256=OTHER)
        with patch.object(h, "read_api", return_value={"path": ".github/workflows/update.yml", "state": "active"}), \
             patch.object(h, "release_ref", return_value=A), \
             patch.object(h, "release_data", side_effect=lambda repo, _: values[repo]), \
             patch.object(h, "last_verified", return_value={"at": h.stamp(NOW), "run_id": 1}), \
             patch.object(h, "lag_since", return_value=h.stamp(NOW-timedelta(hours=25))) as lag:
            self.assertEqual(h.collect(NOW)["problems"][0]["kind"], "content_lag")
            lag.side_effect = ValueError()
            self.assertEqual(h.collect(NOW)["problems"][0]["kind"], "inspection_unavailable")

    def test_producer_unavailable_never_claims_consumers_synced(self):
        with patch.object(h, "read_api", side_effect=OSError()):
            result = h.collect(NOW)
        self.assertEqual(len(result["problems"]), 3)
        self.assertTrue(all(p["kind"] == "inspection_unavailable" for p in result["problems"]))


class HistoryTests(unittest.TestCase):
    def test_subsequent_changes_do_not_reset_lag_clock(self):
        commits = [commit(B, A, 30), commit(C, B, 20), commit(D, C, 1)]
        values = {B: HASH, C: OTHER, D: "1"*64}
        result = h.continuous_lag(commits, A, D, HASH, NOW, lambda s: {"bundle_sha256": values[s]})
        self.assertEqual(result, h.stamp(NOW-timedelta(hours=20)))

    def test_revert_resets_only_when_content_matches_consumer(self):
        commits = [commit(B, A, 30), commit(C, B, 20), commit(D, C, 1)]
        values = {B: OTHER, C: HASH, D: OTHER}
        self.assertEqual(h.continuous_lag(commits, A, D, HASH, NOW, lambda s: {"bundle_sha256": values[s]}),
                         h.stamp(NOW-timedelta(hours=1)))

    def test_unknown_history_never_becomes_zero_lag(self):
        normal = [commit(B, A, 30), commit(C, B, 20)]
        bad = [[], [commit(B, D, 1)], [commit(B, A, -1)],
               [commit(B, A, 1), commit(C, B, 2)], [commit(B, A, 1)]*97]
        for rows in bad:
            with self.subTest(rows=rows[:1]), self.assertRaises(ValueError):
                h.continuous_lag(rows, A, C, HASH, NOW, lambda _: {"bundle_sha256": OTHER})
        with self.assertRaises(ValueError):
            h.continuous_lag(normal, A, C, HASH, NOW, lambda _: {"bundle_sha256": HASH})

    def test_compare_requires_complete_ancestry_and_consumed_identity(self):
        consumer = {"consumed_release": A, "shared_version": HASH, "bundle_sha256": HASH}
        good = {"status": "ahead", "merge_base_commit": {"sha": A}, "total_commits": 1,
                "commits": [commit(B, A, 30)]}
        with patch.object(h, "manifest_at", return_value={"shared_version": HASH, "bundle_sha256": HASH}), \
             patch.object(h, "read_api", return_value=good), patch.object(h, "continuous_lag", return_value="verified"):
            self.assertEqual(h.lag_since(consumer, {"release": B}, NOW), "verified")
        for changes in ({"total_commits": 2}, {"status": "diverged"}, {"merge_base_commit": {"sha": C}}):
            with patch.object(h, "manifest_at", return_value={"shared_version": HASH, "bundle_sha256": HASH}), \
                 patch.object(h, "read_api", return_value={**good, **changes}), self.assertRaises(ValueError):
                h.lag_since(consumer, {"release": B}, NOW)
        with patch.object(h, "manifest_at", return_value={"shared_version": OTHER, "bundle_sha256": HASH}), self.assertRaises(ValueError):
            h.lag_since(consumer, {"release": B}, NOW)

    def test_public_compare_path_allowed_traversal_rejected_and_error_sanitized(self):
        with patch.object(h, "request_bytes", return_value=b"{}") as request:
            h.read_api(h.PRODUCER, "compare/"+A+"..."+B+"?per_page=100&page=1")
            request.assert_called_once()
        for repo, path in (("other/repo", "issues"), (h.PRODUCER, "../issues"),
                           (h.PRODUCER, "/issues"), (h.PRODUCER, "https://evil.test")):
            with self.assertRaises(ValueError): h.read_api(repo, path)
        with patch.object(h, "request_bytes", side_effect=OSError("sensitive response")), self.assertRaisesRegex(h.HealthError, "^public_api_unavailable$"):
            h.read_api(h.PRODUCER, "issues")
        for invalid in (b"[]", b"null", b"not json"):
            with patch.object(h, "request_bytes", return_value=invalid), self.assertRaises(h.HealthError):
                h.read_api(h.PRODUCER, "actions/workflows/update.yml")

    def test_release_data_verifies_bundle_and_lock(self):
        bundle = h.json_bytes({"schema_version": 1, "platforms": {"shadowrocket": {}, "quantumultx": {}, "clash": {}}, "categories": []})
        basis = {"schema_version": 1, "bundle_sha256": h.digest(bundle)}
        shared = {**basis, "shared_version": h.digest(h.json_bytes(basis))}
        client = {"schema_version": 2, "shared_version": shared["shared_version"]}
        lock = {"schema_version": 1, "shared_release": B, "shared_version": shared["shared_version"], "bundle_sha256": shared["bundle_sha256"]}
        def fetch(url):
            if url.endswith("shared/manifest.json"): return h.json_bytes(shared)
            if url.endswith("shared/bundle.json"): return bundle
            if url.endswith("shared-consumer.lock.json"): return h.json_bytes(lock)
            return h.json_bytes(client)
        with patch.object(h, "fetch", side_effect=fetch):
            self.assertEqual(h.release_data(h.REPOSITORIES[1], A)["consumed_release"], B)
            lock["bundle_sha256"] = OTHER
            with self.assertRaises(ValueError): h.release_data(h.REPOSITORIES[1], A)
            with self.assertRaises(ValueError): h.release_data(h.PRODUCER, "main")
        with patch.object(h, "fetch", return_value=b"malformed"), self.assertRaises(ValueError):
            h.release_data(h.PRODUCER, A)

    def test_historical_manifest_version_tampering_fails(self):
        basis = {"schema_version": 1, "bundle_sha256": HASH}
        good = {**basis, "shared_version": h.digest(h.json_bytes(basis))}
        with patch.object(h, "fetch", return_value=h.json_bytes(good)):
            self.assertEqual(h.manifest_at(A), good)
        good["bundle_sha256"] = OTHER
        with patch.object(h, "fetch", return_value=h.json_bytes(good)), self.assertRaises(ValueError):
            h.manifest_at(A)


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"GITHUB_RUN_ID": "123"})
        self.identity = patch.object(h, "identity", return_value=(A, "https://github.com/run/123"))
        self.refs = patch.object(h, "release_ref", return_value=A)
        self.env.start(); self.identity.start(); self.refs.start()
        self.addCleanup(patch.stopall)

    def stale(self):
        rows = observations(); rows[h.PRODUCER]["last_verified"] = None
        return report(rows)

    def test_no_changes_healthy_has_no_writes(self):
        with patch.object(h, "health_issues", return_value=[]), patch.object(h, "issue_api") as api:
            self.assertEqual(h.notify(report(), NOW)["status"], "healthy")
        api.assert_not_called()

    def test_same_reason_deduplicates_even_when_versions_and_age_advance(self):
        first = self.stale()
        body = h.MARKER+"\n<!-- fingerprint:"+h.digest(h.json_bytes(first["problems"]))+" -->"
        first["observations"][h.PRODUCER]["shared_version"] = OTHER
        with patch.object(h, "health_issues", return_value=[{"number": 7, "body": body}]), patch.object(h, "issue_api") as api:
            self.assertEqual(h.notify(first, NOW+timedelta(minutes=5))["status"], "deduplicated")
        api.assert_not_called()

    def test_open_and_changed_reason_update_with_readback(self):
        for existing, expected in (([], "opened"), ([{"number": 7, "body": h.MARKER}], "updated")):
            def api(path, payload, method):
                self.assertIn(method, {"POST", "PATCH"})
                return {**payload, "state": "open", "number": 7}
            with patch.object(h, "health_issues", return_value=existing), patch.object(h, "issue_api", side_effect=api) as call:
                self.assertEqual(h.notify(self.stale(), NOW)["status"], expected)
                self.assertEqual(call.call_count, 1)

    def test_recovery_closes_only_health_incident(self):
        with patch.object(h, "health_issues", return_value=[{"number": 7, "body": h.MARKER}]), \
             patch.object(h, "issue_api", return_value={"state": "closed"}) as api:
            self.assertEqual(h.notify(report(), NOW)["status"], "recovered")
            self.assertEqual(api.call_args.args[0], "issues/7")
        import release_health
        rows = [{"number": n, "body": body, "user": {"login": owner}} for n, body, owner in
                ((1, h.MARKER, "github-actions[bot]"), (2, release_health.MARKER, "github-actions[bot]"),
                 (3, h.MARKER, "human"))]
        with patch.object(h, "issue_api", return_value=rows):
            self.assertEqual([r["number"] for r in h.health_issues()], [1])
        with patch.object(release_health, "api", return_value=rows):
            self.assertEqual([r["number"] for r in release_health.incidents()], [2])

    def test_unavailable_evidence_does_not_close_old_incident(self):
        result = report(errors={h.PRODUCER: "unavailable"})
        def api(path, payload, method):
            self.assertNotIn("state", payload)
            return {**payload, "state": "open", "number": 7}
        with patch.object(h, "health_issues", return_value=[{"number": 7, "body": h.MARKER}]), patch.object(h, "issue_api", side_effect=api):
            self.assertEqual(h.notify(result, NOW)["status"], "updated")

    def test_stale_altered_or_wrong_run_report_cannot_write(self):
        variants = [{"source_commit": B}, {"run_id": "999"}, {"schema_version": 2},
                    {"checked_at": h.stamp(NOW-timedelta(minutes=31))},
                    {"checked_at": h.stamp(NOW+timedelta(seconds=1))},
                    {"problems": [{"repository": h.PRODUCER, "kind": "activity_overdue"}]},
                    {"observations": {}}, {"limits": {"producer_hours": 999}}]
        for change in variants:
            with self.subTest(change=change), patch.object(h, "issue_api") as api, self.assertRaises(ValueError):
                h.notify({**report(), **change}, NOW)
            api.assert_not_called()

    def test_publication_advanced_defers_without_closing_or_opening(self):
        with patch.object(h, "release_ref", return_value=B), patch.object(h, "issue_api") as api:
            self.assertEqual(h.notify(self.stale(), NOW)["status"], "deferred_release_advanced")
        api.assert_not_called()

    def test_multiple_incidents_and_failed_readback_stop(self):
        with patch.object(h, "health_issues", return_value=[{}, {}]), self.assertRaises(ValueError):
            h.notify(self.stale(), NOW)
        with patch.object(h, "health_issues", return_value=[]), patch.object(h, "issue_api", return_value={"body": "wrong", "state": "open"}), self.assertRaises(ValueError):
            h.notify(self.stale(), NOW)

    def test_pr_identity_cannot_notify(self):
        # Exercise the real guard; mocks must not allow an untrusted event to write.
        import release_health
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": h.PRODUCER, "GITHUB_REF": "refs/heads/main",
                                     "GITHUB_EVENT_NAME": "pull_request", "GITHUB_SHA": A}), \
             patch.object(h, "identity", side_effect=release_health.identity), patch.object(h, "issue_api") as api, self.assertRaises(ValueError):
            h.notify(self.stale(), NOW)
        api.assert_not_called()


class WorkflowTests(unittest.TestCase):
    def test_independent_workflow_schedule_and_minimal_permissions(self):
        text = (h.ROOT/".github/workflows/health.yml").read_text()
        self.assertIn("cron: '17 0,12 * * *'", text)
        self.assertIn("needs: inspect", text)
        self.assertIn("&& !inputs.dry_run", text)
        self.assertIn("github.ref == 'refs/heads/main'", text)
        inspect, notify = text.split("  notify:", 1)
        self.assertNotIn("issues: write", inspect)
        self.assertIn("issues: write", notify)
        self.assertNotIn("contents: write", text)
        self.assertNotIn("pull-requests: write", text)
        self.assertIn("repository-health-", text)
        for path in (".github/workflows/health.yml", "scripts/check_health.py", "tests/test_health.py"):
            self.assertEqual(text.count("- '"+path+"'"), 2)
        for word in ("scripts/build.py", "sync_hosts.py", "workflow_run"):
            self.assertNotIn(word, text)

    def test_health_changes_still_get_full_release_validation(self):
        text = (h.ROOT/".github/workflows/update.yml").read_text()
        self.assertNotIn("'scripts/check_health.py'", text)
        self.assertNotIn("'.github/workflows/health.yml'", text)
        self.assertIn("Offline reproducibility check", text)
        self.assertIn("cron: '17 22 * * *'", text)
        self.assertEqual(text.count("'modules/**'"), 2)


if __name__ == "__main__":
    unittest.main()

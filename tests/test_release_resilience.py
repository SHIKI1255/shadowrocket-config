import io
import json
import os
from pathlib import Path
import ssl
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from unittest.mock import MagicMock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"scripts"))
import public_http as http
import release_health as health
from shared_data import digest, json_bytes


class RetryTests(unittest.TestCase):
    def response(self, data=b"ok", url="https://api.github.com/x"):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = data
        response.url = url
        return response

    def test_transient_retry_is_bounded_and_success_stops(self):
        req = urllib.request.Request("https://api.github.com/x")
        with patch.object(http.urllib.request, "urlopen", side_effect=[TimeoutError(), ConnectionResetError(), self.response()]) as get, patch.object(http.time, "sleep") as sleep:
            self.assertEqual(http.request_bytes(req, limit=10), b"ok")
            self.assertEqual(get.call_count, 3)
            self.assertEqual([x.args[0] for x in sleep.call_args_list], [2, 8])
        with patch.object(http.urllib.request, "urlopen", side_effect=TimeoutError()) as get, patch.object(http.time, "sleep"), self.assertRaises(TimeoutError):
            http.request_bytes(req, limit=10)
        self.assertEqual(get.call_count, 3)

    def test_auth_not_found_tls_and_non_get_do_not_retry(self):
        for exc in [urllib.error.HTTPError("u", c, "", {}, None) for c in (400,401,403,404)] + [ssl.SSLError(), urllib.error.URLError(ssl.CertificateError())]:
            with patch.object(http.urllib.request, "urlopen", side_effect=exc) as get, patch.object(http.time, "sleep") as sleep, self.assertRaises(Exception):
                http.request_bytes(urllib.request.Request("https://api.github.com/x"), limit=10)
            self.assertEqual(get.call_count, 1);sleep.assert_not_called()
        with patch.object(http.urllib.request, "urlopen") as get, self.assertRaises(ValueError):
            http.request_bytes(urllib.request.Request("https://api.github.com/x", data=b"x"), limit=10)
        get.assert_not_called()

    def test_rate_limit_delay_and_invalid_payload_are_not_rewritten(self):
        exc = urllib.error.HTTPError("u",429,"",{"Retry-After":"9999"},None)
        self.assertEqual(http.delay(exc,0),60)
        for code in (429,500,502,503,504):
            self.assertTrue(http.transient(urllib.error.HTTPError("u",code,"",{},None)))
        with patch.object(http.urllib.request, "urlopen", return_value=self.response(b"invalid json")) as get:
            self.assertEqual(http.request_bytes(urllib.request.Request("https://api.github.com/x"),limit=10),b"invalid json")
            get.assert_called_once()
        with patch.object(http.urllib.request, "urlopen", return_value=self.response(url="https://other.example/x")) as get, self.assertRaises(ValueError):
            http.request_bytes(urllib.request.Request("https://api.github.com/x"),limit=10,expected_host="api.github.com")
        get.assert_called_once()


class MonitoringTests(unittest.TestCase):
    def test_failure_is_deduplicated_and_recovery_is_one_event(self):
        state = []
        def api(path, payload=None, method=None):
            if path == "issues":
                state.append({"number":1,"body":payload["body"]})
            elif path == "issues/1":
                state[0].update(payload)
            return {}
        failure = {"validate":"failure","publish":"skipped"}
        success = {"validate":"success","publish":"success"}
        with patch.object(health, "identity", return_value=("a"*40,"run")), patch.object(health, "incidents", side_effect=lambda: [r for r in state if r.get("state")!="closed"]), patch.object(health, "api", side_effect=api) as calls:
            self.assertEqual(health.notify(failure)["status"],"opened")
            self.assertEqual(health.notify(failure)["status"],"deduplicated")
            self.assertEqual(calls.call_count,1)
            self.assertEqual(health.notify(failure,{"fingerprint":"new","category":"quantity_review"})["status"],"updated")
            self.assertEqual(health.notify({"validate":"skipped","publish":"skipped"})["status"],"unchanged")
            self.assertEqual(health.notify(success)["status"],"recovered")
            self.assertEqual(health.notify(success)["status"],"healthy")
            self.assertEqual(calls.call_count,3)

    def test_pr_and_stale_runs_cannot_write(self):
        env={"GITHUB_REPOSITORY":health.REPOSITORY,"GITHUB_REF":"refs/heads/main",
             "GITHUB_SHA":"a"*40,"GITHUB_RUN_ID":"1","GITHUB_EVENT_NAME":"pull_request"}
        with patch.dict(os.environ,env), patch.object(health,"api") as api, self.assertRaises(ValueError):
            health.identity()
        api.assert_not_called()
        env["GITHUB_EVENT_NAME"]="schedule"
        with patch.dict(os.environ,env), patch.object(health,"api",return_value={"object":{"sha":"b"*40}}), self.assertRaises(ValueError):
            health.identity()

    def test_public_readback_checks_all_files_and_retries_only_rolling_mismatch(self):
        files={"a.list":b"data","SHA256SUMS":b"sum"}; seen=[]; attempts={}
        def fetch(url):
            seen.append(url);attempts[url]=attempts.get(url,0)+1
            name=url.rsplit("/",1)[1]
            return b"old" if "/release/" in url and attempts[url]==1 else files[name]
        with patch.object(health,"verified_files",return_value=files), patch.object(health,"release_sha",return_value="a"*40), patch.object(health,"fetch",side_effect=fetch), patch.object(health.time,"sleep"):
            self.assertEqual(health.public_readback(Path("unused"))["files"],2)
        self.assertEqual(len(seen),6)
        with patch.object(health,"verified_files",return_value=files), patch.object(health,"release_sha",return_value="a"*40), patch.object(health,"fetch",return_value=b"wrong"), self.assertRaises(ValueError):
            health.public_readback(Path("unused"))

    def test_workflow_preserves_validation_and_restricts_monitor(self):
        text=(health.ROOT/".github/workflows/update.yml").read_text()
        self.assertIn("Offline reproducibility check",text)
        self.assertIn("if ($original -cne $replayed)",text)
        self.assertIn("Verify anonymous fixed and rolling download hashes",text)
        monitor=text.split("  monitor:\n")[1].split("  maintenance:\n")[0]
        self.assertIn("github.ref == 'refs/heads/main'",monitor)
        self.assertNotIn("pull_request_target",text)
        self.assertNotIn("continue-on-error: true",text.split("  publish:\n")[0].replace(
            "", "")) # Evidence upload is allowed to be absent, not a failed test.


if __name__ == "__main__":
    unittest.main()


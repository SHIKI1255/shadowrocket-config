import base64
from datetime import datetime, timedelta, timezone
import json
import os
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import build as b
import keepalive as k

NOW = datetime(2026, 9, 28, 0, 0, tzinfo=timezone.utc)
ENV = {'GITHUB_REPOSITORY': b.REPOSITORY, 'GITHUB_REF': 'refs/heads/main',
       'GITHUB_EVENT_NAME': 'schedule', 'GITHUB_SHA': 'a'*40, 'GITHUB_RUN_ID': '123',
       'VALIDATE_RESULT': 'success', 'PUBLISH_RESULT': 'success'}


class KeepaliveTests(unittest.TestCase):
    def test_29_and_30_day_boundary(self):
        self.assertTrue(k.due(None, NOW))
        for days, expected in [(29, False), (30, True), (31, True)]:
            record = {'schema_version': 1, 'checked_at_utc': (NOW-timedelta(days=days)).isoformat()}
            self.assertEqual(k.due(record, NOW), expected)
        with self.assertRaises(b.BuildError):
            k.due({'schema_version': 1, 'checked_at_utc': (NOW+timedelta(days=1)).isoformat()}, NOW)

    def test_untrusted_events_and_refs_cannot_write(self):
        for change in [{'GITHUB_EVENT_NAME': 'pull_request'}, {'GITHUB_REF': 'refs/heads/feature'},
                       {'GITHUB_REPOSITORY': 'another/repository'}, {'VALIDATE_RESULT': ''}]:
            with self.subTest(change=change), patch.dict(os.environ, ENV | change), patch('keepalive.api') as api:
                with self.assertRaises(b.BuildError):
                    k.maintain(NOW)
                api.assert_not_called()

    def test_stale_main_skips_before_write(self):
        with patch.dict(os.environ, ENV), patch('keepalive.api', return_value={'object': {'sha': 'b'*40}}) as api:
            self.assertEqual(k.maintain(NOW)['status'], 'skipped_main_advanced')
            self.assertEqual(api.call_count, 1)

    def run_fake(self, result='success', previous=None, advance=False, conflict=False):
        mutations, content = [], None
        heads = ['a'*40, 'b'*40 if advance else 'a'*40, 'e'*40]
        def fake(path, payload=None, method=None, missing_ok=False):
            nonlocal content
            if payload is not None: mutations.append((path, payload, method))
            if path == 'git/ref/heads/main': return {'object': {'sha': heads.pop(0)}}
            if path == 'contents/' + k.RECORD + '?ref=' + 'a'*40:
                return None if previous is None else {'type': 'file', 'encoding': 'base64',
                    'content': base64.b64encode(b.json_bytes(previous)).decode()}
            if path == 'git/ref/heads/release': return {'object': {'sha': 'f'*40}}
            if path == 'git/commits/' + 'a'*40: return {'tree': {'sha': 'b'*40}}
            if path == 'git/trees':
                self.assertEqual(payload['base_tree'], 'b'*40)
                self.assertEqual([v['path'] for v in payload['tree']], [k.RECORD])
                content = json.loads(payload['tree'][0]['content'])
                return {'sha': 'c'*40}
            if path == 'git/commits':
                self.assertEqual(payload['parents'], ['a'*40])
                return {'sha': 'e'*40}
            if path == 'git/refs/heads/main':
                self.assertFalse(payload['force'])
                if conflict: raise b.BuildError('Concurrent fast-forward rejected')
                return {'object': {'sha': 'e'*40}}
            if path == 'contents/' + k.RECORD + '?ref=' + 'e'*40:
                return {'content': base64.b64encode(b.json_bytes(content)).decode()}
            self.fail(path)
        with patch.dict(os.environ, ENV | {'VALIDATE_RESULT': result, 'PUBLISH_RESULT': 'skipped' if result == 'failure' else 'success'}), patch('keepalive.api', side_effect=fake):
            output = k.maintain(NOW)
        self.assertFalse(any('refs/heads/release' in path for path, _, _ in mutations))
        return output, mutations, content

    def test_first_run_records_real_status_and_readback(self):
        output, writes, content = self.run_fake()
        self.assertEqual(output['status'], 'recorded')
        self.assertEqual(content['validation_result'], 'success')
        self.assertEqual(content['published_release_commit'], 'f'*40)
        self.assertEqual(content['source_commit'], 'a'*40)
        self.assertEqual(len(writes), 3)

    def test_failure_can_record_but_cannot_publish(self):
        output, _, content = self.run_fake(result='failure')
        self.assertEqual(output['status'], 'recorded')
        self.assertEqual(content['validation_result'], 'failure')
        self.assertEqual(content['publication_result'], 'skipped')

    def test_repeat_does_not_commit(self):
        output, writes, _ = self.run_fake(previous={'schema_version': 1, 'checked_at_utc': NOW.isoformat()})
        self.assertEqual(output['status'], 'not_due')
        self.assertEqual(writes, [])

    def test_concurrent_main_update_is_not_overwritten(self):
        output, writes, _ = self.run_fake(advance=True)
        self.assertEqual(output['status'], 'skipped_main_advanced')
        self.assertFalse(any(path.startswith('git/refs/') for path, _, _ in writes))
        with self.assertRaises(b.BuildError):
            self.run_fake(conflict=True)

    def test_workflow_runs_after_publish_and_has_narrow_write_permissions(self):
        workflow = (b.ROOT/'.github/workflows/update.yml').read_text()
        block = workflow.split('  maintenance:', 1)[1]
        self.assertIn('needs: [validate, publish]', block)
        self.assertIn("github.ref == 'refs/heads/main'", block)
        self.assertIn('contents: write', block)
        self.assertIn("paths-ignore: ['.github/maintenance.json']", workflow)
        self.assertNotIn('pull_request_target', workflow)


if __name__ == '__main__':
    unittest.main()

import json,os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
import build as b
import publish as p

class PublishTests(unittest.TestCase):
    def make_release(self, directory):
        files = {'shadowrocket.conf': b'good', 'manifest.json': b.json_bytes({'config_sha256': b.digest(b'good'), 'source_files_sha256': {}}),
                 'sources.lock.json': b'{}', 'source_stats.json': b'{}', 'THIRD_PARTY.md': b'notice'}
        files['SHA256SUMS'] = b.checksum_files(files)
        for name, data in files.items():
            (directory/name).write_bytes(data)
        return files

    def test_tampering_and_unlisted_file_block_publish(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            self.make_release(path)
            p.verified_files(path)
            (path/'extra.txt').write_text('unknown')
            with self.assertRaises(b.BuildError):
                p.verified_files(path)
            (path/'shadowrocket.conf').write_text('changed')
            with self.assertRaises(b.BuildError):
                p.verified_files(path)

    def test_pr_cannot_publish(self):
        with patch.dict(os.environ, {'GITHUB_REPOSITORY': b.REPOSITORY, 'GITHUB_REF': 'refs/heads/main',
                                     'GITHUB_EVENT_NAME': 'pull_request'}), patch('publish.api') as api:
            with self.assertRaises(b.BuildError):
                p.publish(Path('.'))
            api.assert_not_called()

    def test_stale_main_stops_before_any_write(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            self.make_release(path)
            with patch.dict(os.environ, {'GITHUB_REPOSITORY': b.REPOSITORY, 'GITHUB_REF': 'refs/heads/main',
                                         'GITHUB_EVENT_NAME': 'push', 'GITHUB_SHA': 'a'*40}), \
                 patch('publish.api', return_value={'object': {'sha': 'b'*40}}) as api:
                with self.assertRaises(b.BuildError):
                    p.publish(path)
                self.assertEqual(api.call_count, 1)

    def test_failed_fast_forward_keeps_previous_release(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            self.make_release(path)
            writes = []
            def fake(path, payload=None, method=None, missing_ok=False):
                if path == 'git/ref/heads/main': return {'object': {'sha': 'a'*40}}
                if path == 'git/ref/heads/release': return {'object': {'sha': 'b'*40}}
                if path == 'git/trees': return {'sha': 'c'*40}
                if path == 'git/commits/' + 'b'*40: return {'tree': {'sha': 'd'*40}}
                if path == 'git/commits': return {'sha': 'e'*40}
                if path == 'git/refs/heads/release':
                    writes.append(payload)
                    raise b.BuildError('Concurrent update rejected')
                self.fail(path)
            with patch.dict(os.environ, {'GITHUB_REPOSITORY': b.REPOSITORY, 'GITHUB_REF': 'refs/heads/main',
                                         'GITHUB_EVENT_NAME': 'push', 'GITHUB_SHA': 'a'*40}), patch('publish.api', side_effect=fake):
                with self.assertRaises(b.BuildError):
                    p.publish(path)
            self.assertEqual(writes, [{'sha': 'e'*40, 'force': False}])

"""Versioned public data transport; no downloaded code is executed."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request
import zipfile

RAW = 'https://raw.githubusercontent.com/'
API = 'https://api.github.com/'
COMMON_REPOSITORY = 'SHIKI1255/shadowrocket-config'
MAX_BYTES = 32 * 1024 * 1024


class BuildError(ValueError): pass


def require(condition, message):
    if not condition: raise BuildError(message)


def digest(data): return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)+'\n').encode()


def records(text):
    for index, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if line and not line.startswith(('#', '//')): yield index, line


def fetch(url, *, missing_ok=False):
    from public_http import request_bytes
    p = urllib.parse.urlparse(url)
    require(p.scheme == 'https' and p.hostname in {'raw.githubusercontent.com','api.github.com'}, 'Unapproved public URL')
    require(not p.username and not p.password and not p.query and not p.fragment, 'Credentials/query in URL')
    headers = {'User-Agent': 'SHIKI1255-Rules/1', 'Accept': 'application/vnd.github+json'}
    if p.hostname == 'api.github.com' and os.environ.get('GITHUB_TOKEN'):
        headers['Authorization'] = 'Bearer ' + os.environ['GITHUB_TOKEN']
    try:
        data = request_bytes(urllib.request.Request(url, headers=headers),
                             limit=MAX_BYTES, expected_host=p.hostname)
    except urllib.error.HTTPError as exc:
        if missing_ok and exc.code == 404: return None
        raise BuildError('Public download failed: HTTP '+str(exc.code)) from None
    except (OSError, urllib.error.URLError, http.client.HTTPException): raise BuildError('Public download failed') from None
    except ValueError as exc: raise BuildError(str(exc)) from None
    require(0 < len(data) <= MAX_BYTES, 'Empty or oversized data')
    return data


def release_sha(repository):
    sha = json.loads(fetch(API+'repos/'+repository+'/git/ref/heads/release'))['object']['sha']
    require(re.fullmatch(r'[0-9a-f]{40}', sha), 'Invalid release SHA')
    return sha


def verify(manifest_bytes, bundle_bytes):
    m = json.loads(manifest_bytes)
    require(m.get('schema_version') == 1, 'Unknown shared schema')
    require(digest(bundle_bytes) == m['bundle_sha256'], 'Shared bundle hash mismatch')
    basis = {k: v for k,v in m.items() if k != 'shared_version'}
    require(m['shared_version'] == digest(json_bytes(basis)), 'Shared version mismatch')
    payload = json.loads(bundle_bytes)
    require(payload.get('schema_version') == 1 and set(payload['platforms']) == {'shadowrocket','quantumultx','clash'}, 'Unknown platform inventory')
    for key in payload['categories']:
        require(re.fullmatch(r'[a-z][a-z0-9_]*', key), 'Invalid category identifier')
    return m, payload


def load(directory=None, commit='', replay_repository=None):
    saved_lock = None
    if directory is not None:
        inputs = {p: (directory/p).read_bytes() for p in ('shared/manifest.json','shared/bundle.json')}
        if (directory/'shared-consumer.lock.json').exists():
            lock = json.loads((directory/'shared-consumer.lock.json').read_bytes())
            saved_lock = lock
            sha = lock['shared_release']
        else: sha = 'local'
    else:
        repository = replay_repository or COMMON_REPOSITORY
        sha = commit or release_sha(repository)
        require(re.fullmatch(r'[a-f0-9]{40}', sha), 'Full immutable SHA required')
        prefix = RAW+repository+'/'+sha+'/'
        paths = ['shared/manifest.json','shared/bundle.json']
        with ThreadPoolExecutor(max_workers=2) as pool:
            inputs = dict(zip(paths, pool.map(lambda p: fetch(prefix+p), paths)))
        if replay_repository:
            lock = json.loads(fetch(prefix+'shared-consumer.lock.json'))
            saved_lock = lock
            sha = lock['shared_release']
    m, payload = verify(inputs['shared/manifest.json'], inputs['shared/bundle.json'])
    if saved_lock is not None:
        require(saved_lock.get('schema_version') == 1 and saved_lock.get('shared_version') == m['shared_version']
                and saved_lock.get('bundle_sha256') == m['bundle_sha256'], 'Replay lock mismatch')
    require(sha == 'local' or re.fullmatch(r'[a-f0-9]{40}', sha), 'Invalid common release revision')
    lock = {'schema_version': 1, 'shared_release': sha, 'shared_version': m['shared_version'], 'bundle_sha256': m['bundle_sha256']}
    inputs['shared-consumer.lock.json'] = json_bytes(lock)
    return m, payload, inputs


def checksum_files(files):
    return ''.join(digest(data)+'  '+path+'\n' for path,data in sorted(files.items())).encode()


def deterministic_zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        for name,data in sorted(files.items()):
            info = zipfile.ZipInfo(name, (2020,1,1,0,0,0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info,data)
    return buf.getvalue()


def write_files(root, output, files):
    require(not output.is_relative_to(root), 'Outputs belong outside source checkout')
    require(not output.exists() or not any(output.iterdir()), 'Output directory must be empty')
    output.mkdir(parents=True, exist_ok=True)
    for name,data in sorted(files.items()):
        require(re.fullmatch(r'[a-zA-Z0-9_./-]+', name) and '..' not in name.split('/') and not name.startswith('/'), 'Unsafe output path')
        path = output/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(data)

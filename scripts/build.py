"""Build a local template from validated shared routing data (Python 3.12)."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import re
import sys
from shared_data import (API, RAW, BuildError, require, digest, json_bytes, records, fetch,
    checksum_files, deterministic_zip, release_sha, load, verify, write_files as save_files)
from render_client import render

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads((ROOT/'config/shared.json').read_text(encoding='utf-8'))
CLIENT = CONTRACT['client']
REPOSITORY = 'SHIKI1255/' + CLIENT + '-config'


def previous_stats():
    sha=release_sha(REPOSITORY)
    return json.loads(fetch(RAW+REPOSITORY+'/'+sha+'/source_stats.json'))


def write_files(output, files): save_files(ROOT,output,files)


def build(manifest, payload, inputs):
    require(CONTRACT['schema_version']==1, 'Unknown local contract')
    files=dict(inputs)
    files.update(render(ROOT, CLIENT, payload, CONTRACT))
    for path,text in payload['licenses'].items():
        require(path in {'licenses/blackmatrix7.txt','licenses/v2fly.txt'}, 'Unknown license path')
        files[path]=text.encode()
    files['THIRD_PARTY.md']=(ROOT/'docs/third_party.md').read_bytes()
    if CLIENT=='clash': files['INSTALL.md']=(ROOT/'docs/install.md').read_bytes()
    report=payload['platforms'][CLIENT]['report']
    primary={'shadowrocket':'shadowrocket.conf','quantumultx':'quantumultx.conf','clash':'override.js'}[CLIENT]
    paths=[p for folder in ('scripts','config','tests') for p in sorted((ROOT/folder).rglob('*'))
           if p.is_file() and '__pycache__' not in p.parts]
    paths += [ROOT/'docs/third_party.md']
    if CLIENT=='clash':paths += [ROOT/'docs/install.md']
    source_hashes={p.relative_to(ROOT).as_posix():digest(p.read_bytes()) for p in paths}
    if CLIENT=='shadowrocket': source_hashes.update(manifest['source_files_sha256'])
    metadata={'schema_version':2, **report, 'shared_version':manifest['shared_version'],
              'config_sha256':digest(files[primary]),'source_files_sha256':source_hashes,
              'validation':'Static generation verified; client parsing and real network acceptance are separate.'}
    if CLIENT=='quantumultx':metadata['public_files_sha256']={n:digest(files[n]) for n in ('quantumultx.conf','rules.list','rewrite.list')}
    if CLIENT=='clash':metadata['guard_sha256']=digest(files['guard.yaml'])
    files['manifest.json']=json_bytes(metadata)
    files.setdefault('sources.lock.json',json_bytes({'schema_version':3,'shared_version':manifest['shared_version'],'upstreams':manifest['upstreams']}))
    files.setdefault('source_stats.json',json_bytes({'shared_version':manifest['shared_version'],'rules':report['rules'],'providers':report.get('providers',{})}))
    if CLIENT=='clash':files['clash-overlay.zip']=bundle_clash(ROOT,files,metadata)
    files['SHA256SUMS']=checksum_files(files)
    return files


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--replay',type=Path)
    p.add_argument('--replay-commit',default='')
    p.add_argument('--shared-dir',type=Path,help='Explicit local data package, for validation only')
    p.add_argument('--previous-release',action='store_true')
    p.add_argument('--audit',action='store_true')
    a=p.parse_args()
    try:
        require(sum(bool(x) for x in (a.replay,a.replay_commit,a.shared_dir))<=1,'Choose one input method')
        require(not a.audit or CLIENT=='shadowrocket','Audit is performed only by the common producer')
        if CLIENT=='shadowrocket':
            require(not a.shared_dir,'Producer cannot consume another package')
            sys.path.insert(0,str(ROOT))
            from shared import core
            conf=core.read_json('sources.yaml')
            lock,inputs=core.collect(conf,a.replay,a.replay_commit)
            previous=previous_stats() if a.previous_release else None
            inputs=core.build(conf,lock,inputs,previous,a.audit)
            if a.audit:
                write_files(a.output.resolve(),inputs)
                print(json.dumps({'status':'audit_candidate'}));return 0
            manifest,payload=verify(inputs['shared/manifest.json'],inputs['shared/bundle.json'])
        else:
            manifest,payload,inputs=load(a.shared_dir or a.replay,a.replay_commit,
                                        REPOSITORY if a.replay_commit else None)
        files=build(manifest,payload,inputs)
        write_files(a.output.resolve(),files)
        print(json.dumps({'status':'validated','client':CLIENT,'files':len(files),'shared_version':manifest['shared_version'],
                          'config_sha256':json.loads(files['manifest.json'])['config_sha256']}))
    except (ValueError,OSError,KeyError,TypeError) as exc:
        print('BUILD STOPPED: '+str(exc),file=sys.stderr);return 1
    return 0


if __name__=='__main__':sys.exit(main())

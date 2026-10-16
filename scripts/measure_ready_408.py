#!/usr/bin/env python3
"""Sequential, hash-only /ready ASGI measurements in sealed-source private fixtures.

No production read, HTTP socket, model, download or launchd. Includes installed
payload verification using a synthetic descriptor (NOT a qualified artifact).
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def guard():
    if shutil.disk_usage(ROOT).free < 3_000_000_000:
        raise RuntimeError('Disk guard: 3 GB minimum')


def distribution(values):
    values = sorted(values)
    return {k: values[round((len(values)-1)*q)] for k, q in [('p50_ms', .5), ('p95_ms', .95), ('max_ms', 1)]}


def worker(site, root, items, repeats):
    sys.path.insert(0, str(site))
    from memory_os_v4 import Config, MemoryV4, server
    from memory_os_v4.store import MemoryItem
    import httpx
    import anyio
    from memory_os_v4.readiness import engine_readiness
    from memory_os_v4.capabilities import deployed
    from memory_os_v4.readiness import build_identity
    token = root/'token'
    token.write_text('synthetic-readiness-benchmark-token-'+'x'*40)
    token.chmod(0o600)
    os.environ['MEMORY_V4_TOKEN_FILE'] = str(token)
    os.environ.pop('MEMORY_V4_BUILD_COMMIT', None)
    os.environ.pop('MEMORY_V4_PROJECT_REGISTRY', None)
    with MemoryV4(config=Config(data_dir=str(root/'store'), store_backend='sqlite_s1', work_state=True,
                               embed_model='hash', hash_dim=16)) as m:
        rows = [MemoryItem(id=f'i-{n}', text=f'Synthetic decision {n} on deployment and readiness.', kind='fact',
                           project='synthetic', created_at=1700000000., event_time=1700000000.) for n in range(items)]
        m.store.append_many(rows)
        # Historic versions: production size is a scale, not production data.
        for n in range(items//4):
            rows[n].text += ' Revised.'
        m.store.append_many(rows[:items//4])
        m._rebuild_sql_indexes()
        m.flush()
        server._engine = m
        for n in range(20):
            result = server.dispatch('memory_block_set', {'project':'synthetic', 'name':f'b{n}', 'text':'Synthetic rules.', 'request_id':f'b{n}'})
            assert result.get('ok') is not False, result
        for n in range(30):
            result = server.dispatch('memory_task_create', {'project':'synthetic', 'title':f'Synthetic task {n}', 'request_id':f't{n}'})
            assert result.get('ok') is True, result
        # Detailed component timings before a full envelope warms any cache.
        components = {}
        for name, action in [('store_ms', m.store.check_integrity), ('blocks_ms', m.blocks.check_integrity),
                             ('requests_ms', m.requests.check_integrity), ('identity_ms', build_identity),
                             ('capabilities_ms', lambda:deployed(m, build_identity()))]:
            start = time.perf_counter()
            assert action()
            components[name] = (time.perf_counter()-start)*1000
        app = server._build_http_app('127.0.0.1', 8765)
        async def measure():
            samples = []
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
                for _ in range(repeats):
                    guard()
                    start = time.perf_counter()
                    res = await client.get('http://127.0.0.1:8765/ready', headers={'Authorization':'Bearer '+token.read_text()})
                    samples.append((time.perf_counter()-start)*1000)
                    assert res.status_code == 200 and res.json()['ready'] is True, res.text
            return samples
        samples = anyio.run(measure)
        assert engine_readiness(m)[0]
        report = {'ok':True, 'items':items, 'item_versions':len(rows)+items//4, 'blocks':20, 'tasks':30,
                  'samples':len(samples), 'components':components, 'first_ms':samples[0],
                  'requests_ms':samples, 'warm':distribution(samples[1:]), 'neural_calls':0,
                  'profile':m._compute_profile(), 'transport':'ASGI in process, authenticated, no TCP',
                  'descriptor':'synthetic coherent payload fixture, not a release qualification'}
        server._engine = None
        return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', default='HEAD', help='Full committed source, both arms unchanged parameters')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--items', type=int, default=3500)
    p.add_argument('--repeats', type=int, default=31)
    p.add_argument('--worker', type=Path)
    p.add_argument('--site', type=Path)
    a = p.parse_args()
    guard()
    if a.worker:
        result = worker(a.site, a.worker, a.items, a.repeats)
        a.output.write_text(json.dumps(result, indent=2)+'\n')
        return
    if a.items < 1 or a.repeats < 2:
        p.error('Positive items and at least two repeats required')
    a.output.mkdir(parents=True, exist_ok=False)
    revision = subprocess.check_output(['git','rev-parse',a.source], cwd=ROOT, text=True).strip()
    with tempfile.TemporaryDirectory(prefix='ready408-', dir=a.output) as directory:
        root = Path(directory).resolve()
        raw = subprocess.check_output(['git','archive',revision,'v4/src','src'], cwd=ROOT)
        with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
            tar.extractall(root, filter='data')
        site = root/'site'
        site.mkdir()
        shutil.move(str(root/'v4/src/memory_os_v4'), site/'memory_os_v4')
        shutil.move(str(root/'src/memory_os_ai'), site/'memory_os_ai')
        files = {str(f.relative_to(site)):hashlib.sha256(f.read_bytes()).hexdigest()
                 for f in sorted(site.rglob('*')) if f.is_file()}
        canonical = json.dumps(files, sort_keys=True, separators=(',',':')).encode()
        import re
        version = re.search(r'__version__\s*=\s*"([^"]+)"', (site/'memory_os_v4/__init__.py').read_text())[1]
        descriptor = {'schema_version':1, 'version':version, 'source_commit':revision,
                      'source_inventory_sha256':hashlib.sha256(raw).hexdigest(), 'files':files,
                      'payload_sha256':hashlib.sha256(canonical).hexdigest()}
        (site/'memory_os_v4/_build_identity.json').write_text(json.dumps(descriptor))
        env = {**os.environ, 'PYTHONDONTWRITEBYTECODE':'1','HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1',
               'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','VECLIB_MAXIMUM_THREADS':'1'}
        env.pop('PYTHONPATH',None)
        subprocess.run([sys.executable,'-B',str(Path(__file__).resolve()),'--worker',str(root), '--site',str(site),
                        '--output',str(a.output.resolve()/'report.json'),'--items',str(a.items),'--repeats',str(a.repeats)],
                       env=env,cwd=root, check=True, timeout=300)
    report_path = a.output/'report.json'
    report = json.loads(report_path.read_text())
    report['source_commit'] = revision
    report['method'] = 'synthetic fixed 3500 scale; same machine, concurrency 1, no semantic inference'
    report_path.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ('ok','source_commit','components','first_ms','warm')}))


if __name__ == '__main__':
    main()

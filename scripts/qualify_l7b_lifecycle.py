#!/usr/bin/env python3
"""Claude-only 10k synthetic work-state measurement: sealed CAS<=100 ms / list50<30 ms p95 targets.

nice15, concurrency1, 300 s, real swap/disk guard; no model/API/production writes.
Seed bulk writes are fixture setup, NOT a claim about task-create throughput.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import time
import uuid

from qualify_sqlite_s4_lifecycle import attest, ROOT
from qualify_405_test_isolation import resource_sample


def p95(values):
    return sorted(values)[math.ceil(.95 * len(values)) - 1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--git-dir', type=Path, default=ROOT / '.local-qualification/lexical-dev-git')
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / '.local-qualification'):
        parser.error('Fresh output inside clone .local-qualification required')
    output.mkdir(parents=True, exist_ok=False)
    result = {'ok': False, 'production_modified': False, 'neural_inference': False, 'api_calls': 0,
              'activation_qualified': False, 'tasks': 10000, 'source_commit': args.source_commit,
              'criterion': {'cas_p95_ms': 100, 'list50_p95_ms': 30, 'list50_operator': '<', 'brief_bytes': 6000, 'brief_tokens': 1500}}
    begin = time.monotonic()
    def guard():
        result['last_resource_sample'] = resource_sample(True)
        if time.monotonic() - begin > 300:
            raise RuntimeError('L7b measurement exceeded frozen 300 s')
    try:
        guard()
        if os.getpriority(os.PRIO_PROCESS, 0) < 15:
            raise RuntimeError('nice15 required')
        attest(args.git_dir, args.source_commit)
        os.environ.update(PYTHONDONTWRITEBYTECODE='1', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                          OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', VECLIB_MAXIMUM_THREADS='1')
        from memory_os_v4 import Config, MemoryV4, server, work_state as work
        from memory_os_v4.brief import measures
        config = Config(data_dir=str(output / 'synthetic-store'), embed_model='hash', hash_dim=16,
                        store_backend='sqlite_s1', work_state=True)
        rows = []
        seed_start = time.monotonic()
        with MemoryV4(config=config) as engine:
            server._engine = engine
            for offset in range(0, 10000, 100):
                guard()
                def seed():
                    now = work.stamp()
                    for i in range(offset, offset + 100):
                        task = {'task_id': str(uuid.uuid5(uuid.NAMESPACE_URL, 'l7b-synthetic-' + str(i))),
                                'project': 'l7b-synthetic', 'context_id': None, 'title': f'Synthetic task {i}',
                                'status': 'open', 'owner': None, 'owner_authenticated': False,
                                'next_action': 'Synthetic action', 'blocked_reason': None, 'resolution': None,
                                'evidence': [], 'revision': 1, 'created_at': now, 'updated_at': now,
                                'provenance': {'reason': 'synthetic fixture', 'authenticated': False}}
                        work.write_task(engine, task)
                        if len(rows) < 100:
                            rows.append(work.view(engine, task))
                    return {'ok': True, 'fixture_batch': offset}
                engine.execute_request('synthetic_seed_tasks', 'l7b-synthetic', {'offset': offset}, None, seed)
            result['seed_seconds'] = time.monotonic() - seed_start
            cas, listing = [], []
            for i, task in enumerate(rows):
                guard()
                start = time.perf_counter()
                reply = server.dispatch('memory_task_update', {'project': task['project'], 'task_id': task['task_id'],
                     'expected_etag': task['etag'], 'patch': {'next_action': 'Measured update'},
                     'reason': 'Synthetic timing', 'request_id': 'measure-' + str(i)})
                cas.append((time.perf_counter() - start) * 1000)
                if not reply.get('ok'):
                    raise RuntimeError('CAS failed; no performance verdict')
                start = time.perf_counter()
                reply = server.dispatch('memory_task_list', {'project': task['project'], 'limit': 50})
                listing.append((time.perf_counter() - start) * 1000)
                if len(reply.get('tasks', [])) != 50:
                    raise RuntimeError('List failed; no performance verdict')
            brief = server.dispatch('memory_session_brief', {'project': 'l7b-synthetic'})
            size, tokens = measures(brief)
            count = len(work.all_tasks(engine, 'l7b-synthetic'))
            result.update(cas_ms=cas, list50_ms=listing, cas_p95_ms=p95(cas), list50_p95_ms=p95(listing),
                          brief_bytes=size, brief_tokens=tokens, observed_tasks=count,
                          ok=count == 10000 and p95(cas) <= 100 and p95(listing) < 30 and size <= 6000 and tokens <= 1500)
            # Explicitly attest that the model-free path did not populate the cache.
            result['cache_vectors'] = engine.store._connection.execute('SELECT count(*) FROM embedding_cache').fetchone()[0]
            result['ok'] &= result['cache_vectors'] == 0
            guard()
    except Exception as exc:
        result.update(ok=False, error=f'{type(exc).__name__}: {exc}')
    finally:
        result['seconds'] = round(time.monotonic() - begin, 3)
        (output / 'report.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: result.get(k) for k in ('ok', 'cas_p95_ms', 'list50_p95_ms', 'error')}))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

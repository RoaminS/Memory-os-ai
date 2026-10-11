#!/usr/bin/env python3
"""Offline prepared NEW authority only: warm cache and compare sealed probes.

Run after the migration GO, BEFORE starting the production service on this
directory. No source JSONL access, launchctl or business mutation.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import time

from sqlite_activation_worker import attest_runtime, heads, digest, probe, compare_probes


def check_store(store, reference, plan, guard):
    from memory_os_v4 import Config, MemoryV4
    from memory_os_v4.ops.sqlite_backup import inventory
    from memory_os_v4.sqlite_migrate import _safe, offline_lease, _connect_offline
    store = _safe(store)
    def logical():
        with offline_lease(store):
            con = _connect_offline(store)
            try:
                return inventory(con, guard)
            finally:
                con.close()
    before = logical()
    if (before['store_instance_id'] != reference['store_instance_id']
            or before['snapshot_receipt_sequence'] != reference['logical_inventory']['snapshot_receipt_sequence']):
        raise ValueError('Prepared authority differs from final migration identity/cut')
    cfg = replace(Config.from_env(), data_dir=str(store), store_backend='sqlite_s1',
                  work_state=True, write_mode='read_only', maintenance_mode='disabled', project_registry=None)
    started = time.monotonic()
    with MemoryV4(config=cfg) as engine:
        engine.ensure_retrieval()
        warm_seconds = time.monotonic() - started
        if (digest(heads(engine)) != reference['all_heads_sha256']
                or engine.status()['encoding_profile'] != reference['encoding_profile']
                or engine.status()['total'] != reference['live_total']):
            raise ValueError('Prepared heads/count/profile differ from final cut')
        result = probe(engine, plan['queries'], plan['at_time'], guard)
        compare_probes(reference['search']['baseline'], {'prepared': result})
        misses = engine._embedding_cache.misses
    after = logical()
    if any(after['tables'][name] != value for name, value in before['tables'].items() if name != 'embedding_cache'):
        raise ValueError('Cache warming modified non-cache SQL authority')
    with MemoryV4(config=cfg) as engine:
        engine.ensure_retrieval()
        if engine._embedding_cache.misses:
            raise ValueError('Prepared warm reopen reencoded corpus')
    return {'ok': True, 'search_identical': True, 'live_total': reference['live_total'],
            'store_instance_id': reference['store_instance_id'], 'warm_seconds': warm_seconds,
            'initial_cache_misses': misses, 'reopen_cache_misses': 0,
            'non_cache_authority_unchanged': True, 'logical_inventory': after}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store', type=Path, required=True)
    parser.add_argument('--copy-report', type=Path, required=True)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1] / '.local-qualification'
    if args.output.exists() or not args.output.resolve().is_relative_to(root):
        parser.error('Fresh proof file inside clone .local-qualification required')
    result = {'ok': False, 'production_activation': False}
    try:
        from source_release_checks import resources
        plan = json.loads(args.plan.read_text())
        copy_report = json.loads(args.copy_report.read_text())
        if copy_report['ok'] is not True or copy_report['production_modified'] is not False:
            raise ValueError('Final-cut copy proof incomplete/failed')
        import hashlib
        if copy_report['plan_sha256'] != hashlib.sha256(args.plan.read_bytes()).hexdigest():
            raise ValueError('Plan changed since final-cut proof')
        if os.getpriority(os.PRIO_PROCESS, 0) < 15:
            raise ValueError('nice 15 required')
        started = time.monotonic()
        def guard():
            resources(args.output.parent)
            if time.monotonic() - started > 300:
                raise RuntimeError('Prepared-store 300 s budget exceeded')
        guard()
        result['runtime'] = attest_runtime(Path(plan['wheel']), plan['wheel_sha256'], plan['release_source_commit'])
        result.update(check_store(args.store, copy_report['worker'], plan, guard))
    except Exception as exc:
        result.update(ok=False, error=f'{type(exc).__name__}: {exc}')
    with args.output.open('x') as f:
        json.dump(result, f, indent=2)
        f.write('\n')
    print(json.dumps({k: result.get(k) for k in ('ok', 'error', 'live_total', 'initial_cache_misses')}))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

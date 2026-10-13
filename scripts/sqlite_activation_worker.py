"""Installed 4.0.5 only: migration/cache/S4/rollback on owned disposable copies."""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import time
import zipfile

from search_qualification import search_hit_counts


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def attest_runtime(wheel, expected_sha, commit):
    import memory_os_v4
    if (memory_os_v4.__version__ != '4.0.5' or importlib.metadata.version('memory-os-ai') != '4.0.5'
            or os.environ.get('MEMORY_V4_BUILD_COMMIT') != commit):
        raise ValueError('Qualified installed 4.0.5/build identity required')
    package = Path(memory_os_v4.__file__).resolve().parent.parent
    if 'site-packages' not in package.parts or not package.is_relative_to(Path(os.sys.prefix).resolve()):
        raise ValueError('Checkout/foreign package imports refused')
    if hashlib.sha256(wheel.read_bytes()).hexdigest() != expected_sha:
        raise ValueError('Qualified wheel changed')
    with zipfile.ZipFile(wheel) as archive:
        names = [n for n in archive.namelist() if n.endswith('.py')
                 and n.startswith(('memory_os_ai/', 'memory_os_v4/'))]
        if not names or any((package / n).read_bytes() != archive.read(n) for n in names):
            raise ValueError('Installed runtime differs from qualified wheel')
    return {'version': '4.0.5', 'commit': commit, 'runtime_files': len(names), 'wheel_sha256': expected_sha}


def heads(engine):
    return {i.id: asdict(i) for i in engine.store.all(include_deleted=True)}


def probe(engine, queries, at_time, guard):
    engine.ensure_retrieval()
    engine.store.touch = lambda *a, **k: None  # Disposable copy only; freeze access/recency.
    before = heads(engine)
    rows = {}
    for query in queries:
        guard()
        start = time.monotonic()
        hits = engine.search(query, top_k=10, at_time=at_time, rerank=False, project=None)
        rows[query] = {'ids': [h.item.id for h in hits], 'scores': [h.score for h in hits],
                       'seconds': time.monotonic() - start, 'hit_count': len(hits)}
    if heads(engine) != before:
        raise ValueError('Search probes changed authoritative items')
    return rows


def compare_probes(baseline, candidates):
    search_hit_counts(baseline, tuple(baseline), worker='baseline')
    if not candidates:
        raise ValueError('Non-vacuity: no candidate workers')
    for name, row in candidates.items():
        search_hit_counts(row, tuple(baseline), worker=name)
    expected = {q: v['ids'] for q, v in baseline.items()}
    changed = {name: [q for q in expected if report.get(q, {}).get('ids') != expected[q]]
               for name, report in candidates.items()}
    if any(changed.values()) or any(set(report) != set(expected) for report in candidates.values()):
        raise ValueError('Frozen search probes differ: ' + json.dumps(changed, sort_keys=True))


def run_copy(root, queries, at_time, guard, *, private_drill=True):
    """Tests may disable only the private TCP phase; CLI always requires it."""
    from memory_os_v4 import Config, MemoryV4, work_state, server
    from memory_os_v4.embed import Embedder, HashEmbedder
    from memory_os_v4.ops import sqlite_backup as s4
    from memory_os_v4.ops.auth import write_secret_file
    from memory_os_v4.ops.settings import OpsSettings
    from memory_os_v4.sqlite_migrate import export, _files, _file_manifest
    from migrate_sqlite_activation import migrate_with_attestation as migrate

    source, sql = root / 'snapshot', root / 'sqlite'
    source_before = _file_manifest(_files(source))
    cfg = Config.from_env()
    cfg.project_registry = None
    cfg.maintenance_mode = 'disabled'
    cfg.work_state = False
    cfg.write_mode = 'read_only'
    encoded = []
    originals = [(kind, kind.encode) for kind in (Embedder, HashEmbedder)]
    for kind, original in originals:
        def measured(self, values, _original=original):
            guard()
            encoded.extend(values)
            result = _original(self, values)
            guard()
            return result
        kind.encode = measured
    report = {'ok': False, 'production_activation': False, 'api_calls': 0}
    try:
        baseline_dir = root / 'baseline'
        shutil.copytree(source, baseline_dir)
        started = time.monotonic()
        with MemoryV4(config=replace(cfg, data_dir=str(baseline_dir), store_backend='jsonl')) as engine:
            report['baseline_startup_seconds'] = time.monotonic() - started
            report['baseline_corpus_encoded_texts'] = len(encoded)
            baseline_heads, identity = heads(engine), engine.store.meta['store_instance_id']
            baseline_profile = engine.status()['encoding_profile']
            original_meta = json.loads((source / 'meta.json').read_text())
            if baseline_profile != original_meta.get('embed_compute_profile'):
                raise ValueError('Loaded baseline differs from frozen production encoding attestation')
            baseline = probe(engine, queries, at_time, guard)
            total = engine.status()['total']
        guard()
        started = time.monotonic()
        report['migration_simulation'] = migrate(source, sql)
        report['migration'] = migrate(source, sql, apply=True)
        report['migration_seconds'] = time.monotonic() - started
        if _file_manifest(_files(source)) != source_before:
            raise ValueError('Migration modified source copy')
        report['source_files'] = source_before
        sqlcfg = replace(cfg, data_dir=str(sql), store_backend='sqlite_s1', work_state=True)
        candidates, timings = {}, {}
        for label in ('cold', 'warm'):
            started = time.monotonic()
            before_encoding = len(encoded)
            with MemoryV4(config=sqlcfg) as engine:
                engine.ensure_retrieval()  # L7b cold ready alone does not attest retrieval.
                count = len(encoded) - before_encoding
                timings[label] = {'startup_seconds': time.monotonic() - started,
                                  'corpus_encoded_texts': count,
                                  'cache_hits': engine._embedding_cache.hits,
                                  'cache_misses': engine._embedding_cache.misses}
                if (heads(engine), engine.store.meta['store_instance_id'], engine.status()['total']) != (
                        baseline_heads, identity, total):
                    raise ValueError('Migration heads/count/identity differ from JSONL')
                if engine.status()['encoding_profile'] != baseline_profile:
                    raise ValueError('Migration changed attested encoder profile')
                if label == 'warm' and count:
                    raise ValueError('Warm SQLite reopen reencoded corpus')
                candidates[label] = probe(engine, queries, at_time, guard)
                report.update(store_instance_id=identity, live_total=total,
                              all_heads_sha256=digest(baseline_heads), encoding_profile=baseline_profile)
                report['search'] = {'baseline': baseline, **candidates}
                report['timings'] = timings
                compare_probes(baseline, candidates)
                if label == 'warm':
                    settings = OpsSettings(url='http://127.0.0.1:9/mcp/', token_file=root / 'unused-token',
                        store_dir=sql, backup_dir=root / 'backups', backup_key=root / 'backup.key',
                        ops_dir=root / 'ops', service_label=None)
                    write_secret_file(settings.backup_key, os.urandom(32))
                    report['online_backup'] = s4.backup(settings, apply=True)
                    if not report['online_backup']['ok']:
                        raise ValueError('First online backup failed: ' + report['online_backup'].get('error', 'unknown'))
                    report['private_drill'] = s4.restore_drill(settings, apply=True,
                        start_server=private_drill, reconcile=True)
                    if not report['private_drill']['ok']:
                        raise ValueError('Private drill failed: ' + report['private_drill'].get('error', 'unknown'))
                    if private_drill and (not report['private_drill'].get('private_reopen_verified')
                            or report['private_drill']['server_check']['corpus_reencodings'] != 0):
                        raise ValueError('Private reopen/cache proof missing')
                    if report['private_drill']['store_instance_id'] == identity:
                        raise ValueError('Restore drill failed to create a private identity')
                    report['logical_inventory'] = s4.inventory(engine.store._connection, guard)
        # New-write/rollback exercise only on another disposable store, never on
        # the prepared migration target or a live authority.
        mutations = root / 'post-writes'
        shutil.copytree(sql, mutations)
        with MemoryV4(config=replace(sqlcfg, data_dir=str(mutations), write_mode='read_write')) as engine:
            server._engine = engine
            project = 'zz-test-sqlite-activation-dry-run'
            def batch():
                engine.add_facts([{'item_id': 'activation-old', 'text': 'Synthetic rollback witness before correction'}],
                                 project=project)
                engine.add_facts([{'item_id': 'activation-new', 'text': 'Synthetic rollback witness after correction',
                                  'replaces': ['activation-old']}], project=project)
                engine.add_episode('Synthetic forgotten witness', item_id='activation-forget', project=project)
                engine.forget('activation-forget', project=project)
                engine.blocks.set('project_state', 'Synthetic post-migration block', project=project)
                return {'ok': True, 'witness': 'activation-new'}
            engine.execute_request('activation_dry_run', project, {}, 'activation-ack', batch)
            task = server.dispatch('memory_task_create', {'project': project,
                'title': 'Synthetic rollback task', 'next_action': 'Inspect semantic export',
                'request_id': 'activation-task'})
            if not task.get('ok') or not work_state.all_tasks(engine, project):
                raise ValueError('L7b opt-in task exercise failed')
            expected_heads = heads(engine)
        # close() adds its final flush receipt; capture the authoritative cut
        # AFTER close, rather than accidentally declaring that receipt lost.
        con = s4.readonly(mutations / 'store.sqlite3', sealed=True)
        try:
            expected = {name: [r[0] for r in con.execute(query)] for name, query in (
                ('items.jsonl', 'SELECT record_json FROM item_versions ORDER BY rowid'),
                ('blocks.jsonl', 'SELECT record_json FROM block_versions ORDER BY sequence'),
                ('sqlite-receipts.jsonl', 'SELECT record_json FROM request_receipts ORDER BY sequence'),
                ('task-versions.jsonl', 'SELECT record_json FROM task_versions ORDER BY task_id,revision'))}
        finally:
            con.close()
        before_export = _file_manifest(_files(mutations))
        rollback = root / 'rollback-jsonl'
        report['rollback_export'] = export(mutations, rollback, rollback=True)
        if _file_manifest(_files(mutations)) != before_export:
            raise ValueError('Rollback export changed SQLite authority')
        for name, rows in expected.items():
            if (rollback / name).read_text().splitlines() != rows:
                raise ValueError('Rollback lost history/receipt/sidecar: ' + name)
        meta = json.loads((rollback / 'meta.json').read_text())
        if meta['store_instance_id'] == identity or meta['restored_from_instance_id'] != identity:
            raise ValueError('Rollback identity/lineage missing')
        receipts = [json.loads(line) for line in (rollback / 'requests.jsonl').read_text().splitlines()]
        if not any(r.get('request_id') == 'activation-ack' and r['result']['witness'] == 'activation-new' for r in receipts):
            raise ValueError('Acknowledged write lost after rollback')
        with MemoryV4(config=replace(cfg, data_dir=str(rollback), store_backend='jsonl')) as engine:
            if heads(engine) != expected_heads or engine.store.meta['store_instance_id'] != meta['store_instance_id']:
                raise ValueError('Reopened rollback lost current heads/identity')
            if not engine.store.get('activation-forget').deleted:
                raise ValueError('Forgotten witness resurrected')
        report['rollback_verified_after_new_writes'] = True
        report['ok'] = True
        report['service_drill_qualified'] = private_drill
        report['source_copy_unchanged'] = _file_manifest(_files(source)) == source_before
        return report
    except Exception as exc:
        report.update(error=f'{type(exc).__name__}: {exc}')
        return report
    finally:
        server._engine = None
        for kind, original in originals:
            kind.encode = original


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text())
    root = args.plan.resolve().parent
    if ('.local-qualification' not in root.parts or not (root / 'copy-owner.json').is_file()
            or root.is_relative_to(Path.home() / '.memory-os-v4')
            or root.is_relative_to(Path.home() / 'Memory-os-ai')):
        raise ValueError('Owned disposable copy required')
    from memory_os_v4 import Config
    from memory_os_v4.embed import _resolve_device
    from memory_os_v4.ops.settings import OpsSettings
    from memory_os_v4.ops.storage import machine_guard
    cfg = Config.from_env()
    if (cfg.embed_model == 'hash' or _resolve_device(cfg.device) != 'mps' or cfg.embed_dtype != 'native'
            or cfg.embed_batch_size != 32 or cfg.embed_max_seq_length != 512
            or cfg.bm25_profile != 'scoped_literal' or cfg.lexical_guard_k != 3):
        raise ValueError('Unchanged production MPS/native/batch32/C4 profile required')
    runtime = attest_runtime(Path(plan['wheel']), plan['wheel_sha256'], plan['release_source_commit'])
    settings = OpsSettings(url='http://127.0.0.1:9/mcp/', token_file=root / 'unused-token',
        store_dir=root / 'snapshot', backup_dir=root / 'backups', backup_key=root / 'backup.key',
        ops_dir=root / 'ops', service_label=None)
    start = time.monotonic()
    def guard():
        machine_guard(settings)
        if time.monotonic() - start > plan['criterion']['total_seconds_max']:
            raise RuntimeError('Frozen dry-run wall-time budget exceeded')
    guard()
    # Model loading is strictly offline. Only the private S4 loopback server may
    # be contacted; no provider API or Hugging Face network fallback.
    import socket
    original_connect = socket.socket.connect
    def local_connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6) and address[0] not in {'127.0.0.1', '::1', 'localhost'}:
            raise RuntimeError('Non-loopback network forbidden in activation copy worker')
        return original_connect(sock, address)
    socket.socket.connect = local_connect
    result = run_copy(root, plan['queries'], plan['at_time'], guard)
    result.update(seconds=time.monotonic() - start, runtime=runtime)
    for field, maximum in [('migration_seconds', plan['criterion']['migration_seconds_max'])]:
        if result.get(field, maximum + 1) > maximum:
            result.update(ok=False, error='Frozen migration budget exceeded')
    with (root / 'worker.json').open('x') as output:
        json.dump(result, output, indent=2)
        output.write('\n')
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

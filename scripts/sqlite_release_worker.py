"""Installed-only worker; engine opens private copies, never the live SQLite store."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as md
import json
from pathlib import Path
import socket
import sys
import time


def authority(con):
    from memory_os_v4.ops.sqlite_backup import inventory
    logical = inventory(con)
    # Embeddings/chunk map are derived. Only meta.version may change on upgrade.
    tables = {n: v for n, v in logical['tables'].items() if n not in {'embedding_cache', 'store_metadata'}}
    meta = json.loads(con.execute("SELECT value_json FROM store_metadata WHERE key='metadata'").fetchone()[0])
    meta.pop('version', None)
    canonical = json.dumps(meta, sort_keys=True, separators=(',', ':')).encode()
    return {'schema_version': logical['schema_version'], 'tables': tables,
            'metadata_sha256_except_version': hashlib.sha256(canonical).hexdigest(),
            'other_metadata': con.execute("SELECT key,value_json FROM store_metadata "
                "WHERE key NOT IN ('metadata','chunk_map') ORDER BY key").fetchall(),
            'store_instance_id': logical['store_instance_id']}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plan', type=Path, required=True)
    p.add_argument('--kind', required=True)
    p.add_argument('--snapshot-source', type=Path)
    args = p.parse_args()
    plan = json.loads(args.plan.read_text())
    root = args.plan.parent.resolve()
    if plan['format'] != 'sqlite-release-406-v1' or not (root / 'copy-owner.json').is_file():
        raise ValueError('Owned private plan required')
    # Baseline 4.0.5 predates provision.py. The launcher copies the sealed
    # 4.0.6 guard as standalone tooling; never changes the baseline package.
    from qualification_guard import guard
    guard(root)
    import memory_os_v4
    if Path(sys.executable).parent.parent not in Path(memory_os_v4.__file__).parents:
        raise ValueError('Checkout/foreign package forbidden')
    from memory_os_v4.ops import sqlite_backup as s4
    if args.snapshot_source:
        if args.kind != 'snapshot' or md.version('memory-os-ai') != '4.0.5':
            raise ValueError('Snapshot requires installed baseline 4.0.5')
        logical = s4.snapshot(args.snapshot_source / s4.DB, root / 'snapshot.sqlite3', lambda: guard(root))
        (root / 'snapshot.json').write_text(json.dumps(logical, indent=2) + '\n')
        return
    name = args.kind
    if name not in {'baseline', 'wheel', 'sdist', 'wheel-warm', 'sdist-warm'}:
        raise ValueError('Unknown private worker')
    store = root / (name.removesuffix('-warm') + '-store')
    if not store.is_relative_to(root) or store.is_symlink():
        raise ValueError('Private store only')
    # Weights already cached. Any IP request is a qualification failure.
    def denied(*a, **kw):
        raise RuntimeError('IP network forbidden in copy worker')
    socket.create_connection = denied
    socket.socket.connect = denied
    from memory_os_v4 import Config, MemoryV4
    from memory_os_v4.embed import Embedder, _resolve_device
    from memory_os_v4.readiness import build_identity
    version = '4.0.5' if name == 'baseline' else '4.0.6'
    if memory_os_v4.__version__ != md.version('memory-os-ai') or md.version('memory-os-ai') != version:
        raise ValueError('Wrong installed version')
    if Path(sys.executable).parent.parent not in Path(memory_os_v4.__file__).parents:
        raise ValueError('Checkout/foreign package forbidden')
    if name != 'baseline':
        from memory_os_v4.ops.qualified_runtime import inspect_current
        if not inspect_current(extras=('ops', 'brief'))['ok']:
            raise ValueError('Installed runtime drift')
        identity = build_identity()
        if identity['identity_source'] != 'embedded_descriptor' or identity['commit'] != plan['source_commit']:
            raise ValueError('Installed payload/descriptor mismatch')
    cfg = Config.from_env()
    cfg.data_dir, cfg.store_backend, cfg.work_state, cfg.write_mode = str(store), 'sqlite_s1', True, 'read_only'
    if cfg.project_registry or cfg.bm25_profile != 'scoped_literal' or cfg.lexical_guard_k != 3:
        raise ValueError('Production C4/registry policy mismatch')
    if cfg.embed_model == 'hash' or _resolve_device(cfg.device) != 'mps':
        raise ValueError('Real production model/MPS required')
    con = s4.readonly(store / s4.DB, sealed=True)
    try:
        before = authority(con)
    finally:
        con.close()
    encoded = []
    original = Embedder.encode
    def counted(self, texts):
        guard(root)
        encoded.extend(texts)
        value = original(self, texts)
        guard(root)
        return value
    Embedder.encode = counted
    started = time.monotonic()
    with MemoryV4(config=cfg) as engine:
        engine.ensure_retrieval()  # Equivalent to qualified 4.0.6 --warm startup.
        startup_seconds, corpus_encoded_texts = time.monotonic() - started, len(encoded)
        engine.store.touch = lambda *a, **kw: None  # Disposable diagnostic only.
        rows = {}
        for query in plan['queries']:
            guard(root)
            t = time.monotonic()
            hits = engine.search(query, top_k=10, project='*', at_time=plan['at_time'], rerank=False)
            rows[query] = {'ids': [h.item.id for h in hits], 'scores': [h.score for h in hits],
                           'seconds': time.monotonic() - t}
        after = authority(engine.store._connection)
        if before != after:
            raise ValueError('Authoritative history/receipts/tasks/identity changed')
        report = {'ok': True, 'version': version, 'authority': after, 'search': rows,
                  'store_instance_id': engine.store.meta['store_instance_id'],
                  'encoding_profile': engine.status()['encoding_profile'],
                  'startup_seconds': startup_seconds, 'corpus_encoded_texts': corpus_encoded_texts,
                  'cache_hits': engine._embedding_cache.hits, 'cache_misses': engine._embedding_cache.misses,
                  'total': engine.status()['total'], 'access_tracking_disabled_on_copy_only': True}
    (root / (name + '.json')).write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()

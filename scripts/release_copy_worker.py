"""Private disposable-store worker: installed engine only, no live-store access.

Invoked by qualify_release_copy.py, never directly on a production directory.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import importlib.metadata
import json
from pathlib import Path
import sys
import time

QUERIES = ('memory-os-ai', 'SQLite', 'Romain', 'Claude', 'Codex', 'MPS', 'C4',
           'préférences utilisateur', 'production et sauvegarde', 'shared memory protocol')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--store', required=True, type=Path)
    p.add_argument('--owner-root', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--version', required=True)
    p.add_argument('--kind', choices=['baseline', 'candidate', 'warm'], required=True)
    p.add_argument('--at-time', required=True, type=float)
    a = p.parse_args()
    root, store = a.owner_root.resolve(), a.store.resolve()
    if (not store.is_relative_to(root) or not (root / 'copy-owner.json').is_file()
            or store == Path.home() / '.memory-os-v4' or store.is_symlink()):
        raise ValueError('Only an owned private copy may be opened')
    # Remove any network path before loading the installed package. Tokenizer
    # and weights are cached; no network fallback may silently download.
    import socket
    def denied(*args, **kwargs):
        raise RuntimeError('IP network forbidden in copy encoder worker')
    socket.create_connection = denied
    socket.socket.connect = denied
    import memory_os_v4
    from memory_os_v4 import Config, MemoryV4
    from memory_os_v4.embed import HashEmbedder, Embedder, _resolve_device
    if memory_os_v4.__version__ != a.version or importlib.metadata.version('memory-os-ai') != a.version:
        raise ValueError('Installed version differs')
    package = Path(memory_os_v4.__file__).resolve()
    if Path(sys.executable).parent.parent not in package.parents or 'site-packages' not in package.parts:
        raise ValueError('Checkout/foreign imports forbidden')
    texts = []
    original = Embedder.encode
    def measured(self, values):
        texts.extend(values)
        return original(self, values)
    Embedder.encode = measured
    original_hash = HashEmbedder.encode
    def measured_hash(self, values):
        texts.extend(values)
        return original_hash(self, values)
    HashEmbedder.encode = measured_hash
    started = time.monotonic()
    cfg = Config.from_env()
    cfg.data_dir = str(store)
    if cfg.embed_model != 'hash' and _resolve_device(cfg.device) != 'mps':
        raise ValueError('Production-copy profile requires MPS before model load; no CPU fallback')
    with MemoryV4(config=cfg) as engine:
        startup = time.monotonic() - started
        startup_texts = len(texts)
        items_before = {it.id: asdict(it) for it in engine.store.all(include_deleted=True)}
        # Disable access recording ONLY on this disposable diagnostic copy:
        # otherwise the queries alter recency of later probes and the source.
        engine.store.touch = lambda *args, **kwargs: None
        if hasattr(engine.embedder, 'encoding_profile'):
            profile = engine.status()['encoding_profile']
        elif cfg.embed_model == 'hash':
            profile = {'device': 'hash', 'dtype': 'native', 'batch_size': cfg.embed_batch_size,
                       'max_seq_length': cfg.embed_max_seq_length, 'parameter_dtypes': ['hash-float32'],
                       'parameter_devices': ['hash'], 'model_revision': 'hash-v1'}
        else:
            model = engine.embedder._ensure_model()
            parameters = list(model.parameters())
            revision = getattr(model[0].auto_model.config, '_commit_hash', None)
            profile = {'device': engine.embedder.device, 'dtype': 'native', 'batch_size': cfg.embed_batch_size,
                       'max_seq_length': cfg.embed_max_seq_length,
                       'parameter_dtypes': sorted({str(v.dtype) for v in parameters}),
                       'parameter_devices': sorted({v.device.type for v in parameters}),
                       'model_revision': revision,
                       'runtime': {n: importlib.metadata.version(n) for n in ('torch', 'sentence-transformers', 'transformers', 'numpy')}}
        rows = {}
        for query in QUERIES:
            t = time.monotonic()
            hits = engine.search(query, top_k=10, at_time=a.at_time, rerank=False)
            rows[query] = {'ids': [h.item.id for h in hits], 'scores': [h.score for h in hits],
                           'milliseconds': (time.monotonic() - t) * 1000}
        if {it.id: asdict(it) for it in engine.store.all(include_deleted=True)} != items_before:
            raise RuntimeError('Diagnostic changed authoritative items')
        count = engine.status()['total']
    report = {'ok': True, 'version': a.version, 'kind': a.kind, 'package': str(package),
              'startup_seconds': startup, 'startup_encoded_texts': startup_texts,
              'encoding_profile': profile, 'search': rows,
              'search_hit_counts': {q: len(h['ids']) for q, h in rows.items()}, 'total': count,
              'store_instance_id': engine.store.meta.get('store_instance_id'),
              'source_records_unchanged': True, 'access_tracking_disabled_on_copy_only': True}
    a.output.write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()

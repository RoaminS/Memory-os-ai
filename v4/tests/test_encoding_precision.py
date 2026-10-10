"""Precision/cache/rollback policy without any neural inference."""
from types import SimpleNamespace

import numpy as np
import pytest

from memory_os_v4 import Config, MemoryV4
from memory_os_v4 import embed
from memory_os_v4.embed import HashEmbedder


def test_dtype_and_fact_batch_env_are_explicit_opt_ins(monkeypatch):
    assert Config().embed_dtype == 'native' and Config().batch_facts is False
    monkeypatch.setenv('MEMORY_V4_EMBED_DTYPE', 'float32')
    monkeypatch.setenv('MEMORY_V4_BATCH_FACTS', '1')
    cfg = Config.from_env()
    assert cfg.embed_dtype == 'float32' and cfg.batch_facts is True


@pytest.mark.parametrize('available,resolved', [(True, 'mps'), (False, 'cpu')])
def test_auto_default_preserves_deployed_backend_selection(monkeypatch, available, resolved):
    import torch
    monkeypatch.delenv('MEMORY_V4_DEVICE', raising=False)
    monkeypatch.setattr(torch.backends.mps, 'is_available', lambda: available)
    assert Config().device == Config.from_env().device == 'auto'
    assert embed._resolve_device(Config.from_env().device) == resolved


def test_mps_float32_profile_is_explicit_from_environment(monkeypatch):
    monkeypatch.setenv('MEMORY_V4_DEVICE', 'mps')
    monkeypatch.setenv('MEMORY_V4_EMBED_DTYPE', 'float32')
    monkeypatch.setenv('MEMORY_V4_EMBED_BATCH_SIZE', '4')
    monkeypatch.setenv('MEMORY_V4_BATCH_FACTS', '0')
    cfg = Config.from_env()
    assert (cfg.device, cfg.embed_dtype, cfg.embed_batch_size, cfg.batch_facts) == ('mps', 'float32', 4, False)


def test_bad_precision_is_rejected_before_store_creation(tmp_path):
    with pytest.raises(ValueError, match='embed_dtype'):
        MemoryV4(config=Config(data_dir=str(tmp_path / 'store'), embed_model='hash', embed_dtype='int8'))
    assert not (tmp_path / 'store').exists()


def test_default_fact_path_is_scalar_even_with_episode_batching(tmp_path, monkeypatch):
    calls = []
    original = HashEmbedder.encode
    def encode(self, texts):
        calls.append(len(texts))
        return original(self, texts)
    monkeypatch.setattr(HashEmbedder, 'encode', encode)
    with MemoryV4(config=Config(data_dir=str(tmp_path), embed_model='hash', embed_batch_size=4)) as engine:
        engine.add_facts([{'text': f'Ordinary fact {i}'} for i in range(5)], event_time=100)
    assert calls == [1] * 5


def test_float32_model_cache_is_separate_and_loader_receives_dtype(monkeypatch):
    import torch
    import sentence_transformers
    seen = []
    class Fake:
        def __init__(self, name, device, **kw):
            seen.append(kw)
            self.dtype = kw.get('model_kwargs', {}).get('torch_dtype', torch.bfloat16)
        def parameters(self):
            yield SimpleNamespace(dtype=self.dtype)
        def get_sentence_embedding_dimension(self):
            return 3
        def encode(self, texts, **kw):
            return np.ones((len(texts), 3), dtype=np.float32)
    monkeypatch.setattr(sentence_transformers, 'SentenceTransformer', Fake)
    monkeypatch.setattr(embed, '_MODEL_CACHE', {})
    a = embed.Embedder('fake', device='cpu')
    b = embed.Embedder('fake', device='cpu', dtype='float32')
    c = embed.Embedder('fake', device='cpu', dtype='float32')
    assert a._ensure_model() is not b._ensure_model()
    assert b._ensure_model() is c._ensure_model()
    assert seen == [{}, {'model_kwargs': {'torch_dtype': torch.float32}}]


def test_explicit_mps_never_falls_back_to_cpu(monkeypatch):
    import torch
    monkeypatch.setattr(torch.backends.mps, 'is_available', lambda: False)
    monkeypatch.setattr(embed, '_MODEL_CACHE', {})
    with pytest.raises(RuntimeError, match='no CPU fallback'):
        embed.Embedder('fake', device='mps', dtype='float32')._ensure_model()


@pytest.mark.parametrize('backend', ['jsonl', 'sqlite_s1'])
def test_cache_precision_change_reencodes_once_and_reopen_reuses(tmp_path, monkeypatch, backend):
    calls = []
    original = HashEmbedder.encode
    def encode(self, texts):
        calls.extend(texts)
        return original(self, texts)
    monkeypatch.setattr(HashEmbedder, 'encode', encode)
    def config(dtype):
        return Config(data_dir=str(tmp_path), embed_model='hash', store_backend=backend, embed_dtype=dtype)
    with MemoryV4(config=config('native')) as engine:
        engine.add_facts([{'text': 'A cached fact', 'item_id': 'fact'}], event_time=100)
    calls.clear()
    with MemoryV4(config=config('float32')) as engine:
        assert engine.store.get('fact').text == 'A cached fact'
    assert len(calls) == 1
    calls.clear()
    with MemoryV4(config=config('float32')):
        pass
    assert calls == []


@pytest.mark.parametrize('backend', ['jsonl', 'sqlite_s1'])
def test_resolved_device_and_batch_profile_change_never_reuse_old_vectors(tmp_path, monkeypatch, backend):
    import memory_os_v4.memory as module
    calls = []
    class Fake(HashEmbedder):
        def __init__(self, cfg):
            super().__init__(dim=cfg.hash_dim)
            self.device = cfg.device
        def encode(self, texts):
            calls.extend(texts)
            return super().encode(texts)
    monkeypatch.setattr(module, '_make_embedder', Fake)
    def config(device, batch=4):
        return Config(data_dir=str(tmp_path), embed_model='hash', store_backend=backend,
                      device=device, embed_batch_size=batch)
    with MemoryV4(config=config('mps')) as engine:
        engine.add_facts([{'text': 'Original device-specific fact', 'item_id': 'fact'}], event_time=100)
    for device, batch in [('cpu', 4), ('cpu', 8)]:
        calls.clear()
        with MemoryV4(config=config(device, batch)) as engine:
            assert engine.store.get('fact').text == 'Original device-specific fact'
        assert calls == ['Original device-specific fact'] or (
            len(calls) == 1 and 'Original device-specific fact' in calls[0])
        calls.clear()
        with MemoryV4(config=config(device, batch)):
            pass
        assert calls == []


@pytest.mark.parametrize('missing', ['profile', 'batch_size'])
def test_legacy_jsonl_without_complete_profile_rebuilds_once_in_native_mode(tmp_path, monkeypatch, missing):
    import json
    from dataclasses import asdict
    cfg = Config(data_dir=str(tmp_path), embed_model='hash', embed_dtype='native')
    with MemoryV4(config=cfg) as engine:
        engine.add_facts([{'text': 'Legacy unverified vector', 'item_id': 'fact'}], event_time=100)
        expected = asdict(engine.store.get('fact'))
    p = tmp_path / 'meta.json'
    meta = json.loads(p.read_text())
    if missing == 'profile':
        meta.pop('embed_compute_profile')
    else:
        meta['embed_compute_profile'].pop('batch_size')
    p.write_text(json.dumps(meta))
    calls = []
    original = HashEmbedder.encode
    def encode(self, texts):
        calls.extend(texts)
        return original(self, texts)
    monkeypatch.setattr(HashEmbedder, 'encode', encode)
    with MemoryV4(config=cfg) as engine:
        assert asdict(engine.store.get('fact')) == expected
    assert len(calls) == 1 and 'Legacy unverified vector' in calls[0]
    calls.clear()
    with MemoryV4(config=cfg):
        pass
    assert calls == []


@pytest.mark.parametrize('backend', ['jsonl', 'sqlite_s1'])
def test_auto_to_explicit_mps_reuses_only_an_attested_same_profile(tmp_path, monkeypatch, backend):
    import torch
    import memory_os_v4.memory as module
    calls = []
    monkeypatch.setattr(torch.backends.mps, 'is_available', lambda: True)
    class Fake(HashEmbedder):
        def __init__(self, cfg):
            super().__init__(dim=cfg.hash_dim)
            self.device = embed._resolve_device(cfg.device)
        def encode(self, texts):
            calls.extend(texts)
            return super().encode(texts)
    monkeypatch.setattr(module, '_make_embedder', Fake)
    cfg = dict(data_dir=str(tmp_path), embed_model='hash', store_backend=backend)
    with MemoryV4(config=Config(**cfg, device='auto')) as engine:
        engine.add_facts([{'text': 'Attested MPS fact', 'item_id': 'fact'}], event_time=100)
    calls.clear()
    with MemoryV4(config=Config(**cfg, device='mps')) as engine:
        assert engine.store.get('fact').text == 'Attested MPS fact'
    assert calls == []

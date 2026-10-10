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

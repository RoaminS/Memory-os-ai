"""Explicit synthetic v3 encoder for offline *unit* qualification only.

MEMORY_TEST_HASH_V3=1 never changes production runtime. This is not a neural
semantic-quality test. Loader tests can still override the factory explicitly.
"""
import hashlib
import os
import re

import numpy as np
import pytest


class SyntheticEncoder:
    def __init__(self, *args, **kwargs):
        pass

    def encode(self, texts, **kwargs):
        vectors = np.zeros((len(texts), 384), dtype=np.float32)
        for i, text in enumerate(texts):
            for token in re.findall(r'\w+', text.casefold()):
                index = int(hashlib.sha256(token.encode()).hexdigest()[:8], 16) % 383 + 1
                vectors[i, index] += 1
            # Shared component keeps small toy inputs within the legacy default
            # distance threshold. No neural or semantic-equivalence claim.
            vectors[i, 0] = 2 * max(1, float(np.linalg.norm(vectors[i])))
        vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
        return vectors


@pytest.fixture(autouse=True)
def synthetic_v3_encoder(monkeypatch):
    if os.environ.get('MEMORY_TEST_HASH_V3') == '1':
        # Apply the existing v4 OpenMP policy before either native runtime.
        # Importing the facade does not construct an encoder or run a model.
        import memory_os_v4  # noqa: F401
        import torch
        torch.set_num_threads(1)  # same FAISS/Torch safety rule as the v4 facade
        from memory_os_ai import engine
        monkeypatch.setattr(engine, '_SentenceTransformer', SyntheticEncoder)

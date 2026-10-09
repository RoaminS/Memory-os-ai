"""Disposable, fingerprinted float32 corpus-vector cache; queries stay uncached."""
from __future__ import annotations

import hashlib

import numpy as np

from .sqlite_store import _json


class SQLiteEmbeddings:
    def __init__(self, store):
        self.store = store
        self.hits = self.misses = 0

    def encode(self, texts, encoder, dim, profile):
        if not texts:
            return np.empty((0, dim), dtype=np.float32)
        prefix = _json(profile).encode() + b"\0"
        keys = [hashlib.sha256(prefix + text.encode()).hexdigest() for text in texts]
        unique = list(dict.fromkeys(keys))
        vectors = {}
        with self.store._sql_lock:
            for start in range(0, len(unique), 500):
                batch = unique[start:start + 500]
                rows = self.store._connection.execute(
                    f"SELECT cache_key,dim,vector,vector_sha256 FROM embedding_cache WHERE cache_key IN ({','.join('?' for _ in batch)})",
                    batch,
                )
                for key, saved_dim, blob, checksum in rows:
                    if (saved_dim != dim or len(blob) != dim * 4
                            or hashlib.sha256(blob).hexdigest() != checksum):
                        continue
                    vec = np.frombuffer(blob, dtype="<f4")
                    if np.isfinite(vec).all():
                        vectors[key] = vec
            missing = {key: text for key, text in zip(keys, texts) if key not in vectors}
            self.hits += len(texts) - len(missing)
            self.misses += len(missing)
            if missing:
                encoded = np.asarray(encoder(list(missing.values())), dtype="<f4")
                if encoded.shape != (len(missing), dim) or not np.isfinite(encoded).all():
                    raise ValueError("Invalid encoded vectors; cache write refused")
                # If an engine transaction exists, cache inserts join it. Otherwise
                # warming derived state commits separately without a business receipt.
                with self.store.transaction():
                    for (key, _), vec in zip(missing.items(), encoded):
                        blob = vec.tobytes()
                        self.store.write("embedding_cache", "INSERT INTO embedding_cache VALUES(?,?,?,?) "
                                         "ON CONFLICT(cache_key) DO UPDATE SET dim=excluded.dim,vector=excluded.vector,"
                                         "vector_sha256=excluded.vector_sha256",
                                         (key, dim, blob, hashlib.sha256(blob).hexdigest()))
                        vectors[key] = vec
            return np.stack([vectors[key] for key in keys]).astype(np.float32, copy=False)

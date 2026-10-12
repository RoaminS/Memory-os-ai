"""Disposable, fingerprinted float32 corpus-vector cache; queries stay uncached."""

from __future__ import annotations

import hashlib

import numpy as np

from .sqlite_store import _json


class SQLiteEmbeddings:
    def __init__(self, store):
        self.store = store
        self.hits = self.misses = 0
        self.legacy_hits = 0
        # Snapshot before a cold task mutation updates meta.version. Adopt only
        # the store's declared release; never pick another release's vectors.
        self.legacy_release = store.meta.get("version")

    def _read_vectors(self, keys, dim):
        vectors = {}
        for start in range(0, len(keys), 500):
            batch = keys[start : start + 500]
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
        return vectors

    def _legacy_profile(self, profile):
        # These two releases used the same cache serializer and float32 codec.
        # Lookup still hashes the CURRENT complete encoder attestation + text;
        # meta's historical compute profile never licenses adoption by itself.
        attestation = profile.get("attestation") or {}
        attested = (attestation.get("encoder_source_sha256") is not None
                    or (profile.get("model") == "hash"
                        and attestation.get("model_revision") == "hash-v1"))
        if (self.legacy_release not in {"4.0.5", "4.0.6"}
                or profile.get("cache_schema") != 2 or not attested
                or not attestation.get("model_revision")
                or "unattested_revision_nonce" in attestation):
            return None
        return {**{k: v for k, v in profile.items() if k != "cache_schema"},
                "implementation": self.legacy_release}

    def encode(self, texts, encoder, dim, profile):
        if not texts:
            return np.empty((0, dim), dtype=np.float32)
        prefix = _json(profile).encode() + b"\0"
        keys = [hashlib.sha256(prefix + text.encode()).hexdigest() for text in texts]
        unique = list(dict.fromkeys(keys))
        with self.store._sql_lock:
            vectors = self._read_vectors(unique, dim)
            adopted = {}
            legacy_profile = self._legacy_profile(profile)
            if legacy_profile is not None:
                legacy_prefix = _json(legacy_profile).encode() + b"\0"
                legacy_keys = {key: hashlib.sha256(legacy_prefix + text.encode()).hexdigest()
                               for key, text in zip(keys, texts) if key not in vectors}
                old = self._read_vectors(list(dict.fromkeys(legacy_keys.values())), dim)
                adopted = {key: old[old_key] for key, old_key in legacy_keys.items() if old_key in old}
                vectors.update(adopted)
            missing = {key: text for key, text in zip(keys, texts) if key not in vectors}
            # Requested hits count real cache lookups, not duplicate misses.
            self.hits += sum(key in vectors for key in keys)
            self.legacy_hits += sum(key in adopted for key in keys)
            self.misses += len(missing)
            pending = dict(adopted)
            if missing:
                encoded = np.asarray(encoder(list(missing.values())), dtype="<f4")
                if encoded.shape != (len(missing), dim) or not np.isfinite(encoded).all():
                    raise ValueError("Invalid encoded vectors; cache write refused")
                pending.update({key: vec for key, vec in zip(missing, encoded)})
            if pending:
                # If an engine transaction exists, cache inserts join it. Otherwise
                # warming derived state commits separately without a business receipt.
                # Keep all legacy rows byte-identical for rollback. Alias copies
                # and newly encoded vectors commit together, or neither does.
                with self.store.transaction():
                    for key, vec in pending.items():
                        blob = vec.tobytes()
                        self.store.write(
                            "embedding_cache",
                            "INSERT INTO embedding_cache VALUES(?,?,?,?) "
                            "ON CONFLICT(cache_key) DO UPDATE SET dim=excluded.dim,vector=excluded.vector,"
                            "vector_sha256=excluded.vector_sha256",
                            (key, dim, blob, hashlib.sha256(blob).hexdigest()),
                        )
                        vectors[key] = vec
            return np.stack([vectors[key] for key in keys]).astype(np.float32, copy=False)

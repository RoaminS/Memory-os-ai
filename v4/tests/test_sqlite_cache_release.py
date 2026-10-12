"""Release-independent cache and narrowly attested legacy adoption; no model loads."""
import hashlib

import numpy as np
import pytest

import memory_os_v4
from memory_os_v4 import Config, MemoryV4
from memory_os_v4.embed import HashEmbedder
from memory_os_v4.sqlite_embeddings import SQLiteEmbeddings
from memory_os_v4.sqlite_store import SQLiteStore, _json


def profile():
    return {"model": "fixture-model", "dim": 2, "max_seq_length": 512,
            "batch_size": 32, "device": "mps", "namespace": "v1", "cache_schema": 2,
            "attestation": {"model_revision": "fixture-revision", "encoder_source_sha256": "fixture-source",
                            "device": "mps", "dtype": "native", "runtime": {"torch": "qualified"}}}


def key(p, text):
    return hashlib.sha256(_json(p).encode() + b"\0" + text.encode()).hexdigest()


def seed(store, release, p, text="same indexed text", vec=None):
    vec = np.asarray([0.25, 0.75] if vec is None else vec, dtype="<f4")
    old = {**{k: v for k, v in p.items() if k != "cache_schema"}, "implementation": release}
    blob = vec.tobytes()
    with store.transaction():
        store.write("embedding_cache", "INSERT INTO embedding_cache VALUES(?,?,?,?)",
                    (key(old, text), 2, blob, hashlib.sha256(blob).hexdigest()))
    return key(old, text), vec


def forbidden(texts):
    pytest.fail("No corpus encoding allowed for attested legacy cache")


@pytest.mark.parametrize("release", ["4.0.5", "4.0.6"])
def test_legacy_adoption_is_byte_identical_atomic_and_survives_release_change(tmp_path, release):
    store = SQLiteStore(tmp_path)
    try:
        store.save_meta({"version": release, "store_instance_id": "synthetic-id"})
        old_key, expected = seed(store, release, profile())
        before = store._connection.execute("SELECT * FROM embedding_cache").fetchall()
        cache = SQLiteEmbeddings(store)
        # A cold task update in this process must not lose the opening lineage.
        store.save_meta({"version": "4.0.7", "store_instance_id": "synthetic-id"})
        actual = cache.encode(["same indexed text"] * 2, forbidden, 2, profile())
        assert np.array_equal(actual, np.stack([expected] * 2))
        assert (cache.hits, cache.misses, cache.legacy_hits) == (2, 0, 2)
        assert store._connection.execute("SELECT * FROM embedding_cache WHERE cache_key=?", (old_key,)).fetchall() == before
        assert store._connection.execute("SELECT count(*) FROM embedding_cache").fetchone()[0] == 2
        reopened = SQLiteEmbeddings(store)
        assert np.array_equal(reopened.encode(["same indexed text"], forbidden, 2, profile())[0], expected)
        assert reopened.legacy_hits == 0
    finally:
        store.close()


def test_both_releases_present_preserves_declared_release_not_first_matching_vector(tmp_path):
    store = SQLiteStore(tmp_path)
    try:
        store.save_meta({"version": "4.0.6"})
        seed(store, "4.0.5", profile(), vec=[0.1, 0.9])
        _, expected = seed(store, "4.0.6", profile(), vec=[0.2, 0.8])
        actual = SQLiteEmbeddings(store).encode(["same indexed text"], forbidden, 2, profile())
        assert np.array_equal(actual[0], expected)
    finally:
        store.close()


@pytest.mark.parametrize("change", ["device", "revision", "source", "runtime", "dtype", "namespace", "text", "nonce", "unknown-release"])
def test_changed_or_unattested_profile_never_adopts_legacy(tmp_path, change):
    store = SQLiteStore(tmp_path)
    try:
        store.save_meta({"version": "4.0.5" if change != "unknown-release" else "4.0.1"})
        seed(store, "4.0.5", profile())
        p, text = profile(), "same indexed text"
        if change == "device":
            p["device"] = "cpu"
        elif change == "namespace":
            p["namespace"] = "new"
        elif change == "text":
            text += " changed"
        elif change == "nonce":
            p["attestation"]["unattested_revision_nonce"] = "unknown"
        elif change != "unknown-release":
            field = {"revision": "model_revision", "source": "encoder_source_sha256",
                     "runtime": "runtime", "dtype": "dtype"}[change]
            p["attestation"][field] = "changed"
        calls = []
        def encode(texts):
            calls.extend(texts)
            return np.array([[0.3, 0.7]], dtype="<f4")
        cache = SQLiteEmbeddings(store)
        cache.encode([text], encode, 2, p)
        assert calls == [text] and cache.legacy_hits == 0
    finally:
        store.close()


@pytest.mark.parametrize("corruption", ["checksum", "dimension", "nonfinite"])
def test_invalid_legacy_vector_reencodes_only_the_missing_text(tmp_path, corruption):
    store = SQLiteStore(tmp_path)
    try:
        store.save_meta({"version": "4.0.5"})
        old_key, _ = seed(store, "4.0.5", profile(), vec=[np.nan, 0.1] if corruption == "nonfinite" else None)
        if corruption != "nonfinite":
            with store.transaction():
                sql = "UPDATE embedding_cache SET " + ("dim=3" if corruption == "dimension" else "vector_sha256='bad'") + " WHERE cache_key=?"
                store.write("embedding_cache", sql, (old_key,))
        calls = []
        def encode(texts):
            calls.extend(texts)
            return np.array([[0.3, 0.7]], dtype="<f4")
        cache = SQLiteEmbeddings(store)
        cache.encode(["same indexed text"] * 2, encode, 2, profile())
        assert calls == ["same indexed text"]
        assert (cache.hits, cache.misses, cache.legacy_hits) == (0, 1, 0)
    finally:
        store.close()


def test_legacy_alias_write_failure_never_commits_partial_aliases(tmp_path):
    store = SQLiteStore(tmp_path)
    try:
        store.save_meta({"version": "4.0.5"})
        seed(store, "4.0.5", profile(), text="first")
        seed(store, "4.0.5", profile(), text="second")
        before = store._connection.execute("SELECT * FROM embedding_cache ORDER BY cache_key").fetchall()
        def fault(point):
            if point == "after_embedding_cache":
                raise ValueError("injected alias interruption")
        store.fault_hook = fault
        with pytest.raises(ValueError, match="interruption"):
            SQLiteEmbeddings(store).encode(["first", "second"], forbidden, 2, profile())
        assert store._connection.execute("SELECT * FROM embedding_cache ORDER BY cache_key").fetchall() == before
    finally:
        store.fault_hook = None
        store.close()


def test_engine_package_release_does_not_change_warm_cache_or_rollback(tmp_path, monkeypatch):
    cfg = Config(data_dir=str(tmp_path), store_backend="sqlite_s1", embed_model="hash", hash_dim=16)
    monkeypatch.setattr(memory_os_v4, "__version__", "4.0.7")
    with MemoryV4(config=cfg) as engine:
        engine.add_episode("Version-independent synthetic cache", item_id="synthetic")
        expected = engine.vector_index.export()
    monkeypatch.setattr(memory_os_v4, "__version__", "4.0.8")
    monkeypatch.setattr(HashEmbedder, "encode", lambda *args: forbidden(args))
    with MemoryV4(config=cfg) as engine:
        actual = engine.vector_index.export()
        assert actual[0] == expected[0] and np.array_equal(actual[1], expected[1])
        with pytest.raises(ValueError, match="business failure"):
            engine.execute_request("fail", "synthetic", {}, "synthetic-failure",
                                   lambda: (_ for _ in ()).throw(ValueError("business failure")))
        assert not engine._index_error

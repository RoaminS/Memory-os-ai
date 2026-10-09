"""Corpus-vector cache correctness with a counted hash encoder only."""
import sqlite3

import numpy as np
import pytest

from memory_os_v4 import Config, MemoryV4
from memory_os_v4.embed import HashEmbedder
from memory_os_v4.sqlite_store import MIN_SQLITE

pytestmark = pytest.mark.skipif(sqlite3.sqlite_version_info < MIN_SQLITE, reason="SQLite runtime too old")


def test_warm_start_and_rollback_encode_zero_existing_items(tmp_path, monkeypatch):
    config = Config(data_dir=str(tmp_path), store_backend="sqlite_s1", embed_model="hash", hash_dim=16,
                    multi_vector=True, chunk_tokens=5, chunk_overlap=1)
    with MemoryV4(config=config) as engine:
        engine.add_episodes_bulk([{"text": "Synthetic long episode " * 10, "item_id": str(i)} for i in range(10)])
        expected = engine.vector_index.export()

    def unexpected(*args, **kwargs):
        pytest.fail("Committed corpus must not be re-encoded on warm start or rollback")

    monkeypatch.setattr(HashEmbedder, "encode", unexpected)
    with MemoryV4(config=config) as engine:
        actual = engine.vector_index.export()
        assert actual[0] == expected[0] and np.array_equal(actual[1], expected[1])
        with pytest.raises(ValueError):
            engine.execute_request("fail", "A", {}, "fail", lambda: (_ for _ in ()).throw(ValueError("failure")))
        assert not engine._index_error


def test_corrupt_cache_only_reencodes_affected_vector_and_namespace_isolates(tmp_path, monkeypatch):
    config = Config(data_dir=str(tmp_path), store_backend="sqlite_s1", embed_model="hash", hash_dim=16)
    with MemoryV4(config=config) as engine:
        engine.add_episodes_bulk([{"text": f"Synthetic unique episode {i}", "item_id": str(i)} for i in range(4)])
        key = engine.store._connection.execute("SELECT cache_key FROM embedding_cache LIMIT 1").fetchone()[0]
        engine.store._connection.execute("UPDATE embedding_cache SET vector=x'00' WHERE cache_key=?", (key,))
    counts = []
    original = HashEmbedder.encode

    def counted(self, texts):
        counts.append(len(texts))
        return original(self, texts)

    monkeypatch.setattr(HashEmbedder, "encode", counted)
    with MemoryV4(config=config):
        assert counts == [1]
    counts.clear()
    config.sqlite_embedding_namespace = "changed-weights"
    with MemoryV4(config=config):
        assert counts == [4]


def test_failed_new_item_does_not_persist_derived_vector(tmp_path):
    with MemoryV4(config=Config(data_dir=str(tmp_path), store_backend="sqlite_s1", embed_model="hash")) as engine:
        engine.add_episode("Committed episode", item_id="old")
        count = engine.store._connection.execute("SELECT count(*) FROM embedding_cache").fetchone()[0]

        def fail():
            engine.add_episode("Uncommitted episode", item_id="new")
            raise ValueError("fail")

        with pytest.raises(ValueError):
            engine.execute_request("fail", "A", {}, "fail", fail)
        assert engine.store._connection.execute("SELECT count(*) FROM embedding_cache").fetchone()[0] == count
        assert engine.store.get("new") is None

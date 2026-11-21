"""Profile guard 4.1.0 (contract docs/LEXICAL_PROFILE_4_1_0.md §4) -- written first.

RED UNTIL STEPS 4-7. The profile probe must refuse a store of the other
profile BEFORE any mutating open: no WAL pragma, no migration, no metadata
write, no writer lease file. Every refusal is checked on the exact bytes and
sha256 of every file of the store directory. A store without marker is a
historical semantic store. No implicit conversion in either direction.
"""

import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

import pytest

from memory_os_v4 import Config, MemoryV4
from memory_os_v4.sqlite_store import SQLiteStore

POLICY = "lex-4.1.0-v1"
MARKER = {"name": "lexical", "policy": POLICY}
CORPUS = [
    {"text": "Alice Martin adopted a grey cat named Pixel.", "item_id": "g1", "project": "alpha"},
    {"text": "The budget review moved to Thursday afternoon.", "item_id": "g2", "project": "alpha"},
    {"text": "Bruno Keller maintains the SQLite migration scripts.", "item_id": "g3", "project": "beta"},
]


def lexical_config(path, **overrides):
    return Config(**{"data_dir": str(path), "retrieval_profile": "lexical", "store_backend": "sqlite_s1",
                     "use_dense": False, **overrides})


def semantic_config(path, **overrides):
    return Config(**{"data_dir": str(path), "store_backend": "sqlite_s1", "embed_model": "hash", "hash_dim": 16,
                     **overrides})


def snapshot(directory):
    """Exact inventory: name -> (bytes, sha256, mtime_ns). Bytes are compared, the digest is reported."""
    result = {}
    for path in sorted(Path(directory).rglob("*")):
        if path.is_file():
            data = path.read_bytes()
            result[str(path.relative_to(directory))] = (data, hashlib.sha256(data).hexdigest(), path.stat().st_mtime_ns)
    return result


def assert_unchanged(directory, before, *, volatile=()):
    after = snapshot(directory)
    assert sorted(after) == sorted(before)
    for name, (data, digest, mtime) in before.items():
        if name in volatile:
            continue
        assert after[name][1] == digest and after[name][0] == data, name
        assert after[name][2] == mtime, name


def refused(config, *, defer_retrieval=False):
    with pytest.raises(ValueError) as refusal:
        MemoryV4(config=config, defer_retrieval=defer_retrieval)
    return getattr(refusal.value, "code", None)


def semantic_store(path):
    with MemoryV4(config=semantic_config(path)) as engine:
        engine.add_episodes_bulk([dict(spec) for spec in CORPUS])
    assert sorted(item.name for item in Path(path).iterdir()) == ["store.sqlite3", "writer.lock"]
    return snapshot(path)


def lexical_store(path):
    with MemoryV4(config=lexical_config(path)) as engine:
        engine.add_episodes_bulk([dict(spec) for spec in CORPUS])
    assert sorted(item.name for item in Path(path).iterdir()) == ["store.sqlite3", "writer.lock"]
    return snapshot(path)


def rewrite_metadata(path, change):
    """Offline edit of the existing metadata document, leaving a cleanly checkpointed file."""
    connection = sqlite3.connect(Path(path) / "store.sqlite3")
    try:
        meta = json.loads(connection.execute(
            "SELECT value_json FROM store_metadata WHERE key='metadata'").fetchone()[0])
        change(meta, connection)
        connection.execute("UPDATE store_metadata SET value_json=? WHERE key='metadata'",
                           (json.dumps(meta, ensure_ascii=False, sort_keys=True, separators=(",", ":")),))
        connection.commit()
    finally:
        connection.close()
    assert not (Path(path) / "store.sqlite3-wal").exists() and not (Path(path) / "store.sqlite3-shm").exists()


# ------------------------------------------------ semantic store, lexical open
def test_semantic_store_is_refused_in_lexical_before_any_mutation(tmp_path):
    before = semantic_store(tmp_path)
    assert refused(lexical_config(tmp_path)) == "store_profile_mismatch"
    assert_unchanged(tmp_path, before)
    assert not (tmp_path / "store.sqlite3-wal").exists() and not (tmp_path / "store.sqlite3-shm").exists()
    assert refused(lexical_config(tmp_path)) == "store_profile_mismatch"  # still refused, still untouched
    assert_unchanged(tmp_path, before)
    # The refusal held no writer lease and converted nothing: the semantic profile reopens it warm.
    with MemoryV4(config=semantic_config(tmp_path)) as engine:
        assert "retrieval_profile" not in engine.store.meta
        assert (engine._embedding_cache.hits, engine._embedding_cache.misses) == (len(CORPUS), 0)
        assert {hit.item.id for hit in engine.search("cat Pixel", top_k=3, project="alpha")} >= {"g1"}


def test_semantic_store_without_writer_lock_file_gets_no_new_file(tmp_path):
    """A restored/copied store has no writer.lock: the refusal must not even create the lease file."""
    semantic_store(tmp_path / "source")
    copy = tmp_path / "copy"
    copy.mkdir()
    shutil.copy2(tmp_path / "source" / "store.sqlite3", copy / "store.sqlite3")
    before = snapshot(copy)
    assert sorted(before) == ["store.sqlite3"]
    assert refused(lexical_config(copy)) == "store_profile_mismatch"
    assert_unchanged(copy, before)


def test_historical_stores_without_marker_are_semantic_and_refused(tmp_path):
    # (a) schema-only file: no metadata row at all.
    SQLiteStore(tmp_path / "bare").close()
    # (b) store opened by a 4.0.x engine with deferred retrieval: metadata exists, no encoder trace, no marker.
    with MemoryV4(config=Config(data_dir=str(tmp_path / "deferred"), store_backend="sqlite_s1"), defer_retrieval=True):
        pass
    for name in ("bare", "deferred"):
        directory = tmp_path / name
        before = snapshot(directory)
        connection = sqlite3.connect(f"file:{directory / 'store.sqlite3'}?mode=ro&immutable=1", uri=True)
        try:
            row = connection.execute("SELECT value_json FROM store_metadata WHERE key='metadata'").fetchone()
        finally:
            connection.close()
        assert row is None or "retrieval_profile" not in json.loads(row[0])
        assert refused(lexical_config(directory)) == "store_profile_mismatch", name
        assert_unchanged(directory, before)


def test_semantic_store_with_a_live_wal_is_refused_without_touching_database_or_wal(tmp_path):
    """Copy taken while a writer is open: -wal/-shm exist. Main file and WAL stay byte-identical."""
    with MemoryV4(config=semantic_config(tmp_path / "live")) as engine:
        engine.add_episodes_bulk([dict(spec) for spec in CORPUS])
        hot = tmp_path / "hot"
        shutil.copytree(tmp_path / "live", hot)
    before = snapshot(hot)
    assert {"store.sqlite3", "store.sqlite3-wal", "store.sqlite3-shm"} <= set(before)
    code = refused(lexical_config(hot))
    assert code in {"store_profile_mismatch", "store_profile_unverifiable"}
    # -shm is transient reader bookkeeping; the durable files may not change by one byte.
    assert_unchanged(hot, before, volatile=("store.sqlite3-shm",))


# ------------------------------------------------ lexical store, semantic open
def test_lexical_store_is_refused_in_semantic_before_any_mutation(tmp_path):
    before = lexical_store(tmp_path)
    assert refused(semantic_config(tmp_path)) == "store_profile_mismatch"
    assert_unchanged(tmp_path, before)
    # Default (Granite) configuration, deferred retrieval and dense-disabled variants are refused alike.
    assert refused(Config(data_dir=str(tmp_path), store_backend="sqlite_s1"), defer_retrieval=True) == (
        "store_profile_mismatch")
    assert refused(semantic_config(tmp_path, use_dense=False)) == "store_profile_mismatch"
    assert refused(semantic_config(tmp_path, work_state=True)) == "store_profile_mismatch"
    assert_unchanged(tmp_path, before)
    with MemoryV4(config=lexical_config(tmp_path)) as engine:
        assert engine.store.meta["retrieval_profile"] == MARKER
        assert not {"embed_model", "dim", "embed_compute_profile"} & set(engine.store.meta)
        assert engine.store._connection.execute("SELECT count(*) FROM embedding_cache").fetchone()[0] == 0
        assert [hit.item.id for hit in engine.search("cat Pixel", top_k=3, project="alpha")][0] == "g1"


def test_lexical_marker_with_another_policy_is_refused_unchanged(tmp_path):
    lexical_store(tmp_path)
    rewrite_metadata(tmp_path, lambda meta, _connection: meta.__setitem__(
        "retrieval_profile", {"name": "lexical", "policy": "lex-9.9.9-v1"}))
    before = snapshot(tmp_path)
    assert refused(lexical_config(tmp_path)) == "store_profile_mismatch"
    assert refused(semantic_config(tmp_path)) == "store_profile_mismatch"
    assert_unchanged(tmp_path, before)


@pytest.mark.parametrize("marker", ["lexical", {"name": "lexical"}, {"name": "semantic", "policy": POLICY}, 7, None])
def test_malformed_marker_is_never_interpreted(tmp_path, marker):
    lexical_store(tmp_path)
    rewrite_metadata(tmp_path, lambda meta, _connection: meta.__setitem__("retrieval_profile", marker))
    before = snapshot(tmp_path)
    assert refused(lexical_config(tmp_path)) == "store_profile_mismatch"
    assert refused(semantic_config(tmp_path)) == "store_profile_mismatch"
    assert_unchanged(tmp_path, before)


def test_lexical_store_contaminated_by_a_4_0_x_runtime_is_refused_by_both_profiles(tmp_path):
    """Rollback hazard: a 4.0.x runtime ignores the marker, embeds the corpus and stamps encoder metadata.

    4.1.0 never repairs or adopts that state implicitly: restore a backup or rebuild explicitly.
    """
    lexical_store(tmp_path)

    def contaminate(meta, connection):
        meta.update(embed_model="hash", dim=16, version="4.0.9", embed_compute_profile={
            "device": "hash", "dtype": "native", "batch_size": 32, "max_seq_length": 512,
            "parameter_dtypes": ["hash-float32"], "parameter_devices": ["hash"], "model_revision": "hash-v1"})
        blob = b"\0" * 64
        connection.execute("INSERT INTO embedding_cache VALUES(?,?,?,?)",
                           ("0" * 64, 16, blob, hashlib.sha256(blob).hexdigest()))

    rewrite_metadata(tmp_path, contaminate)
    before = snapshot(tmp_path)
    assert refused(lexical_config(tmp_path)) == "store_profile_contaminated"
    assert refused(semantic_config(tmp_path)) == "store_profile_contaminated"
    assert_unchanged(tmp_path, before)


def test_refused_configuration_never_creates_a_directory_or_a_store(tmp_path):
    target = tmp_path / "missing"
    assert refused(lexical_config(target, rerank_model="some/reranker")) == "profile_option_conflict"
    assert not target.exists()
    empty = tmp_path / "empty"
    empty.mkdir()
    assert refused(lexical_config(empty, use_dense=True)) == "profile_option_conflict"
    assert list(empty.iterdir()) == []

"""Profile guard 4.1.0 (contract docs/LEXICAL_PROFILE_4_1_0.md §4-§5) -- written first.

RED UNTIL STEPS 4-7. The profile probe must refuse a store of the other
profile, a contaminated store or a store whose profile cannot be determined
BEFORE any mutating open: no WAL pragma, no migration, no metadata write, no
writer lease file. Every refusal is checked on the exact bytes and sha256 of
every file of the store directory. A store without marker is a historical
semantic store. No implicit conversion in either direction. The probe runs a
second time under the writer lease, before ``SQLiteStore`` is constructed.

Needs the semantic closure (FAISS): several cases build a semantic hash store.
Not part of the ML-free subset.
"""

import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

import pytest

import memory_os_v4
from memory_os_v4 import Config, MemoryV4
from memory_os_v4.sqlite_store import SQLiteStore
from memory_os_v4.writer_lock import WriterLock

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
    # A complete -wal/-shm pair is probed with mode=ro (no immutable): the verdict is exact.
    assert refused(lexical_config(hot)) == "store_profile_mismatch"
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


# ------------------------------------------ profile that cannot be determined
def bare_store(path, document=None):
    """Schema-8 file built without any engine; optional raw metadata document (text, stored as is)."""
    SQLiteStore(path).close()
    if document is not None:
        connection = sqlite3.connect(Path(path) / "store.sqlite3")
        try:
            connection.execute("INSERT INTO store_metadata VALUES('metadata',?)", (document,))
            connection.commit()
        finally:
            connection.close()
    assert sorted(item.name for item in Path(path).iterdir()) == ["store.sqlite3"]


def make_garbage(path):
    path.mkdir()
    (path / "store.sqlite3").write_bytes(b"this is not a SQLite database\n" * 200)


@pytest.mark.parametrize("build", [
    make_garbage,
    lambda path: bare_store(path, "[1, 2]"),  # metadata is not an object
    lambda path: bare_store(path, "{broken"),  # metadata is not JSON
    lambda path: bare_store(path, "null"),
], ids=["not_a_database", "metadata_not_an_object", "metadata_not_json", "metadata_null"])
def test_undeterminable_store_is_refused_by_both_profiles(tmp_path, build):
    """An existing file whose profile cannot be read is never handed to the mutating open, in ANY profile."""
    target = tmp_path / "store"
    build(target)
    before = snapshot(target)
    assert refused(lexical_config(target)) == "store_profile_unverifiable"
    assert refused(semantic_config(target)) == "store_profile_unverifiable"
    assert refused(Config(data_dir=str(target), store_backend="sqlite_s1"), defer_retrieval=True) == (
        "store_profile_unverifiable")
    assert_unchanged(target, before)  # no writer.lock, no -wal, no -shm


@pytest.mark.parametrize("kept", ["store.sqlite3-wal", "store.sqlite3-shm"])
def test_isolated_wal_or_shm_is_refused_by_both_profiles(tmp_path, kept):
    """An incomplete -wal/-shm pair cannot be probed without writing: refusal, files untouched."""
    with MemoryV4(config=semantic_config(tmp_path / "live")) as engine:
        engine.add_episodes_bulk([dict(spec) for spec in CORPUS])
        hot = tmp_path / "hot"
        shutil.copytree(tmp_path / "live", hot)
    removed = ({"store.sqlite3-wal", "store.sqlite3-shm"} - {kept}).pop()
    (hot / removed).unlink()
    before = snapshot(hot)
    assert kept in before and removed not in before
    assert refused(lexical_config(hot)) == "store_profile_unverifiable"
    assert refused(semantic_config(hot)) == "store_profile_unverifiable"
    assert_unchanged(hot, before)  # strict: the surviving side file is not touched either


@pytest.mark.parametrize("orphan", ["store.sqlite3-wal", "store.sqlite3-shm"])
def test_side_file_without_a_database_is_refused_by_both_profiles(tmp_path, orphan):
    """No store.sqlite3 but a leftover side file: not a fresh directory, nothing is created over it."""
    target = tmp_path / "store"
    target.mkdir()
    (target / orphan).write_bytes(b"leftover" * 64)
    before = snapshot(target)
    assert refused(lexical_config(target)) == "store_profile_unverifiable"
    assert refused(semantic_config(target)) == "store_profile_unverifiable"
    assert_unchanged(target, before)


def test_marker_present_only_in_the_wal_is_seen_by_the_probe(tmp_path):
    """Hot copy of a marked store whose metadata never reached the main file: mode=ro reads the WAL."""
    source = tmp_path / "source"
    bare_store(source)
    document = json.dumps({"retrieval_profile": MARKER, "store_instance_id": "f" * 32, "version": "4.1.0"},
                          sort_keys=True, separators=(",", ":"))
    writer = sqlite3.connect(source / "store.sqlite3")
    try:
        assert writer.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("INSERT INTO store_metadata VALUES('metadata',?)", (document,))
        writer.commit()
        hot = tmp_path / "hot"
        shutil.copytree(source, hot)  # taken while the writer is open: -wal and -shm are live
    finally:
        writer.close()
    before = snapshot(hot)
    assert {"store.sqlite3", "store.sqlite3-wal", "store.sqlite3-shm"} == set(before)
    alone = tmp_path / "main-file-only"
    alone.mkdir()
    shutil.copy2(hot / "store.sqlite3", alone / "store.sqlite3")
    connection = sqlite3.connect(f"file:{alone / 'store.sqlite3'}?mode=ro&immutable=1", uri=True)
    try:  # precondition: the main file alone carries no marker
        assert connection.execute("SELECT value_json FROM store_metadata WHERE key='metadata'").fetchone() is None
    finally:
        connection.close()
    assert refused(semantic_config(hot)) == "store_profile_mismatch"
    assert refused(Config(data_dir=str(hot), store_backend="sqlite_s1"), defer_retrieval=True) == (
        "store_profile_mismatch")
    assert_unchanged(hot, before, volatile=("store.sqlite3-shm",))  # transient -shm excluded from the guarantee
    with MemoryV4(config=lexical_config(hot)) as engine:  # the right profile recovers the WAL and opens it
        assert engine.store.meta["retrieval_profile"] == MARKER
        assert engine.store.meta["store_instance_id"] == "f" * 32


# -------------------------------------------------- contamination, trace by trace
def add_cache_row(_meta, connection):
    blob = b"\0" * 64
    connection.execute("INSERT INTO embedding_cache VALUES(?,?,?,?)",
                       ("0" * 64, 16, blob, hashlib.sha256(blob).hexdigest()))


TRACES = {
    "embed_model": lambda meta, _connection: meta.__setitem__("embed_model", "hash"),
    "dim": lambda meta, _connection: meta.__setitem__("dim", 16),
    "embed_compute_profile": lambda meta, _connection: meta.__setitem__(
        "embed_compute_profile", {"device": "hash", "model_revision": "hash-v1"}),
    "embedding_cache_row": add_cache_row,
    "version_4_0_9": lambda meta, _connection: meta.__setitem__("version", "4.0.9"),
    "version_absent": lambda meta, _connection: meta.pop("version"),
    "version_not_a_string": lambda meta, _connection: meta.__setitem__("version", 410),
    "version_two_segments": lambda meta, _connection: meta.__setitem__("version", "4.1"),
    "version_with_suffix": lambda meta, _connection: meta.__setitem__("version", "4.1.0.dev1"),
    "version_garbage": lambda meta, _connection: meta.__setitem__("version", "banana"),
}


@pytest.mark.parametrize("trace", sorted(TRACES))
def test_each_semantic_trace_alone_contaminates_a_marked_store(tmp_path, trace):
    """One trace is enough; a version below 4.1.0, absent or malformed is refused conservatively."""
    lexical_store(tmp_path)
    connection = sqlite3.connect(f"file:{tmp_path / 'store.sqlite3'}?mode=ro&immutable=1", uri=True)
    try:
        clean = json.loads(connection.execute(
            "SELECT value_json FROM store_metadata WHERE key='metadata'").fetchone()[0])
    finally:
        connection.close()
    assert clean["retrieval_profile"] == MARKER and clean["version"] == memory_os_v4.__version__
    rewrite_metadata(tmp_path, TRACES[trace])
    before = snapshot(tmp_path)
    assert refused(lexical_config(tmp_path)) == "store_profile_contaminated"
    assert refused(semantic_config(tmp_path)) == "store_profile_contaminated"
    assert_unchanged(tmp_path, before)


@pytest.mark.parametrize("version", ["4.1.0", "4.1.7", "4.10.0", "5.0.0"])
def test_version_is_compared_as_an_integer_triple_not_as_text(tmp_path, version):
    """A later release stamp (4.10.0 > 4.1.0 numerically) is not a contamination trace."""
    lexical_store(tmp_path)
    rewrite_metadata(tmp_path, lambda meta, _connection: meta.__setitem__("version", version))
    with MemoryV4(config=lexical_config(tmp_path)) as engine:
        assert engine.store.meta["retrieval_profile"] == MARKER
        assert [hit.item.id for hit in engine.search("cat Pixel", top_k=3, project="alpha")][0] == "g1"


def test_fresh_lexical_store_is_stamped_with_marker_and_version_before_any_write(tmp_path):
    """Marker, store_instance_id and version are created together: no clean marked store lacks a version."""
    with MemoryV4(config=lexical_config(tmp_path)) as engine:
        connection = sqlite3.connect(f"file:{tmp_path / 'store.sqlite3'}?mode=ro", uri=True)
        try:  # read by another connection while the engine has written nothing else
            stored = json.loads(connection.execute(
                "SELECT value_json FROM store_metadata WHERE key='metadata'").fetchone()[0])
        finally:
            connection.close()
        assert stored == {"retrieval_profile": MARKER, "store_instance_id": engine.store.meta["store_instance_id"],
                          "version": memory_os_v4.__version__}


# ------------------------------------------- second probe, under the writer lease
def raced_open(monkeypatch, config, race):
    """Run ``race()`` after the first probe, just before the writer lease is taken; return the refusal code."""
    original = WriterLock.__init__
    state = {"calls": 0}

    def racing(self, data_dir):
        state["calls"] += 1
        if state["calls"] == 1:
            race()
            state["after_race"] = snapshot(data_dir)
        original(self, data_dir)

    monkeypatch.setattr(WriterLock, "__init__", racing)
    try:
        code = refused(config)
    finally:
        monkeypatch.setattr(WriterLock, "__init__", original)
    assert state["calls"] == 1
    return code, state["after_race"]


def assert_only_the_lease_file_may_be_new(directory, after_race):
    """Guarantee on a race: the database and its side files are untouched; writer.lock alone may appear."""
    after = snapshot(directory)
    assert set(after) - set(after_race) <= {"writer.lock"}
    assert not (Path(directory) / "store.sqlite3-wal").exists()
    assert not (Path(directory) / "store.sqlite3-shm").exists()
    for name, (data, digest, mtime) in after_race.items():
        if name != "writer.lock":
            assert after[name][0] == data and after[name][1] == digest and after[name][2] == mtime, name
    WriterLock(Path(directory)).close()  # the refused open released its lease


def contaminate_keeping_the_marker(meta, connection):
    meta.update(embed_model="hash", dim=16)
    add_cache_row(meta, connection)


def test_race_contamination_with_an_unchanged_marker_is_refused_before_the_mutating_open(tmp_path, monkeypatch):
    """A 4.0.x runtime contaminates the store between the two probes; the marker itself does not change."""
    lexical_store(tmp_path)
    code, after_race = raced_open(monkeypatch, lexical_config(tmp_path),
                                  lambda: rewrite_metadata(tmp_path, contaminate_keeping_the_marker))
    assert code == "store_profile_contaminated"
    assert_only_the_lease_file_may_be_new(tmp_path, after_race)


def test_race_replacing_the_store_identity_is_refused_before_the_mutating_open(tmp_path, monkeypatch):
    """Same valid marker, clean, but another store_instance_id: not the store that was probed."""
    lexical_store(tmp_path / "probed")
    lexical_store(tmp_path / "other")

    def swap():
        shutil.copy2(tmp_path / "other" / "store.sqlite3", tmp_path / "probed" / "store.sqlite3")

    code, after_race = raced_open(monkeypatch, lexical_config(tmp_path / "probed"), swap)
    assert code == "store_profile_unverifiable"
    assert_only_the_lease_file_may_be_new(tmp_path / "probed", after_race)


def test_race_changing_the_profile_is_refused_before_the_mutating_open(tmp_path, monkeypatch):
    lexical_store(tmp_path / "lexical")
    semantic_store(tmp_path / "semantic")
    # (a) lexical open: the marker disappears between the probes.
    code, after_race = raced_open(monkeypatch, lexical_config(tmp_path / "lexical"), lambda: rewrite_metadata(
        tmp_path / "lexical", lambda meta, _connection: meta.pop("retrieval_profile")))
    assert code == "store_profile_mismatch"
    assert_only_the_lease_file_may_be_new(tmp_path / "lexical", after_race)
    # (b) semantic open: a marked lexical store replaces the semantic one between the probes.
    lexical_store(tmp_path / "donor")
    code, after_race = raced_open(monkeypatch, semantic_config(tmp_path / "semantic"), lambda: shutil.copy2(
        tmp_path / "donor" / "store.sqlite3", tmp_path / "semantic" / "store.sqlite3"))
    assert code == "store_profile_mismatch"
    assert_only_the_lease_file_may_be_new(tmp_path / "semantic", after_race)


def test_race_creating_a_store_where_none_was_probed_is_refused(tmp_path, monkeypatch):
    """First probe: no file (new store). Under the lease a file exists: never adopted, never rewritten."""
    lexical_store(tmp_path / "donor")
    target = tmp_path / "target"

    def appear():
        shutil.copy2(tmp_path / "donor" / "store.sqlite3", target / "store.sqlite3")

    code, after_race = raced_open(monkeypatch, lexical_config(target), appear)
    assert code == "store_profile_unverifiable"
    assert sorted(after_race) == ["store.sqlite3"]
    assert_only_the_lease_file_may_be_new(target, after_race)


# ------------------------------------------------ creation interrupted before marking
def test_creation_interrupted_before_marking_leaves_an_unmarked_store_that_lexical_refuses(tmp_path, monkeypatch):
    """Crash window between schema creation and the marker write: the empty file is unmarked, hence
    semantic by rule; the lexical profile refuses it untouched (remedy: remove the empty directory)."""
    def crash(self, meta):
        raise RuntimeError("interrupted before the first metadata write")

    with monkeypatch.context() as patch:
        patch.setattr(SQLiteStore, "save_meta", crash)
        with pytest.raises(RuntimeError, match="interrupted before the first metadata write"):
            MemoryV4(config=lexical_config(tmp_path))
    before = snapshot(tmp_path)
    assert sorted(before) == ["store.sqlite3", "writer.lock"]
    connection = sqlite3.connect(f"file:{tmp_path / 'store.sqlite3'}?mode=ro&immutable=1", uri=True)
    try:
        assert connection.execute("SELECT value_json FROM store_metadata WHERE key='metadata'").fetchone() is None
        assert connection.execute("SELECT count(*) FROM items").fetchone()[0] == 0
    finally:
        connection.close()
    assert refused(lexical_config(tmp_path)) == "store_profile_mismatch"
    assert_unchanged(tmp_path, before)
    WriterLock(tmp_path).close()  # the failed creation released its lease

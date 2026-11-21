"""Engine finalizer 4.1.0 (contract docs/LEXICAL_PROFILE_4_1_0.md §5.1, §17).

The automatic finalizer (``engine._writer_finalizer``: garbage collection, interpreter exit) must
close the engine's SQLite connection BEFORE it releases the writer lease, exactly like the
explicit ``close()``. Otherwise the next engine takes the lease while the previous connection is
still open, observes the -wal/-shm pair in probe 2, and its ``mode=ro`` open can recreate the side
files that the late destruction of that connection has just deleted.

Needs the semantic closure (FAISS) for the semantic-hash cases. Not part of the ML-free subset.
"""

import gc
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

import memory_os_v4
from memory_os_v4 import Config, MemoryV4
from memory_os_v4.sqlite_store import SQLiteStore
from memory_os_v4.writer_lock import WriterLock

SIDE_FILES = {"store.sqlite3-wal", "store.sqlite3-shm"}
CORPUS = [
    {"text": "Alice Martin adopted a grey cat named Pixel.", "item_id": "g1", "project": "alpha"},
    {"text": "The budget review moved to Thursday afternoon.", "item_id": "g2", "project": "alpha"},
]


def lexical_config(path):
    return Config(data_dir=str(path), retrieval_profile="lexical", store_backend="sqlite_s1", use_dense=False)


def semantic_config(path):
    return Config(data_dir=str(path), store_backend="sqlite_s1", embed_model="hash", hash_dim=16)


CONFIGS = {"lexical": lexical_config, "semantic": semantic_config}


def names(directory):
    return {entry.name for entry in Path(directory).iterdir()}


def connection_closed(connection):
    try:
        connection.execute("SELECT 1")
    except sqlite3.ProgrammingError:
        return True
    return False


def instrument(monkeypatch, directory):
    """Record, in order, each store close and each lease release with the state seen at that instant.

    Patched on the classes BEFORE the engine exists, so that a finalizer holding a bound method
    captured at construction is observed too.
    """
    events, connections = [], []
    real_init, real_store_close, real_release = SQLiteStore.__init__, SQLiteStore.close, WriterLock.close

    def init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        connections.append(self._connection)

    def store_close(self):
        real_store_close(self)
        events.append(("store_closed", connection_closed(self._connection)))

    def release(self):
        if self._file is not None:  # the state at the instant the lease is given up
            events.append(("lease_released", [connection_closed(connection) for connection in connections],
                           sorted(SIDE_FILES & names(directory))))
        real_release(self)

    monkeypatch.setattr(SQLiteStore, "__init__", init)

    monkeypatch.setattr(SQLiteStore, "close", store_close)
    monkeypatch.setattr(WriterLock, "close", release)
    return events


def written_engine(profile, directory):
    engine = MemoryV4(config=CONFIGS[profile](directory))
    engine.add_episodes_bulk([dict(spec) for spec in CORPUS])
    assert SIDE_FILES <= names(directory)  # live writer: the pair exists
    return engine


@pytest.mark.parametrize("profile", ["lexical", "semantic"])
def test_finalizer_closes_the_store_before_releasing_the_lease(tmp_path, monkeypatch, profile):
    events = instrument(monkeypatch, tmp_path)
    engine = written_engine(profile, tmp_path)
    lock = engine._writer_lock
    engine._writer_finalizer()  # the automatic path, WITHOUT close()

    # At the release: the engine's only connection is already closed, its -wal/-shm are gone.
    assert events == [("store_closed", True), ("lease_released", [True], [])]
    assert lock._file is None and not engine._writer_finalizer.alive
    before = len(events)
    engine.close()
    engine.close()
    engine._writer_finalizer()
    assert events[before:] == []  # nothing closed or released twice
    with pytest.raises(RuntimeError, match="closed"):
        engine.search("cat", project="alpha")
    with MemoryV4(config=CONFIGS[profile](tmp_path)) as reopened:  # the lease is free, the store intact
        assert reopened.store.get("g1") is not None
    assert not SIDE_FILES & names(tmp_path)


@pytest.mark.parametrize("profile", ["lexical", "semantic"])
def test_collected_engine_closes_the_store_before_releasing_the_lease(tmp_path, monkeypatch, profile):
    """The engine is a reference cycle: only the cyclic collector runs its finalizer."""
    events = instrument(monkeypatch, tmp_path)

    def abandon():
        written_engine(profile, tmp_path)

    abandon()
    gc.collect()
    assert events == [("store_closed", True), ("lease_released", [True], [])]
    WriterLock(tmp_path).close()


@pytest.mark.parametrize("profile", ["lexical", "semantic"])
def test_explicit_close_then_finalizer_is_idempotent(tmp_path, monkeypatch, profile):
    events = instrument(monkeypatch, tmp_path)
    engine = written_engine(profile, tmp_path)
    engine.close()
    assert events[-2:] == [("store_closed", True), ("lease_released", [True], [])]
    assert [event[0] for event in events].count("lease_released") == 1
    before = len(events)
    engine._writer_finalizer()
    engine.close()
    assert events[before:] == []


def test_failing_store_close_still_releases_the_lease_after_the_attempt(tmp_path, monkeypatch):
    engine = written_engine("lexical", tmp_path)
    order, real_store_close, real_release = [], SQLiteStore.close, WriterLock.close

    def failing(self):
        order.append("store_close_attempt")
        raise sqlite3.OperationalError("injected close failure")

    def release(self):
        if self._file is not None:
            order.append("lease_released")
        real_release(self)

    monkeypatch.setattr(SQLiteStore, "close", failing)
    monkeypatch.setattr(WriterLock, "close", release)
    with pytest.raises(sqlite3.OperationalError, match="injected"):
        engine._writer_finalizer()
    assert order == ["store_close_attempt", "lease_released"]
    monkeypatch.setattr(SQLiteStore, "close", real_store_close)
    engine.store.close()
    WriterLock(tmp_path).close()  # never held forever


def test_failed_open_closes_the_store_before_releasing_the_lease(tmp_path, monkeypatch):
    events = instrument(monkeypatch, tmp_path)

    def crash(self, meta):
        raise OSError("injected")

    monkeypatch.setattr(SQLiteStore, "save_meta", crash)
    with pytest.raises(OSError, match="injected"):
        MemoryV4(config=lexical_config(tmp_path))
    assert ("store_closed", True) in events and events[-1] == ("lease_released", [True], [])
    assert [event[0] for event in events].count("lease_released") == 1


def test_finalizer_never_references_the_engine_and_a_fork_closes_nothing():
    from memory_os_v4.memory import _release_engine

    calls = []

    class Fake:
        def __init__(self, name):
            self.name = name

        def close(self):
            calls.append(self.name)

    _release_engine(os.getpid(), [Fake("store")], Fake("lease"))
    assert calls == ["store", "lease"]
    calls.clear()
    # Another pid owns the engine (fork): the inherited connection is not ours to close; the
    # lease object decides by itself (WriterLock.close never unlocks from a fork).
    _release_engine(os.getpid() + 1, [Fake("store")], Fake("lease"))
    assert calls == ["lease"]


# ------------------------------------------------------------------ two processes
WRITER = r"""
import json, os, sqlite3, sys
from pathlib import Path
from memory_os_v4 import Config, MemoryV4
from memory_os_v4.sqlite_store import SQLiteStore
from memory_os_v4.writer_lock import WriterLock

directory, log, profile = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
SIDE = {"store.sqlite3-wal", "store.sqlite3-shm"}
state = {}


def record(**event):
    fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, (json.dumps(event, sort_keys=True) + "\n").encode())
        os.fsync(fd)
    finally:
        os.close(fd)


def closed(connection):
    try:
        connection.execute("SELECT 1")
    except sqlite3.ProgrammingError:
        return True
    return False


def side():
    return sorted(SIDE & {entry.name for entry in directory.iterdir()})


real_store_close, real_release = SQLiteStore.close, WriterLock.close


def store_close(self):
    real_store_close(self)
    record(event="store_closed", connection_closed=closed(self._connection), side_files=side())


def release(self):
    held = self._file is not None
    real_release(self)
    if held:
        # The lease is free and this process is still alive: the instant process B is started.
        record(event="lease_released", connection_closed=closed(state["connection"]), side_files=side())
        os.write(1, b"lease_released\n")
        os.read(0, 1)  # until the parent has run process B to completion
        record(event="resumed")


SQLiteStore.close = store_close
WriterLock.close = release
if profile == "lexical":
    config = Config(data_dir=str(directory), retrieval_profile="lexical", store_backend="sqlite_s1", use_dense=False)
else:
    config = Config(data_dir=str(directory), store_backend="sqlite_s1", embed_model="hash", hash_dim=16)
engine = MemoryV4(config=config)
engine.add_episodes_bulk([{"text": "Alice Martin adopted a grey cat named Pixel.", "item_id": "g1",
                           "project": "alpha"}])
state["connection"] = engine.store._connection
record(event="written", side_files=side())
# Normal interpreter exit, WITHOUT close(): only the automatic finalizer runs.
"""

OPENER = r"""
import json, sys
from memory_os_v4 import Config, MemoryV4

directory, profile = sys.argv[1], sys.argv[2]
if profile == "lexical":
    config = Config(data_dir=directory, retrieval_profile="lexical", store_backend="sqlite_s1", use_dense=False)
else:
    config = Config(data_dir=directory, store_backend="sqlite_s1", embed_model="hash", hash_dim=16)
try:
    with MemoryV4(config=config) as engine:
        print(json.dumps({"opened": engine.store.get("g1") is not None}))
except ValueError as refusal:
    print(json.dumps({"refused": getattr(refusal, "code", None)}))
"""


def inventory(directory):
    """name -> (inode, mtime_ns, size): detects a created, replaced or rewritten file."""
    return {entry.name: (entry.stat().st_ino, entry.stat().st_mtime_ns, entry.stat().st_size)
            for entry in sorted(Path(directory).iterdir())}


def child_env():
    source_root = str(Path(memory_os_v4.__file__).resolve().parents[1])
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
           "PYTHONPATH": os.pathsep.join([source_root, os.environ.get("PYTHONPATH", "")]).rstrip(os.pathsep)}
    for name in [key for key in env if key.startswith("MEMORY_V4_")]:
        del env[name]
    return env


@pytest.mark.parametrize("profile", ["lexical", "semantic"])
def test_writer_exiting_without_close_never_frees_the_lease_before_its_connection(tmp_path, profile):
    """Process A writes and exits WITHOUT close(). At the exact instant its lease is free (A still
    alive, blocked inside the release), process B probes then opens the same store under the lease.
    A's connection must already be closed and its -wal/-shm gone: B's probe creates no side file."""
    store, log = tmp_path / "store", tmp_path / "events.jsonl"
    writer_script, opener_script = tmp_path / "writer.py", tmp_path / "opener.py"
    writer_script.write_text(WRITER, encoding="utf-8")
    opener_script.write_text(OPENER, encoding="utf-8")
    env, other = child_env(), {"lexical": "semantic", "semantic": "lexical"}[profile]

    def opener(requested):
        done = subprocess.run([sys.executable, "-B", str(opener_script), str(store), requested], env=env,
                              cwd=tmp_path, capture_output=True, text=True, timeout=120)
        assert done.returncode == 0, done.stderr[-2000:]
        return json.loads(done.stdout.strip().splitlines()[-1])

    writer = subprocess.Popen([sys.executable, "-B", str(writer_script), str(store), str(log), profile], env=env,
                              cwd=tmp_path, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert writer.stdout.readline() == b"lease_released\n", writer.stderr.read().decode()[-2000:]
        assert writer.poll() is None  # A is alive: its lease is free, its interpreter has not exited
        at_release = inventory(store)
        assert not SIDE_FILES & set(at_release)
        # B's probe alone: the other profile is refused by probe 2, under the lease.
        assert opener(other) == {"refused": "store_profile_mismatch"}
        assert inventory(store) == at_release  # nothing created, replaced or rewritten
        # B opens the store for good under the lease, then closes.
        assert opener(profile) == {"opened": True}
        after_open = inventory(store)
        assert not SIDE_FILES & set(after_open)
    finally:
        try:
            _, errors = writer.communicate(input=b"\n", timeout=120)
        finally:
            writer.kill()
    assert writer.returncode == 0, errors.decode()[-2000:]
    assert inventory(store) == after_open  # A's late exit touched nothing
    events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert events == [
        {"event": "written", "side_files": sorted(SIDE_FILES)},
        {"event": "store_closed", "connection_closed": True, "side_files": []},
        {"event": "lease_released", "connection_closed": True, "side_files": []},
        {"event": "resumed"},
    ]

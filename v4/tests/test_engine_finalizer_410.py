"""Engine finalizer 4.1.0 (contract docs/LEXICAL_PROFILE_4_1_0.md §5.1, §17).

The automatic finalizer (``engine._writer_finalizer``: garbage collection, interpreter exit) must
close the engine's SQLite connection BEFORE it releases the writer lease, exactly like the
explicit ``close()``. Otherwise the next engine takes the lease while the previous connection is
still open, observes the -wal/-shm pair in probe 2, and its ``mode=ro`` open can recreate the side
files that the late destruction of that connection has just deleted.

And the lease is released ONLY once that connection is effectively closed: a close that fails,
or returns with the connection still open, keeps the exclusion (proved from another process)
until a later ``close()`` succeeds.

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
from memory_os_v4 import memory as memory_module
from memory_os_v4.memory import StoreCloseError
from memory_os_v4.sqlite_store import SQLiteStore
from memory_os_v4 import sqlite_store as sqlite_store_module
from memory_os_v4 import writer_lock as writer_lock_module
from memory_os_v4.writer_lock import WriterConflictError, WriterLock

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


CONTENDER = r"""
import json, sys
from pathlib import Path
from memory_os_v4 import Config, MemoryV4
from memory_os_v4.writer_lock import WriterConflictError, WriterLock

directory, profile, seen = Path(sys.argv[1]), sys.argv[2], {}
try:
    lease = WriterLock(directory)
except WriterConflictError:
    seen["lease"] = "held"
else:
    lease.close()
    seen["lease"] = "free"
if profile == "lexical":
    config = Config(data_dir=str(directory), retrieval_profile="lexical", store_backend="sqlite_s1",
                    use_dense=False)
else:
    config = Config(data_dir=str(directory), store_backend="sqlite_s1", embed_model="hash", hash_dim=16)
try:
    with MemoryV4(config=config) as engine:
        seen["engine"] = "opened" if engine.store.get("g1") is not None else "empty"
except WriterConflictError:
    seen["engine"] = "refused"
except ValueError as refusal:  # the lease was free: a store-profile refusal, under the lease
    seen["engine"] = getattr(refusal, "code", "error")
print(json.dumps(seen))
"""

HELD = {"lease": "held", "engine": "refused"}
FREE = {"lease": "free", "engine": "opened"}


def contender(directory, profile="lexical"):
    """ANOTHER PROCESS tries to take the writer lease (kernel flock), then to open an engine."""
    done = subprocess.run([sys.executable, "-B", "-c", CONTENDER, str(directory), profile], env=child_env(),
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def assert_exclusion_kept(directory, connection, profile="lexical"):
    """The property: the connection is still open, so the lease is still held, for every contender."""
    assert not connection_closed(connection)
    assert SIDE_FILES <= names(directory)  # the live connection still owns its -wal/-shm
    before = inventory(directory)
    assert contender(directory, profile) == HELD  # refused by the kernel lock, from another process
    with pytest.raises(WriterConflictError):  # and in this process
        MemoryV4(config=CONFIGS[profile](directory))
    assert inventory(directory) == before  # the refused contenders created or rewrote nothing


def break_close(monkeypatch, failure):
    """``raises``: close() fails and leaves the connection open. ``returns_open``: close() returns
    normally but the connection is still open (no error to trust: only the state proves closure)."""
    attempts = []

    def broken(self):
        attempts.append(self)
        if failure == "raises":
            raise sqlite3.OperationalError("injected close failure")

    monkeypatch.setattr(SQLiteStore, "close", broken)
    return attempts


FAILURES = {"raises": (sqlite3.OperationalError, "injected close failure"),
            "returns_open": (StoreCloseError, "still open")}


@pytest.mark.parametrize("profile", ["lexical", "semantic"])
@pytest.mark.parametrize("failure", sorted(FAILURES))
@pytest.mark.parametrize("path", ["close", "finalizer"])
def test_failing_store_close_keeps_the_lease_until_a_retry_closes_the_connection(
        tmp_path, monkeypatch, profile, failure, path):
    """(a) lease still held, proven by another process; (b) the failure propagates; (c) a later
    close() closes the connection THEN releases the lease, and another process can take it."""
    events = instrument(monkeypatch, tmp_path)
    engine = written_engine(profile, tmp_path)
    connection, lock = engine.store._connection, engine._writer_lock
    real_close = SQLiteStore.close  # the instrumented real close
    attempts = break_close(monkeypatch, failure)
    error, message = FAILURES[failure]

    with pytest.raises(error, match=message):  # (b)
        engine.close() if path == "close" else engine._writer_finalizer()
    assert len(attempts) == 1 and not engine._release.released and lock._file is not None
    assert lock._pinned_fd in writer_lock_module._pinned_fds  # held by the process, not by an object
    assert_exclusion_kept(tmp_path, connection, profile)  # (a)
    assert [event[0] for event in events] == []  # nothing closed, nothing released
    if path == "close":
        with pytest.raises(RuntimeError, match="closed"):  # closed for operations, lease still held
            engine.search("cat", project="alpha")

    with pytest.raises(error, match=message):  # the cause is still there: the retry fails the same way
        engine.close()
    assert len(attempts) == 2
    assert_exclusion_kept(tmp_path, connection, profile)
    assert events == []

    monkeypatch.setattr(SQLiteStore, "close", real_close)  # the cause is removed
    engine.close()  # (c) even though the weakref finalizer may already be consumed
    assert events == [("store_closed", True), ("lease_released", [True], [])]
    assert connection_closed(connection) and lock._file is None and engine._release.released
    assert lock._pinned_fd is None and not writer_lock_module._pinned_fds  # the pin went with the lease
    assert not SIDE_FILES & names(tmp_path)
    assert not memory_module._UNRELEASED
    assert contender(tmp_path, profile) == FREE
    before = len(events)
    engine.close()
    engine._writer_finalizer()
    assert events[before:] == []  # released once
    with pytest.raises(RuntimeError, match="closed"):
        engine.search("cat", project="alpha")


@pytest.mark.parametrize("failure", sorted(FAILURES))
def test_collected_engine_with_a_failing_close_keeps_the_lease(tmp_path, monkeypatch, failure):
    """(d) Garbage collection: nobody is left to retry, the lease stays held (until process exit)."""
    events = instrument(monkeypatch, tmp_path)
    connections, unraisable = [], []
    monkeypatch.setattr(sys, "unraisablehook", lambda info: unraisable.append(info.exc_value))

    def abandon():
        engine = written_engine("lexical", tmp_path)
        connections.append(engine.store._connection)

    real_close = SQLiteStore.close
    break_close(monkeypatch, failure)
    abandon()
    gc.collect()
    error, message = FAILURES[failure]
    assert len(unraisable) == 1 and isinstance(unraisable[0], error) and message in str(unraisable[0])
    assert events == []
    gc.collect()
    assert_exclusion_kept(tmp_path, connections[0])  # the engine is gone, its lease is not
    pending = [release for release in memory_module._UNRELEASED
               if release.writer_lock.path.parent == tmp_path]
    assert len(pending) == 1 and not pending[0].released and pending[0].writer_lock._file is not None

    monkeypatch.setattr(SQLiteStore, "close", real_close)
    pending[0]()  # test cleanup: the same release, once the cause is removed
    assert events == [("store_closed", True), ("lease_released", [True], [])]
    assert not memory_module._UNRELEASED
    assert contender(tmp_path) == FREE


def test_failed_open_with_a_failing_close_keeps_the_lease(tmp_path, monkeypatch):
    events = instrument(monkeypatch, tmp_path)
    connections, real_init, real_close = [], SQLiteStore.__init__, SQLiteStore.close

    def init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        connections.append(self._connection)

    def crash(self, meta):
        raise OSError("injected open failure")

    monkeypatch.setattr(SQLiteStore, "__init__", init)
    monkeypatch.setattr(SQLiteStore, "save_meta", crash)
    break_close(monkeypatch, "raises")
    with pytest.raises(sqlite3.OperationalError, match="injected close failure") as raised:
        MemoryV4(config=lexical_config(tmp_path))
    assert isinstance(raised.value.__context__, OSError)  # the open failure is not lost
    assert events == [] and not connection_closed(connections[0])
    assert contender(tmp_path) == HELD
    pending = list(memory_module._UNRELEASED)
    assert len(pending) == 1
    monkeypatch.setattr(SQLiteStore, "close", real_close)
    pending[0]()
    assert events == [("store_closed", True), ("lease_released", [True], [])]
    # The lease is free again; the half-created store itself is refused, as after any failed open.
    assert contender(tmp_path) == {"lease": "free", "engine": "store_profile_mismatch"}
    assert not memory_module._UNRELEASED


@pytest.mark.parametrize("fallback", ["fails", "succeeds"])
def test_store_constructor_failing_after_connect_is_covered_from_the_connection_on(
        tmp_path, monkeypatch, fallback):
    """SQLiteStore.__init__ fails AFTER sqlite3.connect, i.e. before the engine ever receives the
    store. If its own fallback ``connection.close()`` fails too, the connection is open and the
    engine must already know it: the lease is kept. (Before: unknown store, lease released.)"""
    broken, connections, real_connect = {"close": fallback == "fails"}, [], sqlite3.connect

    class Connection(sqlite3.Connection):
        def close(self):
            if broken["close"]:
                raise sqlite3.OperationalError("injected close failure")
            super().close()

    def connect(*args, **kwargs):
        if "check_same_thread" not in kwargs:  # the read-only probes: untouched
            return real_connect(*args, **kwargs)
        connections.append(real_connect(*args, factory=Connection, **kwargs))
        return connections[-1]

    def crash(store):
        raise OSError("injected initialization failure")

    monkeypatch.setattr(sqlite_store_module.sqlite3, "connect", connect)
    monkeypatch.setattr("memory_os_v4.changes.initialize", crash)  # inside SQLiteStore.__init__
    if fallback == "succeeds":
        with pytest.raises(OSError, match="injected initialization failure"):
            MemoryV4(config=lexical_config(tmp_path))
        assert len(connections) == 1 and connection_closed(connections[0])
        assert not memory_module._UNRELEASED and not writer_lock_module._pinned_fds
        assert contender(tmp_path)["lease"] == "free"
        return

    with pytest.raises(sqlite3.OperationalError, match="injected close failure") as raised:
        MemoryV4(config=lexical_config(tmp_path))
    chain, error = [], raised.value
    while error is not None:
        chain.append(str(error))
        error = error.__context__
    assert "injected initialization failure" in chain  # the cause of the failed open is not lost
    assert len(connections) == 1 and not connection_closed(connections[0])
    assert contender(tmp_path) == HELD  # another process: kernel lock refused, engine refused
    pending = list(memory_module._UNRELEASED)
    assert len(pending) == 1 and len(pending[0].stores) == 1 and not pending[0].released
    assert pending[0].stores[0]._connection is connections[0]
    assert pending[0].writer_lock._pinned_fd in writer_lock_module._pinned_fds
    with pytest.raises(sqlite3.OperationalError, match="injected close failure"):
        pending[0]()  # still failing: still held
    assert contender(tmp_path) == HELD

    broken["close"] = False  # the cause is removed: the same release closes, then frees the lease
    pending[0]()
    assert connection_closed(connections[0]) and pending[0].released
    assert not memory_module._UNRELEASED and not writer_lock_module._pinned_fds
    assert contender(tmp_path)["lease"] == "free"


PERSISTENT = r"""
import gc, json, os, sqlite3, sys, weakref
from pathlib import Path
from memory_os_v4 import Config, MemoryV4
from memory_os_v4 import memory as memory_module
from memory_os_v4.sqlite_store import SQLiteStore

directory, mutation = Path(sys.argv[1]), sys.argv[2]
SIDE = {"store.sqlite3-wal", "store.sqlite3-shm"}


def step(**event):
    event["side_files"] = sorted(SIDE & {entry.name for entry in directory.iterdir()})
    os.write(1, (json.dumps(event, sort_keys=True) + "\n").encode())
    os.read(0, 1)  # until the parent has looked at the lease from another process


def broken(self):
    raise sqlite3.OperationalError("injected close failure")


if mutation == "unpinned":  # the previous behaviour: the lease only held by Python references
    from memory_os_v4.writer_lock import WriterLock
    WriterLock.pin = lambda self: None
engine = MemoryV4(config=Config(data_dir=str(directory), retrieval_profile="lexical",
                                store_backend="sqlite_s1", use_dense=False))
engine.add_episodes_bulk([{"text": "Alice Martin adopted a grey cat named Pixel.", "item_id": "g1",
                           "project": "alpha"}])
connection, lock = engine.store._connection, weakref.ref(engine._writer_lock)
SQLiteStore.close = broken  # persistent: never repaired
try:
    engine.close()
except sqlite3.OperationalError:
    pass
step(event="close_failed", lock_alive=lock() is not None)
# What interpreter shutdown does, in the worst order: every Python reference to the lease goes
# first (module globals, the finalizer registry, the engine), the connection is destroyed later.
engine._writer_finalizer.detach()
memory_module._UNRELEASED.clear()
del engine
gc.collect()
step(event="lock_destroyed", lock_alive=lock() is not None)
del connection  # the late userland sqlite3_close: checkpoints and deletes -wal/-shm
gc.collect()
step(event="connection_destroyed", lock_alive=lock() is not None)
# Normal interpreter exit (not os._exit): only the end of the process frees the lease.
"""


def persistent_failure(directory, mutation):
    """Runs PERSISTENT; at each step, another process tries the lease. Returns (steps, exit code)."""
    seen = []
    child = subprocess.Popen([sys.executable, "-B", "-c", PERSISTENT, str(directory), mutation],
                             env=child_env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
    try:
        for _ in range(3):
            line = child.stdout.readline()
            assert line, child.stderr.read().decode()[-2000:]
            assert child.poll() is None  # the process is alive at each observation
            seen.append({**json.loads(line), "contender": contender(directory)})
            child.stdin.write(b"\n")
            child.stdin.flush()
        child.communicate(timeout=120)
    finally:
        child.kill()
    return seen, child.returncode


def test_persistent_close_failure_keeps_the_lease_until_the_process_is_gone(tmp_path):
    """The lease must not depend on the order in which interpreter shutdown destroys the lock and
    the connection. Here the lock object is destroyed FIRST and the connection closed late, while
    the process is alive: another process is refused at every step, and gets the lease only once
    the process has exited."""
    seen, code = persistent_failure(tmp_path, "pinned")
    assert code == 0
    assert seen == [
        {"event": "close_failed", "lock_alive": True, "side_files": sorted(SIDE_FILES), "contender": HELD},
        # No Python object holds the lease any more, the connection is open: still excluded.
        {"event": "lock_destroyed", "lock_alive": False, "side_files": sorted(SIDE_FILES), "contender": HELD},
        # The late close deleted the side files UNDER the lease: nobody could observe the pair.
        {"event": "connection_destroyed", "lock_alive": False, "side_files": [], "contender": HELD},
    ]
    assert contender(tmp_path) == FREE  # the process is gone: the kernel dropped the lease


def test_without_the_pinned_descriptor_the_lease_is_lost_with_the_lock_object(tmp_path):
    """Discrimination of the test above: with ``WriterLock.pin`` disabled (references only), the
    destruction of the lock object frees the lease while the connection and its pair still exist,
    and another process opens the store next to it."""
    seen, _ = persistent_failure(tmp_path, "unpinned")
    assert seen[0]["contender"] == HELD
    assert seen[1] == {"event": "lock_destroyed", "lock_alive": False, "side_files": sorted(SIDE_FILES),
                       "contender": FREE}


def test_partial_failure_keeps_the_lease_and_never_closes_a_store_twice():
    calls, pins = [], []

    class Fake:
        def __init__(self, name, broken=False):
            self.name, self.broken = name, broken

        def close(self):
            calls.append(self.name)
            if self.broken:
                raise OSError(f"{self.name} cannot close")

        def pin(self):
            pins.append(self.name)

    first, second, third, lease = Fake("first"), Fake("second", broken=True), Fake("third"), Fake("lease")
    stores = [first, second, third]
    release = memory_module._EngineRelease(os.getpid(), stores, lease)
    with pytest.raises(OSError, match="second cannot close"):
        release()
    assert calls == ["third", "second", "first"]  # every store is attempted, the lease is not released
    assert stores == [second] and not release.released and release in memory_module._UNRELEASED
    assert pins == ["lease"]  # the kept lease is pinned to the process
    with pytest.raises(OSError, match="second cannot close"):
        release()
    assert calls == ["third", "second", "first", "second"]  # only the pending store is retried
    second.broken = False
    release()
    assert calls == ["third", "second", "first", "second", "second", "lease"]
    assert stores == [] and release.released and release not in memory_module._UNRELEASED
    release()
    assert calls[-1] == "lease" and calls.count("lease") == 1

    # A forked child (another pid owns the engine) closes no store, even a failing one.
    calls.clear()
    forked = memory_module._EngineRelease(os.getpid() + 1, [Fake("store", broken=True)], Fake("lease"))
    forked()
    assert calls == ["lease"] and forked.released and forked not in memory_module._UNRELEASED
    assert pins == ["lease", "lease"]  # the two failures above; a fork pins nothing


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


FORKER = r"""
import fcntl, json, os, sqlite3, sys
from pathlib import Path
from memory_os_v4 import Config, MemoryV4

directory = Path(sys.argv[1])
SIDE = {"store.sqlite3-wal", "store.sqlite3-shm"}


def lease_held():
    with open(directory / "writer.lock", "a+b") as handle:  # a fresh open file description
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return False


def closed(connection):
    try:
        connection.execute("SELECT 1")
    except sqlite3.ProgrammingError:
        return True
    return False


engine = MemoryV4(config=Config(data_dir=str(directory), retrieval_profile="lexical",
                                store_backend="sqlite_s1", use_dense=False))
engine.add_episodes_bulk([{"text": "Alice Martin adopted a grey cat named Pixel.", "item_id": "g1",
                           "project": "alpha"}])
connection = engine.store._connection
pid = os.fork()
if pid == 0:
    status = 0
    try:
        try:
            engine.close()
        except RuntimeError:  # an inherited engine refuses to flush; the release still runs
            pass
        engine.close()
        if closed(connection) or not engine._release.released:
            status = 3
    except BaseException:
        status = 4
    os._exit(status)
child = os.waitpid(pid, 0)[1]
seen = {"child": os.waitstatus_to_exitcode(child), "connection_closed": closed(connection),
        "side_files": sorted(SIDE & {entry.name for entry in directory.iterdir()}),
        "lease_held": lease_held()}
engine.close()
seen["after_close"] = {"connection_closed": closed(connection), "lease_held": lease_held(),
                       "side_files": sorted(SIDE & {entry.name for entry in directory.iterdir()})}
print(json.dumps(seen))
"""


@pytest.mark.skipif(not hasattr(os, "fork"), reason="fork only")
def test_forked_child_closing_the_inherited_engine_closes_nothing_of_the_parent(tmp_path):
    done = subprocess.run([sys.executable, "-B", "-c", FORKER, str(tmp_path)], env=child_env(),
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]
    assert json.loads(done.stdout.strip().splitlines()[-1]) == {
        "child": 0, "connection_closed": False, "side_files": sorted(SIDE_FILES), "lease_held": True,
        "after_close": {"connection_closed": True, "lease_held": False, "side_files": []}}


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

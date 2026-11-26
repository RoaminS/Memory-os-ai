"""4.1.3 (NOTE_4_1_3_WAL_MODE_RO_v1, option B): the read-only open of diagnostics never DELETES a file. Offline it
creates nothing (shared lock on writer.lock); a live read has a declared window in which SQLite may recreate two
empty side files, always reported with ``store_side_files_recreated``.

Before 4.1.3 ``scan_sqlite`` (doctor, health) and ``health._sqlite_records`` opened the store with ``mode=ro``:
on a WAL store whose -wal/-shm the last writer had deleted, that open RECREATED them, and it failed on a
read-only directory or volume.
"""
import os
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

import pytest

if os.name == "nt":  # Windows is not a retained cell: open_readonly refuses there (tested below)
    pytest.skip("POSIX read-only open (fcntl)", allow_module_level=True)

from memory_os_v4 import Config, MemoryV4
from memory_os_v4.ops.health import _sqlite_records
from memory_os_v4.sqlite_stats import scan_sqlite
from memory_os_v4 import sqlite_store
from memory_os_v4.sqlite_store import ReadOnlyStoreError, open_readonly
from memory_os_v4 import writer_lock
from memory_os_v4.writer_lock import WriterConflictError, WriterLock


@pytest.fixture
def stopped_store(tmp_path):
    """A real store written by the engine, then closed: only store.sqlite3 and writer.lock remain."""
    data = tmp_path / "store"
    with MemoryV4(config=Config(data_dir=str(data), store_backend="sqlite_s1", embed_model="hash", hash_dim=16)) as m:
        item = m.add_facts([{"text": "Synthetic read-only fact."}], project="P")["added"][0]
    names = sorted(p.name for p in data.iterdir())
    assert "store.sqlite3" in names and "writer.lock" in names
    assert not any(n.endswith(("-wal", "-shm")) for n in names)
    return data, item


def listing(directory):
    return sorted((p.name, p.stat().st_size) for p in directory.iterdir())


WRITER = """
import sqlite3, sys
from pathlib import Path
from memory_os_v4.writer_lock import WriterLock
data = Path(sys.argv[1])
lease = WriterLock(data)                      # the engine's real lease, held by ANOTHER process
live = sqlite3.connect(data / "store.sqlite3")
live.execute("PRAGMA journal_mode=WAL")
live.execute("SELECT count(*) FROM items").fetchone()
print("ready", flush=True)
for line in sys.stdin:
    command = line.strip()
    if command == "close-sqlite":
        live.close()                          # last connection: SQLite deletes -wal/-shm
    elif command == "release":
        lease.close()
    print("ok", flush=True)
"""


class ExternalWriter:
    """A live writer (lease + WAL connection) in a separate process, as the service is for a diagnostic."""

    def __init__(self, data):
        env = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in sys.path if p))
        self.process = subprocess.Popen([sys.executable, "-c", WRITER, str(data)], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, text=True, env=env)
        assert self.process.stdout.readline().strip() == "ready"

    def send(self, command):
        self.process.stdin.write(command + "\n")
        self.process.stdin.flush()
        assert self.process.stdout.readline().strip() == "ok"

    def stop(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            self.process.wait(timeout=30)


DMS_PROBE = """
import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_RDWR)
try:
    fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB, 1, 128, 0)   # SQLite unix VFS: DMS byte of -shm
    print("free")
except OSError:
    print("locked")
"""


def shm_locked_by_someone(db):
    """From an INDEPENDENT process (an in-process fcntl probe would itself drop our locks when closed): does any
    connection still hold its SQLite shared lock on the -shm DMS byte?"""
    out = subprocess.run([sys.executable, "-c", DMS_PROBE, str(db) + "-shm"], capture_output=True, text=True,
                         check=True).stdout.strip()
    return out == "locked"


@pytest.fixture
def external_writer(stopped_store):
    data, _ = stopped_store
    writer = ExternalWriter(data)
    assert {"store.sqlite3-wal", "store.sqlite3-shm"} <= {p.name for p in data.iterdir()}
    yield data, writer
    writer.stop()


def test_stopped_store_is_read_without_creating_side_files(stopped_store):
    data, item = stopped_store
    before = listing(data)
    with open_readonly(data / "store.sqlite3") as connection:
        assert connection.execute("SELECT count(*) FROM items").fetchone()[0] >= 1
    assert scan_sqlite(data / "store.sqlite3")["live_total"] >= 1
    assert item in _sqlite_records(data / "store.sqlite3", {item})
    assert listing(data) == before                          # nothing created, nothing changed


def test_stopped_store_on_a_read_only_directory(stopped_store):
    data, item = stopped_store
    before = listing(data)
    os.chmod(data, 0o555)
    try:
        assert scan_sqlite(data / "store.sqlite3")["live_total"] >= 1
        assert item in _sqlite_records(data / "store.sqlite3", {item})
    finally:
        os.chmod(data, 0o755)
    assert listing(data) == before


def test_old_mode_ro_open_would_have_written_and_failed(stopped_store):
    """The defect itself, kept as a witness of what open_readonly avoids."""
    data, _ = stopped_store
    os.chmod(data, 0o555)
    try:
        with pytest.raises(sqlite3.OperationalError):
            sqlite3.connect((data / "store.sqlite3").as_uri() + "?mode=ro", uri=True).execute(
                "SELECT count(*) FROM items").fetchone()
    finally:
        os.chmod(data, 0o755)
    sqlite3.connect((data / "store.sqlite3").as_uri() + "?mode=ro", uri=True).execute(
        "SELECT count(*) FROM items").fetchone()
    assert {"store.sqlite3-wal", "store.sqlite3-shm"} <= {p.name for p in data.iterdir()}


def test_running_store_is_read_with_its_side_files(external_writer):
    data, _ = external_writer
    before = {p.name for p in data.iterdir()}
    assert scan_sqlite(data / "store.sqlite3")["live_total"] >= 1
    assert {p.name for p in data.iterdir()} == before
    assert sqlite_store._held_sides == {}                       # descriptors released with the last reader


def test_a_live_read_inside_the_engine_process_is_refused(tmp_path):
    """Closing a descriptor drops every fcntl lock of the process on that file, SQLite's included: a diagnostic
    never reads around an engine running in its own process."""
    data = tmp_path / "store"
    with MemoryV4(config=Config(data_dir=str(data), store_backend="sqlite_s1", embed_model="hash", hash_dim=16)) as m:
        m.add_facts([{"text": "Synthetic live fact."}], project="P")
        before = listing(data)
        with pytest.raises(ReadOnlyStoreError) as caught:
            with open_readonly(data / "store.sqlite3"):
                pass
        assert caught.value.code == "readonly_open_in_writer_process" and listing(data) == before


def test_a_writer_holding_the_lock_makes_the_offline_read_refuse(stopped_store):
    data, _ = stopped_store
    before = listing(data)
    writer = WriterLock(data)                                   # the engine's REAL lease, no SQLite opened yet
    try:
        with pytest.raises(ReadOnlyStoreError) as caught:
            with open_readonly(data / "store.sqlite3"):
                pass
        assert caught.value.code == "store_busy_retry"
    finally:
        writer.close()
    assert listing(data) == before


@pytest.fixture
def no_reader_deletes(monkeypatch):
    """Option B (2026-11-26): a reader NEVER deletes a file: any unlink during these tests fails them."""
    import pathlib
    def forbidden(*args, **kwargs):
        raise AssertionError("a reader deleted a file")
    monkeypatch.setattr(sqlite_store.os, "unlink", forbidden)
    monkeypatch.setattr(sqlite_store.os, "remove", forbidden)
    monkeypatch.setattr(pathlib.Path, "unlink", forbidden)


@pytest.mark.parametrize("lease_released", [True, False])
def test_writer_closing_during_the_open_is_detected_and_nothing_is_deleted(external_writer, monkeypatch,
                                                                           no_reader_deletes, lease_released):
    """The declared residual window: the live writer (another process) closes SQLite between the reader's look
    and its open. lease_released=False is Codex's interleaving: SQLite closed, writer.lock still held."""
    data, writer = external_writer
    db = data / "store.sqlite3"
    real_connect = sqlite3.connect

    def writer_closes_then_connect(*args, **kwargs):
        writer.send("close-sqlite")
        if lease_released:
            writer.send("release")                              # then the lease is released (engine order)
        assert not any(p.name.endswith(("-wal", "-shm")) and p.exists() for p in data.iterdir())
        return real_connect(*args, **kwargs)                    # mode=ro now RECREATES them

    monkeypatch.setattr(sqlite_store.sqlite3, "connect", writer_closes_then_connect)
    with pytest.raises(ReadOnlyStoreError) as caught:
        with open_readonly(db):
            pass
    assert caught.value.code == "store_side_files_recreated"     # reported, never silent
    monkeypatch.undo()
    writer.stop()
    assert sqlite_store._held_sides == {}
    # the declared residue: two side files without data, left in place (never deleted by a reader)
    assert {"store.sqlite3-wal", "store.sqlite3-shm"} <= {p.name for p in data.iterdir()}
    assert (data / "store.sqlite3-wal").stat().st_size == 0
    with MemoryV4(config=Config(data_dir=str(data), store_backend="sqlite_s1", embed_model="hash", hash_dim=16)) as m:
        assert m.add_facts([{"text": "After the residue."}], project="P")["added"]   # next start uses them
    with open_readonly(db) as connection:                       # and a later read succeeds
        assert connection.execute("SELECT count(*) FROM items").fetchone()[0] >= 2


def test_a_recycled_inode_is_still_detected(external_writer, monkeypatch, no_reader_deletes):
    """Codex WAL r3 item 1: a recreated file reusing the old (dev, ino) must not pass. The held descriptors keep
    the old inodes allocated; this simulates the reuse anyway (lstat reports the old identity) and relies on the
    held file being unlinked (st_nlink == 0)."""
    data, writer = external_writer
    db = data / "store.sqlite3"
    real_connect, real_lstat = sqlite3.connect, os.lstat

    def same_identity_lstat(path, *args, **kwargs):
        entry = sqlite_store._held_sides.get(str(db))
        for fd, suffix in zip(entry[1:] if entry else (), ("-wal", "-shm")):
            if os.fspath(path) == str(db) + suffix:
                return os.fstat(fd)                             # the OLD identity, as after an inode reuse
        return real_lstat(path, *args, **kwargs)

    def writer_closes_then_connect(*args, **kwargs):
        writer.send("close-sqlite")
        monkeypatch.setattr(sqlite_store.os, "lstat", same_identity_lstat)
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(sqlite_store.sqlite3, "connect", writer_closes_then_connect)
    with pytest.raises(ReadOnlyStoreError) as caught:
        with open_readonly(db):
            pass
    monkeypatch.undo()
    assert caught.value.code == "store_side_files_recreated"


def test_a_failing_first_read_after_a_recreation_names_the_recreation(external_writer, monkeypatch,
                                                                       no_reader_deletes):
    """Codex WAL r3 item 2: the recreation is reported on the error path too, with the SQLite error as cause."""
    data, writer = external_writer
    db = data / "store.sqlite3"
    real_connect = sqlite3.connect

    class FailingFirstRead:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, *args):
            raise sqlite3.OperationalError("disk I/O error")

        def close(self):
            self.connection.close()

    def writer_closes_then_connect(*args, **kwargs):
        writer.send("close-sqlite")
        return FailingFirstRead(real_connect(*args, **kwargs))

    monkeypatch.setattr(sqlite_store.sqlite3, "connect", writer_closes_then_connect)
    with pytest.raises(ReadOnlyStoreError) as caught:
        with open_readonly(db):
            pass
    monkeypatch.undo()
    assert caught.value.code == "store_side_files_recreated"
    assert isinstance(caught.value.__cause__, sqlite3.OperationalError)
    assert sqlite_store._held_sides == {}


def test_a_failing_first_read_without_recreation_is_not_relabelled(external_writer, monkeypatch):
    data, _ = external_writer
    real_connect = sqlite3.connect

    class FailingFirstRead:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, *args):
            raise sqlite3.DatabaseError("file is not a database")

        def close(self):
            self.connection.close()

    monkeypatch.setattr(sqlite_store.sqlite3, "connect", lambda *a, **k: FailingFirstRead(real_connect(*a, **k)))
    with pytest.raises(sqlite3.DatabaseError):
        with open_readonly(data / "store.sqlite3"):
            pass


def test_two_readers_interleaved_with_the_writer_shutdown(external_writer, no_reader_deletes):
    """The held descriptors are shared: the first reader to finish must not close them (closing would drop the
    second reader's SQLite locks on -shm); they are closed after the LAST connection."""
    data, writer = external_writer
    db = data / "store.sqlite3"
    first_context = open_readonly(db)
    first = first_context.__enter__()
    with open_readonly(db) as second:
        assert sqlite_store._held_sides[str(db)][0] == 2
        writer.send("close-sqlite")                             # the service stops while both readers read
        writer.send("release")
        first_context.__exit__(None, None, None)
        assert sqlite_store._held_sides[str(db)][0] == 1        # still held for the second reader
        assert second.execute("SELECT count(*) FROM items").fetchone()[0] >= 1
    assert sqlite_store._held_sides == {}
    del first


def test_an_offline_reader_on_the_kept_pair_shares_the_held_descriptors(external_writer, no_reader_deletes):
    """Codex WAL r4 item 2: A reads live; the service stops (A keeps the pair); B then obtains the shared lock and
    reads the kept pair offline; A finishes first. B's SQLite locks must survive A's exit."""
    data, writer = external_writer
    db = data / "store.sqlite3"
    first_context = open_readonly(db)
    first_context.__enter__().execute("SELECT count(*) FROM items").fetchone()
    writer.send("close-sqlite")
    writer.send("release")
    assert {"store.sqlite3-wal", "store.sqlite3-shm"} <= {p.name for p in data.iterdir()}
    with open_readonly(db) as second:                           # offline now: lock obtained, pair kept, mode=ro
        assert second.execute("SELECT count(*) FROM items").fetchone()[0] >= 1
        assert sqlite_store._held_sides[str(db)][0] == 2
        first_context.__exit__(None, None, None)
        assert sqlite_store._held_sides[str(db)][0] == 1
        assert shm_locked_by_someone(db)                        # B still holds its SQLite lock
        assert second.execute("SELECT count(*) FROM items").fetchone()[0] >= 1
    assert sqlite_store._held_sides == {} and not shm_locked_by_someone(db)


def test_the_engine_cannot_start_under_a_reader_of_this_process(external_writer):
    """Codex WAL r4 item 1: the exclusion holds for the reader's whole lifetime, across threads."""
    data, writer = external_writer
    db = data / "store.sqlite3"
    opened, finish, errors = threading.Event(), threading.Event(), []

    def reader():
        try:
            with open_readonly(db) as connection:
                connection.execute("SELECT count(*) FROM items").fetchone()
                opened.set()
                finish.wait(30)
                connection.execute("SELECT count(*) FROM items").fetchone()
        except Exception as exc:                                 # reported to the main thread
            errors.append(exc)
            opened.set()

    thread = threading.Thread(target=reader)
    thread.start()
    config = Config(data_dir=str(data), store_backend="sqlite_s1", embed_model="hash", hash_dim=16)
    try:
        assert opened.wait(30) and not errors
        writer.send("close-sqlite")
        writer.send("release")                                  # the service stopped: its lease is free
        with pytest.raises(WriterConflictError, match="read-only diagnostic"):
            MemoryV4(config=config)
        assert shm_locked_by_someone(db)                        # the reader's locks are intact
    finally:
        finish.set()
        thread.join(30)
    assert not errors and sqlite_store._held_sides == {} and writer_lock._active_readers == {}
    writer.stop()
    with MemoryV4(config=config) as m:                          # starts once the reader is closed
        assert m.add_facts([{"text": "After the reader."}], project="P")["added"]


def test_two_concurrent_live_readers_never_delete_anything(external_writer, no_reader_deletes):
    data, _ = external_writer
    before = {p.name for p in data.iterdir()}
    with open_readonly(data / "store.sqlite3") as first:
        with open_readonly(data / "store.sqlite3") as second:
            assert first.execute("SELECT count(*) FROM items").fetchone() == \
                second.execute("SELECT count(*) FROM items").fetchone()
    assert {p.name for p in data.iterdir()} == before


def test_health_names_an_unreadable_supersession_check(stopped_store, monkeypatch):
    """Codex WAL r3 item 2: explicit_supersessions no longer absorbs the refusal code."""
    from memory_os_v4.ops import health
    data, item = stopped_store

    def recreated(*args, **kwargs):
        raise ReadOnlyStoreError("store_side_files_recreated", "synthetic")

    monkeypatch.setattr(health, "_sqlite_records", recreated)
    diagnostics = []
    assert health.explicit_supersessions(data, [item], diagnostics=diagnostics) == (set(), set())
    assert diagnostics == ["store_side_files_recreated"]
    assert health.explicit_supersessions(data, [item]) == (set(), set())   # unchanged without the list


def test_doctor_run_does_not_crash_on_sqlite_errors(stopped_store, monkeypatch, tmp_path):
    """Codex WAL r2 item 3: through doctor.run (not only _store), a SQLite error is a structured check."""
    from memory_os_v4.ops import doctor
    from memory_os_v4.ops.settings import OpsSettings
    data, _ = stopped_store
    monkeypatch.setenv("MEMORY_V4_DIR", str(data))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("attempt to write a readonly database")
    monkeypatch.setattr("memory_os_v4.sqlite_stats.scan_sqlite", broken)
    checks = {c.area: c for c in doctor.run(OpsSettings.from_env())}
    assert checks["store"].status == "FAIL"
    assert checks["journal_size"].status == "WARN" and "unavailable" in checks["journal_size"].detail


def test_stale_pair_without_writer_is_read_under_the_lock(stopped_store):
    data, _ = stopped_store
    db = data / "store.sqlite3"
    rw = sqlite3.connect(db)
    rw.execute("PRAGMA journal_mode=WAL")
    rw.execute("SELECT 1").fetchone()
    pair = {p.name for p in data.iterdir() if p.name.endswith(("-wal", "-shm"))}
    assert pair == {"store.sqlite3-wal", "store.sqlite3-shm"}   # an open connection, no engine lease
    with open_readonly(db) as connection:
        assert connection.execute("SELECT count(*) FROM items").fetchone()[0] >= 1
    rw.close()


def test_windows_is_refused_rather_than_written(stopped_store, monkeypatch):
    data, _ = stopped_store
    before = listing(data)
    monkeypatch.setattr(sqlite_store, "_windows", lambda: True)
    with pytest.raises(ReadOnlyStoreError) as caught:
        with open_readonly(data / "store.sqlite3"):
            pass
    monkeypatch.undo()
    assert caught.value.code == "readonly_open_unqualified_platform" and listing(data) == before


@pytest.mark.parametrize("state, status, code", [("busy", "WARN", "store_busy_retry"),
                                                ("no-lock", "FAIL", "writer_lock_missing")])
def test_doctor_names_the_refusal(stopped_store, monkeypatch, tmp_path, state, status, code):
    from memory_os_v4.ops import doctor
    from memory_os_v4.ops.settings import OpsSettings
    data, _ = stopped_store
    monkeypatch.setenv("MEMORY_V4_DIR", str(data))
    writer = None
    if state == "busy":
        writer = WriterLock(data)
    else:
        (data / "writer.lock").rename(tmp_path / "writer.lock")
    try:
        check = doctor._store(OpsSettings.from_env())
    finally:
        if writer:
            writer.close()
    assert check.status == status and code in check.detail, check


def test_incomplete_side_files_and_missing_or_linked_lock_are_refused(stopped_store, tmp_path):
    data, _ = stopped_store
    db = data / "store.sqlite3"
    (data / "store.sqlite3-wal").write_bytes(b"")
    with pytest.raises(ReadOnlyStoreError) as caught:
        with open_readonly(db):
            pass
    assert caught.value.code == "store_side_files_incomplete"
    (data / "store.sqlite3-wal").unlink()
    lock = data / "writer.lock"
    lock.rename(tmp_path / "lock-elsewhere")
    with pytest.raises(ReadOnlyStoreError) as caught:
        with open_readonly(db):
            pass
    assert caught.value.code == "writer_lock_missing" and not lock.exists()   # never created by a reader
    lock.symlink_to(tmp_path / "lock-elsewhere")
    with pytest.raises(ReadOnlyStoreError) as caught:
        with open_readonly(db):
            pass
    assert caught.value.code == "writer_lock_missing"


def test_the_shared_lock_does_not_block_another_reader(stopped_store):
    data, _ = stopped_store
    with open_readonly(data / "store.sqlite3"):
        with open_readonly(data / "store.sqlite3") as second:
            assert second.execute("SELECT 1").fetchone() == (1,)


CRASHED_WRITER = """
import os, sqlite3, sys
connection = sqlite3.connect(sys.argv[1])
connection.execute("PRAGMA journal_mode=WAL")
connection.execute("SELECT count(*) FROM items").fetchone()
os._exit(0)                                   # no close: the pair stays, nobody holds it
"""


@pytest.fixture
def kept_pair(stopped_store):
    """A pair left by a crashed writer: present, held by no connection of any process."""
    data, _ = stopped_store
    db = data / "store.sqlite3"
    subprocess.run([sys.executable, "-c", CRASHED_WRITER, str(db)], check=True)
    assert {"store.sqlite3-wal", "store.sqlite3-shm"} <= {p.name for p in data.iterdir()}
    assert not shm_locked_by_someone(db)
    return data


def descriptors_on(path):
    """Descriptors of THIS process open on ``path`` (a native SQLite connection keeps one on the database)."""
    try:
        target = os.stat(path)
    except FileNotFoundError:
        return 0                                                # an absent file: an unlinked one would be
    count = 0                                                   # caught by the held-descriptor checks
    for name in os.listdir("/dev/fd"):
        try:
            st = os.fstat(int(name))
        except (OSError, ValueError):
            continue
        count += (st.st_dev, st.st_ino) == (target.st_dev, target.st_ino)
    return count


def assert_reader_fully_released(db):
    assert descriptors_on(db) == 0 and descriptors_on(str(db) + "-shm") == 0
    assert sqlite_store._held_sides == {} and writer_lock._active_readers == {}


def test_a_partly_consumed_cursor_does_not_outlive_the_read(kept_pair):
    """Codex WAL r5 item 1: connection.close() alone defers the native close while a cursor holds an unfinished
    statement; the cursors are closed first, so nothing survives the read."""
    db = kept_pair / "store.sqlite3"
    with open_readonly(db) as connection:
        kept = connection.execute("SELECT name FROM sqlite_master")
        assert kept.fetchone() is not None                      # partly consumed, still referenced
        assert descriptors_on(db) >= 1 and shm_locked_by_someone(db)
    assert_reader_fully_released(db)
    with pytest.raises(sqlite3.ProgrammingError):
        kept.fetchone()                                         # closed, not a zombie statement
    for refused in ("iterdump", "backup", "blobopen"):
        with open_readonly(db) as connection:
            with pytest.raises(AttributeError):
                getattr(connection, refused)


def test_an_error_during_the_scan_releases_everything(kept_pair, monkeypatch):
    """Codex WAL r5 item 1: a decoding error raised while rows are consumed, its traceback kept."""
    import types
    from memory_os_v4 import sqlite_stats
    db = kept_pair / "store.sqlite3"

    def broken(text):
        raise ValueError("synthetic decoding error")

    monkeypatch.setattr(sqlite_stats, "json", types.SimpleNamespace(loads=broken))
    with pytest.raises(ValueError, match="synthetic decoding error") as kept:
        scan_sqlite(db)
    assert kept.value.__traceback__ is not None                 # the traceback is still alive here
    assert_reader_fully_released(db)


def test_every_spelling_of_the_store_shares_one_identity(kept_pair, tmp_path, monkeypatch, no_reader_deletes):
    """Codex WAL r5 item 2: relative, absolute and linked-directory spellings share the held descriptors."""
    db = kept_pair / "store.sqlite3"
    (tmp_path / "alias").symlink_to(kept_pair)
    monkeypatch.chdir(kept_pair.parent)
    spellings = [Path(kept_pair.name) / "store.sqlite3", tmp_path / "alias" / "store.sqlite3", db]
    contexts = [open_readonly(spelling) for spelling in spellings]
    connections = [context.__enter__() for context in contexts]
    try:
        for connection in connections:
            assert connection.execute("SELECT count(*) FROM items").fetchone()[0] >= 1
        assert list(sqlite_store._held_sides.values())[0][0] == 3 and len(sqlite_store._held_sides) == 1
        assert list(writer_lock._active_readers.values()) == [3]
        for context in contexts[:2]:
            context.__exit__(None, None, None)
        assert shm_locked_by_someone(db)                        # the last reader keeps its SQLite locks
        assert connections[2].execute("SELECT count(*) FROM items").fetchone()[0] >= 1
    finally:
        contexts[2].__exit__(None, None, None)
    assert_reader_fully_released(db)


FLOCK_PROBE = """
import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_RDONLY)
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    print("free")
except OSError:
    print("locked")
"""


def writer_lock_free_for_another_process(data):
    out = subprocess.run([sys.executable, "-c", FLOCK_PROBE, str(data / "writer.lock")], capture_output=True,
                         text=True, check=True).stdout.strip()
    return out == "free"


def release_pinned():
    """Test cleanup only (production keeps pinned resources for the life of the process)."""
    for entry in sqlite_store._pinned:
        if entry["holds_side_files"]:
            sqlite_store._release_side_files(Path(entry["database"]))
        sqlite_store._unregister_reader(entry["registered"])
        os.close(entry["writer_lock_fd"])
    sqlite_store._pinned.clear()


def test_an_unestablished_native_close_keeps_the_protections(kept_pair, monkeypatch):
    """If the cursors or the connection cannot be closed, the descriptors, the registration and the writer.lock
    shared lock stay: no engine of this store can start, here or in another process."""
    db = kept_pair / "store.sqlite3"
    real_close = sqlite_store._ReadOnlyConnection._close

    def failing_close(self):
        real_close(self)
        raise sqlite3.OperationalError("synthetic close failure")

    monkeypatch.setattr(sqlite_store._ReadOnlyConnection, "_close", failing_close)
    with pytest.raises(sqlite3.OperationalError, match="synthetic close failure"):
        with open_readonly(db) as connection:
            connection.execute("SELECT 1").fetchone()
    key = (kept_pair / "writer.lock").resolve()
    try:
        assert writer_lock._active_readers == {key: 1} and sqlite_store._held_sides[str(db)][0] == 1
        with pytest.raises(WriterConflictError, match="read-only diagnostic"):
            WriterLock(kept_pair)
        assert not writer_lock_free_for_another_process(kept_pair)
    finally:
        release_pinned()
    assert_reader_fully_released(db)


def test_a_real_close_failure_keeps_the_writer_lock_shared_lock(stopped_store):
    """Codex WAL r6 item 2: offline immutable read opened in thread A, context closed in another thread: SQLite
    refuses the close (check_same_thread) and the connection stays usable in A; an independent writer must stay
    locked out."""
    data, _ = stopped_store
    db = data / "store.sqlite3"
    state, opened, finish = {}, threading.Event(), threading.Event()

    def thread_a():
        context = open_readonly(db)
        connection = context.__enter__()
        connection.execute("SELECT count(*) FROM items").fetchone()
        state["context"] = context
        opened.set()
        finish.wait(30)
        state["still_usable"] = connection.execute("SELECT count(*) FROM items").fetchone()[0]
        for entry in sqlite_store._pinned:                      # cleanup in the owning thread
            entry["connection"].close()

    thread = threading.Thread(target=thread_a)
    thread.start()
    try:
        assert opened.wait(30)
        with pytest.raises(sqlite3.ProgrammingError):
            state["context"].__exit__(None, None, None)         # a close from the wrong thread fails for real
        assert len(sqlite_store._pinned) == 1 and not sqlite_store._pinned[0]["holds_side_files"]
        assert not writer_lock_free_for_another_process(data)   # shared lock kept: no external writer starts
        with pytest.raises(WriterConflictError):
            WriterLock(data)
    finally:
        finish.set()
        thread.join(30)
        release_pinned()
    assert state["still_usable"] >= 1
    assert writer_lock_free_for_another_process(data)
    assert_reader_fully_released(db)


def test_the_proxy_never_exposes_the_native_connection(kept_pair):
    """Codex WAL r6 item 1: no public route to an untracked cursor (cursor.connection, context manager, cursor
    factory, delegated attributes)."""
    db = kept_pair / "store.sqlite3"
    with open_readonly(db) as connection:
        cursor = connection.cursor()
        assert cursor.connection is connection and connection.execute("SELECT 1").connection is connection
        escaped = cursor.connection.execute("SELECT name FROM sqlite_master")
        assert escaped.fetchone() is not None                   # partly consumed, kept
        for name in ("__enter__", "iterdump", "backup", "blobopen", "row_factory", "create_function",
                     "set_authorizer", "_unknown"):
            if name.startswith("_") and name != "__enter__":
                continue
            with pytest.raises(AttributeError):
                getattr(connection, name)
        with pytest.raises(TypeError):
            connection.cursor(sqlite3.Cursor)                   # no cursor factory
        with pytest.raises(AttributeError):
            cursor.arraysize = 10                               # explicit API only
        assert [row for row in connection.execute("SELECT 1 UNION ALL SELECT 2")] == [(1,), (2,)]
    assert_reader_fully_released(db)


@pytest.mark.parametrize("link", ["symbolic", "hard"])
def test_a_linked_database_is_refused_by_the_opener_and_by_health(stopped_store, tmp_path, link):
    """Codex WAL r6 item 3: refused before any resolution, for every caller."""
    from memory_os_v4.ops import health
    data, item = stopped_store
    other = tmp_path / "other"
    other.mkdir()
    (other / "writer.lock").write_bytes(b"")
    alias = other / "store.sqlite3"
    if link == "symbolic":
        alias.symlink_to(data / "store.sqlite3")
    else:
        os.link(data / "store.sqlite3", alias)
    with pytest.raises(ReadOnlyStoreError) as caught:
        with open_readonly(alias):
            pass
    assert caught.value.code == "store_database_linked"
    diagnostics = []
    assert health.explicit_supersessions(other, [item], diagnostics=diagnostics) == (set(), set())
    assert diagnostics == ["store_database_linked"]
    assert_reader_fully_released(data / "store.sqlite3")

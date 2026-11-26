"""4.1.3 (NOTE_4_1_3_WAL_MODE_RO_v1): the read-only open of diagnostics never creates a file in the store.

Before 4.1.3 ``scan_sqlite`` (doctor, health) and ``health._sqlite_records`` opened the store with ``mode=ro``:
on a WAL store whose -wal/-shm the last writer had deleted, that open RECREATED them, and it failed on a
read-only directory or volume.
"""
import fcntl
import os
import sqlite3

import pytest

from memory_os_v4 import Config, MemoryV4
from memory_os_v4.ops.health import _sqlite_records
from memory_os_v4.sqlite_stats import scan_sqlite
from memory_os_v4.sqlite_store import ReadOnlyStoreError, open_readonly


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


def test_running_store_is_read_with_its_side_files(tmp_path):
    data = tmp_path / "store"
    with MemoryV4(config=Config(data_dir=str(data), store_backend="sqlite_s1", embed_model="hash", hash_dim=16)) as m:
        m.add_facts([{"text": "Synthetic live fact."}], project="P")
        assert {"store.sqlite3-wal", "store.sqlite3-shm"} <= {p.name for p in data.iterdir()}
        before = {p.name for p in data.iterdir()}
        assert scan_sqlite(data / "store.sqlite3")["live_total"] >= 1
        assert {p.name for p in data.iterdir()} == before


def test_a_writer_holding_the_lock_makes_the_offline_read_refuse(stopped_store):
    data, _ = stopped_store
    before = listing(data)
    fd = os.open(data / "writer.lock", os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)      # what the engine holds while it runs
        with pytest.raises(ReadOnlyStoreError) as caught:
            with open_readonly(data / "store.sqlite3"):
                pass
        assert caught.value.code == "store_busy_retry"
    finally:
        os.close(fd)
    assert listing(data) == before


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

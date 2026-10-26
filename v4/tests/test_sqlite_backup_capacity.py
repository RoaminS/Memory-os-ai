"""Boundary/compatibility tests; large fixture is real, local, hash-only and removed."""

from contextlib import closing
import hashlib
import io
from pathlib import Path
import shutil
import tarfile
import tempfile
from types import SimpleNamespace

import pytest

from memory_os_v4 import Config, MemoryV4
from memory_os_v4.ops import sqlite_backup as s4, sqlite_archive, sqlite_limits as limits, storage, doctor
from memory_os_v4.ops.backup import encrypt, decrypt, build_archive, BackupError
from memory_os_v4.ops.settings import OpsSettings
from memory_os_v4.sqlite_store import SQLiteStore


@pytest.mark.parametrize(
    "stage", ["database", "wal", "shm", "member", "manifest", "decompressed", "decrypted", "archive"]
)
@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_every_stage_exact_boundary(stage, offset):
    cap = limits.Limits(1000, 100)
    maximum = {
        "database": 1000,
        "wal": 1000,
        "shm": 1000,
        "member": 1000,
        "manifest": 100,
        "decompressed": cap.unpacked,
        "decrypted": cap.compressed,
        "archive": cap.encrypted,
    }[stage]
    if offset > 0:
        with pytest.raises(ValueError, match="cap exceeded"):
            cap.check(maximum + offset, stage)
    else:
        cap.check(maximum + offset, stage)


@pytest.mark.parametrize("value", ["", "invalid", "1.5", "-1", "67108863", "17179869185"])
def test_invalid_explicit_limit_is_never_ignored(monkeypatch, value):
    monkeypatch.setenv("MEMORY_V4_SQLITE_BACKUP_MAX_BYTES", value)
    with pytest.raises(ValueError, match="MEMORY_V4_SQLITE_BACKUP_MAX_BYTES"):
        limits.configured()


def test_default_and_explicit_limits(monkeypatch):
    monkeypatch.delenv("MEMORY_V4_SQLITE_BACKUP_MAX_BYTES", raising=False)
    assert limits.configured().db == 4 * 1024**3
    monkeypatch.setenv("MEMORY_V4_SQLITE_BACKUP_MAX_BYTES", "1073741824")
    cap = limits.configured()
    assert cap.db == 1024**3 and cap.encrypted > cap.compressed > cap.unpacked > cap.db


@pytest.fixture(autouse=True)
def explicit_capacity_environment(monkeypatch):
    """Test defaults deliberately; individual override tests set their own values."""
    monkeypatch.delenv("MEMORY_V4_SQLITE_BACKUP_MAX_BYTES", raising=False)
    monkeypatch.delenv("MEMORY_V4_SQLITE_STORAGE_POLICY", raising=False)


def seeded(root):
    store = root / "store"
    with MemoryV4(
        config=Config(data_dir=str(store), embed_model="hash", hash_dim=16, store_backend="sqlite_s1")
    ) as engine:
        engine.add_facts([{"text": "Synthetic capacity witness."}], project="test")
    return store


def test_streaming_cipher_is_compatible_with_both_existing_directions(tmp_path):
    store = seeded(tmp_path)
    database = store / s4.DB
    key = b"k" * 32
    with closing(s4.readonly(database, sealed=True)) as con:
        logical = s4.inventory(con)
    manifest = {
        "format": s4.FORMAT,
        "qualified": True,
        "logical_inventory": logical,
        "files": {s4.DB: {"bytes": database.stat().st_size, "sha256": s4.sha256(database)}},
    }
    output = tmp_path / "new.mosb"
    sqlite_archive.build(database, manifest, output, key, limits.configured(), lambda: None)
    # High-level existing reader authenticates/decrypts a streaming-produced blob.
    with tarfile.open(fileobj=io.BytesIO(decrypt(output.read_bytes(), key)), mode="r:gz") as tar:
        assert sorted(tar.getnames()) == ["memory-os-v4/MANIFEST.json", "memory-os-v4/store.sqlite3"]
    assert s4.verify_archive(output, key, tmp_path / "new-verified")["logical_inventory"] == logical
    old = tmp_path / "old.mosb"
    old.write_bytes(encrypt(build_archive({s4.DB: database.read_bytes()}, manifest), key))
    assert s4.verify_archive(old, key, tmp_path / "old-verified")["logical_inventory"] == logical
    bad = tmp_path / "bad.mosb"
    blob = bytearray(output.read_bytes())
    blob[-1] ^= 1
    bad.write_bytes(blob)
    with pytest.raises(BackupError, match="authentication"):
        s4.verify_archive(bad, key, tmp_path / "unauthenticated")
    assert not (tmp_path / "unauthenticated").exists()


def test_real_database_beyond_64_mib_snapshot_archive_and_drill(tmp_path, monkeypatch):
    # No large model, random array or 4-GiB allocation. ~70-MiB real SQLite BLOB.
    def guard():
        if shutil.disk_usage(tmp_path).free < 3_000_000_000:
            raise RuntimeError("3 GB test guard")

    guard()
    with tempfile.TemporaryDirectory(dir=tmp_path) as private:
        root = Path(private)
        store = seeded(root)
        block = bytes(1024 * 1024)
        size = 65 * 1024 * 1024
        digest = hashlib.sha256()
        for _ in range(65):
            digest.update(block)
        with closing(SQLiteStore(store)) as writer:
            with writer.transaction():
                writer.write(
                    "large-fixture",
                    "INSERT INTO legacy_files VALUES(?,zeroblob(?),?)",
                    ("synthetic.large", size, digest.hexdigest()),
                )
            assert (
                writer.db_path.stat().st_size + Path(str(writer.db_path) + "-wal").stat().st_size
                > 64 * 1024**2
            )
            snapshot = root / "cut.sqlite3"
            cut = s4.snapshot(writer.db_path, snapshot, guard)
            assert snapshot.stat().st_size > 64 * 1024**2
            assert cut["tables"]["legacy_files"]["rows"] == 1
            settings = OpsSettings(
                store_dir=store,
                backup_dir=root / "backups",
                ops_dir=root / "ops",
                backup_key=root / "key",
                token_file=root / "unused.token",
                url="http://127.0.0.1:9/mcp/",
                service_label=None,
            )
            from memory_os_v4.ops.backup import load_or_create_key

            load_or_create_key(settings.backup_key)
            # Synthetic test only: real I/O/crypto/inventory, guarded at 3GB,
            # no syscall nice/swap privilege or service/network needed.
            monkeypatch.setattr(s4, "Budget", lambda _: SimpleNamespace(check=guard))
            import tracemalloc

            tracemalloc.start()
            backup = s4.backup(settings, apply=True)
            assert backup["ok"] and backup["verified"], backup
            drill = s4.restore_drill(settings, settings.backup_dir / backup["archive"], apply=True)
            assert drill["ok"] and drill["verified"] and drill["quarantined"], drill
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            assert peak < 24 * 1024**2, peak  # Python heap only; not native SQLite/RSS.
            assert not drill.get("private_reopen_verified")  # files only, never pretend service qualification
            # Actual DB cap is inclusive; reject without any source change.
            monkeypatch.setattr(s4, "MAX_DB", snapshot.stat().st_size - 1)
            with pytest.raises(BackupError, match="cap exceeded"):
                s4.readonly(snapshot, sealed=True)
            monkeypatch.setattr(s4, "MAX_DB", snapshot.stat().st_size)
            with closing(s4.readonly(snapshot, sealed=True)) as con:
                assert con.execute("PRAGMA quick_check").fetchone() == ("ok",)
            monkeypatch.setattr(s4, "MAX_DB", snapshot.stat().st_size + 1)
            with closing(s4.readonly(snapshot, sealed=True)) as con:
                assert con.execute("PRAGMA quick_check").fetchone() == ("ok",)


def test_storage_policy_is_explicit_bounded_and_not_cap_sized(tmp_path, monkeypatch):
    monkeypatch.delenv("MEMORY_V4_SQLITE_STORAGE_POLICY", raising=False)
    _, reserve, work = storage.policy(80_000_000)
    assert reserve == 5_000_000_000 and work > 6 * 80_000_000
    monkeypatch.setenv("MEMORY_V4_SQLITE_STORAGE_POLICY", "adaptive_v1")
    _, reserve, work = storage.policy(80_000_000)
    assert reserve == 3_000_000_000 and reserve + work < 4_800_000_000
    assert storage.policy(2_000_000_000)[1] == 4_000_000_000
    with pytest.raises(ValueError):
        storage.policy(1, sqlite=False)
    monkeypatch.setenv("MEMORY_V4_SQLITE_STORAGE_POLICY", "anything")
    with pytest.raises(ValueError):
        storage.policy(1)


def test_doctor_capacity_and_health_render_include_margin(tmp_path, monkeypatch):
    from memory_os_v4.ops import health

    store = seeded(tmp_path)
    settings = SimpleNamespace(store_dir=store)
    check = doctor._backup_capacity(settings)
    assert check.status == "PASS" and "margin=" in check.detail
    monkeypatch.setenv("MEMORY_V4_SQLITE_BACKUP_MAX_BYTES", "67108864")
    assert limits.capacity(store)["limits"]["database_bytes"] == 67108864
    monkeypatch.setattr(limits, "configured", lambda: limits.Limits(1))
    assert doctor._backup_capacity(settings).status == "FAIL"
    text = health.render(
        {"at": "now", "anomalies": [], "superseded_new": [], "backup_capacity": {"margin_bytes": -1}}
    )
    assert "Backup capacity" in text and "margin_bytes" in text


def test_adaptive_copy_budget_is_explicit(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    from qualify_sqlite_release_408 import start_disk_budget

    monkeypatch.setenv("MEMORY_V4_SQLITE_STORAGE_POLICY", "adaptive_v1")
    result = start_disk_budget(tmp_path, 4_800_000_000)
    assert result["storage_policy"] == "adaptive_v1"
    assert 3_000_000_000 < result["required_start_free_bytes"] < 4_800_000_000
    monkeypatch.delenv("MEMORY_V4_SQLITE_STORAGE_POLICY")
    assert start_disk_budget(tmp_path, 4_800_000_000)["required_start_free_bytes"] == 7_000_000_000


def test_invalid_s4_capacity_refuses_template_before_token(tmp_path, monkeypatch, capsys):
    from memory_os_v4.ops.cli import cmd_setup

    settings = OpsSettings(
        store_dir=tmp_path / "store",
        backup_dir=tmp_path / "backups",
        ops_dir=tmp_path / "ops",
        backup_key=tmp_path / "key",
        token_file=tmp_path / "token",
        url="http://127.0.0.1:9/mcp/",
        service_label=None,
    )
    monkeypatch.setenv("MEMORY_V4_SQLITE_BACKUP_MAX_BYTES", "invalid")
    assert cmd_setup(SimpleNamespace(sqlite_s4_jobs=True), settings) == 2
    assert not settings.token_file.exists()
    assert "sqlite_s4_not_configured" in capsys.readouterr().out

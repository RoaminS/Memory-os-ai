"""SQLite S4 increment 1: pinned online snapshot and quarantined file drill.

No source Store/engine construction, migration, launchd control or production
restore. Simulation by default. Semantic reopening/forget reconciliation is a
separate explicit gate; production promotion is never performed here.
"""
from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import io
import math
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import struct
import tarfile
import tempfile
import time
import uuid

from ..sqlite_store import APPLICATION_ID, MIN_SQLITE, MIGRATIONS, SCHEMA_VERSION, schema_checksums, _json
from ..store import MemoryItem, VALID_KINDS
from .backup import BackupError, ROOT, build_archive, decrypt, encrypt, load_key, sha256, _log
from .storage import machine_guard, margin
from .storecheck import loads_strict

FORMAT = "memory-os-v4-sqlite-backup/1"
PREFIX = "memory-os-sqlite-"
DB = "store.sqlite3"
MAX_DB = 64 * 1024 ** 2
MAX_ARCHIVE = MAX_DB + 4 * 1024 ** 2
MAX_MANIFEST = 2 * 1024 ** 2


def plain_path(path):
    path = Path(path).expanduser().absolute()
    if '..' in path.parts:
        raise BackupError("Parent traversal refused")
    for part in (path, *path.parents):
        if part.is_symlink():
            raise BackupError("Symlink path refused")
    if path.exists() and path.is_file() and path.stat().st_nlink != 1:
        raise BackupError("Hardlinked file refused")
    return path


def disjoint(settings):
    source = plain_path(settings.store_dir)
    for path in (settings.backup_dir, settings.ops_dir):
        path = plain_path(path)
        if path == source or path.is_relative_to(source) or source.is_relative_to(path):
            raise BackupError("Source, backup and ops paths must be disjoint")


class Budget:
    def __init__(self, settings):
        self.settings, self.start, self.last = settings, time.monotonic(), 0.0
        machine_guard(settings)

    def check(self):
        now = time.monotonic()
        if now - self.start > 300:
            raise BackupError("SQLite S4 300 s wall-time budget exceeded")
        if now - self.last >= 1:
            machine_guard(self.settings)
            self.last = now


def lease(settings):
    """One S4 backup/drill per ops installation, without locking the writer."""
    import fcntl
    settings.ops_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(settings.ops_dir / "sqlite-s4.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if info.st_nlink != 1 or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise BackupError("Unsafe S4 lease file")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BaseException:
        os.close(fd)
        raise


def readonly(path, *, sealed=False):
    path = plain_path(path)
    if not path.is_file() or path.stat().st_size > MAX_DB:
        raise BackupError("Missing database or 64 MiB database cap exceeded")
    if sqlite3.sqlite_version_info < MIN_SQLITE:
        raise BackupError("SQLite runtime >=3.51.3 required; no runtime download")
    if not sealed:
        # WAL-mode read-only opens can create missing shared-memory artifacts.
        # Never let this CLI repair/create them in the live source directory.
        for suffix in ("-wal", "-shm"):
            child = plain_path(Path(str(path) + suffix))
            if not child.is_file():
                raise BackupError("Online source requires existing WAL/SHM; open the writer, never repair from backup")
            if child.stat().st_size > MAX_DB:
                raise BackupError("Source WAL/SHM exceeds S4 cap")
    uri = path.as_uri() + ("?mode=ro&immutable=1" if sealed else "?mode=ro")
    con = sqlite3.connect(uri, uri=True, timeout=1)
    con.execute("PRAGMA temp_store=MEMORY")
    con.execute("PRAGMA query_only=ON")
    return con


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def register(settings, name, record):
    _log(settings, name, record)
    with (settings.ops_dir / 'logs' / name).open('rb') as fh:
        os.fsync(fh.fileno())
    sync_directory(settings.ops_dir / 'logs')
    sync_directory(settings.ops_dir)


def _schema(con):
    return con.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()


def _expected_schema():
    con = sqlite3.connect(":memory:")
    try:
        for migration in MIGRATIONS:
            for statement in migration:
                con.execute(statement)
        return _schema(con)
    finally:
        con.close()


def _cell(value):
    if isinstance(value, bytes):
        return {"blob_bytes": len(value), "blob_sha256": hashlib.sha256(value).hexdigest()}
    return value


def inventory(con, guard=lambda: None):
    """Every table/column/row, including legacy blobs, cache and future-known schema.

    Schema/checksums, foreign keys, immutable-record hashes and receipt high-water
    are verified without ever opening SQLiteStore (which would be a writer).
    """
    guard()
    if (con.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
            or con.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION):
        raise BackupError("Unknown/future SQLite schema")
    if _schema(con) != _expected_schema():
        raise BackupError("Schema definitions differ from the attested migrations")
    if con.execute("SELECT version,checksum FROM schema_migrations ORDER BY version").fetchall() != schema_checksums():
        raise BackupError("Migration checksum mismatch")
    if con.execute("PRAGMA integrity_check").fetchall() != [("ok",)] or con.execute("PRAGMA foreign_key_check").fetchone():
        raise BackupError("SQLite structural integrity failed")
    tables = {}
    for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
        quoted = '"' + name.replace('"', '""') + '"'
        columns = [row[1] for row in con.execute(f"PRAGMA table_info({quoted})")]
        order = ','.join(str(i + 1) for i in range(len(columns)))
        digest, count = hashlib.sha256(), 0
        for row in con.execute(f"SELECT * FROM {quoted} ORDER BY {order}"):
            guard()
            obj = dict(zip(columns, row))
            if "record_json" in obj and "record_sha256" in obj:
                if hashlib.sha256(obj["record_json"].encode()).hexdigest() != obj["record_sha256"]:
                    raise BackupError("Record checksum mismatch")
                loads_strict(obj["record_json"].encode())
            if name == "embedding_cache" and (len(obj["vector"]) != obj["dim"] * 4
                                              or hashlib.sha256(obj["vector"]).hexdigest() != obj["vector_sha256"]
                                              or not all(math.isfinite(v[0]) for v in struct.iter_unpack('<f', obj["vector"]))):
                raise BackupError("Embedding cache dimension/checksum mismatch")
            if name == "block_versions":
                block = loads_strict(obj["record_json"].encode())
                if (block.get("scope") not in {"global", "project"} or not isinstance(block.get("name"), str)
                        or not isinstance(block.get("text"), str)
                        or (block["scope"] == "global" and block.get("project") is not None)
                        or (block["scope"] == "project" and (not isinstance(block.get("project"), str)
                                                            or not block["project"] or block["project"] == '*'))
                        or _json([block.get("project"), block["name"]]) != obj["block_key"]):
                    raise BackupError("Block scope/identity inconsistent")
            if name == "legacy_files" and hashlib.sha256(obj["contents"]).hexdigest() != obj["sha256"]:
                raise BackupError("Legacy blob checksum mismatch")
            digest.update((_json([_cell(value) for value in row]) + '\n').encode())
            count += 1
        tables[name] = {"columns": columns, "rows": count, "sha256": digest.hexdigest()}
    head = con.execute("SELECT value_json FROM store_metadata WHERE key='metadata'").fetchone()
    meta = loads_strict(head[0].encode()) if head else {}
    identity = meta.get("store_instance_id")
    if not isinstance(identity, str) or not identity:
        raise BackupError("Store identity missing")
    counter = con.execute("SELECT value FROM receipt_counter WHERE id=1").fetchone()[0]
    maximum = con.execute("SELECT COALESCE(max(sequence),0) FROM request_receipts").fetchone()[0]
    if counter < maximum:
        raise BackupError("Receipt high-water is inconsistent")
    if con.execute("SELECT 1 FROM items i WHERE current_revision<>(SELECT max(revision) FROM item_versions WHERE item_id=i.id) LIMIT 1").fetchone():
        raise BackupError("Current item revision inconsistent")
    if con.execute("SELECT 1 FROM items i JOIN item_versions v ON v.item_id=i.id AND v.revision=i.current_revision "
                   "WHERE json_extract(v.record_json,'$.id') IS NOT i.id OR json_extract(v.record_json,'$.project') IS NOT i.project "
                   "OR json_extract(v.record_json,'$.kind') IS NOT i.kind LIMIT 1").fetchone():
        raise BackupError("Current item identity inconsistent")
    for item_id, record in con.execute("SELECT item_id,record_json FROM item_versions"):
        guard()
        item = MemoryItem.from_dict(loads_strict(record.encode()))
        if item.id != item_id or not isinstance(item.text, str) or item.kind not in VALID_KINDS or not item.project or item.project == '*':
            raise BackupError("Invalid item version")
    for sequence, scope, request_id, record in con.execute("SELECT sequence,scope,request_id,record_json FROM request_receipts"):
        guard()
        value = loads_strict(record.encode())
        if (value.get("sequence"), value.get("scope"), value.get("request_id"), value.get("state")) != (sequence, scope, request_id, "complete"):
            raise BackupError("Receipt identity/state inconsistent")
        if not isinstance(value.get("fingerprint"), str) or (value.get("explicit", True) and "result" not in value):
            raise BackupError("Receipt completion evidence missing")
    return {"schema_version": SCHEMA_VERSION, "schema_sha256": hashlib.sha256(_json(_schema(con)).encode()).hexdigest(),
            "tables": tables, "store_instance_id": identity, "snapshot_receipt_sequence": counter,
            "encoding_profile": meta.get("embed_compute_profile"), "package_version": meta.get("version")}


def snapshot(source, destination, guard=lambda: None, fault=lambda point: None):
    """Pin one source read transaction, copy with SQLite's online backup API."""
    source, destination = plain_path(source), plain_path(destination)
    if destination.exists() or destination.is_relative_to(source.parent):
        raise BackupError("Fresh destination outside the source store required")
    fault("before_source_open")
    src = readonly(source)
    dst = None
    try:
        src.set_progress_handler(lambda: (guard(), 0)[1], 2000)
        src.execute("BEGIN")
        src.execute("SELECT value_json FROM store_metadata WHERE key='metadata'").fetchone()  # Pin before cut/identity.
        fault("after_pin")
        before = inventory(src, guard)
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        dst = sqlite3.connect(destination, timeout=1)
        dst.set_progress_handler(lambda: (guard(), 0)[1], 2000)
        busy_at = None
        page_size = src.execute("PRAGMA page_size").fetchone()[0]

        def progress(status, remaining, total):
            nonlocal busy_at
            guard()
            if total * page_size > MAX_DB:
                raise BackupError("Snapshot exceeds 64 MiB cap")
            if status in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                busy_at = busy_at or time.monotonic()
                if time.monotonic() - busy_at > 30:
                    raise BackupError("Online backup BUSY budget exceeded")
            else:
                busy_at = None

        fault("before_backup")
        src.backup(dst, pages=128, progress=progress, sleep=0.01)
        fault("after_backup")
        after = inventory(dst, guard)
        if before != after:
            raise BackupError("Snapshot differs from pinned source cut")
        if dst.execute("PRAGMA journal_mode=DELETE").fetchone()[0].lower() != "delete":
            raise BackupError("Destination is not a standalone database")
        dst.close()
        dst = None
        with destination.open("rb") as fh:
            os.fsync(fh.fileno())
        fault("after_snapshot_verify")
        return before
    finally:
        if dst is not None:
            dst.close()
        src.close()


def verify_archive(archive, key, target, guard=lambda: None):
    archive, target = plain_path(archive), plain_path(target)
    if archive.stat().st_size > MAX_ARCHIVE or target.exists():
        raise BackupError("Archive cap exceeded or target is not fresh")
    data = decrypt(archive.read_bytes(), key)
    if len(data) > MAX_ARCHIVE:
        raise BackupError("Decrypted archive cap exceeded")
    # Bound decompression before tarfile interprets extension headers (PAX etc.).
    # A small encrypted gzip must not allocate an unbounded tar/header payload.
    guard()
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
        unpacked = stream.read(MAX_DB + MAX_MANIFEST + 65536 + 1)
    if len(unpacked) > MAX_DB + MAX_MANIFEST + 65536:
        raise BackupError("Decompressed archive cap exceeded")
    guard()
    target.mkdir(mode=0o700)
    allowed = {f"{ROOT}/{DB}": MAX_DB, f"{ROOT}/MANIFEST.json": MAX_MANIFEST}
    seen = set()
    with tarfile.open(fileobj=io.BytesIO(unpacked), mode="r:") as tar:
        for member in tar:
            guard()
            if not member.isfile() or member.name not in allowed or member.name in seen or member.size > allowed[member.name]:
                raise BackupError("Unsafe/duplicate/oversized SQLite archive member")
            seen.add(member.name)
            value = tar.extractfile(member).read(member.size + 1)
            if len(value) != member.size:
                raise BackupError("Truncated archive member")
            path = target / member.name.split('/')[-1]
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(value)
                stream.flush()
                os.fsync(stream.fileno())
    if seen != set(allowed):
        raise BackupError("Missing SQLite archive member")
    manifest = loads_strict((target / "MANIFEST.json").read_bytes())
    if manifest.get("format") != FORMAT or manifest.get("qualified") is not True:
        raise BackupError("Unknown or unqualified archive format")
    expected = manifest.get("files", {}).get(DB, {})
    if set(manifest.get("files", {})) != {DB} or expected != {"bytes": (target / DB).stat().st_size, "sha256": sha256(target / DB)}:
        raise BackupError("Database bytes differ from manifest")
    con = readonly(target / DB, sealed=True)
    try:
        if inventory(con, guard) != manifest.get("logical_inventory"):
            raise BackupError("Exhaustive logical snapshot comparison failed")
    finally:
        con.close()
    return manifest


def backup(settings, *, apply=False, fault=lambda point: None):
    disjoint(settings)
    plan = {"ok": True, "mode": "simulation", "backend": "sqlite", "online": True,
            "storage": margin(settings), "source_sql_writes": False, "qualified": False,
            "production_activation": False, "cap_bytes": MAX_DB}
    if not apply:
        return plan
    record = {**plan, "ok": False, "mode": "apply", "verified": False,
              "stamp": dt.datetime.now().strftime("%Y%m%d-%H%M%S")}
    work = lock = None
    try:
        budget = Budget(settings)
        lock = lease(settings)
        key = load_key(settings.backup_key)  # Never create/replace a key as a side effect.
        settings.backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        sync_directory(settings.backup_dir.parent)
        work = Path(tempfile.mkdtemp(prefix="sqlite-s4-", dir=settings.backup_dir))
        logical = snapshot(settings.store_dir / DB, work / DB, budget.check, fault)
        manifest = {"format": FORMAT, "created": record["stamp"], "qualified": True,
                    "snapshot_cut_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "cut_semantics": "pinned source transaction; UTC is observation, sequence is authority",
                    "backup_implementation_sha256": sha256(Path(__file__)),
                    "source_build": {"version": logical["package_version"], "commit": None,
                                     "attested": False, "reason": "not recorded by existing store schema"},
                    "logical_inventory": logical, "files": {DB: {"bytes": (work / DB).stat().st_size,
                                                                  "sha256": sha256(work / DB)}}}
        blob = encrypt(build_archive({DB: (work / DB).read_bytes()}, manifest), key)
        if len(blob) > MAX_ARCHIVE:
            raise BackupError("Encrypted archive cap exceeded")
        partial = work / "archive.partial"
        fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as fh:
            fh.write(blob)
            fh.flush()
            os.fsync(fh.fileno())
        fault("before_archive_verify")
        verify_archive(partial, key, work / "verify", budget.check)
        fault("before_publish")
        name = PREFIX + record["stamp"] + '-' + uuid.uuid4().hex[:8] + '.mosb'
        final = settings.backup_dir / name
        os.link(partial, final)  # Never overwrite; both paths kept if a later fsync fails.
        sync_directory(settings.backup_dir)
        partial.unlink()  # Final archive must be single-link before qualification is logged.
        sync_directory(work)
        fault("after_publish")
        record.update(ok=True, qualified=True, verified=True, archive=name, archive_sha256=sha256(final),
                      snapshot_receipt_sequence=logical["snapshot_receipt_sequence"],
                      store_instance_id=logical["store_instance_id"], level="snapshot",
                      bytes=len(blob), restore_reopening_qualified=False)
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
        if work is not None:
            record["preserved_partial"] = str(work)
    try:
        if work is not None:
            fault('before_registration')
            register(settings, "backup.jsonl", record)
            fault('after_registration')
            if record["ok"]:
                shutil.rmtree(work)  # Successful disposable work only; archives and failures are kept.
                try:
                    record['rotated'] = rotate_archives(settings, pinned=record['archive'])
                except Exception as exc:
                    # Rotation failure cannot invalidate a fully verified durable snapshot.
                    record['rotation_warning'] = type(exc).__name__
    except Exception as exc:
        record.update(ok=False, error=f"Backup registration/cleanup failed: {type(exc).__name__}", preserved_partial=str(work))
    finally:
        if lock is not None:
            os.close(lock)
    return record


def rotate_archives(settings, *, pinned, fault_hook=None):
    from .backup import rotate, verified_archives
    eligible = {name: value for name, value in verified_archives(settings).items()
                if name.startswith(PREFIX) and Path(name).name == name
                and (settings.backup_dir / name).is_file()
                and not (settings.backup_dir / name).is_symlink()
                and (settings.backup_dir / name).stat().st_nlink == 1}
    if pinned not in eligible or sha256(settings.backup_dir / pinned) != eligible[pinned][0]:
        raise BackupError('Pinned qualified SQLite archive missing/divergent; no rotation')
    removed = rotate(settings.backup_dir, eligible, pinned=pinned, fault_hook=fault_hook)
    if fault_hook:
        fault_hook('before_rotation_fsync')
    sync_directory(settings.backup_dir)
    if fault_hook:
        fault_hook('before_rotation_registration')
    register(settings, 'sqlite_rotation.jsonl', {'ok': True, 'pinned': pinned, 'removed': removed})
    if fault_hook:
        fault_hook('after_rotation_registration')
    return removed


def restore_drill(settings, archive=None, *, apply=False, start_server=False, reconcile=False):
    disjoint(settings)
    result = {"ok": True, "mode": "simulation", "backend": "sqlite", "level": "files",
              "quarantined": True, "reopening_allowed": False, "forgets_replayed": [],
              "forget_reconciliation": "pending", "storage": margin(settings)}
    if not apply:
        return result
    result.update(ok=False, mode="apply")
    work = lock = None
    start = time.monotonic()
    try:
        if start_server and not reconcile:
            raise BackupError("Service reopening unavailable without explicit --reconcile-forgets")
        budget = Budget(settings)
        lock = lease(settings)
        key = load_key(settings.backup_key)
        if archive is None:
            from .backup import verified_archives
            eligible = verified_archives(settings)
            candidates = sorted(settings.backup_dir / name for name in eligible if name.startswith(PREFIX))
            archive = candidates[-1] if candidates else None
            if archive is None or sha256(archive) != eligible[archive.name][0]:
                raise BackupError("No intact, logged qualified SQLite archive")
        archive = plain_path(archive)
        settings.ops_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix="sqlite-drill-", dir=settings.ops_dir))
        manifest = verify_archive(archive, key, work / "restored", budget.check)
        result.update(verified=True, archive=archive.name, archive_sha256=sha256(archive),
                      snapshot_receipt_sequence=manifest["logical_inventory"]["snapshot_receipt_sequence"],
                      store_instance_id=manifest["logical_inventory"]["store_instance_id"])
        if reconcile:
            from .sqlite_recovery import prepare_recovery, authority_cut
            recovered = prepare_recovery(work / 'restored' / DB, settings.store_dir / DB,
                                         manifest, guard=budget.check)
            result.update(recovered, level='reconciled')
            if start_server:
                from .sqlite_probe import probe
                result['server_check'] = probe(work / 'restored', settings, recovered, guard=budget.check)
                result.update(level='service', private_reopen_verified=True)
            authority_cut(settings.store_dir / DB, recovered['reconciliation'], budget.check)
        result['ok'] = True
    except Exception as exc:
        result.update(ok=False, private_reopen_verified=False)
        result["error"] = f"{type(exc).__name__}: {exc}"
        if work is not None:
            result["preserved_partial"] = str(work)
    result["duration_s"] = round(time.monotonic() - start, 3)
    result["completed_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    try:
        if work is not None:
            register(settings, "restore_drill.jsonl", result)
            if result["ok"]:
                shutil.rmtree(work)
    except Exception as exc:
        result.update(ok=False, error=f"Drill registration/cleanup failed: {type(exc).__name__}", preserved_partial=str(work))
    finally:
        if lock is not None:
            os.close(lock)
    return result

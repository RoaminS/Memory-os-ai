"""Strict offline migration on copies; immutable originals and verified exports.

No implicit backend switch. CLI defaults to dry-run. Read-only kernel leases
require an existing writer.lock and refuse a running engine. macOS/Linux only.
"""

from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
import hashlib
import ctypes
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time
import uuid

from .ops.storecheck import _jsonl, check_files, loads_strict
from .sqlite_engine import SQLiteBlocks, SQLiteRequests, fingerprint
from .sqlite_store import APPLICATION_ID, SCHEMA_VERSION, SQLiteStore, _json, schema_checksums
from .store import MemoryItem
from .writer_lock import WriterLock

MAX_SOURCE_BYTES = 128 * 1024 * 1024


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _safe(path):
    path = Path(path).expanduser()
    if path.is_symlink():
        raise ValueError("Linked source/destination refused")
    path = path.resolve()
    for forbidden in (Path.home() / ".memory-os-v4", Path.home() / "Memory-os-ai"):
        forbidden = forbidden.resolve()
        if path == forbidden or forbidden in path.parents:
            raise ValueError("Production/original-repository path refused; qualification copies only")
    return path


def _paths(source, destination):
    source, destination = _safe(source), _safe(destination)
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError("Source and destination must be disjoint")
    if not source.is_dir() or not destination.parent.is_dir():
        raise ValueError("Source and destination parent must already exist")
    return source, destination


@contextmanager
def offline_lease(source):
    if os.name == "nt":
        raise RuntimeError("Read-only offline migration lease not qualified on Windows")
    import fcntl

    path = source / "writer.lock"
    if not path.is_file() or path.is_symlink() or path.stat().st_nlink != 1:
        raise ValueError("Existing regular writer.lock required; source is never changed to create one")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError(
                "Source has an active writer; migration/export requires offline maintenance"
            ) from exc
        yield
    finally:
        os.close(fd)


def _files(source):
    files = {}
    size = 0
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise ValueError("Linked snapshot entry refused")
        if path.is_dir():
            continue
        if not path.is_file() or path.stat().st_nlink != 1:
            raise ValueError("Non-regular/hardlinked snapshot entry refused")
        size += path.stat().st_size
        if size > MAX_SOURCE_BYTES:
            raise ValueError("Snapshot exceeds qualified 128 MiB bound")
        files[path.relative_to(source).as_posix()] = path.read_bytes()
    return files


def _file_manifest(files):
    return {name: {"sha256": _sha(data), "bytes": len(data)} for name, data in files.items()}


def _inventory(files):
    if "store.sqlite3" in files:
        raise ValueError("Source must be JSONL-only, no dual authority")
    check_files(files)
    for name, data in files.items():
        if name.endswith(".jsonl"):
            _jsonl(name, data)  # Includes repair/audit traces, never skip malformed lines.
    items, blocks = [], []
    normalized = {}
    for raw in _jsonl("items.jsonl", files["items.jsonl"]):
        row = dict(raw)
        for key, default in (("project", "default"), ("created_at", 0), ("event_time", 0)):
            if key not in row:
                row[key] = default
                normalized[f"item.{key}"] = normalized.get(f"item.{key}", 0) + 1
        items.append(row)
    for raw in _jsonl("blocks.jsonl", files.get("blocks.jsonl", b"")):
        row = dict(raw)
        row.setdefault("scope", "project")
        row["project"] = None if row["scope"] == "global" else row.get("project", "default")
        row.setdefault("updated_at", 0)
        if row != raw:
            normalized["block.scope_defaults"] = normalized.get("block.scope_defaults", 0) + 1
        blocks.append(row)
    requests = _jsonl("requests.jsonl", files.get("requests.jsonl", b""))
    last = {}
    for row in requests:
        key = (row["scope"], row["request_id"])
        if (
            row.get("state") not in {"pending", "complete"}
            or not isinstance(row.get("fingerprint"), str)
            or (row["state"] == "complete" and "result" not in row)
        ):
            raise ValueError("Invalid legacy receipt state/fingerprint/result")
        if key in last and row["fingerprint"] != last[key]["fingerprint"]:
            raise ValueError("Conflicting legacy fingerprints for one request identity")
        last[key] = row
    return {
        "items": items,
        "blocks": blocks,
        "requests": requests,
        "last_requests": last,
        "meta": loads_strict(files.get("meta.json", b"{}")),
        "normalizations": normalized,
    }


def _authority_digest(connection):
    tables = (
        "items",
        "item_versions",
        "block_versions",
        "request_receipts",
        "receipt_counter",
        "store_metadata",
        "legacy_request_log",
        "legacy_pending_requests",
    )
    rows = {table: connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall() for table in tables}
    rows["legacy_files"] = connection.execute("SELECT path,sha256 FROM legacy_files ORDER BY path").fetchall()
    return _sha(_json(rows).encode())


def _connect_offline(source):
    path = source / "store.sqlite3"
    if not path.is_file() or path.is_symlink() or path.stat().st_nlink != 1:
        raise ValueError("Regular SQLite database required")
    wal = source / "store.sqlite3-wal"
    if wal.exists() and wal.stat().st_size:
        raise ValueError("Offline checkpointed database required; nonempty WAL cannot be ignored")
    # immutable=1 is safe only AFTER the offline lease and WAL check. It avoids
    # even creating WAL/SHM files in the source directory.
    db = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        if (
            db.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
            or db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION
            or db.execute("SELECT version,checksum FROM schema_migrations ORDER BY version").fetchall()
            != schema_checksums()
            or db.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
            or db.execute("PRAGMA foreign_key_check").fetchone() is not None
        ):
            raise ValueError("Offline SQLite schema/integrity check failed")
        return db
    except BaseException:
        db.close()
        raise


def _compare(store, files, expected):
    store.load()
    store.load_chunk_map()
    if not store.check_integrity():
        raise ValueError("Imported SQLite source integrity failed")
    if store._connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise ValueError("Imported SQLite integrity_check failed")
    actual_items = [
        json.loads(row[0])
        for row in store._connection.execute("SELECT record_json FROM item_versions ORDER BY rowid")
    ]
    actual_blocks = [
        json.loads(row[0])
        for row in store._connection.execute("SELECT record_json FROM block_versions ORDER BY sequence")
    ]
    actual_requests = [
        json.loads(row[0])
        for row in store._connection.execute("SELECT record_json FROM legacy_request_log ORDER BY ordinal")
    ]
    archived = dict(store._connection.execute("SELECT path,contents FROM legacy_files"))
    if (
        actual_items != expected["items"]
        or actual_blocks != expected["blocks"]
        or actual_requests != expected["requests"]
        or archived != files
        or store.meta != expected["meta"]
    ):
        raise ValueError("Exhaustive migration comparison failed")
    heads = {row["id"]: row for row in expected["items"]}
    if {key: item.to_dict() for key, item in store.items.items()} != {
        key: MemoryItem.from_dict(row).to_dict() for key, row in heads.items()
    }:
        raise ValueError("Imported item heads disagree")
    SQLiteBlocks(store, 2000)  # Validate block identities and scopes, including unknown fields.
    journal = SQLiteRequests(store)
    for key, row in expected["last_requests"].items():
        record = journal.records[key]
        if record["state"] != row["state"] or record["fingerprint"] != row["fingerprint"]:
            raise ValueError("Imported request state mismatch")
        if row["state"] == "complete" and record["result"] != row["result"]:
            raise ValueError("Imported completed response mismatch")
    return {
        "item_versions": len(actual_items),
        "items": len(heads),
        "block_versions": len(actual_blocks),
        "legacy_request_versions": len(actual_requests),
        "legacy_pending": sum(row["state"] == "pending" for row in expected["last_requests"].values()),
        "archived_files": len(files),
        "comparison": "all records, heads, fields, responses and original file bytes",
    }


def _fsync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _publish(stage, destination):
    if destination.exists():
        raise ValueError("Destination exists; never overwrite")
    # Atomic no-replace, including a destination created after the existence check.
    libc = ctypes.CDLL(None, use_errno=True)
    if hasattr(libc, "renamex_np"):  # Darwin, sys/stdio.h RENAME_EXCL=0x4.
        rename = libc.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rc = rename(os.fsencode(stage), os.fsencode(destination), 4)
    elif hasattr(libc, "renameat2"):  # Linux, AT_FDCWD=-100, RENAME_NOREPLACE=1.
        rename = libc.renameat2
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        rc = rename(-100, os.fsencode(stage), -100, os.fsencode(destination), 1)
    else:
        raise RuntimeError("Atomic no-replace directory publication unavailable; no unsafe fallback")
    if rc != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(destination))
    _fsync_directory(destination.parent)


def migrate(source, destination, *, apply=False, fault_hook=None):
    source, destination = _paths(source, destination)
    with offline_lease(source):
        files = _files(source)
        expected = _inventory(files)
        manifest = {
            "format": "memory-os-jsonl-sqlite-v1",
            "files": _file_manifest(files),
            "normalizations": expected["normalizations"],
            "schema": SCHEMA_VERSION,
        }
        source_digest = _sha(_json(manifest["files"]).encode())
        manifest["source_sha256"] = source_digest
        abandoned = []
        for candidate in sorted(destination.parent.glob("." + destination.name + ".migrating-*")):
            intent = candidate / "migration-intent.json"
            if candidate.is_symlink() or intent.is_symlink() or not intent.is_file():
                continue
            try:
                prior_intent = loads_strict(intent.read_bytes())
            except ValueError:
                continue
            if prior_intent == {"source": str(source), "source_sha256": source_digest}:
                abandoned.append(candidate.name)
        manifest["abandoned_stages"] = abandoned  # Preserve for audit; never append/reuse a partial DB.
        if destination.exists():
            with offline_lease(destination):
                db = _connect_offline(destination)
                try:
                    prior = loads_strict((destination / "migration.json").read_bytes())
                    if (
                        prior["source_sha256"] != source_digest
                        or _authority_digest(db) != prior["authority_sha256"]
                    ):
                        raise ValueError("Existing target differs or has new writes; no reimport/downgrade")
                    if _verify_archives(db) != files:
                        raise ValueError("Existing target archive differs from source")
                    return {**prior, "already_migrated": True}
                finally:
                    db.close()
        if not apply:
            return {
                **manifest,
                "dry_run": True,
                "item_versions": len(expected["items"]),
                "block_versions": len(expected["blocks"]),
                "request_versions": len(expected["requests"]),
            }
        stage = destination.parent / ("." + destination.name + ".migrating-" + uuid.uuid4().hex)
        stage.mkdir(mode=0o700)
        store = None
        try:
            with (stage / "migration-intent.json").open("w") as fh:
                fh.write(_json({"source": str(source), "source_sha256": source_digest}) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            _fsync_directory(stage)
            with closing(WriterLock(stage)):
                store = SQLiteStore(stage)
                store.fault_hook = fault_hook
                with store.transaction():
                    revisions, identities = {}, {}
                    for row in expected["items"]:
                        item_id = row["id"]
                        identity = (row["project"], row["kind"])
                        if item_id in identities and identities[item_id] != identity:
                            raise ValueError(
                                "Legacy item changes project/kind; explicit reconciliation required"
                            )
                        identities[item_id] = identity
                        revision = revisions[item_id] = revisions.get(item_id, 0) + 1
                        record = _json(row)
                        store.write(
                            "migration_item_version",
                            "INSERT INTO item_versions VALUES(?,?,?,?,?)",
                            (item_id, revision, record, _sha(record.encode()), row["created_at"]),
                        )
                        store.write(
                            "migration_item_head",
                            "INSERT INTO items VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE "
                            "SET current_revision=excluded.current_revision",
                            (item_id, *identity, revision),
                        )
                    for row in expected["blocks"]:
                        record = _json(row)
                        store.write(
                            "migration_block",
                            "INSERT INTO block_versions(block_key,record_json,record_sha256) VALUES(?,?,?)",
                            (_json((row["project"], row["name"])), record, _sha(record.encode())),
                        )
                    for ordinal, row in enumerate(expected["requests"], 1):
                        record = _json(row)
                        store.write(
                            "legacy_request_log",
                            "INSERT INTO legacy_request_log VALUES(?,?,?)",
                            (ordinal, record, _sha(record.encode())),
                        )
                    sequence = 0
                    for (scope, request_id), row in expected["last_requests"].items():
                        if row["state"] == "pending":
                            record = _json(row)
                            store.write(
                                "legacy_pending",
                                "INSERT INTO legacy_pending_requests VALUES(?,?,?,?)",
                                (scope, request_id, record, _sha(record.encode())),
                            )
                        else:
                            sequence += 1
                            record = _json(
                                {
                                    "scope": scope,
                                    "request_id": request_id,
                                    "sequence": sequence,
                                    "fingerprint": row["fingerprint"],
                                    "state": "complete",
                                    "explicit": True,
                                    "operation": row.get("operation", "legacy_unknown"),
                                    "committed_at": 0,
                                    "result": row["result"],
                                    "legacy_record": row,
                                }
                            )
                            store.write(
                                "legacy_complete",
                                "INSERT INTO request_receipts VALUES(?,?,?,?,?)",
                                (sequence, scope, request_id, record, _sha(record.encode())),
                            )
                    store.write(
                        "legacy_counter", "UPDATE receipt_counter SET value=? WHERE id=1", (sequence,)
                    )
                    for name, data in files.items():
                        store.write(
                            "legacy_file", "INSERT INTO legacy_files VALUES(?,?,?)", (name, data, _sha(data))
                        )
                    store.save_meta(expected["meta"])
                    if "chunk_map.json" in files:
                        store.save_chunk_map(loads_strict(files["chunk_map.json"]))
                    SQLiteRequests(store).complete(
                        "__migration__",
                        source_digest,
                        "migrate_jsonl",
                        {"source_sha256": source_digest},
                        {"ok": True, "item_versions": len(expected["items"])},
                    )
                store.fault_hook = None
                manifest["comparison"] = _compare(store, files, expected)
                manifest["authority_sha256"] = _authority_digest(store._connection)
                store._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                store.close()
                store = None
                if _file_manifest(_files(source)) != manifest["files"]:
                    raise ValueError("Source changed during migration; publication refused")
                with (stage / "migration.json").open("w") as fh:
                    fh.write(_json(manifest) + "\n")
                    fh.flush()
                    os.fsync(fh.fileno())
                _fsync_directory(stage)
            if fault_hook:
                fault_hook("before_publish")
            _publish(stage, destination)
            if fault_hook:
                fault_hook("after_publish")
            return manifest
        except BaseException:
            if store is not None:
                store.close()
            if stage.exists():
                shutil.rmtree(stage)
            raise


def _verify_archives(connection):
    files = {}
    for name, data, checksum in connection.execute("SELECT path,contents,sha256 FROM legacy_files"):
        if _sha(data) != checksum:
            raise ValueError("Legacy archive checksum mismatch")
        rel = Path(name)
        if rel.is_absolute() or ".." in rel.parts or not rel.parts:
            raise ValueError("Unsafe archive path")
        files[name] = data
    return files


def export(source, destination, *, original=False, rollback=False, fault_hook=None):
    """Original byte restore only while authority is unchanged; otherwise semantic export."""
    if original and rollback:
        raise ValueError('Rollback requires the current semantic authority, never an original byte restore')
    source, destination = _paths(source, destination)
    with offline_lease(source):
        if destination.exists():
            raise ValueError("Export destination exists; never overwrite")
        before = _file_manifest(_files(source))
        connection = _connect_offline(source)
        stage = destination.parent / ("." + destination.name + ".exporting-" + uuid.uuid4().hex)
        try:
            connection.execute("BEGIN")
            files = _verify_archives(connection)
            if original:
                manifest = loads_strict((source / "migration.json").read_bytes())
                if _authority_digest(connection) != manifest["authority_sha256"]:
                    raise ValueError(
                        "New SQLite writes exist; original restore would lose data; use semantic export"
                    )
                if _file_manifest(files) != manifest["files"]:
                    raise ValueError("Archive differs from migration manifest")
            else:
                # Preserve exact original files separately, and export all current versions.
                files = {"legacy-original/" + name: data for name, data in files.items()}
                for name, query in (
                    ("items.jsonl", "SELECT record_json FROM item_versions ORDER BY rowid"),
                    ("blocks.jsonl", "SELECT record_json FROM block_versions ORDER BY sequence"),
                    ("sqlite-receipts.jsonl", "SELECT record_json FROM request_receipts ORDER BY sequence"),
                ):
                    files[name] = b"".join(row[0].encode() + b"\n" for row in connection.execute(query))
                pending = [
                    json.loads(row[0])
                    for row in connection.execute("SELECT record_json FROM legacy_pending_requests")
                ]
                receipts = [
                    json.loads(row[0])
                    for row in connection.execute(
                        "SELECT record_json FROM request_receipts WHERE COALESCE(json_extract(record_json,'$.explicit'),1)=1"
                    )
                ]
                files["requests.jsonl"] = b"".join(
                    _json(row).encode() + b"\n" for row in [*receipts, *pending]
                )
                row = connection.execute(
                    "SELECT value_json FROM store_metadata WHERE key='metadata'"
                ).fetchone()
                files["meta.json"] = (row[0] if row else "{}").encode()
                files["writer.lock"] = b""
                files["export.json"] = _json(
                    {
                        "format": "sqlite-semantic-export-v1",
                        "at": time.time(),
                        "authority_sha256": _authority_digest(connection),
                        "automatic_downgrade": False,
                    }
                ).encode()
                if rollback:
                    meta = loads_strict(files['meta.json'])
                    old_id = meta.get('store_instance_id')
                    if not isinstance(old_id, str) or not old_id:
                        raise ValueError('Rollback requires an enrolled source identity')
                    event = {'operation': 'sqlite_jsonl_rollback', 'at': time.time(),
                             'store_instance_id': uuid.uuid4().hex, 'restored_from_instance_id': old_id,
                             'source_authority_sha256': _authority_digest(connection),
                             'source_receipt_sequence': connection.execute(
                                 'SELECT value FROM receipt_counter WHERE id=1').fetchone()[0]}
                    meta.update(store_instance_id=event['store_instance_id'], restored_from_instance_id=old_id)
                    meta['recovery_history'] = [*meta.get('recovery_history', []), event]
                    files['meta.json'] = _json(meta).encode()
                    result = {**event, 'ok': True, 'automatic_downgrade': False,
                              'work_state': False, 'block_cas': False, 'reopening_allowed': False}
                    receipt = {'scope': 'operator:s4', 'request_id': 's4-rollback-' + event['store_instance_id'],
                               'fingerprint': fingerprint('sqlite_jsonl_rollback', event),
                               'operation': 'sqlite_jsonl_rollback', 'state': 'complete', 'explicit': True,
                               'result': result, 'store_instance_id': event['store_instance_id']}
                    files['requests.jsonl'] += _json(receipt).encode() + b'\n'
                    files['rollback.json'] = _json(result).encode()
            stage.mkdir(mode=0o700)
            for name, data in files.items():
                if fault_hook:
                    fault_hook('before_export_file:' + name)
                path = stage / name
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as fh:
                    fh.write(data)
                    fh.flush()
                    os.fsync(fh.fileno())
                if fault_hook:
                    fault_hook('after_export_file:' + name)
            if _file_manifest(_files(stage)) != _file_manifest(files):
                raise ValueError("Export byte comparison failed")
            for directory in sorted(
                (p for p in stage.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True
            ):
                _fsync_directory(directory)
            _fsync_directory(stage)
            if _file_manifest(_files(source)) != before:
                raise ValueError("SQLite export source changed; publication refused")
            if fault_hook:
                fault_hook('before_export_publish')
            _publish(stage, destination)
            if fault_hook:
                fault_hook('after_export_publish')
            return {"original": original, "rollback": rollback, "files": _file_manifest(files),
                    "automatic_downgrade": False, "reopening_allowed": False}
        except BaseException:
            if stage.exists():
                shutil.rmtree(stage)
            raise
        finally:
            connection.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    imp = sub.add_parser("import")
    imp.add_argument("source")
    imp.add_argument("destination")
    imp.add_argument("--apply", action="store_true")
    out = sub.add_parser("export")
    out.add_argument("source")
    out.add_argument("destination")
    out.add_argument("--original", action="store_true")
    out.add_argument('--rollback', action='store_true', help='Current-authority export with a new store identity; no activation')
    args = parser.parse_args(argv)
    try:
        result = (
            migrate(args.source, args.destination, apply=args.apply)
            if args.operation == "import"
            else export(args.source, args.destination, original=args.original, rollback=args.rollback)
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

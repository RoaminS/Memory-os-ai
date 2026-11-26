"""Read-only SQLite operational counters, including logical receipt size."""

from __future__ import annotations

import json
from pathlib import Path
import time

IMPLICIT_RECEIPT_LIMIT = 256


def receipt_statistics(connection, path):
    explicit = implicit = logical_bytes = 0
    for kind, count, size in connection.execute(
        "SELECT COALESCE(json_extract(record_json,'$.explicit'),1),count(*),"
        "COALESCE(sum(length(CAST(record_json AS BLOB))),0) FROM request_receipts GROUP BY 1"
    ):
        if kind:
            explicit += count
        else:
            implicit += count
        logical_bytes += size
    path = Path(path)
    pending, pending_bytes = (
        connection.execute(
            "SELECT count(*),COALESCE(sum(length(CAST(record_json AS BLOB))),0) FROM legacy_pending_requests"
        ).fetchone()
        if connection.execute("SELECT 1 FROM sqlite_master WHERE name='legacy_pending_requests'").fetchone()
        else (0, 0)
    )
    return {
        "explicit": explicit,
        "implicit": implicit,
        "implicit_limit": IMPLICIT_RECEIPT_LIMIT,
        "json_bytes": logical_bytes,
        "db_bytes": path.stat().st_size,
        "wal_bytes": Path(str(path) + "-wal").stat().st_size if Path(str(path) + "-wal").exists() else 0,
        "explicit_retention": "indefinite",
        "implicit_result": False,
        "legacy_pending": pending,
        "legacy_pending_json_bytes": pending_bytes,
    }


def scan_sqlite(path):
    from .sqlite_store import APPLICATION_ID, SCHEMA_VERSION

    path = Path(path)
    if path.is_symlink() or path.stat().st_nlink != 1:
        raise ValueError("Linked SQLite database refused")
    path = path.resolve()
    from .sqlite_store import open_readonly   # 4.1.3: lock-protected offline read; live read with a declared window

    with open_readonly(path) as connection:
        return _scan(connection, path, APPLICATION_ID, SCHEMA_VERSION)


def _scan(connection, path, APPLICATION_ID, SCHEMA_VERSION):
    """One consistent read snapshot of an opened store (statistics for doctor/health)."""
    connection.execute("PRAGMA query_only=ON")
    connection.execute("BEGIN")
    if (
        connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
        or connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION
    ):
        raise ValueError("SQLite schema unsupported; doctor never upgrades it")
    if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise ValueError("SQLite integrity check failed")
    rows = [
        json.loads(row[0])
        for row in connection.execute(
            "SELECT v.record_json FROM items i JOIN item_versions v ON i.id=v.item_id AND i.current_revision=v.revision"
        )
    ]
    live = [row for row in rows if not row.get("deleted")]
    added = {}
    for row in rows:
        if row.get("created_at", 0) >= time.time() - 86400:
            added[row["kind"]] = added.get(row["kind"], 0) + 1
    result = {
        "backend": "sqlite",
        "live_total": len(live),
        "facts_valid": sum(row["kind"] == "fact" and row.get("valid_until") is None for row in live),
        "added_24h": added,
        "forgotten_24h": connection.execute(
            "SELECT count(*) FROM item_versions v JOIN item_versions p ON v.item_id=p.item_id AND v.revision=p.revision+1 "
            "WHERE json_extract(v.record_json,'$.deleted')=1 AND COALESCE(json_extract(p.record_json,'$.deleted'),0)=0 "
            "AND v.recorded_at>=?",
            (time.time() - 86400,),
        ).fetchone()[0],
        "malformed_lines": 0,
        "truncated_tail": False,
        "superseded_ids": sorted(row["id"] for row in rows if row.get("invalidated_by")),
        "receipts": receipt_statistics(connection, path),
    }
    from .changes import statistics
    result["change_journal"] = statistics(connection)
    return result

"""Model-free exhaustive schema-seven/eight comparison for disposable copies."""

from __future__ import annotations

import hashlib
import json
import sqlite3


def canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def inventory(con):
    if con.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise ValueError("SQLite integrity check failed")
    if con.execute("PRAGMA foreign_key_check").fetchall():
        raise ValueError("SQLite foreign keys invalid")
    tables = {}
    for (name,) in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ):
        quoted = '"' + name.replace('"', '""') + '"'
        rows = [
            [{"blob_hex": v.hex()} if isinstance(v, bytes) else v for v in row]
            for row in con.execute("SELECT * FROM " + quoted)
        ]
        tables[name] = sorted(rows, key=canonical)
    return {
        "schema": con.execute("PRAGMA user_version").fetchone()[0],
        "application_id": con.execute("PRAGMA application_id").fetchone()[0],
        "objects": con.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        ).fetchall(),
        "tables": tables,
    }


def verify_upgrade(before, after, *, started, ended):
    """Only migration eight, its empty table and prospective journal state may appear.

    Does not waive checksum validation: SQLiteStore must perform that first.
    No authority data or journal secret is included in the returned report.
    """
    from memory_os_v4.sqlite_store import SCHEMA_CHANGES, schema_checksums

    if (
        before["schema"] != 7
        or after["schema"] != 8
        or before["application_id"] != after["application_id"]
    ):
        raise ValueError("Expected exact schema-seven to eight transition")
    if (
        set(after["tables"]) != set(before["tables"]) | {"change_events"}
        or after["tables"]["change_events"]
    ):
        raise ValueError("Unexpected tables or retrospective events")
    new_objects = [row for row in after["objects"] if row not in before["objects"]]
    if any(row not in after["objects"] for row in before["objects"]):
        raise ValueError("Existing SQL objects changed")
    if {row[3] for row in new_objects} != set(SCHEMA_CHANGES):
        raise ValueError("Unexpected migration SQL objects")
    checked = {}
    for name, rows in before["tables"].items():
        actual = after["tables"][name]
        if name == "schema_migrations":
            added = [r for r in actual if r not in rows]
            if (
                len(added) != 1
                or tuple(added[0][:2]) != schema_checksums()[7]
                or not started <= added[0][2] <= ended
            ):
                raise ValueError("Unexpected migration ledger")
            actual = [r for r in actual if r[0] != 8]
        elif name == "store_metadata":
            added = [r for r in actual if r not in rows]
            if len(added) != 1 or added[0][0] != "change_journal":
                raise ValueError("Existing metadata changed")
            journal = json.loads(added[0][1])
            if (
                set(journal) != {"generation", "key", "coverage_start", "floor", "high"}
                or len(journal["generation"]) != 32
                or len(journal["key"]) != 64
                or not started <= journal["coverage_start"] <= ended
                or journal["floor"] != 0
                or journal["high"] != 0
            ):
                raise ValueError("Invalid prospective journal initialization")
            int(journal["generation"], 16)
            int(journal["key"], 16)
            actual = [r for r in actual if r[0] != "change_journal"]
        if actual != rows:
            raise ValueError("Existing table changed: " + name)
        checked[name] = {"rows": len(rows), "sha256": fingerprint(rows)}
    metadata = json.loads(dict(before["tables"]["store_metadata"])["metadata"])
    return {
        "ok": True,
        "from_schema": 7,
        "to_schema": 8,
        "tables_preserved": checked,
        "store_instance_id": metadata["store_instance_id"],
        "generation": journal["generation"],
        "coverage_start": journal["coverage_start"],
        "coverage": "prospective",
        "event_count": 0,
        "cache_rows_preserved": len(before["tables"]["embedding_cache"]),
    }


def readonly(path):
    # Use only on sealed/private snapshots, not on a live WAL database.
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)

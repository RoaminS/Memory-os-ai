"""4.1.3 decision D1-c: read-only audit of instants a pre-4.1.3 server may have stored from a naive DATE.

Before 4.1.3 an explicit naive date ("2026-11-26") in ``event_time``, ``valid_from`` or ``valid_until`` was read
in the SERVER's local time zone (midnight of Paris = 23:00 UTC the day before); since 4.1.3 it means midnight UTC.
Stored instants are never rewritten. This audit lists, by identifier only (no memory text is printed), the
current item revisions whose time fields fall EXACTLY on a local midnight of ``zone`` that is not a UTC midnight,
excluding event times inferred from the fact's own text (``date_from_text``, always UTC).

Limits (stated in the report): a naive date-time with an arbitrary hour leaves no detectable trace; an instant
that happens to be a genuine local midnight given with an explicit offset is listed too (review required); the
zone must be the one the server ran in at the time (``--tz``).
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path

FIELDS = ("event_time", "valid_from", "valid_until")
LIMITS = [
    "only naive DATES are detectable: a naive date-time with an arbitrary hour leaves no trace",
    "a real local midnight given with an explicit offset is listed too: review each candidate",
    "the zone must be the one the server ran in when the item was written (--tz)",
    "read-only: nothing is rewritten; identifiers only, no memory text",
]


def system_zone_name() -> str | None:
    """IANA name of the system zone (``TZ`` or the ``/etc/localtime`` link), or None."""
    name = os.environ.get("TZ", "").lstrip(":")
    if name:
        return name
    try:
        target = os.readlink("/etc/localtime")
    except OSError:
        return None
    return target.split("zoneinfo/", 1)[1] if "zoneinfo/" in target else None


def audit(store_dir, zone: str | None = None) -> dict:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    from ..sqlite_store import ReadOnlyStoreError, open_readonly

    name = zone or system_zone_name()
    if not name:
        return {"ok": False, "code": "zone_unknown", "error": "system zone not found; pass --tz <IANA zone>"}
    try:
        tz = ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return {"ok": False, "code": "zone_invalid", "error": f"unknown IANA zone: {name!r}"}
    database = Path(store_dir).expanduser() / "store.sqlite3"
    if not database.is_file() or database.is_symlink() or database.stat().st_nlink != 1:
        return {"ok": False, "code": "store_missing", "error": "no regular SQLite store.sqlite3 in the store directory"}
    candidates, scanned = [], 0
    try:
        with open_readonly(database.resolve()) as connection:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            rows = connection.execute(
                "SELECT i.id, v.record_json FROM items i JOIN item_versions v "
                "ON i.id=v.item_id AND i.current_revision=v.revision ORDER BY i.id").fetchall()
    except ReadOnlyStoreError as exc:
        return {"ok": False, "code": exc.code, "error": str(exc)}
    for item_id, raw in rows:
        try:
            record = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(record, dict) or record.get("deleted"):
            continue
        scanned += 1
        inferred = "date_from_text" in (record.get("tags") or [])
        for field in FIELDS:
            value = record.get(field)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            if inferred and (field == "event_time" or (field == "valid_from" and value == record.get("event_time"))):
                continue                                  # inferred from the text: always UTC, not concerned
            utc = datetime.fromtimestamp(value, timezone.utc)
            local = utc.astimezone(tz)
            if (local.hour, local.minute, local.second, local.microsecond) != (0, 0, 0, 0):
                continue
            if (utc.hour, utc.minute, utc.second, utc.microsecond) == (0, 0, 0, 0):
                continue                                  # same instant under both rules: nothing to review
            meaning = datetime(local.year, local.month, local.day, tzinfo=timezone.utc)
            candidates.append({"id": item_id, "project": record.get("project"), "kind": record.get("kind"),
                               "field": field, "stored_utc": utc.isoformat(), "naive_date": local.date().isoformat(),
                               "meaning_since_4_1_3_utc": meaning.isoformat(),
                               "difference_hours": round((meaning - utc).total_seconds() / 3600, 2)})
    return {"ok": True, "zone": name, "scanned": scanned, "candidates": candidates,
            "rewritten": 0, "limits": LIMITS}

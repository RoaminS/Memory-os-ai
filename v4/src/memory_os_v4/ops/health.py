"""Daily health report (PLAN §10.1): read-only checks, never a traceback — every problem becomes an anomaly.

Checks: ``/health`` and ``/ready`` (404 = server predates readiness, reported as info; anything else but 200 is an
anomaly), strict JSONL scan or read-only SQLite counters/quick_check, *new* fact
unacknowledged supersessions requiring operator review (no inferred legitimacy), age of the last backup that
is qualified, read-back verified, still present and unchanged on disk, and free disk space.
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess
import time
from pathlib import Path

from .auth import SecretFileError, http_get
from .backup import SQLITE_MARKERS, sha256
from .settings import OpsSettings

DISK_MIN_GIB = 5.0
BACKUP_MAX_AGE_H = 26.0


def scan_store(store: Path) -> dict:
    items = store / "items.jsonl"
    if (store / "store.sqlite3").exists():
        from ..sqlite_stats import scan_sqlite

        return scan_sqlite(store / "store.sqlite3")
    if any(p.name.endswith(SQLITE_MARKERS) for p in store.iterdir()):
        raise ValueError("SQLite store: not supported by this report (ADR-001 S4)")
    last: dict[str, dict] = {}
    added_24h: dict[str, int] = {}
    forgotten_24h = malformed = 0
    day = time.time() - 86400
    data = items.read_bytes()
    truncated = bool(data) and not data.endswith(b"\n")
    for line in data.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            malformed += 1
            continue
        if not isinstance(d, dict) or not isinstance(d.get("id"), str):
            malformed += 1
            continue
        prev = last.get(d["id"])
        if prev is None and isinstance(d.get("created_at"), (int, float)) and d["created_at"] >= day:
            added_24h[d.get("kind", "?")] = added_24h.get(d.get("kind", "?"), 0) + 1
        if prev is not None and d.get("deleted") and not prev.get("deleted"):
            forgotten_24h += 1
        last[d["id"]] = d
    live = [d for d in last.values() if not d.get("deleted")]
    return {
        "live_total": len(live),
        "facts_valid": sum(1 for d in live if d.get("kind") == "fact" and not d.get("valid_until")),
        "added_24h": added_24h,
        "forgotten_24h": forgotten_24h,
        "malformed_lines": malformed,
        "truncated_tail": truncated,
        "superseded_ids": sorted(d["id"] for d in last.values() if d.get("invalidated_by")),
    }


def last_good_backup_age(settings: OpsSettings) -> float | None:
    """Age (h) of the newest backup that is qualified, verified, present and unchanged."""
    log = settings.ops_dir / "logs/backup.jsonl"
    if not log.exists():
        return None
    for line in reversed(log.read_text().splitlines()):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not (isinstance(rec, dict) and rec.get("ok") and rec.get("qualified") and rec.get("verified")):
            continue
        archive = settings.backup_dir / str(rec.get("archive", ""))
        if not archive.is_file() or sha256(archive) != rec.get("archive_sha256"):
            continue
        stamp = dt.datetime.strptime(rec["stamp"], "%Y%m%d-%H%M%S")
        return round((dt.datetime.now() - stamp).total_seconds() / 3600, 1)
    return None


def last_drill(settings):
    """Latest intact qualified file/service drill; levels must never be conflated."""
    log = settings.ops_dir / "logs/restore_drill.jsonl"
    if not log.exists():
        return None
    for line in reversed(log.read_text().splitlines()):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or row.get("ok") is not True or not row.get("archive_sha256"):
            continue
        archive = settings.backup_dir / row.get("archive", "")
        if archive.is_file() and sha256(archive) == row["archive_sha256"]:
            return {
                "level": row.get("level"),
                "quarantined": row.get("quarantined"),
                "duration_s": row.get("duration_s"),
                "archive": archive.name,
                "completed_at": row.get("completed_at"),
                "reopening_allowed": row.get("reopening_allowed", False),
                "private_reopen_verified": row.get("private_reopen_verified", False),
            }
    return None


def report(settings: OpsSettings | None = None, confined: bool | None = None) -> dict:
    settings = settings or OpsSettings.from_env()
    from ..config import inert_cos_settings

    anomalies: list[str] = []
    info: list[str] = []
    r: dict = {"at": dt.datetime.now().isoformat(timespec="seconds")}
    if confined is not None:
        info.append("--confined/confined is deprecated and has no control over engine mutations")
    if inert_cos_settings():
        info.append("Deprecated *_COS settings are inert; they do not establish confinement")
    try:
        health_status, _ = http_get(settings.health_url, settings)
        ready_status, ready_body = http_get(settings.ready_url, settings)
    except SecretFileError as exc:
        health_status = ready_status = -1
        ready_body = b""
        anomalies.append(f"token file unusable: {exc}")
    r["health_status"], r["ready_status"] = health_status, ready_status
    try:
        ready_data = json.loads(ready_body or b"{}")
        policy = ready_data.get("capabilities", {}).get("policies", {}).get("mutations")
    except (ValueError, AttributeError, UnicodeError):
        policy = None
    r["mutation_policy"] = policy if isinstance(policy, dict) else None
    if r["mutation_policy"] is None:
        info.append("deployed mutation policy unavailable; no confinement claim can be made")
    else:
        from ..fact_updates import mutation_policy

        if r["mutation_policy"] != mutation_policy():
            anomalies.append("deployed mutation policy differs from the explicit-only contract")
    if health_status != 200:
        anomalies.append(f"server /health status {health_status}")
    if ready_status == 404:
        info.append("server predates /ready (4.0.0): readiness not checked")
    elif ready_status != 200:
        reason = ""
        try:
            reason = json.loads(ready_body or b"{}").get("reason", "")
        except ValueError:
            pass
        anomalies.append(f"server /ready status {ready_status} {reason}".strip())
    sup: list[str] = []
    try:
        scan = scan_store(settings.store_dir)
        sup = scan.pop("superseded_ids")
        r.update(scan)
        if scan.get("change_journal", {}).get("alert"):
            anomalies.append("journal_size: change journal >= 10 MiB; explicit maintenance review required")
            r.setdefault("alerts", []).append(
                {
                    "category": "journal_size",
                    "severity": "warning",
                    "json_bytes": scan["change_journal"]["json_bytes"],
                }
            )
        if scan["malformed_lines"]:
            anomalies.append(f"{scan['malformed_lines']} malformed line(s) in items.jsonl")
        if scan["truncated_tail"]:
            anomalies.append("items.jsonl does not end with a newline: uncertain write, see RB-08")
    except Exception as exc:
        anomalies.append(f"store scan failed: {type(exc).__name__}: {exc}")
    settings.ops_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    known_file = settings.ops_dir / "known_supersessions.json"
    try:
        if not known_file.exists():
            # First observation is not an operator acknowledgement.
            from .auth import write_secret_file

            try:
                write_secret_file(
                    known_file, json.dumps({"baseline_at": r["at"], "ids": []}, indent=1).encode()
                )
            except FileExistsError:
                pass  # Another observer created the baseline; never overwrite it.
        known = set(json.loads(known_file.read_text())["ids"])
    except Exception as exc:
        known = set()
        anomalies.append(f"supersession baseline unreadable: {type(exc).__name__}")
    r["superseded_total"] = len(sup)
    r["superseded_new"] = [i for i in sup if i not in known]
    if r["superseded_new"]:
        anomalies.append(
            f"{len(r['superseded_new'])} unacknowledged supersession(s): operator review required; legitimacy not inferred"
        )
    try:
        age = last_good_backup_age(settings)
    except Exception as exc:
        age = None
        anomalies.append(f"backup log unreadable: {type(exc).__name__}")
    r["last_backup_age_h"] = age
    if age is None or age > BACKUP_MAX_AGE_H:
        anomalies.append(f"no verified qualified backup in the last {BACKUP_MAX_AGE_H:.0f} h (age {age})")
    try:
        from .storage import margin

        r["storage_margin"] = margin(settings)
        r["disk_free_gib"] = round(r["storage_margin"]["volumes"]["source"]["free_bytes"] / 2**30, 1)
        if not r["storage_margin"]["ok"]:
            anomalies.append(
                "storage margin insufficient: inspect effective policy/reserve in storage_margin"
            )
        r["last_restore_drill"] = last_drill(settings)
        if (settings.store_dir / "store.sqlite3").exists():
            from .sqlite_limits import capacity

            r["backup_capacity"] = capacity(settings.store_dir)
            if not r["backup_capacity"]["ok"] or r["backup_capacity"]["warning"]:
                anomalies.append(
                    f"backup capacity limit pressure: margin {r['backup_capacity']['margin_bytes']} bytes"
                )
                r.setdefault("alerts", []).append(
                    {
                        "category": "backup_capacity",
                        "severity": "warning" if r["backup_capacity"]["ok"] else "error",
                        "margin_bytes": r["backup_capacity"]["margin_bytes"],
                    }
                )
            drill = r["last_restore_drill"]
            try:
                age = (
                    (
                        dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(drill["completed_at"])
                    ).total_seconds()
                    / 3600
                    if drill
                    else None
                )
            except (ValueError, TypeError, KeyError):
                age = None
            if age is None or age > 7 * 24:
                anomalies.append("no intact SQLite restore drill within 7 days")
            if drill and drill["private_reopen_verified"]:
                info.append(
                    "private SQLite reopening verified; production promotion requires separate operator approval"
                )
            elif drill:
                info.append(
                    "last SQLite drill validates files in quarantine; semantic reopening is not qualified"
                )
    except (OSError, ValueError) as exc:
        anomalies.append(f"disk check failed: {type(exc).__name__}")
    r["info"], r["anomalies"] = info, anomalies
    reports = settings.ops_dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    with (reports / "health.jsonl").open("a") as fh:
        fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    (reports / f"{dt.date.today().isoformat()}.md").write_text(render(r))
    return r


def render(r: dict) -> str:
    lines = [
        f"# memory-os-v4 health — {r['at']}",
        "",
        f"- Server: /health {r.get('health_status')} · /ready {r.get('ready_status')} · live items "
        f"{r.get('live_total')} · valid facts {r.get('facts_valid')}",
        f"- Added 24 h: {r.get('added_24h')} · forgotten 24 h: {r.get('forgotten_24h')} · supersessions "
        f"{r.get('superseded_total')} (new {len(r.get('superseded_new', []))})",
        f"- Last verified backup: {r.get('last_backup_age_h')} h · free disk {r.get('disk_free_gib')} GiB",
        f"- Change journal: {r.get('change_journal', 'not available')}",
        f"- Receipts (SQLite): {r.get('receipts', 'not available')}",
        f"- Storage margin: {r.get('storage_margin', 'not available')}",
        f"- Backup capacity: {r.get('backup_capacity', 'not available')}",
        f"- Last restore drill (level matters): {r.get('last_restore_drill', 'not available')}",
        f"- Deployed mutation policy: {r.get('mutation_policy') or 'unavailable; no confinement claim'}",
        "",
        "## Anomalies",
        *(f"- {a}" for a in r["anomalies"] or ["none"]),
    ]
    if r.get("info"):
        lines += ["", "## Info", *(f"- {i}" for i in r["info"])]
    return "\n".join(lines) + "\n"


def notify(r: dict) -> None:
    if not r["anomalies"] or shutil.which("osascript") is None:
        return
    msg = "; ".join(r["anomalies"])[:200].replace('"', "'").replace("\\", "/")
    subprocess.run(
        ["osascript", "-e", f'display notification "{msg}" with title "memory-os anomaly"'],
        capture_output=True,
        check=False,
    )

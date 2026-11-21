"""SQLite library loaded by THIS interpreter, verified before any install (SETUP_4_1_0.md §3).

No side effect at import, no ML import. ``inspect`` opens an in-memory database only and writes
nothing. ``wal_probe`` is the functional proof used at apply time: it creates one throwaway
database inside the private directory it is given and removes it with its ``-wal``/``-shm``.

Two attestations exist and are never confused: this module speaks for the interpreter of the
running command; the running server publishes its own ``capabilities.store.sqlite_version``.
"""
from __future__ import annotations

from pathlib import Path

from ..sqlite_store import MIN_SQLITE

MIN_VERSION = MIN_SQLITE  # the engine floor itself, never a second constant
PROBE_NAME = ".sqlite-wal-probe.sqlite3"
WAL_PROOF = "WAL obtained on the volume of the probe directory only"


def _dotted(version) -> str:
    return ".".join(str(part) for part in version)


def inspect(*, module=None) -> dict:
    """Report on the loaded library; the first failing check sets ``code``."""
    checks = {"loaded": False, "version": False, "json1": False, "wal_compiled": False}
    report = {"ok": False, "code": None, "version": None, "minimum": _dotted(MIN_VERSION),
              "checks": checks, "fts5": False, "writes": "none"}

    def refuse(code: str) -> dict:
        if report["code"] is None:
            report["code"] = code
        return report

    try:
        if module is None:
            import sqlite3 as module
        connection = module.connect(":memory:")
    except Exception:  # unloadable library or unusable build: a refusal, never a crash
        return refuse("sqlite_runtime_unavailable")
    try:
        checks["loaded"] = True
        try:
            version = tuple(int(part) for part in module.sqlite_version_info[:3])
            report["version"] = _dotted(version)
            checks["version"] = version >= tuple(MIN_VERSION)
        except (TypeError, ValueError, AttributeError):
            checks["version"] = False
        if not checks["version"]:
            refuse("sqlite_runtime_too_old")
        try:
            row = connection.execute("SELECT json_extract('{\"a\": 7}', '$.a')").fetchone()
            checks["json1"] = bool(row) and row[0] == 7
        except Exception:
            checks["json1"] = False
        if not checks["json1"]:
            refuse("sqlite_json_missing")
        try:
            options = {str(row[0]).upper() for row in connection.execute("PRAGMA compile_options")}
            checks["wal_compiled"] = "OMIT_WAL" not in options
            report["fts5"] = "ENABLE_FTS5" in options  # informational: the store uses no FTS table
        except Exception:
            checks["wal_compiled"] = False
        if not checks["wal_compiled"]:
            refuse("sqlite_wal_unavailable")
    finally:
        try:
            connection.close()
        except Exception:
            pass
    report["ok"] = report["code"] is None
    return report


def wal_probe(directory, *, module=None) -> dict:
    """Functional WAL proof inside ``directory`` (apply time only); the probe database is removed.

    Declared limit: it proves WAL on the volume of ``directory`` only. WAL on the volume of the
    store is proven by the engine when it opens the store, then by the readiness check.
    """
    directory = Path(directory)
    target = directory / PROBE_NAME
    report = {"ok": False, "code": "sqlite_wal_unavailable", "journal_mode": None, "proves": WAL_PROOF}
    connection = None
    try:
        if module is None:
            import sqlite3 as module
        connection = module.connect(str(target))
        row = connection.execute("PRAGMA journal_mode=WAL").fetchone()
        mode = str(row[0]).lower() if row else None
        report["journal_mode"] = mode
        if mode == "wal":
            report.update(ok=True, code=None)
    except Exception:
        pass
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass
        for suffix in ("", "-wal", "-shm", "-journal"):
            try:
                Path(str(target) + suffix).unlink()
            except OSError:
                pass
    return report

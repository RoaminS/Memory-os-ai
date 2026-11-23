"""SQLite library loaded by THIS interpreter, verified before any install (SETUP_4_1_0.md §3).

No side effect at import, no ML import. ``inspect`` opens an in-memory database only and writes
nothing. ``wal_probe`` is the functional proof used at apply time: it creates one throwaway
database inside the private directory it is given and removes it with its ``-wal``/``-shm``.

Two attestations exist and are never confused: this module speaks for the interpreter of the
running command; the running server publishes its own ``capabilities.store.sqlite_version``.
"""
from __future__ import annotations

from pathlib import Path
import re
import sys
import sysconfig

from ..sqlite_store import MIN_SQLITE

MIN_VERSION = MIN_SQLITE  # the engine floor itself, never a second constant
PROBE_NAME = ".sqlite-wal-probe.sqlite3"
WAL_PROOF = "WAL obtained on the volume of the probe directory only"

# ---- 4.1.1 (contract §3): two routes to a conformant SQLite, never a second journal mode ----
# (a) any durable interpreter whose LOADED SQLite meets the floor, whatever its provider;
# (i) a standalone CPython obtained through uv: a named, consented download, never implicit.
# Only the value read back counts: a provider name or a declared version proves nothing.
UV_MINIMUM = (0, 13, 9)  # older uv releases only offer CPython builds carrying SQLite 3.50.4
STANDALONE_BUILD = "20261110"
STANDALONE_SOURCE = "python-build-standalone " + STANDALONE_BUILD + " (github.com/astral-sh/python-build-standalone), via uv"
STANDALONE_SQLITE_DECLARED = "3.53.1"  # declared by the build, to be read back after installation
# One named acquisition per cell that has a route (i). The reference cell uses route (a) only.
STANDALONE_CPYTHON = {
    "macos-arm64-py312": {"python": "3.12.16", "megabytes": 25.4},
    "macos-arm64-py313": {"python": "3.13.18", "megabytes": 25.6},
    "ubuntu2404-arm64-py311": {"python": "3.11.18", "megabytes": 49.5},
}
DEFAULT_VENV = "~/.memory-os/venv"
# "/cache/uv/" alone (4.1.0) missed the real uv cache, ~/.cache/uv: its two default spellings are named.
EPHEMERAL_MARKERS = ("/cache/uv/", "/.cache/uv/", "/Caches/uv/", "pip-build-env", "uvx")
READBACK = 'import sqlite3; print(sqlite3.sqlite_version)'


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


def is_ephemeral(python) -> bool:
    """A throwaway interpreter (uvx, uv cache, pip build environment): never installed from."""
    return any(marker in str(python) for marker in EPHEMERAL_MARKERS)


def triplet(value):
    """``(major, minor, patch)`` of an exact ``x.y.z`` text, else None."""
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value):
        return None
    return tuple(int(part) for part in value.split("."))


def provider() -> str:
    """Informational only (never a decision): how THIS interpreter was built."""
    return "python-build-standalone" if sysconfig.get_config_var("PYTHON_BUILD_STANDALONE") else "other"


def uv_version(text) -> dict:
    """Parse the output of ``uv --version`` (given by the caller; nothing is executed here)."""
    match = re.match(r"\s*uv\s+([0-9]+)\.([0-9]+)\.([0-9]+)(?![0-9.]*[a-zA-Z-])", text or "")
    version = tuple(int(part) for part in match.groups()) if match else None
    report = {"version": _dotted(version) if version else None, "minimum": _dotted(UV_MINIMUM),
              "ok": version is not None and version >= UV_MINIMUM, "code": None}
    if not report["ok"]:
        report["code"] = "uv_unreadable" if version is None else "uv_too_old"
    return report


def standalone_plan(cell_id, *, venv=DEFAULT_VENV, uv="uv"):
    """Route (i) for ``cell_id``: what would be acquired and the exact commands. Pure; runs nothing.

    None when the cell has no named standalone CPython. The first command is the only download and
    is launched by the user; the venv creation forbids any further interpreter download.
    """
    record = STANDALONE_CPYTHON.get(cell_id)
    if record is None:
        return None
    version, python = record["python"], f"{venv}/bin/python"
    commands = [[uv, "python", "install", version],
                [uv, "venv", "--python", version, "--managed-python", "--no-python-downloads", venv],
                [python, "-c", READBACK]]
    text = [f"{uv} python install {version}",
            f"{uv} venv --python {version} --managed-python --no-python-downloads {venv}",
            f'{python} -c "{READBACK}"']
    return {"route": "i", "cell_id": cell_id, "download": True, "implicit_download": False,
            "uv_minimum": _dotted(UV_MINIMUM), "venv": venv, "python": python, "commands": commands,
            "command_lines": text, "minimum": _dotted(MIN_VERSION),
            "sqlite_declared": STANDALONE_SQLITE_DECLARED, "sqlite_attested": None,
            "acquisitions": [{"object": "CPython " + version, "source": STANDALONE_SOURCE, "version": version,
                              "megabytes": record["megabytes"], "destination": "uv managed Python directory"}],
            "limits": "declared SQLite version only: the cell stays untested until the venv reads back "
                      f">= {_dotted(MIN_VERSION)}; uv flags checked against uv 0.10.8 help, not {_dotted(UV_MINIMUM)}"}


def server_attestation(value, *, running=True) -> dict:
    """The value published by the running server (``capabilities.store.sqlite_version``)."""
    if not running:
        return {"version": None, "ok": None, "state": "not_running", "code": None}
    version = triplet(value)
    if version is None:
        return {"version": None, "ok": False, "state": "unattested", "code": "sqlite_runtime_unattested"}
    ok = version >= tuple(MIN_VERSION)
    return {"version": value, "ok": ok, "state": "attested" if ok else "too_old",
            "code": None if ok else "sqlite_runtime_too_old"}


def attestation(report, *, python, cell_id=None, server=None, built_by=None, executable=None) -> dict:
    """Both read-back values and the route, from an ``inspect`` report. Pure: nothing is executed.

    ``interpreter`` is the library loaded by the interpreter running this command; ``server`` is the
    value published by the service once it runs. Route ``a``: durable interpreter, conformant
    library; ``i``: the same on a standalone CPython; ``none``: refused, with ``message``.
    """
    built_by = provider() if built_by is None else built_by
    durable = not is_ephemeral(python)
    conformant = bool(report.get("ok"))
    route = "none" if not (durable and conformant) else "i" if built_by == "python-build-standalone" else "a"
    code = report.get("code") if not conformant else None if durable else "runtime_unqualified"
    remedy = standalone_plan(cell_id) if not conformant else None
    result = {"minimum": _dotted(MIN_VERSION), "route": route, "ok": route != "none", "code": code,
              "durable": durable, "provider": built_by,
              "interpreter": {"version": report.get("version"), "ok": conformant, "code": report.get("code"),
                              "executable": executable or sys.executable, "target_python": str(python)},
              "server": server or server_attestation(None, running=False),
              "remedy": remedy, "message": None, "journal_modes": "WAL only (no DELETE fallback)"}
    if not conformant:
        result["message"] = refusal_message(report, cell_id=cell_id, remedy=remedy)
    return result


def refusal_message(report, *, cell_id=None, remedy=None) -> str:
    """Actionable refusal of an unfit library; names every download, launches none."""
    code = report.get("code") or "sqlite_runtime_unavailable"
    message = (f"{code}: SQLite loaded by this interpreter is unfit (version {report.get('version')}, "
               f"minimum {report.get('minimum')}); nothing written; no other journal mode and no implicit "
               "download. Route (a): run quickstart from any durable Python of this cell whose loaded SQLite "
               f"meets the minimum (check: python -c \"{READBACK}\")")
    if remedy is None:
        return message + ("; no standalone CPython is named for this cell" if cell_id else "") + ", then re-preview"
    item = remedy["acquisitions"][0]
    return (message + f". Route (i), a DOWNLOAD you launch yourself ({item['object']}, {item['source']}, about "
            f"{item['megabytes']} MB; needs uv >= {remedy['uv_minimum']}): "
            + " && ".join(remedy["command_lines"][:2]) + f"; then read it back ({remedy['command_lines'][2]}): "
            f"it must print >= {remedy['minimum']} (declared {remedy['sqlite_declared']}, not yet attested), "
            f"install memory-os-ai in that venv under the constraints of the cell and re-preview from it")

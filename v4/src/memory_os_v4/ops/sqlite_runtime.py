"""SQLite library loaded by THIS interpreter, verified before any install (SETUP_4_1_0.md §3).

No side effect at import, no ML import. ``inspect`` opens an in-memory database only and writes
nothing. ``wal_probe`` is the functional proof used at apply time: it creates one throwaway
database inside the private directory it is given and removes it with its ``-wal``/``-shm``.

Two attestations exist and are never confused: this module speaks for the interpreter of the
running command; the running server publishes its own ``capabilities.store.sqlite_version``.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import subprocess
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
# ``request`` is the complete uv key (implementation, exact version, system, architecture, libc):
# never a bare minor that a later uv could resolve to another interpreter. uv has no request
# syntax for the build itself (uv 0.13.9 help): the build is pinned by naming it here and by
# CHECKING it after installation (``standalone_check``), never by trusting what uv picked.
# ``megabytes`` is the size of the ``install_only.tar.gz`` asset of that build and architecture on the
# GitHub release ``20261110``, in decimal MB rounded to 0.1 (x86_64: 49,244,720 bytes).
STANDALONE_CPYTHON = {
    "macos-arm64-py312": {"python": "3.12.16", "megabytes": 25.4, "request": "cpython-3.12.16-macos-aarch64-none"},
    "macos-arm64-py313": {"python": "3.13.18", "megabytes": 25.6, "request": "cpython-3.13.18-macos-aarch64-none"},
    "ubuntu2404-arm64-py311": {"python": "3.11.18", "megabytes": 49.5,
                               "request": "cpython-3.11.18-linux-aarch64-gnu"},
    "ubuntu2404-x86_64-py311": {"python": "3.11.18", "megabytes": 49.2,
                                "request": "cpython-3.11.18-linux-x86_64-gnu"},
}
DEFAULT_VENV = "~/.memory-os/venv"
# "/cache/uv/" alone (4.1.0) missed the real uv cache, ~/.cache/uv: its two default spellings are named.
EPHEMERAL_MARKERS = ("/cache/uv/", "/.cache/uv/", "/Caches/uv/", "pip-build-env", "uvx")
READBACK = 'import sqlite3; print(sqlite3.sqlite_version)'
# Run BY the target interpreter (``probe_target``): the same checks as ``inspect`` plus what
# identifies the interpreter. Standard library only, in-memory database only, one JSON line.
PROBE_SCRIPT = r"""
import json, os, platform, sys, sysconfig
out = {"executable": sys.executable, "base_prefix": sys.base_prefix, "python": list(sys.version_info[:3]),
       "implementation": platform.python_implementation().lower(), "sys_version": sys.version,
       "standalone": bool(sysconfig.get_config_var("PYTHON_BUILD_STANDALONE")), "build": None,
       "sqlite": None, "json1": False, "wal_compiled": False, "fts5": False, "loaded": False}
try:
    with open(os.path.join(sys.base_prefix, "BUILD"), encoding="utf-8") as handle:
        out["build"] = handle.read(64).strip() or None
except OSError:
    pass
try:
    import sqlite3
    connection = sqlite3.connect(":memory:")
    out["loaded"] = True
    out["sqlite"] = list(sqlite3.sqlite_version_info[:3])
    try:
        out["json1"] = connection.execute("SELECT json_extract(?, '$.a')", ('{"a": 7}',)).fetchone()[0] == 7
    except Exception:
        pass
    try:
        options = {str(row[0]).upper() for row in connection.execute("PRAGMA compile_options")}
        out["wal_compiled"] = "OMIT_WAL" not in options
        out["fts5"] = "ENABLE_FTS5" in options
    except Exception:
        pass
    connection.close()
except Exception:
    pass
print(json.dumps(out))
"""
PROBE_TIMEOUT = 30


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
    """Route (i) for ``cell_id``: what would be acquired and the exact commands. Runs nothing.

    None when the cell has no named standalone CPython. The first command is the only download and
    is launched by the user; the venv creation forbids any further interpreter download.
    ``commands`` are argument lists: no shell reads them, so ``~`` is expanded here (``venv`` and
    ``uv`` keep the spelling of the caller for display); ``command_lines`` are the same commands
    quoted for a POSIX shell, so a path with spaces can be pasted as is.
    """
    record = STANDALONE_CPYTHON.get(cell_id)
    if record is None:
        return None
    version, request = record["python"], record["request"]
    venv_path, uv_path = os.path.expanduser(str(venv)), os.path.expanduser(str(uv))
    python = venv_path + "/bin/python"
    commands = [[uv_path, "python", "install", request],
                [uv_path, "venv", "--python", request, "--managed-python", "--no-python-downloads", venv_path],
                [python, "-c", READBACK]]
    return {"route": "i", "cell_id": cell_id, "download": True, "implicit_download": False,
            "uv_minimum": _dotted(UV_MINIMUM), "venv": venv, "venv_path": venv_path, "python": python,
            "request": request, "build": STANDALONE_BUILD, "commands": commands,
            "command_lines": [shlex.join(command) for command in commands], "minimum": _dotted(MIN_VERSION),
            "sqlite_declared": STANDALONE_SQLITE_DECLARED, "sqlite_attested": None,
            "acquisitions": [{"object": "CPython " + version, "source": STANDALONE_SOURCE, "version": version,
                              "megabytes": record["megabytes"], "destination": "uv managed Python directory"}],
            "limits": "declared SQLite version only: the cell stays untested until the venv reads back "
                      f">= {_dotted(MIN_VERSION)}; the build {STANDALONE_BUILD} is named, then checked after "
                      "installation (standalone_check), because uv has no request syntax for a build; "
                      f"uv flags checked against the help of uv {_dotted(UV_MINIMUM)}, never executed here"}


def probe_target(python, *, run=subprocess.run, timeout=PROBE_TIMEOUT) -> dict:
    """``inspect`` for ANOTHER interpreter: ``python`` is executed and reads back its own SQLite.

    The report has the shape of ``inspect`` plus ``target`` (what the interpreter says of itself).
    An interpreter that cannot be run or answers anything else is ``sqlite_runtime_unattested``:
    never assumed conformant, never described with the library of the running command. The child
    runs isolated (``-I -S``: no PYTHON* variable, no user site, ``site`` not imported, hence no
    ``.pth`` file and no sitecustomize of the target), writes no bytecode and opens an in-memory
    database only.
    """
    report = {"ok": False, "code": "sqlite_runtime_unattested", "version": None, "minimum": _dotted(MIN_VERSION),
              "checks": {"loaded": False, "version": False, "json1": False, "wal_compiled": False}, "fts5": False,
              "writes": "none", "probed": str(python), "target": None}
    try:
        result = run([str(python), "-I", "-S", "-B", "-c", PROBE_SCRIPT], capture_output=True, text=True, timeout=timeout,
                     env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        if result.returncode != 0:
            return report
        target = json.loads((result.stdout or "").strip().splitlines()[-1])
        version = None if target["sqlite"] is None else tuple(int(part) for part in target["sqlite"])
        python_version = tuple(int(part) for part in target["python"])
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError, IndexError):
        return report
    if not isinstance(target, dict) or len(python_version) != 3 or (version is not None and len(version) != 3):
        return report
    checks = report["checks"]
    checks.update(loaded=target.get("loaded") is True, version=version is not None and version >= tuple(MIN_VERSION),
                  json1=target.get("json1") is True, wal_compiled=target.get("wal_compiled") is True)
    report.update(version=_dotted(version) if version else None, fts5=target.get("fts5") is True,
                  target={"executable": target.get("executable"), "base_prefix": target.get("base_prefix"),
                          "python": _dotted(python_version), "implementation": target.get("implementation"),
                          "standalone": target.get("standalone") is True, "build": target.get("build"),
                          "sys_version": target.get("sys_version")})
    report["code"] = next((code for name, code in (("loaded", "sqlite_runtime_unavailable"),
                                                   ("version", "sqlite_runtime_too_old"),
                                                   ("json1", "sqlite_json_missing"),
                                                   ("wal_compiled", "sqlite_wal_unavailable")) if not checks[name]), None)
    report["ok"] = report["code"] is None
    return report


def standalone_check(report, cell_id) -> dict:
    """After installation: is the probed interpreter the standalone CPython named for ``cell_id``?

    ``report`` comes from ``probe_target``. The Python version must be the named one exactly and
    the SQLite read back must meet the floor. The build: uv writes the python-build-standalone
    release into ``BUILD`` at the root of a managed installation; when that file is readable it
    must say ``STANDALONE_BUILD``. Anything else REFUSES, an unreadable file included
    (``standalone_build_unattested``): an exact Python version does not identify a build, and an
    unproven build is never accepted as the named one.
    """
    record, target = STANDALONE_CPYTHON.get(cell_id), (report or {}).get("target") or {}
    if record is None:
        return {"ok": False, "code": "standalone_not_named", "cell_id": cell_id, "python": None, "sqlite": None,
                "build": None, "build_state": "unattested", "expected": None}
    build = target.get("build")
    state = "unattested" if not build else "attested" if build == STANDALONE_BUILD else "mismatch"
    facts = {"python": target.get("python") == record["python"], "sqlite": bool((report or {}).get("ok")),
             "standalone": target.get("standalone") is True, "build": state == "attested"}
    code = None if all(facts.values()) else ("standalone_python_mismatch" if not facts["python"] else
                                             (report or {}).get("code") or "sqlite_runtime_unattested"
                                             if not facts["sqlite"] else "standalone_build_" + state
                                             if not facts["build"] else "standalone_not_python_build_standalone")
    return {"ok": code is None, "code": code, "cell_id": cell_id, "python": target.get("python"),
            "sqlite": (report or {}).get("version"), "build": build, "build_state": state, "checks": facts,
            "expected": {"python": record["python"], "build": STANDALONE_BUILD, "sqlite_minimum": _dotted(MIN_VERSION)}}


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

    ``interpreter`` is the library read back by the interpreter that ``report`` describes: the one
    running this command (``inspect``), or the target itself when it is another one
    (``probe_target``, whose report says so in ``probed``; the provider and the executable are
    then the target's, never those of the running command). ``server`` is the value published by
    the service once it runs. Route ``a``: durable interpreter, conformant library; ``i``: the
    same on a standalone CPython; ``none``: refused, with ``message``.
    """
    target = report.get("target") if report.get("probed") else None
    if report.get("probed"):
        executable = executable or (target or {}).get("executable") or report["probed"]
        if built_by is None:
            built_by = "unknown" if target is None else "python-build-standalone" if target["standalone"] else "other"
    built_by = provider() if built_by is None else built_by
    durable = not is_ephemeral(python)
    conformant = bool(report.get("ok"))
    route = "none" if not (durable and conformant) else "i" if built_by == "python-build-standalone" else "a"
    code = report.get("code") if not conformant else None if durable else "runtime_unqualified"
    remedy = standalone_plan(cell_id) if not conformant else None
    result = {"minimum": _dotted(MIN_VERSION), "route": route, "ok": route != "none", "code": code,
              "durable": durable, "provider": built_by,
              "interpreter": {"version": report.get("version"), "ok": conformant, "code": report.get("code"),
                              "executable": executable or sys.executable, "target_python": str(python),
                              "read_by": "target" if report.get("probed") else "command",
                              "python": (target or {}).get("python") if report.get("probed")
                              else _dotted(sys.version_info[:3])},
              "server": server or server_attestation(None, running=False),
              "remedy": remedy, "message": None, "journal_modes": "WAL only (no DELETE fallback)"}
    if not conformant:
        result["message"] = refusal_message(report, cell_id=cell_id, remedy=remedy)
    return result


def refusal_message(report, *, cell_id=None, remedy=None) -> str:
    """Actionable refusal of an unfit library; names every download, launches none."""
    code = report.get("code") or "sqlite_runtime_unavailable"
    # A probed report speaks for the target interpreter, named by its path; never for this command.
    subject = f"the target interpreter {report['probed']}" if report.get("probed") else "this interpreter"
    unfit = ("could not be read back by executing it" if code == "sqlite_runtime_unattested" else
             f"is unfit (version {report.get('version')}, minimum {report.get('minimum')})")
    message = (f"{code}: SQLite loaded by {subject} {unfit}; nothing written; no other journal mode and no implicit "
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

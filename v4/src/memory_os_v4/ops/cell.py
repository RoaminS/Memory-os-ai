"""Read-only detection of the platform cell of THIS interpreter (4.1.1, lot L1).

A cell is (system, architecture, Python minor). Four cells are retained for 4.1.1; every other
combination is refused with an actionable message, never announced as supported. Detection reads
``sys``/``platform``/``sqlite3`` and a few well-known files; it starts no process, writes nothing
and never touches the network. Every input can be injected, so a test simulates any cell without
forcing the rest of the process to another platform.

Being retained is not enough to be accepted: ``acceptance`` also requires the embedded qualified
lock of the cell for the requested profile (``qualified_runtime.cell_lock``). The semantic
profile stays limited to ``macos-arm64-py311``.

Declared limits: the service manager is detected by presence only (nothing is started or queried);
the SQLite floor is reported here but enforced by ``sqlite_runtime.inspect``.
"""
from __future__ import annotations

import os
from pathlib import Path
import platform
import re
import shutil
import sys

RETAINED_CELLS = ("macos-arm64-py311", "macos-arm64-py312", "macos-arm64-py313", "ubuntu2404-arm64-py311")
SEMANTIC_CELLS = ("macos-arm64-py311",)
# The decision: exact facts per retained cell, compared one by one. The identifier is a name given
# AFTER the match; no label is ever normalized and then looked up. The architecture is always
# arm64 (arm64/aarch64) and the implementation CPython.
_CELL_FACTS = {
    "macos-arm64-py311": {"os": "darwin", "python": (3, 11)},
    "macos-arm64-py312": {"os": "darwin", "python": (3, 12)},
    "macos-arm64-py313": {"os": "darwin", "python": (3, 13)},
    "ubuntu2404-arm64-py311": {"os": "linux", "distribution_id": "ubuntu", "version_id": "24.04", "python": (3, 11)},
}
OS_RELEASE_PATHS = (Path("/etc/os-release"), Path("/usr/lib/os-release"))
KERNEL_RELEASE_PATH = Path("/proc/sys/kernel/osrelease")
LAUNCHCTL = Path("/bin/launchctl")
SYSTEMD_BOOTED = Path("/run/systemd/system")
_READ = object()  # "measure it": distinct from None, which simulates an absent source
_ARM64 = {"arm64", "aarch64"}


def _dotted(version) -> str:
    return ".".join(str(part) for part in version)


def parse_os_release(text) -> dict:
    """``ID`` and ``VERSION_ID`` of an os-release document, verbatim (unquoted, never normalized).

    A missing key is ``None``. A key repeated with different values cannot be attested: it is
    ``None`` too and named in ``conflicts``, which refuses the cell.
    """
    values, conflicts = {}, set()
    for line in (text or "").splitlines():
        match = re.fullmatch(r"\s*([A-Z0-9_]+)=(.*)", line)
        if not match:
            continue
        value = match[2].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if values.setdefault(match[1], value) != value:
            conflicts.add(match[1])
    found = {key: None if key in conflicts else (values.get(key) or None) for key in ("ID", "VERSION_ID")}
    return {"id": found["ID"], "version_id": found["VERSION_ID"],
            "conflicts": sorted(conflicts & {"ID", "VERSION_ID"})}


def _read_first(paths):
    for path in paths:
        try:
            return Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return None


def _sqlite_triplet(value):
    match = re.match(r"(\d+)\.(\d+)\.(\d+)", value or "")
    return tuple(int(part) for part in match.groups()) if match else None


def _service_manager(system, environ, which, exists) -> dict:
    """Presence only: no ``launchctl``/``systemctl`` call, nothing started."""
    if system == "darwin":
        present = bool(exists(LAUNCHCTL))
        return {"kind": "launchd", "available": present, "evidence": {"launchctl": str(LAUNCHCTL) if present else None},
                "attestation": "presence only"}
    if system == "linux":
        systemctl = which("systemctl")
        runtime = environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
        evidence = {"systemctl": systemctl, "system_booted": bool(exists(SYSTEMD_BOOTED)),
                    "user_manager_dir": bool(exists(Path(runtime) / "systemd"))}
        return {"kind": "systemd-user", "available": bool(systemctl) and evidence["system_booted"]
                and evidence["user_manager_dir"], "evidence": evidence, "attestation": "presence only"}
    return {"kind": None, "available": False, "evidence": {}, "attestation": "presence only"}


def detect(*, sys_platform=None, machine=None, python_version=None, implementation=None, sqlite_version=_READ,
           sqlite_minimum=None, os_release=_READ, kernel_release=_READ, environ=None, which=None, exists=None) -> dict:
    """Structured, deterministic report on the cell of the running interpreter.

    ``cell_id`` is one of ``RETAINED_CELLS`` or ``None``; ``label`` names the detected combination
    in every case; ``refusal`` says why a combination is not retained.
    """
    raw_platform = sys.platform if sys_platform is None else sys_platform
    raw_machine = platform.machine() if machine is None else machine
    version = tuple(sys.version_info[:3] if python_version is None else python_version)
    implementation = (platform.python_implementation() if implementation is None else implementation).lower()
    environ = os.environ if environ is None else environ
    which = shutil.which if which is None else which
    exists = os.path.exists if exists is None else exists
    if sqlite_version is _READ:
        try:
            import sqlite3
            sqlite_version = sqlite3.sqlite_version
        except Exception:  # unloadable library: reported, refused by sqlite_runtime.inspect
            sqlite_version = None
    if sqlite_minimum is None:
        from ..sqlite_store import MIN_SQLITE as sqlite_minimum  # the engine floor, never a second constant
    loaded = _sqlite_triplet(sqlite_version)

    system = "darwin" if raw_platform == "darwin" else "linux" if raw_platform.startswith("linux") else "other"
    arch = "arm64" if raw_machine.lower() in _ARM64 else (raw_machine.lower() or "unknown")
    python = f"{version[0]}.{version[1]}"
    distribution, wsl = None, False
    if system == "linux":
        distribution = parse_os_release(_read_first(OS_RELEASE_PATHS) if os_release is _READ else os_release)
        if kernel_release is _READ:
            kernel_release = _read_first((KERNEL_RELEASE_PATH,)) or platform.release()
        wsl = ("microsoft" in (kernel_release or "").lower()
               or bool(environ.get("WSL_DISTRO_NAME")) or bool(environ.get("WSL_INTEROP")))

    conflicts = (distribution or {}).get("conflicts") or []
    facts = {"os": system, "python": tuple(version[:2])}
    if system == "linux":
        facts.update(distribution_id=distribution["id"], version_id=distribution["version_id"])
    matched = None
    if arch == "arm64" and not conflicts:
        matched = next((name for name in RETAINED_CELLS if _CELL_FACTS[name] == facts), None)

    # Display only. The dashed form of an unmatched Linux can never spell a retained identifier.
    if matched:
        label = matched
    else:
        if system == "darwin":
            base = "macos"
        elif system == "linux":
            base = "-".join(re.sub(r"[^0-9A-Za-z._]", "", part) or "unknown" for part in
                            (distribution["id"] or "linux", distribution["version_id"]) if part is not None)
        else:
            base = re.sub(r"[^0-9a-z]", "", raw_platform.lower()) or "unknown"
        label = f"{base}-{arch}-py{version[0]}{version[1]}"

    refusal = None
    if conflicts:
        refusal = (f"{label}: os-release repeats {', '.join(conflicts)} with different values; "
                   "the distribution cannot be attested")
    elif matched is None:
        refusal = f"{label} is not one of the retained cells"
    elif implementation != "cpython":
        refusal = f"Python implementation {implementation} is not CPython"
    elif wsl:
        refusal = "WSL is not a retained platform (WSL2 is postponed)"
    cell_id = None if refusal else matched
    return {"schema_version": 1, "cell_id": cell_id, "label": label, "retained": cell_id is not None,
            "refusal": refusal, "retained_cells": list(RETAINED_CELLS),
            "os": system, "sys_platform": raw_platform, "arch": arch, "machine": raw_machine,
            "python": python, "python_full": _dotted(version), "implementation": implementation,
            "sqlite_version": sqlite_version, "sqlite_minimum": _dotted(sqlite_minimum),
            "sqlite_ok": loaded is not None and loaded >= tuple(sqlite_minimum),
            "wsl": wsl, "distribution": distribution,
            "service_manager": _service_manager(system, environ, which, exists),
            "writes": "none", "network": "none"}


def linux_platform_retained(report: dict) -> bool:
    """The OS side of a retained Linux cell (distribution, version, arm64, not WSL), whatever the Python minor.

    Used to choose the service manager (4.1.1 L4): ``systemd --user`` is driven only where a Linux
    cell is retained. Compared with the same fact table as ``detect``; no second constant.
    """
    if report.get("os") != "linux" or report.get("arch") != "arm64" or report.get("wsl"):
        return False
    distribution = report.get("distribution") or {}
    if distribution.get("conflicts"):
        return False
    return any(facts["os"] == "linux" and facts.get("distribution_id") == distribution.get("id")
               and facts.get("version_id") == distribution.get("version_id") for facts in _CELL_FACTS.values())


def acceptance(cell: dict, *, profile: str, lock=None) -> dict:
    """Whether ``profile`` may be installed on ``cell``: retained, profile allowed, lock embedded.

    ``lock`` is the lock lookup, ``qualified_runtime.cell_lock`` by default. A refusal carries the
    stable blocker code ``runtime_unqualified`` and a ``reason`` among ``cell_not_retained``,
    ``profile_not_qualified_on_cell`` and ``cell_lock_missing``.
    """
    if lock is None:
        from .qualified_runtime import cell_lock as lock
    retained = ", ".join(RETAINED_CELLS)

    def refuse(reason, message):
        return {"ok": False, "code": "runtime_unqualified", "reason": reason, "cell_id": cell.get("cell_id"),
                "label": cell.get("label"), "profile": profile, "lock": None,
                "message": "runtime_unqualified: " + message}

    if not cell.get("retained"):
        return refuse("cell_not_retained",
                      f"{cell.get('refusal') or 'unknown platform cell'}; quickstart accepts only the retained cells: "
                      f"{retained}; other platforms remain manual; nothing written")
    cell_id = cell["cell_id"]
    if profile == "semantic" and cell_id not in SEMANTIC_CELLS:
        return refuse("profile_not_qualified_on_cell",
                      f"the semantic profile is available only on {', '.join(SEMANTIC_CELLS)}; cell {cell_id} "
                      "accepts the lexical profile only (--profile lexical); nothing written")
    found = lock(cell_id, profile=profile)
    if found is None:
        return refuse("cell_lock_missing",
                      f"no qualified lock embedded for cell {cell_id} (profile {profile}); this build cannot be "
                      "installed on this cell; install a release that embeds its lock; nothing written")
    return {"ok": True, "code": None, "reason": None, "cell_id": cell_id, "label": cell["label"],
            "profile": profile, "lock": found, "message": None}

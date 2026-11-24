#!/usr/bin/env python3
"""Memory OS AI bootstrap (4.1.1, contract section 4): ONE script, standard library only.

It is published NEXT TO the wheel (never inside it: it pins the SHA-256 of that wheel) and is
identified by its own SHA-256. Getting it is a declared acquisition: one command, one source, one
published digest. It never imports ``memory_os_ai`` or ``memory_os_v4``: nothing is installed yet.

    python3 memory_os_bootstrap.py --uv /path/to/uv --profile lexical            # PREVIEW (default)
    python3 memory_os_bootstrap.py --uv /path/to/uv --profile lexical --apply    # named option

PREVIEW (first pass): the bootstrap itself writes nothing and opens no connection; it starts two
processes only (``uv --version`` and the target interpreter, isolated with ``-I -S``). What it
can claim is limited: no write DETECTED on the inventoried destinations (a difference refuses the
preview); the absence of network use is NOT proven here (gate P3, lot L6). It reports the system,
the architecture, the Python and the SQLite it loads (read back by EXECUTING the target
interpreter), the service manager, the
version of ``uv`` (``uv --version`` of the binary given by an explicit path; minimum 0.13.9), the
platform cell and its PROOF STATE, the chosen lock, and the complete list of acquisitions (object,
source, version, size, destination). The proof state never comes from the network: it is read in
a LOCAL P9 manifest given with ``--p9-manifest``; ``PASS``/``FAIL`` is announced only if that
manifest carries the exact digest of the pinned wheel, the detected cell and the digest of its
lock; otherwise ``non testé``. Declared limit: the manifest is not signed.

APPLY (``--apply``): drives ``uv`` (NEVER ``uvx``) to create a DURABLE venv (default
``~/.memory-os/venv``), binary wheels only, under the exact hashed constraints of the cell, with
interpreter downloads disabled, except the one consented step ``--acquire-cpython`` that installs
the standalone CPython named for the cell. It then reads back the venv interpreter (a standalone
CPython whose build 20261110 is not PROVEN is refused), checks the installed artifact against the
pinned digest and runs ``quickstart`` (preview only) FROM that venv, in an environment rebuilt
from an allow-list (no ``MEMORY_V4_*`` variable is inherited). It never runs ``quickstart
--apply``, never starts a service, never deletes or overwrites anything: every file it writes is
created exclusively inside a private work directory that it creates itself (refused if it
exists), which also holds the uv cache; symbolic links on the destinations are refused.

RELEASE PIN: the block below is rewritten by the release tooling (``fill_pin``) once the wheel is
built and the locks are frozen. The repository copy is a TEMPLATE: it previews, and refuses to
apply (``release_pin_unfilled``). Format (JSON, ``schema_version`` 1):

    {"schema_version": 1, "state": "release" | "test" | "template",
     "package": "memory-os-ai", "version": "4.1.1",
     "wheel": {"filename": "...whl", "sha256": "<64 hex>", "size": <bytes>,
               "content_sha256": "<64 hex, wheel_content_fingerprint()>", "source": "<url>"},
     "cells": {"<cell id>": {"<profile>": {
         "constraints": ["name==version", ...],              # exact, no marker: WHAT to install
         "artifacts": [{"name": "...", "version": "...", "filename": "...whl", "url": "https://...",
                        "sha256": "<64 hex>", "size": <bytes>}, ...],   # WHAT was proven
         "lock_sha256": "<lock_fingerprint(), optional but checked when present>",
         "interpreter": {"sys_version": "<exact sys.version, optional>"}}}}}
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys
import time
import zipfile

BOOTSTRAP_FORMAT = 1
PIN_BEGIN = "# --- BEGIN RELEASE PIN (written by the release tooling: fill_pin; never edited by hand) ---"
PIN_END = "# --- END RELEASE PIN ---"
# --- BEGIN RELEASE PIN (written by the release tooling: fill_pin; never edited by hand) ---
RELEASE_PIN = r"""
{
 "schema_version": 1,
 "state": "template",
 "package": "memory-os-ai",
 "version": null,
 "wheel": null,
 "cells": {}
}
"""
# --- END RELEASE PIN ---

PACKAGE = "memory-os-ai"
MODULE = "memory_os_v4"
PROFILES = ("lexical", "semantic")
EXTRAS = {"lexical": "ops,brief", "semantic": "ops,brief,semantic"}
# The four retained cells: exact facts, compared one by one (same table as ops/cell.py).
CELLS = {
    "macos-arm64-py311": {"os": "darwin", "python": (3, 11)},
    "macos-arm64-py312": {"os": "darwin", "python": (3, 12)},
    "macos-arm64-py313": {"os": "darwin", "python": (3, 13)},
    "ubuntu2404-arm64-py311": {"os": "linux", "distribution_id": "ubuntu", "version_id": "24.04", "python": (3, 11)},
}
SEMANTIC_CELLS = ("macos-arm64-py311",)
MIN_SQLITE = (3, 51, 3)
UV_MINIMUM = (0, 13, 9)  # older uv releases only offer CPython builds carrying SQLite 3.50.4
STANDALONE_BUILD = "20261110"
STANDALONE_SOURCE = ("python-build-standalone " + STANDALONE_BUILD
                     + " (github.com/astral-sh/python-build-standalone), via uv")
STANDALONE_SQLITE_DECLARED = "3.53.1"
# Route (i): the complete uv key per cell. uv has no request syntax for a BUILD: the build is
# named here and checked after installation (BUILD file of the managed installation, sys.version).
STANDALONE = {
    "macos-arm64-py312": {"python": "3.12.16", "megabytes": 25.4, "request": "cpython-3.12.16-macos-aarch64-none"},
    "macos-arm64-py313": {"python": "3.13.18", "megabytes": 25.6, "request": "cpython-3.13.18-macos-aarch64-none"},
    "ubuntu2404-arm64-py311": {"python": "3.11.18", "megabytes": 49.5,
                               "request": "cpython-3.11.18-linux-aarch64-gnu"},
}
DEFAULT_VENV = "~/.memory-os/venv"
EPHEMERAL_MARKERS = ("/cache/uv/", "/.cache/uv/", "/Caches/uv/", "pip-build-env", "uvx")
NOT_TESTED, UNSIGNED = "non testé", ("unsigned manifest (manifeste non signé): the preview reports what the local "
                                     "file declares, nothing more")
P9_KIND, P9_MAX_BYTES = "memory-os-ai-p9-manifest", 5_000_000
CL100K_BYTES = 1_681_126
OS_RELEASE_PATHS = ("/etc/os-release", "/usr/lib/os-release")
KERNEL_RELEASE_PATH = "/proc/sys/kernel/osrelease"
# Files an installer writes or rewrites itself: outside the artifact fingerprint.
INSTALLER_FILES = ("RECORD", "INSTALLER", "REQUESTED", "direct_url.json", "uv_cache.json")
HEX64 = re.compile(r"[0-9a-f]{64}")
CONSTRAINT = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9][A-Za-z0-9.!+_-]*)")
# Environment handed to uv: rebuilt from this list, nothing else is inherited (no UV_*, PIP_*,
# PYTHON*, VIRTUAL_ENV or MEMORY_V4_* variable can steer the installation).
UV_ENV_KEEP = ("HOME", "PATH", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR",
               "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy")
# Environment of every interpreter the bootstrap starts (probes, installed-artifact check and the
# quickstart preview run from the venv): rebuilt from this list. No MEMORY_V4_*, PYTHON*, XDG data
# or configuration variable (including XDG_CONFIG_HOME), nor any client-relocating variable
# (CLAUDE_CONFIG_DIR, CODEX_HOME, GEMINI_CLI_HOME, GROK_HOME), is inherited.
# Declared limit (4.1.1 L5): because XDG_CONFIG_HOME is not forwarded here, the child quickstart
# preview resolves Linux systemd units and Zed's config under the default ~/.config/; users with a
# custom XDG_CONFIG_HOME under HOME should run `memory-os-admin quickstart` directly from the
# durable venv after bootstrap. Relocating client variables are detected by the bootstrap itself
# and reported consistently with ``client_env_override`` without being forwarded to the child.
CHILD_ENV_KEEP = ("HOME", "PATH", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "XDG_RUNTIME_DIR")
DEFAULT_CLIENTS = ("claude-code", "codex")
CLIENT_OVERRIDE_ENV = {
    "claude-code": ("CLAUDE_CONFIG_DIR",),
    "codex": ("CODEX_HOME",),
    "gemini-cli": ("GEMINI_CLI_HOME",),
    "grok": ("GROK_HOME",),
}
PREVIEW_WRITES = "none detected on the inventoried destinations (preview)"
PREVIEW_NETWORK = "none opened by the bootstrap; absence of network use not proven here (gate P3, lot L6)"
PROBE_TIMEOUT, UV_TIMEOUT, QUICKSTART_TIMEOUT = 30, 1800, 300

# Run BY another interpreter: what it says of itself and of the SQLite it loads (in memory only).
PROBE_SCRIPT = r"""
import json, os, platform, sys, sysconfig
out = {"executable": sys.executable, "prefix": sys.prefix, "base_prefix": sys.base_prefix,
       "python": list(sys.version_info[:3]), "implementation": platform.python_implementation().lower(),
       "sys_version": sys.version, "standalone": bool(sysconfig.get_config_var("PYTHON_BUILD_STANDALONE")),
       "build": None, "sqlite": None, "json1": False, "wal_compiled": False, "loaded": False}
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
    except Exception:
        pass
    connection.close()
except Exception:
    pass
print(json.dumps(out))
"""

# Run BY the venv interpreter after installation: the installed distribution, hashed file by file.
INSTALLED_SCRIPT = r"""
import hashlib, importlib.metadata, importlib.util, json, sys
name, module, skipped = sys.argv[1], sys.argv[2], set(sys.argv[3].split(","))
out = {"version": None, "content_sha256": None, "files": 0, "origin": None, "prefix": sys.prefix, "error": None}
try:
    dist = importlib.metadata.distribution(name)
    out["version"] = dist.version
    lines = []
    for item in dist.files or []:
        text = item.as_posix()
        if text.startswith("../") or text.endswith(".pyc") or "/__pycache__/" in "/" + text:
            continue
        if ".dist-info/" in text and text.rsplit("/", 1)[-1] in skipped:
            continue
        with open(dist.locate_file(item), "rb") as handle:
            lines.append(text + "\t" + hashlib.sha256(handle.read()).hexdigest())
    out["files"] = len(lines)
    out["content_sha256"] = hashlib.sha256("\n".join(sorted(lines)).encode("utf-8")).hexdigest()
    spec = importlib.util.find_spec(module)
    out["origin"] = spec.origin if spec else None
except Exception as exc:
    out["error"] = type(exc).__name__
print(json.dumps(out))
"""


def _dotted(version) -> str:
    return ".".join(str(part) for part in version)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def quote(argv) -> str:
    """One command, quoted for a POSIX shell: a path with spaces can be pasted as is."""
    return " ".join(shlex.quote(str(part)) for part in argv)


def is_ephemeral(path) -> bool:
    return any(marker in str(path) for marker in EPHEMERAL_MARKERS)


# ---------------------------------------------------------------- release pin

def lock_fingerprint(cell_id: str, profile: str, lock: dict) -> str:
    """Digest of one lock: the cell, the profile, the exact constraints and every hashed artifact."""
    body = {"cell_id": cell_id, "profile": profile, "constraints": sorted(lock["constraints"]),
            "artifacts": sorted([item["filename"], item["sha256"]] for item in lock["artifacts"])}
    return _sha256_bytes(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _lock_problem(cell_id: str, profile: str, lock) -> str | None:
    if cell_id not in CELLS or profile not in PROFILES:
        return f"{cell_id}/{profile} is not a retained cell and profile"
    if profile == "semantic" and cell_id not in SEMANTIC_CELLS:
        return f"the semantic profile is not available on {cell_id}"
    if not isinstance(lock, dict) or not isinstance(lock.get("constraints"), list) \
            or not isinstance(lock.get("artifacts"), list) or not lock["constraints"]:
        return f"{cell_id}/{profile}: constraints and artifacts lists are required"
    pinned = {}
    for line in lock["constraints"]:
        match = CONSTRAINT.fullmatch(line) if isinstance(line, str) else None
        if not match or canonical_name(match[1]) in pinned or canonical_name(match[1]) == PACKAGE:
            return f"{cell_id}/{profile}: constraint {line!r} is not one exact, unique 'name==version' pin"
        pinned[canonical_name(match[1])] = match[2]
    covered = set()
    for item in lock["artifacts"]:
        if not isinstance(item, dict) or not all(isinstance(item.get(key), str) for key in
                                                 ("name", "version", "filename", "url", "sha256")):
            return f"{cell_id}/{profile}: an artifact lacks name, version, filename, url or sha256"
        name = canonical_name(item["name"])
        if pinned.get(name) != item["version"]:
            return f"{cell_id}/{profile}: artifact {item['filename']} matches no constraint"
        if not item["filename"].endswith(".whl") or "/" in item["filename"]:
            return f"{cell_id}/{profile}: {item['filename']} is not a binary wheel"
        if not HEX64.fullmatch(item["sha256"]) or not item["url"].startswith("https://") \
                or not isinstance(item.get("size"), int) or item["size"] <= 0:
            return f"{cell_id}/{profile}: {item['filename']} needs an https url, a sha256 and a size"
        covered.add(name)
    if covered != set(pinned):
        return f"{cell_id}/{profile}: no hashed artifact for " + ", ".join(sorted(set(pinned) - covered))
    declared = lock.get("lock_sha256")
    if declared is not None and declared != lock_fingerprint(cell_id, profile, lock):
        return f"{cell_id}/{profile}: lock_sha256 does not match the constraints and artifacts"
    return None


def load_pin(text: str | None = None) -> dict:
    """The embedded release pin, validated. ``ok`` only for a filled, coherent pin."""
    result = {"ok": False, "code": "release_pin_invalid", "message": None, "state": None, "version": None,
              "wheel": None, "cells": {}}
    try:
        pin = json.loads(RELEASE_PIN if text is None else text)
        state = pin["state"]
        if pin["schema_version"] != 1 or pin.get("package") != PACKAGE or state not in ("template", "test", "release"):
            raise ValueError("schema_version 1, package memory-os-ai and a known state are required")
        result["state"] = state
        if state == "template":
            result.update(code="release_pin_unfilled", message="release_pin_unfilled: this copy of the bootstrap "
                          "is the repository template; it pins no wheel and no lock. Use the bootstrap published "
                          "next to the wheel (compare its SHA-256 with the published one); nothing written")
            return result
        wheel, version = pin["wheel"], pin["version"]
        if not isinstance(version, str) or not re.fullmatch(r"[0-9]+(\.[0-9]+)*([a-z]+[0-9]+|\.dev[0-9]+)?", version):
            raise ValueError("version is not an exact release version")
        if not (isinstance(wheel.get("filename"), str) and wheel["filename"].endswith(".whl")
                and "/" not in wheel["filename"] and f"-{version}-" in wheel["filename"]
                and HEX64.fullmatch(wheel.get("sha256") or "") and HEX64.fullmatch(wheel.get("content_sha256") or "")
                and isinstance(wheel.get("size"), int) and wheel["size"] > 0):
            raise ValueError("wheel needs filename (of this version), sha256, content_sha256 and size")
        cells = pin["cells"]
        for cell_id, profiles in cells.items():
            for profile, lock in profiles.items():
                problem = _lock_problem(cell_id, profile, lock)
                if problem:
                    raise ValueError(problem)
        result.update(ok=True, code=None, version=version, wheel=wheel, cells=cells)
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        detail = str(exc) if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError) else "unreadable"
        result["message"] = f"release_pin_invalid: the embedded release pin is not usable ({detail}); nothing written"
    return result


def render_pin_block(pin: dict) -> str:
    """The pin block as it is embedded in this script (release tooling; pure)."""
    text = json.dumps(pin, indent=1, sort_keys=True, ensure_ascii=True)
    if '"""' in text:
        raise ValueError("the pin cannot be embedded")
    return f'{PIN_BEGIN}\nRELEASE_PIN = r"""\n{text}\n"""\n{PIN_END}'


def fill_pin(source: str, pin: dict) -> str:
    """``source`` (the text of this script) with its pin block replaced by ``pin``. Pure.

    The release tooling calls it once the wheel is built and the locks are frozen, then publishes
    the result with its SHA-256. A pin that does not validate is refused: nothing half-filled.
    """
    block = render_pin_block(pin)
    checked = load_pin(json.dumps(pin))
    if not checked["ok"]:
        raise ValueError(checked["message"])
    start, end = source.find(PIN_BEGIN + "\nRELEASE_PIN"), source.find("\n" + PIN_END + "\n")
    if start < 0 or end < start or source.count(PIN_BEGIN + "\nRELEASE_PIN") != 1:
        raise ValueError("pin block markers not found exactly once")
    return source[:start] + block + source[end + len(PIN_END) + 1:]


def wheel_content_fingerprint(path) -> str:
    """Digest of what a wheel puts under ``site-packages`` (``wheel.content_sha256`` of the pin).

    Sorted ``path<TAB>sha256`` lines of every archive member outside ``*.data/``, except the files
    an installer writes itself. The same digest is recomputed from the INSTALLED files after
    ``uv pip install``. Declared limit: data files and console scripts are outside this digest
    (they are covered by the wheel SHA-256 that uv enforces at installation).
    """
    lines = []
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if name.endswith("/") or ".data/" in name.split("/", 1)[0] + "/":
                continue
            if ".dist-info/" in name and name.rsplit("/", 1)[-1] in INSTALLER_FILES:
                continue
            lines.append(name + "\t" + _sha256_bytes(archive.read(name)))
    return _sha256_bytes("\n".join(sorted(lines)).encode("utf-8"))


# ---------------------------------------------------------------- detection (read-only)

def parse_os_release(text) -> dict:
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


_READ = object()


def detect_host(*, sys_platform=None, machine=None, os_release=_READ, kernel_release=_READ, environ=None,
                which=None, exists=None) -> dict:
    """System, architecture, distribution, WSL and service manager (presence only). Starts nothing."""
    raw_platform = sys.platform if sys_platform is None else sys_platform
    raw_machine = platform.machine() if machine is None else machine
    environ = os.environ if environ is None else environ
    which = shutil.which if which is None else which
    exists = os.path.exists if exists is None else exists
    system = "darwin" if raw_platform == "darwin" else "linux" if raw_platform.startswith("linux") else "other"
    arch = "arm64" if raw_machine.lower() in ("arm64", "aarch64") else (raw_machine.lower() or "unknown")
    distribution, wsl = None, False
    manager = {"kind": None, "available": False, "attestation": "presence only"}
    if system == "linux":
        distribution = parse_os_release(_read_first(OS_RELEASE_PATHS) if os_release is _READ else os_release)
        if kernel_release is _READ:
            kernel_release = _read_first((KERNEL_RELEASE_PATH,)) or platform.release()
        wsl = ("microsoft" in (kernel_release or "").lower()
               or bool(environ.get("WSL_DISTRO_NAME")) or bool(environ.get("WSL_INTEROP")))
        runtime = environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
        manager = {"kind": "systemd-user", "attestation": "presence only",
                   "available": bool(which("systemctl")) and bool(exists("/run/systemd/system"))
                   and bool(exists(os.path.join(runtime, "systemd")))}
    elif system == "darwin":
        manager = {"kind": "launchd", "available": bool(exists("/bin/launchctl")), "attestation": "presence only"}
    return {"os": system, "sys_platform": raw_platform, "arch": arch, "machine": raw_machine,
            "distribution": distribution, "wsl": wsl, "service_manager": manager}


def match_cell(host: dict, python_version, implementation: str = "cpython") -> dict:
    """The retained cell of (host, Python), by exact comparison; never a normalized label lookup."""
    version = tuple(python_version)
    distribution = host.get("distribution") or {}
    conflicts = distribution.get("conflicts") or []
    facts = {"os": host["os"], "python": version[:2]}
    if host["os"] == "linux":
        facts.update(distribution_id=distribution.get("id"), version_id=distribution.get("version_id"))
    matched = None
    if host["arch"] == "arm64" and not conflicts:
        matched = next((name for name, wanted in CELLS.items() if wanted == facts), None)
    if matched:
        label = matched
    else:
        if host["os"] == "darwin":
            base = "macos"
        elif host["os"] == "linux":
            base = "-".join(re.sub(r"[^0-9A-Za-z._]", "", part) or "unknown" for part in
                            (distribution.get("id") or "linux", distribution.get("version_id")) if part is not None)
        else:
            base = re.sub(r"[^0-9a-z]", "", host["sys_platform"].lower()) or "unknown"
        label = f"{base}-{host['arch']}-py{version[0]}{version[1]}"
    refusal = None
    if conflicts:
        refusal = (f"{label}: os-release repeats {', '.join(conflicts)} with different values; "
                   "the distribution cannot be attested")
    elif matched is None:
        refusal = f"{label} is not one of the retained cells"
    elif implementation != "cpython":
        refusal = f"Python implementation {implementation} is not CPython"
    elif host.get("wsl"):
        refusal = "WSL is not a retained platform (WSL2 is postponed)"
    return {"cell_id": None if refusal else matched, "label": label, "retained": refusal is None,
            "refusal": refusal, "retained_cells": list(CELLS)}


def standalone_minors(host: dict) -> list:
    """Python minors for which a standalone CPython is named on this host (route (i))."""
    found = []
    for cell_id in STANDALONE:
        minor = CELLS[cell_id]["python"]
        if match_cell(host, minor)["cell_id"] == cell_id:
            found.append(_dotted(minor))
    return found


# ---------------------------------------------------------------- processes (uv, interpreters)

def _run(run, argv, *, env, timeout):
    """(return code, stdout); None when the command could not be run or timed out."""
    try:
        result = run([str(part) for part in argv], capture_output=True, text=True, timeout=timeout, env=env,
                     stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None, ""
    return result.returncode, result.stdout or ""


def uv_environment(environ, *, python_downloads: str = "never", offline: bool = False, cache_dir=None,
                   python_dir=None) -> dict:
    """The whole environment of a uv command: an allow-list plus explicit switches.

    The cache and the managed Python directory are never left to uv's defaults at ``--apply``:
    both are named, listed in the preview and inventoried.
    """
    env = {key: environ[key] for key in UV_ENV_KEEP if environ.get(key)}
    env.update(UV_NO_CONFIG="1", UV_PYTHON_DOWNLOADS=python_downloads, UV_NO_PROGRESS="1")
    if offline:
        env["UV_OFFLINE"] = "1"
    if cache_dir:
        env["UV_CACHE_DIR"] = cache_dir
    if python_dir:
        env["UV_PYTHON_INSTALL_DIR"] = python_dir
    return env


def child_environment(environ) -> dict:
    """The whole environment of an interpreter started by the bootstrap (allow-list)."""
    env = {key: environ[key] for key in CHILD_ENV_KEEP if environ.get(key)}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def parse_clients_arg(value, default=DEFAULT_CLIENTS):
    """Copy of ``memory_os_v4.ops.quickstart.parse_clients_arg`` (the bootstrap is stdlib only).

    Absent or empty -> ``default``; otherwise an exact ``split(",")``, nothing stripped or case-folded.
    """
    return tuple(value.split(",")) if value else default


def detect_client_env_overrides(environ, clients_arg=None) -> list:
    """Detect relocating client env vars by presence (empty values included), without forwarding them.

    A client is ``selected`` only when its exact name is in the parsed ``--clients`` tuple.
    """
    selected = parse_clients_arg(clients_arg)
    found = []
    for client_key, variables in CLIENT_OVERRIDE_ENV.items():
        for var in variables:
            if var in environ:
                found.append({
                    "client": client_key,
                    "variable": var,
                    "selected": client_key in selected,
                    "code": "client_env_override",
                    "message": (
                        f"{client_key} config location is overridden by {var}; automatic configuration "
                        f"is refused without guessing a path (unset {var} or configure {client_key} manually)"
                    ),
                })
    return found


def path_problem(path, home) -> str | None:
    """Why ``path`` is not a safe destination, or None. Nothing is created or followed.

    Every EXISTING component below HOME (below the root for a path outside HOME) must be a real
    directory: a symbolic link anywhere on the way could redirect a write.
    """
    path, home = os.path.abspath(path), os.path.abspath(home)
    anchor = home if path == home or path.startswith(home.rstrip(os.sep) + os.sep) else os.sep
    current = anchor
    for part in [item for item in os.path.relpath(path, anchor).split(os.sep) if item not in ("", ".")]:
        current = os.path.join(current, part)
        try:
            status = os.lstat(current)
        except OSError:
            return None  # the rest does not exist yet
        if os.path.islink(current):
            return f"{current} is a symbolic link"
        if current != path and not os.path.isdir(current):
            return f"{current} is not a directory"
        if not os.path.isdir(current) and status.st_nlink > 1:
            return f"{current} has several hard links"
    return None


def write_new(path, text: str) -> None:
    """Create ``path`` (0600) and write it; never truncates, follows or shares an existing file."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        status = os.fstat(handle.fileno())
        if status.st_nlink != 1 or not os.path.isfile(path) or os.path.islink(path):
            raise OSError("destination is not a private regular file")
        handle.write(text)


def check_uv(path, *, run, environ) -> dict:
    """``uv --version`` of the binary given by an explicit path. Nothing else of uv runs in a preview."""
    report = {"path": None, "version": None, "minimum": _dotted(UV_MINIMUM), "ok": False, "code": None,
              "message": None, "size": None, "sha256": None}
    minimum = report["minimum"]
    hint = (f"give the uv binary by an explicit path: --uv /path/to/uv (uv >= {minimum}, from the astral-sh/uv "
            "release, compared with its published SHA-256); the bootstrap downloads nothing by itself and never "
            "looks uv up on PATH; nothing written")

    def refuse(code, message):
        report.update(code=code, message=f"{code}: {message}")
        return report
    if not path:
        return refuse("uv_missing", "no uv binary was given; " + hint)
    resolved = os.path.abspath(os.path.expanduser(str(path)))
    report["path"] = resolved
    if os.path.basename(resolved).lower().startswith("uvx"):
        return refuse("uvx_refused", "uvx is never used (ephemeral environments, downloads before any consent); " + hint)
    if not os.path.isfile(resolved) or not os.access(resolved, os.X_OK):
        return refuse("uv_not_executable", f"{resolved} is not an executable file; " + hint)
    report["size"], report["sha256"] = os.path.getsize(resolved), sha256_file(resolved)
    code, out = _run(run, [resolved, "--version"], env=uv_environment(environ, offline=True), timeout=PROBE_TIMEOUT)
    match = re.match(r"\s*uv\s+([0-9]+)\.([0-9]+)\.([0-9]+)(?![0-9.]*[a-zA-Z-])", out or "") if code == 0 else None
    if not match:
        return refuse("uv_unreadable", f"{resolved} --version gave no readable release version; " + hint)
    version = tuple(int(part) for part in match.groups())
    report["version"] = _dotted(version)
    if version < UV_MINIMUM:
        return refuse("uv_too_old", f"uv {report['version']} is older than the minimum {minimum} (older releases "
                      "only offer standalone CPython builds carrying SQLite 3.50.4); install uv "
                      f"{minimum} or later in a dedicated directory and pass it with --uv; nothing written")
    report["ok"] = True
    return report


def probe_python(python, *, run, environ) -> dict:
    """Execute ``python`` so that IT reads back its version, its build and the SQLite it loads."""
    report = {"ok": False, "code": "python_unprobed", "probed": str(python), "python": None, "implementation": None,
              "sqlite": None, "sqlite_ok": False, "sqlite_code": "sqlite_runtime_unattested", "standalone": None,
              "build": None, "sys_version": None, "executable": None, "prefix": None, "base_prefix": None}
    # -I -S: no PYTHON* variable, no user site, and ``site`` is not imported at all, so no .pth
    # file and no sitecustomize of the target runs; sqlite3, sysconfig and BUILD need none of them.
    code, out = _run(run, [python, "-I", "-S", "-B", "-c", PROBE_SCRIPT], env=child_environment(environ),
                     timeout=PROBE_TIMEOUT)
    try:
        if code != 0:
            raise ValueError
        seen = json.loads(out.strip().splitlines()[-1])
        version = tuple(int(part) for part in seen["python"])
        sqlite = None if seen["sqlite"] is None else tuple(int(part) for part in seen["sqlite"])
        if len(version) != 3 or (sqlite is not None and len(sqlite) != 3):
            raise ValueError
    except (ValueError, KeyError, TypeError, IndexError, AttributeError):
        return report
    checks = (("loaded", "sqlite_runtime_unavailable", seen.get("loaded") is True),
              ("version", "sqlite_runtime_too_old", sqlite is not None and sqlite >= MIN_SQLITE),
              ("json1", "sqlite_json_missing", seen.get("json1") is True),
              ("wal", "sqlite_wal_unavailable", seen.get("wal_compiled") is True))
    sqlite_code = next((failed for _, failed, passed in checks if not passed), None)
    report.update(ok=True, code=None, python=_dotted(version), implementation=seen.get("implementation"),
                  sqlite=_dotted(sqlite) if sqlite else None, sqlite_ok=sqlite_code is None, sqlite_code=sqlite_code,
                  standalone=seen.get("standalone") is True, build=seen.get("build"),
                  sys_version=seen.get("sys_version"), executable=seen.get("executable"),
                  prefix=seen.get("prefix"), base_prefix=seen.get("base_prefix"))
    return report


# ---------------------------------------------------------------- proof state (local P9 manifest)

def proof_state(manifest_path, *, pin: dict, cell_id, profile) -> dict:
    """``PASS``/``FAIL`` only from a LOCAL manifest naming this very artifact, cell and lock."""
    result = {"state": NOT_TESTED, "reason": "manifest_not_provided", "means": None, "limit": UNSIGNED,
              "manifest": None, "source": "local file only; the preview never fetches a proof"}

    def said(reason, **facts):
        result.update(reason=reason, **facts)
        return result
    if not manifest_path:
        return result
    path = os.path.abspath(os.path.expanduser(str(manifest_path)))
    result["manifest"] = {"path": path, "sha256": None}
    try:
        if os.path.getsize(path) > P9_MAX_BYTES:
            return said("manifest_unreadable")
        data = Path(path).read_bytes()
        result["manifest"]["sha256"] = _sha256_bytes(data)
        document = json.loads(data.decode("utf-8"))
    except (OSError, ValueError, RecursionError):
        return said("manifest_unreadable")
    # Any JSON is accepted as INPUT; only the exact expected shape is read. A wrong type anywhere
    # is "malformed" and proves nothing: never an exception, never a guess.
    if not isinstance(document, dict) or isinstance(document.get("schema_version"), bool) \
            or document.get("schema_version") != 1 or document.get("kind") != P9_KIND:
        return said("manifest_unreadable")
    artifact, entries = document.get("artifact"), document.get("cells")
    artifact = artifact.get("sha256") if isinstance(artifact, dict) else None
    if not isinstance(artifact, str) or not isinstance(entries, list) \
            or not all(isinstance(entry, dict) and isinstance(entry.get("cell_id"), str)
                       and isinstance(entry.get("state"), str)
                       and isinstance(entry.get("lock_sha256", ""), str)
                       and isinstance(entry.get("means", ""), (str, type(None))) for entry in entries):
        return said("manifest_malformed")
    if not pin.get("ok"):
        return said("release_pin_unfilled")
    if artifact != pin["wheel"]["sha256"]:
        return said("artifact_mismatch")
    lock = (pin["cells"].get(cell_id) or {}).get(profile) if cell_id else None
    if not cell_id or lock is None:
        return said("cell_or_lock_absent_from_pin")
    wanted = lock_fingerprint(cell_id, profile, lock)
    named = [entry for entry in entries if entry["cell_id"] == cell_id]
    if not named:
        return said("cell_absent_from_manifest")
    exact = [entry for entry in named if entry.get("lock_sha256") == wanted]
    if not exact:
        return said("lock_mismatch")
    states = {entry.get("state") for entry in exact}
    if len(states) != 1 or not states <= {"PASS", "FAIL"}:
        return said("state_not_proven" if len(states) == 1 else "manifest_ambiguous")
    return said("manifest_matches_artifact_cell_and_lock", state=states.pop(), means=exact[0].get("means"))


# ---------------------------------------------------------------- plan (preview)

def _inventory(paths) -> list:
    """What the destinations look like (no content read): used before and after a preview."""
    seen = []
    for path in paths:
        try:
            status = os.lstat(path)
            names = sorted(os.listdir(path)) if os.path.isdir(path) else None
            seen.append([str(path), status.st_mode, status.st_size, status.st_mtime_ns, names])
        except OSError:
            seen.append([str(path), None, None, None, None])
    return seen


def requirements_text(pin: dict, cell_id: str, profile: str) -> str:
    """The hashed requirements handed to ``uv pip install --require-hashes``: wheel + whole lock."""
    lock, wheel = pin["cells"][cell_id][profile], pin["wheel"]
    lines = [f"# memory-os-ai {pin['version']} | cell {cell_id} | profile {profile} | lock "
             f"{lock_fingerprint(cell_id, profile, lock)}",
             f"{PACKAGE}[{EXTRAS[profile]}]=={pin['version']} --hash=sha256:{wheel['sha256']}"]
    hashes = {}
    for item in lock["artifacts"]:
        hashes.setdefault(canonical_name(item["name"]), []).append(item["sha256"])
    for line in sorted(lock["constraints"], key=str.lower):
        name = canonical_name(line.split("==", 1)[0])
        lines.append(line + "".join(f" --hash=sha256:{digest}" for digest in sorted(hashes[name])))
    return "\n".join(lines) + "\n"


def uv_commands(*, uv, venv, route, base_python, cell_id, requirements, wheelhouse) -> dict:
    """Every uv command of the application, as argument lists (no shell, ``~`` already expanded)."""
    python = os.path.join(venv, "bin", "python")
    request = STANDALONE[cell_id]["request"] if route == "i" else base_python
    commands = {}
    if route == "i":
        # The ONLY step allowed to download an interpreter, and only with --acquire-cpython.
        # --no-bin: no executable is added to a directory on PATH; only the managed directory is written.
        commands["acquire_cpython"] = [uv, "python", "install", "--no-config", "--no-bin", request]
    commands["venv"] = [uv, "venv", "--no-config", "--no-project", "--offline", "--no-python-downloads",
                        *(["--managed-python"] if route == "i" else []), "--python", request, venv]
    # --link-mode copy: the durable venv shares no hard link with the cache of the work directory.
    install = [uv, "pip", "install", "--no-config", "--no-python-downloads", "--python", python,
               "--require-hashes", "--only-binary", ":all:", "--link-mode", "copy", "-r", requirements]
    if wheelhouse:
        install += ["--offline", "--no-index", "--find-links", wheelhouse]
    commands["install"] = install
    return commands


def quickstart_command(venv, profile, clients, *, apply=False) -> list:
    argv = [os.path.join(venv, "bin", "python"), "-I", "-m", MODULE + ".ops.cli", "quickstart", "--profile", profile]
    if clients:
        argv += ["--clients", clients]
    return argv + (["--apply", "--download-approved"] if apply else [])


def build_plan(options, *, pin=None, run=subprocess.run, environ=None, host=None, clock=time.time) -> dict:
    """PREVIEW: reads only; the bootstrap writes nothing and opens no connection. Returns the report.

    Limits, stated in the report: writes are checked on the inventoried destinations only, and the
    absence of network use by the two child processes is not proven here (gate P3).
    """
    environ = os.environ if environ is None else environ
    pin = load_pin() if pin is None else pin
    host = detect_host(environ=environ) if host is None else host
    blockers, codes, notices = [], [], []

    def block(code, message):
        codes.append(code)
        blockers.append(message if message.startswith(code) else f"{code}: {message}")
    home = environ.get("HOME") or os.path.expanduser("~")

    def expand(path):
        text = str(path)
        if text == "~" or text.startswith("~/"):
            text = home + text[1:]
        return os.path.abspath(text)
    venv = expand(options.venv or DEFAULT_VENV)
    # A PRIVATE work directory per run, created exclusively at --apply (never an existing one).
    work_dir = expand(options.work_dir) if options.work_dir else os.path.join(
        os.path.dirname(venv), time.strftime("bootstrap-%Y%m%dT%H%M%SZ", time.gmtime(clock())) + f"-{os.getpid()}")
    uv_cache = os.path.join(work_dir, "uv-cache")
    # The managed Python directory is named explicitly (uv's own default location), only for route (i).
    uv_python_dir = None
    if options.cpython:
        uv_python_dir = expand(environ.get("UV_PYTHON_INSTALL_DIR") or os.path.join(
            environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share"), "uv", "python"))
    destinations = [venv, os.path.dirname(venv), work_dir, os.path.dirname(work_dir), uv_cache]
    if uv_python_dir:
        destinations += [uv_python_dir, os.path.dirname(uv_python_dir)]
    destinations = list(dict.fromkeys(destinations))
    before = _inventory(destinations)
    profile = options.profile
    if profile is None:
        block("profile_required", "choose --profile lexical or --profile semantic; no default is chosen; nothing written")

    uv = check_uv(options.uv, run=run, environ=environ)
    if not uv["ok"]:
        block(uv["code"], uv["message"])

    # ---- target interpreter and route
    minors = standalone_minors(host)
    offer = (" or use the standalone CPython named for this system: --cpython " + " | --cpython ".join(minors)
             + " (a download, launched only with --apply --acquire-cpython)") if minors else ""
    standalone = interpreter = None
    if options.cpython and options.python:
        block("python_choice_conflict", "--python (route a) and --cpython (route i) exclude each other; nothing written")
    if options.cpython:
        route = "i"
        match = re.fullmatch(r"([0-9]+)\.([0-9]+)", options.cpython)
        target = match_cell(host, (int(match[1]), int(match[2])) if match else (0, 0))
        standalone = STANDALONE.get(target["cell_id"])
        if standalone is None:
            block("standalone_not_named", f"no standalone CPython is named for {target['label']}"
                  + (f"; named here: --cpython {' | --cpython '.join(minors)}" if minors else
                     "; this system has no route (i)") + "; nothing written")
        base_python = None
        interpreter = {"route": "i", "state": "installed or reused through uv at --apply", "probed": None,
                       "python": standalone and standalone["python"], "sqlite": None,
                       "sqlite_declared": STANDALONE_SQLITE_DECLARED if standalone else None, "sqlite_ok": None,
                       "build": STANDALONE_BUILD if standalone else None,
                       "request": standalone and standalone["request"]}
    else:
        route = "a"
        base_python = expand(options.python) if options.python else os.path.abspath(sys.executable)
        probe = probe_python(base_python, run=run, environ=environ)
        interpreter = {"route": "a", "state": "existing interpreter, read back by executing it", **probe}
        version = tuple(int(part) for part in probe["python"].split(".")) if probe["ok"] else (0, 0)
        target = match_cell(host, version, probe["implementation"] or "cpython") if probe["ok"] else \
            {"cell_id": None, "label": "unknown", "retained": False, "refusal": None, "retained_cells": list(CELLS)}
        if is_ephemeral(base_python):
            block("python_ephemeral", f"{base_python} is a throwaway interpreter (uv cache, uvx, build environment); "
                  "a durable interpreter is required: --python /path/to/python" + offer + "; nothing written")
        elif not probe["ok"]:
            block("python_unprobed", f"{base_python} could not be executed to read back its version and its SQLite; "
                  "give a durable CPython with --python /path/to/python" + offer + "; nothing written")
        elif not probe["sqlite_ok"]:
            block(probe["sqlite_code"], f"SQLite loaded by {base_python} is unfit (version {probe['sqlite']}, minimum "
                  f"{_dotted(MIN_SQLITE)}); no other journal mode and no implicit download. Route (a): give any "
                  "durable Python of a retained cell whose loaded SQLite meets the minimum (--python /path/to/python)"
                  + offer + "; nothing written")
    if (route == "i" and standalone is None) or (route == "a" and not interpreter["ok"]):
        pass  # already refused above: the cell cannot be named
    elif not target["retained"]:
        block("cell_not_retained", f"{target['refusal']}; retained cells: {', '.join(CELLS)}; give a Python of a "
              "retained cell with --python /path/to/python" + offer + "; other platforms remain manual; "
              "nothing written")
    cell_id = target["cell_id"]

    # ---- pin, lock, proof
    lock = None
    if not pin["ok"]:
        block(pin["code"], pin["message"])
    elif cell_id and profile:
        if profile == "semantic" and cell_id not in SEMANTIC_CELLS:
            block("profile_not_qualified_on_cell", f"the semantic profile is available only on "
                  f"{', '.join(SEMANTIC_CELLS)}; cell {cell_id} accepts --profile lexical only; nothing written")
        else:
            lock = (pin["cells"].get(cell_id) or {}).get(profile)
            if lock is None:
                block("cell_lock_missing", f"this release pins no lock for cell {cell_id} (profile {profile}); it "
                      "cannot be installed on this cell; nothing written")
    lock_report = None if lock is None else {
        "cell_id": cell_id, "profile": profile, "sha256": lock_fingerprint(cell_id, profile, lock),
        "constraints": len(lock["constraints"]), "artifacts": len(lock["artifacts"]),
        "bytes": sum(item["size"] for item in lock["artifacts"])}
    proof = proof_state(options.p9_manifest, pin=pin, cell_id=cell_id, profile=profile)

    # ---- destinations
    if os.path.lexists(venv):
        block("venv_exists", f"{venv} already exists; it is never cleared or reused: choose another --venv or remove "
              "it yourself; nothing written")
    if os.path.lexists(work_dir):
        block("work_dir_exists", f"{work_dir} already exists; the work directory is created by the bootstrap itself "
              "and never reused: choose another --work-dir; nothing written")
    for destination in dict.fromkeys([venv, work_dir, *([uv_python_dir] if uv_python_dir else [])]):
        problem = path_problem(os.path.dirname(destination), home) or (
            path_problem(destination, home) if destination == uv_python_dir else None)
        if problem:
            block("destination_unsafe", f"{problem}: a destination or one of its parents is a link, so a write "
                  f"could be redirected ({destination}); give real directories; nothing written")
    if is_ephemeral(venv):
        block("venv_ephemeral", f"{venv} is inside a cache or throwaway location; a durable venv is required")
    wheelhouse = expand(options.wheelhouse) if options.wheelhouse else None
    wheel_local = None
    if wheelhouse and pin["ok"]:
        candidate = os.path.join(wheelhouse, pin["wheel"]["filename"])
        if not os.path.isfile(candidate):
            block("wheel_missing", f"{candidate} is absent from the wheelhouse; nothing written")
        else:
            wheel_local = {"path": candidate, "sha256": sha256_file(candidate)}
            if wheel_local["sha256"] != pin["wheel"]["sha256"]:
                block("wheel_mismatch", f"{candidate} has SHA-256 {wheel_local['sha256']}, the bootstrap pins "
                      f"{pin['wheel']['sha256']}; nothing written")

    # ---- acquisitions: everything named, nothing implicit
    acquisitions = [{"object": "bootstrap (this script)", "source": "published next to the wheel, with its SHA-256",
                     "version": f"format {BOOTSTRAP_FORMAT}", "size": _own("size"), "destination": _own("path"),
                     "sha256": _own("sha256"), "when": "already acquired"}]
    acquisitions.append({"object": "uv", "source": "given by --uv (astral-sh/uv release or equivalent); never "
                         "downloaded by the bootstrap", "version": uv["version"], "size": uv["size"],
                         "destination": uv["path"], "sha256": uv["sha256"], "when": "already acquired"})
    if proof["manifest"]:
        acquisitions.append({"object": "P9 manifest", "source": "published next to the bootstrap, with its SHA-256",
                             "version": None, "size": None, "destination": proof["manifest"]["path"],
                             "sha256": proof["manifest"]["sha256"], "when": "already acquired"})
    if route == "i" and standalone:
        acquisitions.append({"object": "CPython " + standalone["python"], "source": STANDALONE_SOURCE,
                             "version": standalone["python"] + "+" + STANDALONE_BUILD,
                             "size": int(standalone["megabytes"] * 1_000_000),
                             "destination": uv_python_dir, "sha256": None,
                             "when": "--apply --acquire-cpython (skipped by uv when already installed)"})
    if pin["ok"]:
        wheel = pin["wheel"]
        origin = f"wheelhouse {wheelhouse} (offline)" if wheelhouse else "pypi.org / files.pythonhosted.org"
        acquisitions.append({"object": "wheel " + wheel["filename"], "source": wheel.get("source") or origin,
                             "version": pin["version"], "size": wheel["size"], "destination": venv,
                             "sha256": wheel["sha256"], "when": "--apply"})
        for item in sorted(lock["artifacts"], key=lambda entry: entry["filename"]) if lock else []:
            acquisitions.append({"object": "wheel " + item["filename"], "source": origin if wheelhouse else item["url"],
                                 "version": item["version"], "size": item["size"], "destination": venv,
                                 "sha256": item["sha256"], "when": "--apply"})
    acquisitions.append({"object": "uv cache", "source": "written by uv pip install (copies of the wheels above); "
                         "kept, never reused by another run", "version": None, "size": None, "destination": uv_cache,
                         "sha256": None, "when": "--apply (no additional download)"})
    acquisitions.append({"object": "cl100k_base.tiktoken", "source": "named by the quickstart preview run from the "
                         "venv; downloaded only by quickstart --apply --download-approved", "version": None,
                         "size": CL100K_BYTES, "destination": "resource directory named by quickstart",
                         "sha256": None, "when": "later, quickstart (never the bootstrap)"})
    if profile == "semantic":
        acquisitions.append({"object": "semantic model", "source": "named, with its size, by the quickstart preview "
                             "run from the venv", "version": None, "size": None,
                             "destination": "resource directory named by quickstart", "sha256": None,
                             "when": "later, quickstart (never the bootstrap)"})

    requirements = os.path.join(work_dir, f"requirements-{cell_id}-{profile}.txt")
    commands = {}
    if uv["path"] and cell_id and profile and (route == "a" or standalone):
        commands = uv_commands(uv=uv["path"], venv=venv, route=route, base_python=base_python, cell_id=cell_id,
                               requirements=requirements, wheelhouse=wheelhouse)
        commands["quickstart_preview"] = quickstart_command(venv, profile, options.clients)
    if route == "i" and standalone:
        notices.append("route (i): the standalone CPython is installed only with --apply --acquire-cpython; without "
                       "that option the venv is created only if uv already manages " + standalone["request"])
        notices.append(f"SQLite {STANDALONE_SQLITE_DECLARED} is DECLARED by the build, not attested: it is read back "
                       "from the venv before anything is installed")
    client_env_overrides = detect_client_env_overrides(environ, options.clients)
    for override in client_env_overrides:
        if override["selected"]:
            block("client_env_override", override["message"])
        else:
            notices.append(f"client_env_override (non-selected client {override['client']}): {override['message']}")

    after = _inventory(destinations)
    writes = PREVIEW_WRITES
    if before != after:
        changed = ", ".join(a[0] for a, b in zip(after, before) if a != b)
        block("preview_destination_changed", "an inventoried destination changed during the preview ("
              + changed + "); the preview is not valid")
        writes = "CHANGE DETECTED on inventoried destinations during the preview: " + changed
    return {
        "schema_version": 1, "tool": "memory-os-bootstrap", "bootstrap": {"format": BOOTSTRAP_FORMAT,
                                                                           "sha256": _own("sha256"), "path": _own("path")},
        "stage": "preview", "ok": not blockers, "writes": writes, "network": PREVIEW_NETWORK,
        "blockers": blockers, "blocker_codes": codes, "notices": notices,
        "client_env_overrides": client_env_overrides,
        "system": {"os": host["os"], "arch": host["arch"], "machine": host["machine"],
                   "distribution": host["distribution"], "wsl": host["wsl"]},
        "service_manager": host["service_manager"], "uv": uv, "route": route, "interpreter": interpreter,
        "cell": {**target, "proof": proof}, "profile": profile,
        "release": {"state": pin["state"], "version": pin["version"], "wheel": pin["wheel"]},
        "lock": lock_report, "wheel_local": wheel_local,
        "sqlite_attestation": _attestation(interpreter, venv_probe=None),
        "venv": venv, "work_dir": work_dir, "uv_cache": uv_cache, "uv_python_dir": uv_python_dir,
        "requirements": requirements, "acquisitions": acquisitions,
        # What --apply may fetch from the network: nothing for the wheels when a wheelhouse is given.
        "download_bytes": sum(item["size"] or 0 for item in acquisitions if item["when"].startswith("--apply")
                              and not (wheelhouse and item["object"].startswith("wheel "))),
        "commands": {name: quote(argv) for name, argv in commands.items()}, "_argv": commands,
        "no_write_check": {"scope": destinations, "identical": before == after,
                           "limit": "only these destinations are compared before and after (type, size, date, "
                                    "listing); a write elsewhere is not detected here: the proof is the inventory "
                                    "of HOME and the confinement of gate P3 (lot L6)"},
        "next": (quote([sys.executable, _own("path") or "memory_os_bootstrap.py", *options.replay, "--apply"])
                 if not blockers else None),
    }


def _attestation(interpreter, *, venv_probe) -> dict:
    """Both SQLite read-back values (venv, server), in EVERY report, failures included."""
    base = interpreter or {}
    if venv_probe is not None:
        venv = {"state": "attested" if venv_probe["sqlite_ok"] else "refused", "version": venv_probe["sqlite"],
                "ok": venv_probe["sqlite_ok"], "code": venv_probe["sqlite_code"], "python": venv_probe["python"],
                "read_by": "venv interpreter"}
    else:
        venv = {"state": "not_created", "version": None, "ok": None, "code": None, "python": None, "read_by": None}
    return {"minimum": _dotted(MIN_SQLITE), "journal_modes": "WAL only (no DELETE fallback)", "venv": venv,
            "base_interpreter": {"version": base.get("sqlite"), "ok": base.get("sqlite_ok"),
                                 "declared": base.get("sqlite_declared"), "python": base.get("python"),
                                 "read_by": "base interpreter" if base.get("probed") else None},
            "server": {"version": None, "ok": None, "state": "not_running", "code": None}}


_OWN = {}


def _own(key):
    """Path, size and SHA-256 of this very script (read once, never written)."""
    if not _OWN:
        try:
            path = os.path.abspath(__file__)
            _OWN.update(path=path, size=os.path.getsize(path), sha256=sha256_file(path))
        except (OSError, NameError):
            _OWN.update(path=None, size=None, sha256=None)
    return _OWN[key]


# ---------------------------------------------------------------- application (named option)

def apply_plan(plan: dict, options, *, pin=None, run=subprocess.run, environ=None, clock=time.time) -> dict:
    """APPLY: drive uv, read back the venv, check the installed artifact, preview quickstart from it."""
    environ = os.environ if environ is None else environ
    pin = load_pin() if pin is None else pin
    report = {key: value for key, value in plan.items() if key not in ("next", "no_write_check")}
    report.update(stage="apply", ok=False, code=None, error=None, steps=[], writes=[],
                  network="used by uv only (index or wheelhouse); not confined here",
                  venv_kept=None, installed=None, quickstart=None)
    if not plan["ok"]:
        report.update(code="preflight_refused", error="preflight refused: " + "; ".join(plan["blockers"]),
                      writes="none by the bootstrap (refused before any write)",
                      network="none opened by the bootstrap (refused before any uv command)")
        return report
    selected_overrides = [item for item in detect_client_env_overrides(environ, options.clients) if item["selected"]]
    if selected_overrides:
        report.update(code="client_env_override",
                      error="client_env_override: " + "; ".join(item["message"] for item in selected_overrides),
                      writes="none by the bootstrap (refused before any write)",
                      network="none opened by the bootstrap (refused before any uv command)")
        return report
    argv, venv, work_dir, route = plan["_argv"], plan["venv"], plan["work_dir"], plan["route"]
    home = environ.get("HOME") or os.path.expanduser("~")
    cache_dir, python_dir = plan["uv_cache"], plan["uv_python_dir"]
    child_env = child_environment(environ)
    python = os.path.join(venv, "bin", "python")
    cell_id, profile = plan["cell"]["cell_id"], plan["profile"]

    class Refused(Exception):
        def __init__(self, code, message):
            super().__init__(message)
            self.code = code

    def step(name, command, *, env, timeout=UV_TIMEOUT):
        started = clock()
        code, out = _run(run, command, env=env, timeout=timeout)
        report["steps"].append({"step": name, "command": quote(command), "returncode": code,
                                "elapsed_s": round(clock() - started, 3)})
        return code, out
    def safe(path, *, parents_only=False):
        problem = path_problem(os.path.dirname(path) if parents_only else path, home)
        if problem:
            raise Refused("destination_unsafe", f"{problem}; nothing is written through a link ({path})")
    created = False
    try:
        # The private work directory: parents made if needed, the directory itself created
        # EXCLUSIVELY (an existing one is refused), then checked: ours, a real directory, 0700.
        safe(work_dir, parents_only=True)
        os.makedirs(os.path.dirname(work_dir), mode=0o700, exist_ok=True)
        safe(work_dir, parents_only=True)
        try:
            os.mkdir(work_dir, 0o700)
        except FileExistsError:
            raise Refused("work_dir_exists", f"{work_dir} appeared since the preflight; it is never reused or "
                          "overwritten; nothing written") from None
        created = True
        status = os.lstat(work_dir)
        if os.path.islink(work_dir) or not os.path.isdir(work_dir) or status.st_uid != os.getuid() \
                or status.st_mode & 0o077:
            raise Refused("destination_unsafe", f"{work_dir} is not a private directory of this user")
        report["writes"].append(work_dir)
        requirements = plan["requirements"]
        write_new(requirements, requirements_text(pin, cell_id, profile))
        report["writes"].append(requirements)
        os.mkdir(cache_dir, 0o700)
        report["writes"].append(cache_dir)
        uv_env = dict(cache_dir=cache_dir, python_dir=python_dir)
        if route == "i":
            safe(python_dir)
        if route == "i" and options.acquire_cpython:
            # The one consented interpreter download: explicit request, explicit option.
            code, _ = step("acquire_cpython", argv["acquire_cpython"],
                           env=uv_environment(environ, python_downloads="manual", **uv_env))
            if code != 0:
                raise Refused("cpython_acquisition_failed", f"uv could not install {STANDALONE[cell_id]['request']} "
                              "(see the uv output by running the command yourself); nothing else was attempted")
            report["writes"].append(python_dir)
        # Checked again HERE, not only at the preflight: an existing venv is never handed to uv.
        safe(venv, parents_only=True)
        if os.path.lexists(venv):
            raise Refused("venv_exists", f"{venv} exists (it appeared since the preflight); it is never cleared or "
                          "reused; uv venv was not run")
        code, _ = step("venv", argv["venv"], env=uv_environment(environ, offline=True, **uv_env))
        report["venv_kept"] = os.path.lexists(venv)
        if code != 0:
            hint = ("; the standalone CPython is not installed: re-run with --apply --acquire-cpython (a download of "
                    f"about {STANDALONE[cell_id]['megabytes']} MB)") if route == "i" and not options.acquire_cpython else ""
            raise Refused("venv_creation_failed", f"uv could not create {venv} without downloading an interpreter" + hint)
        report["writes"].append(venv)
        # Reservation (c): the TARGET interpreter is executed and speaks for itself, before any install.
        probe = probe_python(python, run=run, environ=environ)
        report["venv_interpreter"] = probe
        report["sqlite_attestation"] = _attestation(plan["interpreter"], venv_probe=probe) if probe["ok"] else \
            {**report["sqlite_attestation"], "venv": {"state": "unattested", "version": None, "ok": False,
                                                      "code": "sqlite_runtime_unattested", "python": None,
                                                      "read_by": None}}
        if not probe["ok"]:
            raise Refused("python_unprobed", f"{python} could not be executed to read back its SQLite; nothing installed")
        seen = match_cell(detect_host(environ=environ) if options.host is None else options.host,
                          tuple(int(part) for part in probe["python"].split(".")), probe["implementation"])
        if seen["cell_id"] != cell_id:
            raise Refused("venv_cell_mismatch", f"the venv interpreter is {seen['label']}, the plan was for {cell_id}; "
                          "nothing installed")
        if not probe["sqlite_ok"]:
            raise Refused(probe["sqlite_code"], f"SQLite read back by the venv interpreter is unfit (version "
                          f"{probe['sqlite']}, minimum {_dotted(MIN_SQLITE)}); the cell stays {NOT_TESTED}; nothing "
                          "installed and no other acquisition is attempted")
        if route == "i":
            expected = STANDALONE[cell_id]
            pinned_version = ((pin["cells"][cell_id][profile].get("interpreter") or {}).get("sys_version"))
            build_state = ("unattested" if not probe["build"] else
                           "attested" if probe["build"] == STANDALONE_BUILD else "mismatch")
            if pinned_version is not None:
                build_state = "attested" if probe["sys_version"] == pinned_version and build_state != "mismatch" \
                    else "mismatch"
            proven = build_state == "attested"
            report["standalone"] = {"python": probe["python"], "expected_python": expected["python"],
                                    "standalone": probe["standalone"], "build": probe["build"],
                                    "expected_build": STANDALONE_BUILD, "build_state": build_state,
                                    "rule": "the build must be PROVEN: by the BUILD file uv writes in a managed "
                                            "installation, or by the exact sys.version pinned in the release; "
                                            "an exact Python version alone does not identify a build"}
            if probe["python"] != expected["python"] or not probe["standalone"] or build_state == "mismatch":
                raise Refused("standalone_mismatch", f"the venv interpreter is not the named standalone CPython "
                              f"{expected['python']} build {STANDALONE_BUILD} (found {probe['python']}, build "
                              f"{probe['build']}); nothing installed")
            if not proven:
                raise Refused("standalone_build_unattested", f"the build {STANDALONE_BUILD} of the venv interpreter "
                              "is not proven (no BUILD file in the managed installation and no sys.version pinned "
                              f"by the release); the cell stays {NOT_TESTED}; nothing installed")
        elif probe["python"] != plan["interpreter"]["python"]:
            raise Refused("venv_python_mismatch", "the venv interpreter is not the probed base interpreter; nothing installed")
        code, _ = step("install", argv["install"],
                       env=uv_environment(environ, offline=bool(options.wheelhouse), **uv_env))
        if code != 0:
            raise Refused("install_failed", "uv pip install refused the hashed, binary-only requirements "
                          f"({requirements}); the venv is kept as it is for inspection")
        # The installed artifact against the pinned digest (the same value the P9 manifest must carry).
        # Declared limit: this check needs ``site`` (importlib.metadata over site-packages), so the
        # .pth files of the hashed, just-installed distributions are processed; nothing else is.
        code, out = _run(run, [python, "-I", "-B", "-c", INSTALLED_SCRIPT, PACKAGE, MODULE, ",".join(INSTALLER_FILES)],
                         env=child_env, timeout=PROBE_TIMEOUT * 4)
        try:
            installed = json.loads(out.strip().splitlines()[-1]) if code == 0 else {}
        except (ValueError, IndexError):
            installed = {}
        wheel = pin["wheel"]
        inside = bool(installed.get("origin")) and os.path.realpath(installed["origin"]).startswith(
            os.path.realpath(venv) + os.sep)
        report["installed"] = {"version": installed.get("version"), "content_sha256": installed.get("content_sha256"),
                               "expected_content_sha256": wheel["content_sha256"], "wheel_sha256": wheel["sha256"],
                               "files": installed.get("files"), "module_origin": installed.get("origin"),
                               "inside_venv": inside,
                               "ok": installed.get("version") == pin["version"] and inside
                               and installed.get("content_sha256") == wheel["content_sha256"]}
        if not report["installed"]["ok"]:
            raise Refused("artifact_mismatch", "the installed memory-os-ai is not the pinned artifact (version, "
                          "location or content digest differ); quickstart is not run")
        # Allow-listed environment: no MEMORY_V4_* (nor any other product variable) reaches the child.
        code, out = step("quickstart_preview", argv["quickstart_preview"], env=child_env, timeout=QUICKSTART_TIMEOUT)
        try:
            preview = json.loads(out)
        except ValueError:
            preview = None
        if not isinstance(preview, dict):
            raise Refused("quickstart_unreadable", "quickstart (preview) run from the venv gave no readable report")
        report["quickstart"] = {"returncode": code, "ok": preview.get("ok"), "stage": preview.get("stage"),
                                "writes": preview.get("writes"), "blockers": preview.get("blockers", []),
                                "blocker_codes": preview.get("blocker_codes", []),
                                "sqlite_attestation": preview.get("sqlite_attestation"),
                                "cell_id": (preview.get("cell") or {}).get("cell_id"),
                                "downloads_required_bytes": preview.get("downloads_required_bytes")}
        if preview.get("ok") is not True:
            raise Refused("quickstart_blocked", "the venv is installed and attested, but the quickstart preview "
                          "reports blockers: " + "; ".join(str(item) for item in preview.get("blockers", [])))
        report.update(ok=True, stage="complete",
                      next=quote(quickstart_command(venv, profile, options.clients, apply=True)))
    except Refused as refusal:
        report.update(code=refusal.code, error=f"{refusal.code}: {refusal}")
    except OSError as exc:
        report.update(code="bootstrap_write_failed", error="bootstrap_write_failed: " + type(exc).__name__)
    if report["venv_kept"] is None:
        report["venv_kept"] = os.path.lexists(venv)
    report["report_path"] = None
    if created:  # kept in OUR private directory only, failures included; nothing is ever removed
        try:
            target = os.path.join(work_dir, "report.json")
            report["report_path"] = target
            write_new(target, json.dumps(_public(report), indent=1, ensure_ascii=False))
        except OSError:
            report["report_path"] = None
    return report


def _public(report: dict) -> dict:
    return {key: value for key, value in report.items() if not key.startswith("_")}


# ---------------------------------------------------------------- command line

def render(report: dict) -> str:
    """Human-readable report (``--json`` prints the whole structure)."""
    system, uv, cell, proof = report["system"], report["uv"], report["cell"], report["cell"]["proof"]
    interpreter, attested, release = report["interpreter"] or {}, report["sqlite_attestation"], report["release"]
    distribution = system["distribution"] or {}
    lines = [
        f"Memory OS AI bootstrap - {report['stage']} - writes: {report['writes'] if isinstance(report['writes'], str) else len(report['writes'])}"
        f" - network: {report['network']}",
        f"  bootstrap sha256 : {report['bootstrap']['sha256']}",
        f"  system           : {system['os']} {system['arch']}"
        + (f" ({distribution.get('id')} {distribution.get('version_id')})" if distribution else ""),
        f"  service manager  : {report['service_manager']['kind']} (available: {report['service_manager']['available']}, presence only)",
        f"  uv               : {uv['version'] or 'not read'} (minimum {uv['minimum']}) {uv['path'] or ''}",
        f"  route            : ({report['route']}) Python {interpreter.get('python')} {interpreter.get('probed') or interpreter.get('request') or ''}",
        f"  SQLite           : base {attested['base_interpreter']['version'] or 'declared ' + str(attested['base_interpreter']['declared'])}"
        f" | venv {attested['venv']['version'] or attested['venv']['state']} | server {attested['server']['state']}"
        f" (minimum {attested['minimum']})",
        f"  cell             : {cell['label']} (retained: {cell['retained']})",
        f"  proof state      : {proof['state']} [{proof['reason']}] - {proof['limit']}",
        f"  release          : {release['version'] or release['state']} "
        + (f"{release['wheel']['filename']} sha256 {release['wheel']['sha256']}" if release["wheel"] else "(no wheel pinned)"),
        "  lock             : " + (f"{report['lock']['profile']} / {report['lock']['constraints']} packages / sha256 "
                                     f"{report['lock']['sha256']}" if report["lock"] else "none"),
        f"  venv             : {report['venv']}",
        "  acquisitions (object | version | size in bytes | source | destination | when):",
    ]
    for item in report["acquisitions"]:
        lines.append(f"    - {item['object']} | {item['version'] or '-'} | {item['size'] if item['size'] is not None else 'unknown'}"
                     f" | {item['source']} | {item['destination']} | {item['when']}")
    lines.append(f"  bytes fetched by --apply: {report['download_bytes']}")
    for name, command in report["commands"].items():
        lines.append(f"  command [{name}]: {command}")
    for step in report.get("steps", []):
        lines.append(f"  step {step['step']}: return code {step['returncode']} in {step['elapsed_s']} s")
    if report.get("installed"):
        lines.append(f"  installed artifact: version {report['installed']['version']}, content digest "
                     f"{'matches' if report['installed']['ok'] else 'DOES NOT MATCH'} the pinned value")
    if report.get("quickstart"):
        lines.append(f"  quickstart preview (from the venv): ok={report['quickstart']['ok']}")
    lines += ["  notice: " + notice for notice in report["notices"]]
    lines += ["  REFUSED: " + blocker for blocker in report["blockers"]]
    if report.get("error"):
        lines.append("  FAILED: " + report["error"])
    if report.get("report_path"):
        lines.append("  report: " + report["report_path"])
    if report.get("next"):
        lines.append("  next (you launch it): " + report["next"])
    lines.append(f"  result: {'OK' if report['ok'] else 'NOT OK'}")
    return "\n".join(lines)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="memory_os_bootstrap.py", description="Memory OS AI bootstrap: preview by default "
                                "(no write, no network); --apply creates a durable venv through uv (never uvx).")
    p.add_argument("--uv", help="explicit path of the uv binary (>= 0.13.9); never looked up on PATH")
    p.add_argument("--profile", choices=PROFILES, help="install profile; no default")
    p.add_argument("--python", help="route (a): an existing durable interpreter (default: the one running this script)")
    p.add_argument("--cpython", metavar="X.Y", help="route (i): the standalone CPython named for this system and minor")
    p.add_argument("--venv", help=f"durable venv to create (default {DEFAULT_VENV})")
    p.add_argument("--work-dir", help="where the requirements file and the reports are written by --apply "
                   "(default: 'bootstrap' next to the venv)")
    p.add_argument("--wheelhouse", help="directory holding the pinned wheels: install offline, without any index")
    p.add_argument("--p9-manifest", help="LOCAL P9 manifest giving the proof state of this artifact (not signed)")
    p.add_argument("--clients", help="passed to the quickstart preview run from the venv")
    p.add_argument("--apply", action="store_true", help="create the venv and install (default: preview only)")
    p.add_argument("--acquire-cpython", action="store_true",
                   help="with --apply and --cpython: consent to the download of the named standalone CPython")
    p.add_argument("--json", action="store_true", help="print the whole report as JSON")
    return p


def main(argv=None, *, pin=None, run=subprocess.run, environ=None, host=None, out=None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    options = parser().parse_args(arguments)
    out = sys.stdout if out is None else out
    if options.acquire_cpython and not (options.apply and options.cpython):
        print("--acquire-cpython is a consent given with --apply and --cpython X.Y", file=sys.stderr)
        return 2
    options.replay = [item for item in arguments if item != "--apply"]
    options.host = host
    plan = build_plan(options, pin=pin, run=run, environ=environ, host=host)
    report = apply_plan(plan, options, pin=pin, run=run, environ=environ) if options.apply else plan
    print(json.dumps(_public(report), indent=1, ensure_ascii=False) if options.json else render(report), file=out)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Memory OS AI bootstrap (4.1.1, contract section 4): ONE script, standard library only.

A pinned copy never travels inside the wheel (it pins the SHA-256 of that wheel) and is identified
by its own SHA-256, which every report states. It never imports ``memory_os_ai`` or
``memory_os_v4``: nothing is installed yet.

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

ONE-COMMAND INSTALL (``--interactive``, 4.1.3, decision of Romain 2026-11-26; what ``install.sh`` runs): the same
preview, plus what the install as a whole would write: the MCP clients DETECTED on this machine (their user-level
configuration file, or their command-line tool, at the per-OS paths of ``ops/clients.py``; a relocating variable or
an unsafe path excludes a client, never guessed), the service (label, port, manager, definition), the token, the
store. Profile ``lexical`` by default (``semantic`` is refused here: its model download would not be announced).
Every refusal of the preview happens BEFORE any question; what can only be attested once acquired (route (i): the
SQLite and the build of the downloaded standalone CPython, read back from the created venv; the installed artifact;
the quickstart preview of the venv) is checked after the answer, before anything else is installed or written. Then
ONE question, read from ``/dev/tty`` (``[y/N]``, default No; anything but y/yes is No; without a terminal: preview
only). The bootstrap writes nothing before the answer: nothing in HOME, the store or any client configuration, and no
service (``install.sh``, when it launches it, has only written the two verified files of its private temporary
directory, and the xcrun cache that Apple's ``/usr/bin/python3`` launcher may create there, removed at exit). After ``y``: the plan is recomputed and must be the announced one; the venv is created
as by ``--apply``; the quickstart preview run from the venv must match the announcement (profile, cell, interpreter,
clients and paths, service argv/environment/definition/state, store, ops directory, token, no download, no blocker),
otherwise it stops fail-closed; its ``plan_digest`` is recomputed here and handed to ``quickstart --apply
--expect-plan``, which refuses before any write a plan that is not that one; then an authenticated ``/ready`` (token
read from its file, never printed). The confirmation is also the consent to the standalone CPython download of route
(i) that the preview lists with its source and size. Follow-up commands name the installation explicitly (env).

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
from pathlib import Path, PurePosixPath
import platform
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
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
# or configuration variable, nor any client-relocating variable (CLAUDE_CONFIG_DIR, CODEX_HOME,
# GEMINI_CLI_HOME, GROK_HOME and, on Linux, COPILOT_HOME), is inherited. Relocating client variables are detected by the
# bootstrap itself and reported consistently with ``client_env_override`` without being forwarded.
# 4.1.3 (lifts the 4.1.1 L5 limit): the quickstart preview ALONE also receives XDG_CONFIG_HOME, see
# ``quickstart_environment``; quickstart validates it itself (client_xdg_invalid, client_xdg_outside_home).
# xcrun_db: where Apple's /usr/bin/python3 launcher (an xcrun shim) writes its lookup cache; install.sh points it into
# its private temporary directory, so that probing --python /usr/bin/python3 writes nothing elsewhere before y.
CHILD_ENV_KEEP = ("HOME", "PATH", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "XDG_RUNTIME_DIR",
                  "xcrun_db")
DEFAULT_CLIENTS = ("claude-code", "codex")
CLIENT_OVERRIDE_ENV = {
    "claude-code": ("CLAUDE_CONFIG_DIR",),
    "codex": ("CODEX_HOME",),
    "gemini-cli": ("GEMINI_CLI_HOME",),
    "grok": ("GROK_HOME",),
}
# 4.1.3 (1af4619, Codex review of Lot 3 point 6): VS Code on Linux is the Copilot user configuration, relocated by
# COPILOT_HOME (present, even empty, refuses it) on Linux ONLY; macOS keeps its own path and ignores the variable.
LINUX_CLIENT_OVERRIDE_ENV = {"vscode": ("COPILOT_HOME",)}
# The known clients, as ``ops/clients.py`` CLIENTS (same keys, names, per-OS paths relative to HOME, relocating
# variables and Zed's XDG subpath; a test compares them field by field). ``cli``: the command-line tool whose presence
# on PATH also detects the client (claude-code is configured ONLY through it: ``claude mcp add --scope user``).
CLIENT_TABLE = {
    "claude-code": {"name": "Claude Code", "darwin": ".claude.json", "linux": ".claude.json",
                    "override": ("CLAUDE_CONFIG_DIR",), "cli": "claude"},
    "claude-desktop": {"name": "Claude Desktop", "darwin": "Library/Application Support/Claude/claude_desktop_config.json",
                       "linux": None},
    "codex": {"name": "Codex (CLI/app)", "darwin": ".codex/config.toml", "linux": ".codex/config.toml",
              "override": ("CODEX_HOME",), "cli": "codex"},
    "vscode": {"name": "VS Code / GitHub Copilot", "darwin": "Library/Application Support/Code/User/mcp.json",
               "linux": ".copilot/mcp-config.json", "linux_override": ("COPILOT_HOME",)},
    "cursor": {"name": "Cursor", "darwin": ".cursor/mcp.json", "linux": ".cursor/mcp.json"},
    "antigravity": {"name": "Antigravity", "darwin": ".gemini/config/mcp_config.json",
                    "linux": ".gemini/config/mcp_config.json"},
    "gemini-cli": {"name": "Gemini CLI", "darwin": ".gemini/settings.json", "linux": ".gemini/settings.json",
                   "override": ("GEMINI_CLI_HOME",), "cli": "gemini"},
    "grok": {"name": "Grok CLI", "darwin": ".grok/config.toml", "linux": ".grok/config.toml",
             "override": ("GROK_HOME",), "cli": "grok"},
    "zed": {"name": "Zed", "darwin": ".config/zed/settings.json", "linux": ".config/zed/settings.json",
            "xdg": "zed/settings.json"},
    "devin": {"name": "Devin Desktop", "darwin": ".config/devin/mcp_config.json", "linux": ".config/devin/mcp_config.json"},
    "windsurf": {"name": "Windsurf", "darwin": ".codeium/windsurf/mcp_config.json", "linux": None},
}
# Same text as ``ops/clients.py`` LINUX_UNSUPPORTED_REASONS.
LINUX_UNSUPPORTED_REASONS = {
    "windsurf": ("Windsurf is not supported on Linux in 4.1.2 (official documentation cites conflicting user-level "
                 "paths; re-evaluated in 4.1.3); no path guessed, nothing written"),
    "claude-desktop": ("Claude Desktop is not supported on Linux in 4.1.2 (official Linux version is in beta; "
                       "re-evaluated in 4.1.3); no path guessed, nothing written"),
}
# What a quickstart run from the venv uses when no MEMORY_V4_* variable reaches it (OpsSettings.from_env defaults).
SERVICE_LABEL, SERVICE_PORT = "local.memory-os-v4", 8765
SERVICE_URL = f"http://127.0.0.1:{SERVICE_PORT}/mcp/"
READY_URL = f"http://127.0.0.1:{SERVICE_PORT}/ready"
TOKEN_SUBPATH, STORE_SUBPATH, OPS_SUBPATH = ".config/memory-os/http.token", ".memory-os-v4", ".local/state/memory-os"
# quickstart.settings_for_install: a NEW SQLite install without MEMORY_V4_DIR, without a service definition and
# without the legacy directory gets this store instead of the legacy ``STORE_SUBPATH`` (the quickstart run from the
# venv never receives MEMORY_V4_DIR; an existing definition is refused here as an existing installation).
NEW_STORE_SUBPATH = ".local/share/memory-os/store"
JOURNAL_SUBPATH = ".local/state/memory-os/quickstart"
# Actions of the quickstart preview that write a client entry as announced ("unchanged" writes nothing).
WRITING_ACTIONS = ("create", "replace", "unchanged")
CONFIRM_PROMPT = "Install Memory OS AI with exactly this plan? [y/N] "
# Same digest as ``ops/quickstart.plan_digest`` (a test compares them on a real plan).
PLAN_DIGEST_VERSION = 2
PLAN_DIGEST_CLIENT_KEYS = ("client", "path", "file_sha256", "action", "desired_sha256", "current_class", "ownership")
PLAN_DIGEST_SERVICE_KEYS = ("label", "path", "argv", "env", "state", "sha256", "manager", "plist", "unit",
                            "loaded_state")
WRITES_BEFORE_ANSWER = ("nothing is written in HOME, in the store or in any client configuration, and no service is "
                        "installed, before your y: this bootstrap writes nothing before it (when install.sh launched "
                        "it, its only writes are the two verified files, this bootstrap and SHA256SUMS, and the xcrun "
                        "cache that Apple's python3 launcher may create, all in a private temporary directory, the "
                        "only thing created in TMPDIR, removed at exit)")
CHECKED_BEFORE = ("cell, release pin and lock, uv, the interpreter of route (a) and its SQLite (read back by executing "
                  "it), destinations, clients and their paths, service manager and definition, existing installation")
CHECKED_AFTER = ("only once acquired, after y and before anything else is installed: route (i) the SQLite and the "
                 "build of the downloaded standalone CPython (read back from the created venv), then the digest of "
                 "the installed artifact, then the quickstart preview of the venv (compared with this announcement "
                 "and bound to quickstart --apply by its plan digest)")
EXIT_NOT_CONFIRMED = 3  # nothing written: no terminal, or an answer other than y/yes
ENTRY_SCAN_MAX_BYTES = 16_000_000
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43,256}")
PREVIEW_WRITES = "none detected on the inventoried destinations (preview)"
PREVIEW_NETWORK = "none opened by the bootstrap; absence of network use not proven here (gate P3, lot L6)"
PROBE_TIMEOUT, UV_TIMEOUT, QUICKSTART_TIMEOUT = 30, 1800, 300
QUICKSTART_APPLY_TIMEOUT, READY_TIMEOUT, READY_ATTEMPTS, READY_PAUSE = 900, 5, 3, 2.0

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
                          "is a template; it pins no wheel and no lock: the preview stays possible, --apply is refused. To "
                          "install, use the two pip commands of the README, with a Python whose loaded SQLite "
                          "is >= 3.51.3; nothing written")
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

    The release tooling calls it once the wheel is built and the locks are frozen; the result is
    identified by its SHA-256. A pin that does not validate is refused: nothing half-filled.
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


def quickstart_environment(environ) -> dict:
    """Environment of the quickstart preview only: the child allow-list plus ``XDG_CONFIG_HOME`` when set.

    The value is forwarded verbatim and validated by quickstart itself, with the same rules as a direct
    ``memory-os-admin quickstart`` (absolute, under HOME, no ``..``, no symbolic link): the preview then
    resolves Zed and the systemd unit exactly where a direct run would. Probes never receive it."""
    env = child_environment(environ)
    if environ.get("XDG_CONFIG_HOME"):
        env["XDG_CONFIG_HOME"] = environ["XDG_CONFIG_HOME"]
    return env


def parse_clients_arg(value, default=DEFAULT_CLIENTS):
    """Copy of ``memory_os_v4.ops.quickstart.parse_clients_arg`` (the bootstrap is stdlib only).

    Absent or empty -> ``default``; otherwise an exact ``split(",")``, nothing stripped or case-folded.
    """
    return tuple(value.split(",")) if value else default


def normalize_os(value=None) -> str:
    """``darwin``, ``linux`` or the raw value (same rule as ``ops/clients.normalize_platform``)."""
    raw = sys.platform if value is None else str(value)
    return "darwin" if raw == "darwin" else "linux" if raw.startswith("linux") else raw


def client_override_env(os_kind=None) -> dict:
    """Relocating variables per client on ``os_kind``: COPILOT_HOME (vscode) is a Linux variable only."""
    table = {key: tuple(variables) for key, variables in CLIENT_OVERRIDE_ENV.items()}
    if normalize_os(os_kind) == "linux":
        for key, variables in LINUX_CLIENT_OVERRIDE_ENV.items():
            table[key] = table.get(key, ()) + tuple(variables)
    return table


def detect_client_env_overrides(environ, clients_arg=None, *, os_kind=None) -> list:
    """Detect relocating client env vars by presence (empty values included), without forwarding them.

    A client is ``selected`` only when its exact name is in the parsed ``--clients`` tuple. ``os_kind``
    (default: this system) decides the per-OS variables: COPILOT_HOME counts for vscode on Linux only.
    """
    selected = parse_clients_arg(clients_arg)
    found = []
    for client_key, variables in client_override_env(os_kind).items():
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


def quickstart_command(venv, profile, clients, *, apply=False, download_approved=True, expect_plan=None) -> list:
    argv = [os.path.join(venv, "bin", "python"), "-I", "-B", "-m", MODULE + ".ops.cli", "quickstart", "--profile",
            profile]
    if clients:
        argv += ["--clients", clients]
    if not apply:
        return argv
    return argv + ["--apply"] + (["--download-approved"] if download_approved else []) + (
        ["--expect-plan", expect_plan] if expect_plan else [])


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
    acquisitions = [{"object": "bootstrap (this script)", "source": "this script (its own SHA-256 is reported)",
                     "version": f"format {BOOTSTRAP_FORMAT}", "size": _own("size"), "destination": _own("path"),
                     "sha256": _own("sha256"), "when": "already acquired"}]
    acquisitions.append({"object": "uv", "source": "given by --uv (astral-sh/uv release or equivalent); never "
                         "downloaded by the bootstrap", "version": uv["version"], "size": uv["size"],
                         "destination": uv["path"], "sha256": uv["sha256"], "when": "already acquired"})
    if proof["manifest"]:
        acquisitions.append({"object": "P9 manifest",
                             "source": "a local file given with --p9-manifest (not fetched, not signed)",
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
    # 4.1.3 (D3): the cl100k_base table ships inside the pinned wheel; it is no acquisition any more.
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
    client_env_overrides = detect_client_env_overrides(environ, options.clients, os_kind=host["os"])
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
    selected_overrides = [item for item in detect_client_env_overrides(environ, options.clients,
                                                                        os_kind=plan["system"]["os"])
                          if item["selected"]]
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
        code, out = step("quickstart_preview", argv["quickstart_preview"], env=quickstart_environment(environ),
                         timeout=QUICKSTART_TIMEOUT)
        try:
            preview = json.loads(out)
        except ValueError:
            preview = None
        if not isinstance(preview, dict):
            raise Refused("quickstart_unreadable", "quickstart (preview) run from the venv gave no readable report")
        report["_quickstart_plan"] = preview  # the whole preview, compared by --interactive (never saved)
        report["quickstart"] = {"returncode": code, "ok": preview.get("ok"), "stage": preview.get("stage"),
                                "writes": preview.get("writes"), "blockers": preview.get("blockers", []),
                                "blocker_codes": preview.get("blocker_codes", []),
                                "sqlite_attestation": preview.get("sqlite_attestation"),
                                "cell_id": (preview.get("cell") or {}).get("cell_id"),
                                "downloads_required_bytes": preview.get("downloads_required_bytes")}
        if preview.get("ok") is not True:
            raise Refused("quickstart_blocked", "the venv is installed and attested, but the quickstart preview "
                          "reports blockers: " + "; ".join(str(item) for item in preview.get("blockers", [])))
        if code != 0:
            raise Refused("quickstart_returncode", f"the quickstart preview says ok but its process returned {code}; "
                          "it is not trusted")
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


# ---------------------------------------------------------------- one-command install (--interactive)

def _under(path: PurePosixPath, base: PurePosixPath) -> bool:
    try:
        path.relative_to(base)
    except ValueError:
        return False
    return True


def client_path(key: str, home, *, os_kind, environ) -> tuple:
    """``(path, None, None)`` or ``(None, code, message)``: ``ops/clients.Client.config_path``, rule for rule.

    Order and texts are those of the product: unsupported on the OS, then a relocating variable (present, even
    empty), then, for Zed on Linux only, ``XDG_CONFIG_HOME`` (absolute, under HOME, no ``..``, no symbolic link).
    Nothing is created; a refused client is never given a guessed path.
    """
    record, os_kind = CLIENT_TABLE[key], normalize_os(os_kind)
    name = record["name"]
    if os_kind == "darwin":
        relative = record["darwin"]
    elif os_kind == "linux":
        relative = record["linux"]
        if relative is None:
            return None, "client_unsupported_on_os", LINUX_UNSUPPORTED_REASONS.get(
                key, f"{name} ({key}) is not supported on Linux in 4.1.2; no path guessed, nothing written")
    else:
        return None, "client_unsupported_on_os", (f"{name} ({key}) has no established user-level config path on "
                                                  f"platform {os_kind!r}; no path guessed, nothing written")
    for var in client_override_env(os_kind).get(key, ()):
        if var in environ:
            return None, "client_env_override", (
                f"{name} ({key}) config location is overridden by {var}; automatic configuration is refused without "
                f"guessing a path (unset {var} or configure {name} manually)")
    xdg = environ.get("XDG_CONFIG_HOME") if os_kind == "linux" and record.get("xdg") else None
    if xdg:
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in xdg):
            return None, "client_xdg_invalid", (f"{name} ({key}): XDG_CONFIG_HOME contains control characters; "
                                                "refusing to guess or write")
        if not os.path.isabs(xdg):
            return None, "client_xdg_invalid", (
                f"{name} ({key}): XDG_CONFIG_HOME must be an absolute path under HOME (relative XDG_CONFIG_HOME is "
                "refused); unset XDG_CONFIG_HOME or set an absolute path under HOME")
        if ".." in PurePosixPath(xdg).parts:
            return None, "client_xdg_outside_home", (f"{name} ({key}): XDG_CONFIG_HOME contains parent traversal "
                                                     "('..'); refusing to write outside HOME")
        norm_home = PurePosixPath(os.path.normpath(os.path.abspath(str(home))))
        norm_xdg = PurePosixPath(os.path.normpath(xdg))
        if not _under(norm_xdg, norm_home):
            return None, "client_xdg_outside_home", (
                f"{name} ({key}): XDG_CONFIG_HOME is outside the selected HOME (XDG_CONFIG_HOME outside HOME is not "
                "supported, consistent with _safe_path and L4 systemd unit rules); move XDG_CONFIG_HOME under HOME "
                "or configure Zed manually")
        cursor = norm_home
        for part in norm_xdg.relative_to(norm_home).parts:
            cursor = cursor / part
            if os.path.islink(str(cursor)):
                return None, "client_xdg_outside_home", (f"{name} ({key}): XDG_CONFIG_HOME traverses a symbolic "
                                                         "link under HOME; refusing to write")
        return str(norm_xdg / record["xdg"]), None, None
    return str(PurePosixPath(str(home)) / relative), None, None


def config_file_problem(path, home) -> str | None:
    """Why quickstart's ``_safe_path`` would refuse ``path``, or None (read-only, nothing followed)."""
    target, base = PurePosixPath(os.path.abspath(str(path))), PurePosixPath(os.path.abspath(str(home)))
    if not _under(target, base):
        return f"{target} is outside the selected HOME"
    for candidate in [target, *target.parents]:
        if candidate == base.parent:
            break
        if os.path.islink(str(candidate)):
            return f"{candidate} is a symbolic link (manual setup required)"
    try:
        status = os.stat(str(target))
    except FileNotFoundError:
        return None
    except OSError:
        return f"{target} cannot be inspected"
    if not os.path.isfile(str(target)):
        return f"{target} is not a regular file"
    if status.st_nlink != 1 or status.st_uid != os.getuid():
        return f"{target} must be owned by this user with one link"
    return None


def mentions_server(path) -> bool | None:
    """Whether the file already names ``memory-os-ai`` (bytes only, nothing parsed or kept); None when unknown."""
    try:
        if os.path.getsize(path) > ENTRY_SCAN_MAX_BYTES:
            return None
        with open(path, "rb") as handle:
            return b"memory-os-ai" in handle.read(ENTRY_SCAN_MAX_BYTES + 1)
    except FileNotFoundError:
        return False
    except OSError:
        return None


def detect_clients(home, *, os_kind, environ, which, explicit=None) -> dict:
    """The clients the one-command install would configure (read-only).

    Without ``explicit`` (``--clients``): a client is DETECTED by its user-level configuration file at the per-OS
    path, or by its command-line tool on PATH. A relocated, unsupported or unsafe client, a Claude Code without its
    ``claude`` CLI, or a file that already names ``memory-os-ai`` is listed as excluded, with its reason, and never
    configured. With ``explicit``, each named client must be configurable: any such reason is a blocker.
    """
    os_kind = normalize_os(os_kind)
    selected, excluded, blockers = [], [], []
    path_env = environ.get("PATH") or ""

    def refuse(key, code, message, **facts):
        if explicit is not None:
            blockers.append((code, message if message.startswith(code) else f"{code}: {message}"))
        else:
            excluded.append({"client": key, "code": code, "reason": message, **facts})
    for key in (explicit if explicit is not None else tuple(CLIENT_TABLE)):
        if key not in CLIENT_TABLE:
            blockers.append(("client_unknown", f"client_unknown: {key!r} is not a known client (known: "
                             f"{', '.join(CLIENT_TABLE)}); nothing written"))
            continue
        path, code, message = client_path(key, home, os_kind=os_kind, environ=environ)
        if code:
            refuse(key, code, message)
            continue
        cli = CLIENT_TABLE[key].get("cli")
        cli_path = which(cli, path=path_env) if cli and path_env else None
        cli_path = cli_path if cli_path and os.path.isabs(cli_path) else None  # never a command of the current dir
        evidence = (["configuration file present"] if os.path.lexists(path) else []) + (
            [f"{cli} command on PATH ({cli_path})"] if cli_path else [])
        if explicit is None and not evidence:
            continue  # not present on this machine
        if key == "claude-code" and not cli_path:
            refuse(key, "client_cli_missing", "client_cli_missing: Claude Code is configured only through "
                   "`claude mcp add --scope user` and the claude CLI is not on PATH; nothing written", path=path)
            continue
        problem = config_file_problem(path, home)
        if problem:
            refuse(key, "client_path_unsafe", f"client_path_unsafe: {problem}; nothing written", path=path)
            continue
        if mentions_server(path) is not False:
            refuse(key, "client_entry_present", f"client_entry_present: {path} already names memory-os-ai (or "
                   "cannot be read); the one-command install never touches an existing entry: configure this "
                   f"client afterwards with quickstart --clients {key} (it decides, with --replace-existing for a "
                   "named consent); nothing written", path=path)
            continue
        selected.append({"client": key, "name": CLIENT_TABLE[key]["name"], "path": path,
                         "evidence": evidence or ["named with --clients"],
                         "written_by": ("claude mcp add --scope user (claude CLI)" if key == "claude-code"
                                        else "quickstart (backup and journal, undoable)")})
    return {"selected": selected, "excluded": excluded, "blockers": blockers,
            "mode": "named with --clients" if explicit is not None else "detected"}


def service_definition(home, *, manager_kind, environ) -> tuple:
    """(definition path, problem): ``service.Launchd`` / ``SystemdUser.definition_path`` for the default label."""
    home_path = PurePosixPath(str(home))
    if manager_kind == "launchd":
        path = home_path / "Library/LaunchAgents" / (SERVICE_LABEL + ".plist")
    elif manager_kind == "systemd-user":
        base = environ.get("XDG_CONFIG_HOME") or ""
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in base) or ".." in PurePosixPath(base).parts:
            return None, "XDG_CONFIG_HOME holds control characters or '..'; the unit path is not attested"
        root = PurePosixPath(base) if os.path.isabs(base) else home_path / ".config"
        path = root / "systemd/user" / (SERVICE_LABEL + ".service")
    else:
        return None, "no service manager is available on this system"
    if not _under(path, home_path):
        return None, (f"the service definition {path} is outside the selected HOME (XDG_CONFIG_HOME outside HOME is "
                      "not supported)")
    return str(path), None


def store_directory(home) -> str:
    """The store quickstart selects for a new install run from the venv (``settings_for_install``, same rule)."""
    legacy = str(PurePosixPath(str(home)) / STORE_SUBPATH)
    return legacy if os.path.exists(legacy) else str(PurePosixPath(str(home)) / NEW_STORE_SUBPATH)


def interactive_extras(options, *, host, environ, which) -> dict:
    """Everything the one-command install writes beyond the venv, and its own refusals (read-only)."""
    home = environ.get("HOME") or os.path.expanduser("~")
    home = os.path.abspath(home)
    explicit = parse_clients_arg(options.clients, None)
    clients = detect_clients(home, os_kind=host["os"], environ=environ, which=which, explicit=explicit)
    blockers, notices = list(clients["blockers"]), []
    if options.profile != "lexical":
        blockers.append(("interactive_profile_lexical_only", "interactive_profile_lexical_only: the one-command "
                         "install is the lexical profile (no resource download); for the semantic profile follow "
                         "the 'Semantic profile (recommended)' commands of the README manual install (or --apply, "
                         "then the quickstart preview and apply yourself); nothing written"))
    if not clients["selected"] and explicit is None:
        blockers.append(("no_client_detected", "no_client_detected: no MCP client was found on this machine "
                         "(user-level configuration file or command-line tool); name them with --clients a,b; "
                         "nothing written"))
    manager = host.get("service_manager") or {}
    definition, problem = service_definition(home, manager_kind=manager.get("kind"), environ=environ)
    if problem:
        blockers.append(("service_definition_unavailable", f"service_definition_unavailable: {problem}; nothing "
                         "written"))
    elif not manager.get("available"):
        blockers.append(("service_manager_unavailable", f"service_manager_unavailable: {manager.get('kind')} is not "
                         "usable here (presence check); the service cannot be installed; nothing written"))
    else:
        unsafe = config_file_problem(definition, home)
        if unsafe:
            blockers.append(("service_definition_unavailable", f"service_definition_unavailable: {unsafe}; nothing "
                             "written"))
    store = store_directory(home)
    token = str(PurePosixPath(home) / TOKEN_SUBPATH)

    def used(path):
        try:
            return os.path.islink(path) or (os.path.isdir(path) and bool(os.listdir(path))) or (
                os.path.lexists(path) and not os.path.isdir(path))
        except OSError:
            return True
    existing = [f"{path} (store already used)" for path in dict.fromkeys(
        [store, str(PurePosixPath(home) / STORE_SUBPATH), str(PurePosixPath(home) / NEW_STORE_SUBPATH)]) if used(path)]
    if definition and os.path.lexists(definition):
        existing.append(f"{definition} (service definition)")
    legacy = str(PurePosixPath(home) / STORE_SUBPATH)
    if os.path.lexists(legacy):
        # quickstart would select it, and the SQLite engine refuses it even empty (validate_sqlite_target): never
        # touched, never used, refused here before any question instead of after the venv.
        blockers.append(("legacy_store_present", f"legacy_store_present: {legacy} exists (even empty); quickstart "
                         "would select it and the SQLite store is never created there, so the one-command install "
                         "refuses it: leave it untouched and use the upgrade runbook, or install by hand with "
                         "MEMORY_V4_DIR set to a fresh directory; nothing written"))
    else:
        resolved = os.path.realpath(store)
        for protected in (legacy, str(PurePosixPath(home) / "Memory-os-ai")):
            protected = os.path.realpath(protected)
            if resolved == protected or resolved.startswith(protected.rstrip(os.sep) + os.sep):
                blockers.append(("store_path_forbidden", f"store_path_forbidden: {store} resolves to {resolved}, "
                                 f"inside {protected}, where the SQLite store is never created; nothing written"))
    if existing:
        blockers.append(("existing_installation", "existing_installation: " + ", ".join(existing) + " already "
                         "exist; the one-command install is for a new installation only (use quickstart from the "
                         "existing venv, or the upgrade runbook); nothing written"))
    if any(key.startswith("MEMORY_V4_") for key in environ):
        notices.append("MEMORY_V4_* variables of this shell are not passed on: the install uses the default "
                       "locations shown here")
    if any(item["client"] == "claude-desktop" for item in clients["selected"]):
        notices.append("Claude Desktop: quit the app before answering y (quickstart refuses while it runs, "
                       "client_app_running, and the install would stop after the venv)")
    if {"claude-code", "grok"} <= {item["client"] for item in clients["selected"]}:
        notices.append("Grok also reads ~/.claude.json: configuring both claude-code and grok can expose duplicate "
                       "memory-os-ai entries in Grok (disable compatibility import in ~/.grok/config.toml with "
                       "[compat.claude] mcps = false)")
    announced = {
        "profile": options.profile, "mode": clients["mode"],
        "clients": clients["selected"], "excluded_clients": clients["excluded"],
        "service": {"label": SERVICE_LABEL, "port": SERVICE_PORT, "url": SERVICE_URL, "manager": manager.get("kind"),
                    "definition": definition, "listens": "127.0.0.1 only"},
        "token": {"path": token, "exists": os.path.lexists(token),
                  "state": "exists: validated and reused, never printed" if os.path.lexists(token)
                  else "created (0600), never printed"},
        "store": store, "ops_dir": str(PurePosixPath(home) / OPS_SUBPATH),
        "resource_downloads_bytes": 0, "backend": "sqlite", "python": None,
    }
    announced["client_env"] = {"MEMORY_V4_URL": SERVICE_URL, "MEMORY_V4_TOKEN_FILE": token,
                               "MEMORY_V4_OPS_DIR": announced["ops_dir"]}
    return {"announced": announced, "blockers": blockers, "notices": notices,
            "clients_arg": ",".join(item["client"] for item in clients["selected"])}


def quickstart_plan_digest(plan) -> str | None:
    """``ops/quickstart.plan_digest`` of a printed preview (same fields, same canonical JSON); None if unusable."""
    if not isinstance(plan, dict):
        return None
    service = plan.get("service") if isinstance(plan.get("service"), dict) else {}
    clients = plan.get("clients") if isinstance(plan.get("clients"), list) else []
    if not all(isinstance(client, dict) for client in clients):
        return None
    body = {"version": PLAN_DIGEST_VERSION, "profile": plan.get("profile"), "backend": plan.get("backend"),
            "python": plan.get("python"), "port": plan.get("port"), "token": plan.get("token"),
            "provision": plan.get("provision"), "write_targets": plan.get("write_targets"),
            "client_env": plan.get("client_env"),
            "cell_id": (plan.get("cell") or {}).get("cell_id") if isinstance(plan.get("cell"), dict) else None,
            "identity": plan.get("identity"), "downloads_required_bytes": plan.get("downloads_required_bytes"),
            "replace_existing": plan.get("replace_existing"),
            "clients": [{key: client.get(key) for key in PLAN_DIGEST_CLIENT_KEYS} for client in clients],
            "service": {key: service.get(key) for key in PLAN_DIGEST_SERVICE_KEYS}}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def expected_service(*, python, store, ops_dir, token) -> dict:
    """The service quickstart defines for a new lexical SQLite install from ``python`` (``_service_plan``)."""
    argv = [python, "-m", "memory_os_v4.server", "--transport", "http", "--host", "127.0.0.1", "--port",
            str(SERVICE_PORT), "--warm"]
    env = {"MEMORY_V4_DIR": store, "MEMORY_V4_TOKEN_FILE": token, "MEMORY_V4_URL": SERVICE_URL,
           "MEMORY_V4_SERVICE_LABEL": SERVICE_LABEL, "MEMORY_V4_STORE_BACKEND": "sqlite_s1",
           "MEMORY_V4_WORK_STATE": "true", "MEMORY_V4_MAINTENANCE_MODE": "disabled",
           "MEMORY_V4_BM25_PROFILE": "scoped_literal", "MEMORY_V4_LEXICAL_GUARD_K": "3",
           "TOKENIZERS_PARALLELISM": "false", "MEMORY_V4_OPS_DIR": ops_dir, "HF_HUB_OFFLINE": "1",
           "TRANSFORMERS_OFFLINE": "1", "MEMORY_V4_RETRIEVAL_PROFILE": "lexical"}
    return {"argv": argv, "env": env}


def installation_env(announced) -> list:
    """``NAME=value`` words naming the announced installation, so that no inherited variable selects another."""
    return [f"MEMORY_V4_SERVICE_LABEL={announced['service']['label']}", f"MEMORY_V4_URL={announced['service']['url']}",
            f"MEMORY_V4_DIR={announced['store']}", f"MEMORY_V4_TOKEN_FILE={announced['token']['path']}",
            f"MEMORY_V4_OPS_DIR={announced['ops_dir']}"]


def follow_up(announced, argv) -> str:
    """A follow-up command (doctor, undo, uninstall, quickstart) bound to the announced installation."""
    return quote(["env", *installation_env(announced), *argv])


def compare_preview(preview, announced, *, cell_id) -> list:
    """Every difference between the quickstart preview run from the venv and what was announced (empty: same)."""
    if not isinstance(preview, dict):
        return ["the quickstart preview is not a report"]
    diffs = []
    if preview.get("ok") is not True or preview.get("blockers"):
        diffs.append("blockers: " + "; ".join(str(item) for item in preview.get("blockers") or ["(not ok)"]))
    if preview.get("stage") != "preview" or preview.get("writes") != "none (preview)":
        diffs.append("the report is not a quickstart preview")
    if preview.get("profile") != announced["profile"]:
        diffs.append(f"profile {preview.get('profile')!r} instead of {announced['profile']!r}")
    if (preview.get("cell") or {}).get("cell_id") != cell_id:
        diffs.append(f"cell {(preview.get('cell') or {}).get('cell_id')!r} instead of {cell_id!r}")
    rows = preview.get("clients")
    rows = rows if isinstance(rows, list) and all(isinstance(row, dict) for row in rows) else []
    seen = {row.get("client"): row for row in rows}
    wanted = {item["client"]: item for item in announced["clients"]}
    if len(seen) != len(rows) or set(seen) != set(wanted) or preview.get("unknown_clients"):
        diffs.append(f"clients {sorted(str(key) for key in seen)} instead of {sorted(wanted)}")
    for key in sorted(set(seen) & set(wanted)):
        if seen[key].get("path") != wanted[key]["path"]:
            diffs.append(f"{key}: path {seen[key].get('path')!r} instead of {wanted[key]['path']!r}")
        if seen[key].get("action") not in WRITING_ACTIONS:
            diffs.append(f"{key}: action {seen[key].get('action')!r}")
    service, expected = preview.get("service") or {}, announced["service"]
    if not isinstance(service, dict):
        service = {}
    if service.get("label") != expected["label"] or service.get("path") != expected["definition"]:
        diffs.append(f"service {service.get('label')!r} at {service.get('path')!r} instead of {expected['label']!r} "
                     f"at {expected['definition']!r}")
    if (service.get("manager") or "launchd") != expected["manager"]:
        diffs.append(f"service manager {service.get('manager') or 'launchd'!r} instead of {expected['manager']!r}")
    if service.get("state") != "absent":
        diffs.append(f"service state {service.get('state')!r} (a new service was announced)")
    if preview.get("port") != expected["port"]:
        diffs.append(f"port {preview.get('port')!r} instead of {expected['port']}")
    if (preview.get("token") or {}).get("path") != announced["token"]["path"]:
        diffs.append(f"token {(preview.get('token') or {}).get('path')!r} instead of {announced['token']['path']!r}")
    if preview.get("downloads_required_bytes") != announced["resource_downloads_bytes"]:
        diffs.append(f"resource downloads {preview.get('downloads_required_bytes')!r} bytes, announced "
                     f"{announced['resource_downloads_bytes']}")
    if preview.get("python") != announced["python"]:
        diffs.append(f"interpreter {preview.get('python')!r} instead of {announced['python']!r}")
    if preview.get("backend") != announced["backend"]:
        diffs.append(f"backend {preview.get('backend')!r} instead of {announced['backend']!r}")
    if preview.get("client_env") != announced["client_env"]:
        diffs.append("client environment differs from the announced one (URL, token file, ops directory)")
    if service.get("argv") != expected.get("argv"):
        diffs.append(f"service program {service.get('argv')!r} instead of {expected.get('argv')!r}")
    if service.get("env") != expected.get("env"):
        wanted_env = expected.get("env") or {}
        seen_env = service.get("env") if isinstance(service.get("env"), dict) else {}
        keys = sorted(key for key in set(wanted_env) | set(seen_env) if wanted_env.get(key) != seen_env.get(key))
        diffs.append("service environment differs on " + ", ".join(keys))
    if service.get("loaded_state") != "absent":
        diffs.append(f"service job state {service.get('loaded_state')!r} (no job was announced)")
    token_state = (preview.get("token") or {}).get("state") if isinstance(preview.get("token"), dict) else None
    if token_state != ("ok" if announced["token"]["exists"] else "absent"):
        diffs.append(f"token state {token_state!r} instead of "
                     f"{'reused (ok)' if announced['token']['exists'] else 'created (absent)'}")
    targets = preview.get("write_targets") if isinstance(preview.get("write_targets"), dict) else {}
    token_target = targets.get("token")
    if (token_target is None) == announced["token"]["exists"] or (
            token_target is not None and (token_target.get("kind") != "file" or token_target.get("mode") != 0o600
                                          or token_target.get("nlink") != 1)):
        diffs.append("token file identity differs from the announcement")
    store_target = targets.get("store", "missing")
    if store_target == "missing" or (store_target is not None and (
            store_target.get("kind") != "directory" or store_target.get("entries") != 0)):
        diffs.append("the store is not new and empty")
    provision = preview.get("provision") if isinstance(preview.get("provision"), dict) else {}
    if provision.get("ready") is not True or provision.get("download_bytes") != 0 or \
            provision.get("resources") != ["cl100k"] or provision.get("table_source") != "embedded":
        diffs.append("the resource plan is not the embedded table with nothing to fetch or write")
    digest = preview.get("plan_digest")
    if not isinstance(digest, str) or not HEX64.fullmatch(digest) or digest != quickstart_plan_digest(preview):
        diffs.append("plan_digest absent or not the digest of this preview")
    return diffs


class Terminal:
    """The controlling terminal, read and written directly (``curl ... | sh``: stdin is the script)."""

    def __init__(self, descriptor: int):
        self.descriptor = descriptor

    def write(self, text: str) -> None:
        data = text.encode("utf-8", "replace")
        while data:
            data = data[os.write(self.descriptor, data):]

    def readline(self, limit: int = 1024) -> str:
        data = b""
        while len(data) < limit:
            chunk = os.read(self.descriptor, 1)
            if not chunk or chunk == b"\n":
                break
            data += chunk
        return data.decode("utf-8", "replace")

    def close(self) -> None:
        os.close(self.descriptor)


def open_terminal():
    """``/dev/tty`` or None (no controlling terminal: the one-command install stays a preview)."""
    try:
        return Terminal(os.open("/dev/tty", os.O_RDWR | getattr(os, "O_NOCTTY", 0)))
    except OSError:
        return None


def confirmed(answer) -> bool:
    """Only y or yes (any case, surrounding spaces ignored) is a yes; everything else, EOF included, is a no."""
    return isinstance(answer, str) and answer.strip().lower() in ("y", "yes")


def read_token_file(path) -> str:
    """The bearer token of a private file (regular, this user's, 0600, no link); never printed. ValueError."""
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise ValueError(f"token file {path} cannot be opened ({type(exc).__name__})") from None
    try:
        status = os.fstat(descriptor)
        if not os.path.isfile(path) or status.st_uid != os.getuid() or status.st_mode & 0o077 or status.st_size > 257:
            raise ValueError(f"token file {path} is not a private regular file of this user")
        raw = os.read(descriptor, 258)
    finally:
        os.close(descriptor)
    raw = raw[:-1] if raw.endswith(b"\n") else raw
    text = raw.decode("ascii", "replace")
    if not TOKEN_PATTERN.fullmatch(text):
        raise ValueError(f"token file {path} does not hold a token of the expected format")
    return text


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def check_ready(url, token_path, *, expected_version=None, timeout=READY_TIMEOUT) -> dict:
    """Authenticated ``GET /ready`` of the local service; the token goes in the header only, never in the report."""
    result = {"ok": False, "code": None, "status": None, "url": url, "token_file": str(token_path),
              "ready": None, "reason": None, "version": None}
    try:
        token = read_token_file(token_path)
    except ValueError as exc:
        result.update(code="token_unreadable", reason=str(exc))
        return result
    request = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            result["status"], body = response.status, response.read(65536)
    except urllib.error.HTTPError as exc:
        result.update(status=exc.code, code="ready_refused")
        return result
    except (urllib.error.URLError, OSError, ValueError) as exc:
        result.update(code="ready_unreachable", reason=type(exc).__name__)
        return result
    try:
        document = json.loads(body)
    except ValueError:
        document = None
    if not isinstance(document, dict):
        result["code"] = "ready_unreadable"
        return result
    result.update(ready=document.get("ready"), reason=document.get("reason"), version=document.get("version"))
    if result["status"] != 200 or document.get("ready") is not True or document.get("reason") != "ready":
        result["code"] = "ready_not_ready"
    elif expected_version is not None and document.get("version") != expected_version:
        result["code"] = "ready_version_mismatch"
    else:
        result["ok"] = True
    return result


def render_interactive(extras: dict) -> str:
    announced = extras["announced"]
    service, token = announced["service"], announced["token"]
    lines = ["", "ONE-COMMAND INSTALL: one confirmation covers everything listed above and below",
             "  before y         : " + WRITES_BEFORE_ANSWER,
             f"  profile          : {announced['profile']} (no resource download; the wheels listed above come from "
             "their pinned sources)"]
    for item in extras.get("downloads_after_y") or []:
        lines.append(f"  download after y : {item['object']} ({item['version']}), {item['size']} bytes "
                     f"(about {item['size'] / 1e6:.1f} MB), from {item['source']}, into {item['destination']}")
    lines += [f"  interpreter      : {announced['python']} (the venv)",
             f"  service          : {service['label']} on {service['url']} ({service['listens']}), manager "
             f"{service['manager']}, definition {service['definition']}",
             "  service program  : " + quote(service.get("argv") or []),
             f"  token            : {token['path']} ({token['state']})",
             f"  store            : {announced['store']} (new SQLite store); logs and journals: {announced['ops_dir']}",
             f"  clients ({announced['mode']}), files written:"]
    lines += [f"    - {item['client']}: {item['path']} [{'; '.join(item['evidence'])}] via {item['written_by']}"
              for item in announced["clients"]] or ["    (none)"]
    if announced["excluded_clients"]:
        lines.append("  clients NOT configured:")
        lines += [f"    - {item['client']}: {item['reason']}" for item in announced["excluded_clients"]]
    lines += ["  checked now      : " + CHECKED_BEFORE,
              "  checked after y  : " + CHECKED_AFTER,
              "  after y          : the plan is computed again and must not change; the venv is created; the "
              "quickstart preview run from it must match this announcement exactly (any blocker or difference stops "
              "before quickstart --apply); then quickstart --apply --expect-plan <its digest> and an authenticated "
              "/ready check",
              "  undo afterwards  : quickstart --undo STAMP --with-service, or uninstall (printed at the end); data "
              "is never deleted"]
    lines += ["  notice: " + notice for notice in extras["notices"]]
    lines += ["  REFUSED: " + message for _, message in extras["blockers"]]
    return "\n".join(lines)


def _announcement(plan: dict, extras: dict) -> str:
    """What the user confirmed, as one comparable text (the plan computed again after y must give the same)."""
    keys = ("venv", "work_dir", "uv_cache", "uv_python_dir", "requirements", "route", "profile", "acquisitions",
            "commands", "lock", "release", "download_bytes", "wheel_local")
    body = {key: plan.get(key) for key in keys}
    body["cell"] = (plan.get("cell") or {}).get("cell_id")
    body["announced"] = extras["announced"]
    return json.dumps(body, sort_keys=True, default=str)


def interactive(options, *, pin=None, run=subprocess.run, environ=None, host=None, out=None, terminal=None,
                ready=None, which=None, sleep=time.sleep, clock=time.time) -> dict:
    """The one-command install: complete preview, ONE confirmation, then venv, compared preview, apply, /ready."""
    environ = os.environ if environ is None else environ
    pin = load_pin() if pin is None else pin
    host = detect_host(environ=environ) if host is None else host
    out = sys.stdout if out is None else out
    terminal = open_terminal if terminal is None else terminal
    ready = check_ready if ready is None else ready
    which = shutil.which if which is None else which
    options = argparse.Namespace(**vars(options))
    options.profile = options.profile or "lexical"
    options.host = host
    report = {"schema_version": 1, "tool": "memory-os-bootstrap", "stage": "preview", "ok": False, "code": None,
              "error": None, "steps": [], "writes": "none (preview)", "announced": None, "plan": None, "apply": None,
              "quickstart_apply": None, "ready": None, "next": None}

    def say(text=""):
        print(text, file=out)

    def prepare(work_dir=None):
        extras = interactive_extras(options, host=host, environ=environ, which=which)
        planned = argparse.Namespace(**vars(options))
        planned.clients = extras["clients_arg"] or None
        planned.work_dir = work_dir or options.work_dir
        planned.replay = []
        plan = build_plan(planned, pin=pin, run=run, environ=environ, host=host, clock=clock)
        announced = extras["announced"]
        home_now = os.path.abspath(environ.get("HOME") or os.path.expanduser("~"))
        legacy = str(PurePosixPath(home_now) / STORE_SUBPATH)
        protected = (
            (announced["store"], "destination_in_store", "the store {}, which must stay new and empty"),
            (legacy, "destination_in_legacy_store", "the historical store location {} (even absent: creating it "
             "would make quickstart select it, and it is never used)"))
        for name in ("venv", "work_dir", "uv_cache", "uv_python_dir"):  # every place the bootstrap creates or fills
            value = plan.get(name)
            if not value:
                continue
            forms = {os.path.abspath(value), os.path.realpath(value)}
            for base, code, what in protected:
                bases = {os.path.abspath(base), os.path.realpath(base)}
                if any(form == item or form.startswith(item.rstrip(os.sep) + os.sep) for form in forms
                       for item in bases):
                    extras["blockers"].append((code, f"{code}: the {name} {value} is inside " + what.format(base)
                                               + "; choose another --venv/--work-dir/UV_PYTHON_INSTALL_DIR; "
                                               "nothing written"))
        announced["python"] = os.path.join(plan["venv"], "bin", "python")
        announced["service"].update(expected_service(python=announced["python"], store=announced["store"],
                                                     ops_dir=announced["ops_dir"], token=announced["token"]["path"]))
        for item in plan["acquisitions"]:
            if item["object"].startswith("CPython "):  # consented by the single y (one-command install)
                item["when"] = "after y (skipped by uv when already installed)"
        extras["downloads_after_y"] = [item for item in plan["acquisitions"] if item["object"].startswith("CPython ")]
        for code, message in extras["blockers"]:
            plan["blocker_codes"].append(code)
            plan["blockers"].append(message)
        plan["ok"], plan["next"] = not plan["blockers"], None
        return plan, extras, planned

    def stop(stage, code, message, *, kept=None):
        report.update(stage=stage, code=code, error=message if message.startswith(code) else f"{code}: {message}")
        say("  STOPPED: " + report["error"])
        if kept:
            say("  " + kept)
        return report

    plan, extras, planned = prepare()
    report.update(announced=extras["announced"], plan=_public(plan))
    try:  # the question is asked only when the WHOLE preview reached its output
        say(render(plan))
        say(render_interactive(extras))
        for stream in (out, sys.stdout, sys.stderr):  # visible before the question (tee, pipes)
            stream.flush()
    except (OSError, ValueError, AttributeError) as exc:
        report.update(stage="refused", code="preview_output_failed",
                      error=f"preview_output_failed: the preview could not be written out ({type(exc).__name__}); "
                            "no question is asked; the bootstrap wrote nothing")
        try:
            print(report["error"], file=sys.stderr)
        except (OSError, ValueError):
            pass
        return report
    if not plan["ok"]:
        return stop("refused", "preflight_refused", "preflight_refused: refused before any question; nothing written")
    tty = terminal()
    if tty is None:
        report.update(stage="no-terminal", code="no_terminal")
        say("  No terminal (/dev/tty) to ask the question: this was the preview only; the bootstrap wrote nothing "
            "(nothing in HOME, the store or any client configuration; no service).")
        say("  Run the same command from a terminal to answer it.")
        return report
    try:
        tty.write(CONFIRM_PROMPT)
        answer = tty.readline()
    except (OSError, KeyboardInterrupt):  # Ctrl-C at the question is a no
        answer = ""
    finally:
        try:
            tty.close()
        except OSError:
            pass
    if not confirmed(answer):
        report.update(stage="declined", code="not_confirmed")
        say("  Not confirmed (only y or yes installs): the bootstrap wrote nothing (nothing in HOME, the store or "
            "any client configuration; no service).")
        return report
    # ---- after y: nothing has been written yet; the plan must be the one that was announced
    again, extras_again, planned = prepare(work_dir=plan["work_dir"])
    if not again["ok"] or _announcement(again, extras_again) != _announcement(plan, extras):
        return stop("refused", "preflight_changed", "preflight_changed: the plan computed again after the "
                    "confirmation differs from the announced one (" + ("; ".join(again["blockers"]) or
                                                                        "machine state changed") + "); nothing written")
    planned.acquire_cpython = again["route"] == "i"  # the confirmation is the consent to the CPython listed above
    say("Confirmed. [1/3] venv, pinned wheels and quickstart preview from the venv (uv; this can take a minute)...")
    applied = apply_plan(again, planned, pin=pin, run=run, environ=environ, clock=clock)
    report.update(apply=_public(applied), steps=list(applied["steps"]), writes=list(applied["writes"]))
    venv, profile, clients = again["venv"], again["profile"], extras["clients_arg"]
    admin = os.path.join(venv, "bin", "memory-os-admin")
    announced = extras["announced"]
    by_hand = (f"The venv {venv} is kept (nothing is ever removed). To continue by hand from it: "
               + follow_up(announced, quickstart_command(venv, profile, clients)) + " (preview). The one-command "
               "install refuses "
               f"an existing venv: remove {venv} yourself before running it again, or give --venv.")
    if not applied["ok"]:
        return stop("failed", applied["code"] or "apply_failed", applied["error"] or "the venv step failed",
                    kept=by_hand if applied.get("venv_kept") else "Nothing was installed.")
    differences = compare_preview(applied.get("_quickstart_plan"), extras["announced"],
                                  cell_id=(again.get("cell") or {}).get("cell_id"))
    if differences:
        report["differences"] = differences
        return stop("failed", "interactive_preview_mismatch", "interactive_preview_mismatch: the quickstart preview "
                    "run from the venv differs from what was announced (" + "; ".join(differences) + "); "
                    "quickstart --apply was NOT run; no service, token or client entry was written", kept=by_hand)
    # ---- quickstart --apply with exactly the announced clients (no download is announced, none is approved)
    say("[2/3] the quickstart preview matches the announcement; quickstart --apply (token, service, clients)...")
    command = quickstart_command(venv, profile, clients, apply=True, download_approved=False,
                                 expect_plan=applied["_quickstart_plan"]["plan_digest"])
    started = clock()
    code, text = _run(run, command, env=quickstart_environment(environ), timeout=QUICKSTART_APPLY_TIMEOUT)
    report["steps"].append({"step": "quickstart_apply", "command": quote(command), "returncode": code,
                            "elapsed_s": round(clock() - started, 3)})
    try:
        result = json.loads(text)
    except ValueError:
        result = None
    result = result if isinstance(result, dict) else {}
    stamp = result.get("stamp") if isinstance(result.get("stamp"), str) else None
    report["quickstart_apply"] = {"returncode": code, "ok": result.get("ok"), "stage": result.get("stage"),
                                  "code": result.get("code"), "error": result.get("error"), "stamp": stamp}
    undo = follow_up(announced, [admin, "quickstart", "--undo", stamp, "--with-service"]) if stamp else None
    doctor = follow_up(announced, [admin, "doctor"])
    if code != 0 or result.get("ok") is not True:
        return stop("failed", "quickstart_apply_failed", "quickstart_apply_failed: quickstart --apply did not "
                    f"complete (code {result.get('code')!r}: {result.get('error')!r})",
                    kept=(f"Its journal is kept; roll back with: {undo}" if undo else
                          "No journal stamp was reported.") + f" Then inspect with {doctor}.")
    # ---- authenticated /ready, token read from its file
    token_path = extras["announced"]["token"]["path"]
    say(f"[3/3] authenticated {READY_URL} check...")
    seen = None
    for attempt in range(READY_ATTEMPTS):
        started = clock()
        seen = ready(READY_URL, token_path, expected_version=pin.get("version"))
        report["steps"].append({"step": "ready", "command": "GET " + READY_URL + " (Authorization from "
                                + token_path + ")", "returncode": seen.get("status"),
                                "elapsed_s": round(clock() - started, 3)})
        if seen.get("ok") or attempt == READY_ATTEMPTS - 1:
            break
        sleep(READY_PAUSE)
    report["ready"] = seen
    if not seen.get("ok"):
        return stop("failed", "ready_check_failed", f"ready_check_failed: {READY_URL} answered {seen.get('code')} "
                    f"(status {seen.get('status')})", kept=f"Inspect with {doctor}"
                    + (f"; roll back with: {undo}" if undo else ""))
    report.update(ok=True, stage="complete", code=None, error=None,
                  next={"doctor": doctor, "undo": undo,
                        "uninstall": follow_up(announced, [admin, "uninstall"]) + " (preview), then the same with "
                                     "--apply"})
    say("")
    say(f"Memory OS AI is installed and ready ({READY_URL}, profile {profile}).")
    say("  configured clients (restart them, then check that the memory-os-ai tools appear): "
        + ", ".join(item["client"] for item in extras["announced"]["clients"]))
    say("  next: " + report["next"]["doctor"])
    say("  undo: " + (undo or "quickstart --undo STAMP --with-service (no stamp was reported)") + "  |  remove: "
        + report["next"]["uninstall"] + " (your data is never deleted)")
    try:
        target = os.path.join(again["work_dir"], "interactive-report.json")
        write_new(target, json.dumps(_public(report), indent=1, ensure_ascii=False))
        report["report_path"] = target
        say("  report: " + target)
    except OSError:
        report["report_path"] = None
    return report


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
    p.add_argument("--interactive", action="store_true",
                   help="one-command install (lexical by default): complete preview, ONE [y/N] question read from "
                        "/dev/tty (no terminal: preview only), then venv, compared quickstart preview, quickstart "
                        "--apply and /ready; --clients overrides the detected clients")
    return p


def main(argv=None, *, pin=None, run=subprocess.run, environ=None, host=None, out=None, terminal=None, ready=None,
         which=None, sleep=time.sleep) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    options = parser().parse_args(arguments)
    out = sys.stdout if out is None else out
    if options.interactive:
        conflict = ("--apply" if options.apply else "--acquire-cpython" if options.acquire_cpython else
                    "--json" if options.json else None)
        if conflict:
            print(f"--interactive excludes {conflict}: it is the one-command install for a person at a terminal "
                  "(its single [y/N] confirmation creates the venv and consents to the listed CPython download); "
                  "use the preview / --apply route for scripts", file=sys.stderr)
            return 2
        options.replay = []
        report = interactive(options, pin=pin, run=run, environ=environ, host=host, out=out, terminal=terminal,
                             ready=ready, which=which, sleep=sleep)
        if report["ok"]:
            return 0
        return EXIT_NOT_CONFIRMED if report["stage"] in ("no-terminal", "declined") else 1
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

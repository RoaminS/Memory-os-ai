#!/usr/bin/env python3
"""Standard-library helpers of ``qualify_install_410.sh`` and of the 4.1.0 chain (QUALIFY_4_1_0 §3, G7, G8, M1).

Observation only: this file never loads, unloads or signals anything. ``launchctl print`` and a
local ``bind`` are its only probes. An observation that cannot be made is a refusal (exit 3),
never "absent". Runs under ``python3 -I`` (3.9+), imports nothing from the repository. One exception, read
only: ``store-logical``, run by the Python of the run's venv, submits the receipts of the store to the
installed product's own reader when it is importable (``product_receipts``); elsewhere it says "not checked".
"""
from __future__ import annotations

import argparse
import base64
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import plistlib
import re
import socket
import stat
import sqlite3
import subprocess
import sys
import time
import urllib.request

TEST_LABEL = "local.memory-os-v4.qual410"
TEST_PORT = 18765
PRODUCTION_LABEL = "com.romain.memory-os-v4"
PRODUCTION_PORT = 8765
FORBIDDEN_NAMES = ("brief_v5_val", "brient_v5_val")  # never read or listed; refused below
ML_LIBRARIES = re.compile(r"torch|sentence_transformers|faiss|transformers", re.I)
ML_PACKAGES = {"torch", "sentence-transformers", "transformers", "faiss-cpu", "faiss", "tokenizers", "safetensors",
               "huggingface-hub", "scipy", "scikit-learn"}
BOOTSTRAP = {"pip", "setuptools", "wheel"}
# Every client configuration of the product catalogue (memory_os_v4.ops.clients.CLIENTS); a unit test keeps
# this list equal to the catalogue. This file stays import-free of the repository.
REAL_CLIENT_CONFIGS = (
    ".claude.json", "Library/Application Support/Claude/claude_desktop_config.json", ".codex/config.toml",
    "Library/Application Support/Code/User/mcp.json", ".cursor/mcp.json", ".gemini/config/mcp_config.json",
    ".gemini/settings.json", ".grok/config.toml", ".config/zed/settings.json", ".config/devin/mcp_config.json",
    ".codeium/windsurf/mcp_config.json",
)
# Rewritten continuously by a running client: only its MCP sections are fingerprinted.
VOLATILE_JSON_CONFIGS = (".claude.json",)
PROTECTED_TREES = (".config/memory-os",)  # real token, key, tables: never written by a qualification
TEST_CLIENTS = {"codex": ".codex/config.toml", "cursor": ".cursor/mcp.json"}  # the two clients a run requests
TEST_CLIENT_FILES = tuple(TEST_CLIENTS.values())
REQUIRED_DOCTOR_AREAS = ("token", "local-runtime", "server", "security", "ready", "service", "store", "storage",
                         "store-profile", "relay-journals", "client:codex", "client:cursor")
EXIT_UNOBSERVABLE = 3


class Unobservable(RuntimeError):
    """The state could not be observed: the caller must stop and keep its lock."""


def sha256_file(path):
    path = refuse_forbidden(path)  # before EVERY open, constructed paths and link targets included
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def refuse_forbidden(path):
    """Refuse a sealed validation folder: lexical form first (no stat), then the resolved form.

    Called before every open and before every descent: on arguments, on every path built from them,
    and on every entry met while walking (a link is judged by where it leads). Resolving a path
    reads link targets only; it never opens or lists the destination.
    """
    text = os.path.abspath(os.fspath(path))
    if any(name in Path(text).parts for name in FORBIDDEN_NAMES):
        raise SystemExit("REFUS: sealed validation folder (never read or listed)")
    if any(name in Path(os.path.realpath(text)).parts for name in FORBIDDEN_NAMES):
        raise SystemExit("REFUS: path resolves into a sealed validation folder")
    return Path(text)


def read_text(path):
    return refuse_forbidden(path).read_text()


def guarded_walk(root):
    """``os.walk`` that checks every entry BEFORE any descent, never follows links, and never hides an error.

    A sub-folder with a sealed name is refused before it is listed; a link is refused when it leads
    inside one; a listing error is a refusal (an incomplete inventory is never returned).
    """
    root = refuse_forbidden(root)

    def fail(error):
        raise Unobservable("folder unreadable while walking: %s (%s)" % (getattr(error, "filename", "?"),
                                                                        type(error).__name__))

    for folder, names, files in os.walk(root, onerror=fail, followlinks=False):
        names.sort()
        for name in names + files:
            refuse_forbidden(os.path.join(folder, name))  # top-down: checked before os.walk descends
        yield folder, names, sorted(files)


def check_test_identity(label, port):
    """Never a label starting with the production one, never the production port."""
    if label != TEST_LABEL or label.startswith(PRODUCTION_LABEL) or int(port) != TEST_PORT or int(port) == PRODUCTION_PORT:
        raise SystemExit("REFUS: test label/port must be %s / %d" % (TEST_LABEL, TEST_PORT))


# ---------- launchd and port observation ----------

PROBE_TIMEOUT = 20


def label_state(label, uid=None, run=subprocess.run, timeout=PROBE_TIMEOUT):
    """'absent' only when launchctl explicitly says the service does not exist; else 'present' or a refusal."""
    uid = os.getuid() if uid is None else uid
    try:
        done = run(["/bin/launchctl", "print", "gui/%d/%s" % (uid, label)], capture_output=True, text=True,
                   timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Unobservable("launchctl print failed: " + type(exc).__name__)
    text = (done.stdout or "") + (done.stderr or "")
    if done.returncode == 0 and (label + " = {") in text.replace("gui/%d/" % uid, ""):
        return "present", text
    if done.returncode == 113 and "Could not find service" in text:
        return "absent", text
    raise Unobservable("launchctl print: unexpected answer (rc=%d)" % done.returncode)


PROBE_ADDRESSES = ("127.0.0.1", "0.0.0.0")


def port_state(port, bind=None):
    """'free' when nothing listens on or holds 127.0.0.1:port right now, 'busy' on EADDRINUSE, otherwise a refusal.

    The trial binds set SO_REUSEADDR and never SO_REUSEPORT. On macOS/BSD, SO_REUSEADDR lets a bind ignore
    the connections a stopped server leaves in TIME_WAIT (about 30 s, during which a plain bind answers
    EADDRINUSE although nothing listens), but still refuses an address that another socket holds, bound or
    listening, whatever the options of that socket. It also allows a specific address next to a wildcard
    one, so the wildcard is tried too: a listener on 0.0.0.0 or on a dual-stack [::] is "busy" as before.
    LIMIT, not detected by a bind: a connection still open on the port without any listener (the same kernel
    state as TIME_WAIT for a bind), held by a process outside or older than the run. The launchd label check
    and the chain's process sweep only cover the identifiable processes of the run. Nothing is ever listened
    on or connected to; the wildcard bind is closed at once.
    """
    def default_bind(number):
        for address in PROBE_ADDRESSES:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((address, number))

    try:
        (bind or default_bind)(int(port))
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            return "busy"
        raise Unobservable("port probe failed: errno %s" % exc.errno)
    return "free"


def service_state(label, port, **kw):
    state, _ = label_state(label, uid=kw.get("uid"), run=kw.get("run", subprocess.run),
                           timeout=kw.get("timeout", PROBE_TIMEOUT))
    return {"label": label, "port": int(port), "label_state": state, "port_state": port_state(port, kw.get("bind")),
            "label_absent": state == "absent"}


def wait_absent(label, port, prefix, timeout, interval, *, clock=time.monotonic, sleep=time.sleep,
                observe=service_state, write=None):
    """Bounded wait after a stop of the test service: 0 only when an observation COMPLETED BEFORE the deadline
    says label absent and port free; 1 when the deadline is reached first; 3 when an observation is impossible.

    The deadline is on a monotonic clock and is strict: every pause and every probe is cut to the time left,
    no probe starts at or after it, and an observation that ends at or after it never counts, favourable or
    not. A failure at the deadline is final: nothing is observed again here. Every observation is kept
    (<prefix>.waitN.json) with a summary (<prefix>.wait.json). This never decides alone: the caller still
    makes its own final observation.
    """
    write = write or out
    start = clock()
    deadline = start + float(timeout)
    count, outcome, error = 0, "deadline", None
    while True:
        left = deadline - clock()
        if left <= 0:
            break
        sleep(min(float(interval), left))
        left = deadline - clock()
        if left <= 0:
            break
        count += 1
        try:
            row = observe(label, port, timeout=min(PROBE_TIMEOUT, left))
        except Unobservable as exc:
            outcome, error = "unobservable", str(exc)
            break
        ended = clock()
        in_time = ended < deadline
        write(dict(row, elapsed_seconds=round(ended - start, 3), before_deadline=in_time),
              "%s.wait%d.json" % (prefix, count))
        if not in_time:
            break
        if row["label_state"] == "absent" and row["port_state"] == "free":
            outcome = "absent"
            break
    write({"label": label, "port": int(port), "outcome": outcome, "observations": count, "error": error,
           "timeout_seconds": float(timeout), "interval_seconds": float(interval), "clock": "monotonic",
           "elapsed_seconds": round(clock() - start, 3)}, "%s.wait.json" % prefix)
    return {"absent": 0, "deadline": 1, "unobservable": EXIT_UNOBSERVABLE}[outcome]


def parse_print(text):
    """pid, state, program and plist path from ``launchctl print``."""
    def field(name):
        found = re.search(r"^\s*%s = (.+)$" % re.escape(name), text, flags=re.M)
        return found.group(1).strip() if found else None

    pid = field("pid")
    return {"pid": int(pid) if pid and pid.isdigit() else None, "state": field("state"), "program": field("program"),
            "path": field("path")}


def listener_pids(port, run=subprocess.run):
    done = run(["/usr/sbin/lsof", "-nP", "-iTCP:%d" % int(port), "-sTCP:LISTEN", "-Fp"], capture_output=True, text=True,
               timeout=20)
    if done.returncode not in (0, 1) or (done.returncode == 1 and (done.stderr or "").strip()):
        raise Unobservable("lsof failed")
    return sorted({int(line[1:]) for line in (done.stdout or "").splitlines() if line.startswith("p") and line[1:].isdigit()})


def attest_service(label, port, home, venv, profile, *, uid=None, run=subprocess.run, fetch=None):
    """G7: the loaded test service belongs to THIS run: plist below the disposable HOME, program = run venv."""
    home, venv = Path(home), Path(venv)
    state, text = label_state(label, uid=uid, run=run)
    row = {"label": label, "port": int(port), "present": state == "present", "belongs_to_run": False}
    if state != "present":
        return row
    info = parse_print(text)
    plist = home / "Library/LaunchAgents" / (label + ".plist")
    refuse_forbidden(plist)
    declared = plistlib.loads(plist.read_bytes()) if plist.is_file() and not plist.is_symlink() else {}
    arguments = declared.get("ProgramArguments") or [""]
    environment = declared.get("EnvironmentVariables") or {}
    row.update(
        pid=info["pid"], launchd_state=info["state"],
        plist_under_home=bool(declared) and info["path"] == str(plist) and declared.get("Label") == label,
        program_is_run_venv=str(arguments[0]).startswith(str(venv) + "/bin/")
        and (info["program"] is None or str(info["program"]).startswith(str(venv) + "/bin/")),
        nice_in_plist=declared.get("Nice"),
        declared_profile=environment.get("MEMORY_V4_RETRIEVAL_PROFILE"),
    )
    pids = listener_pids(port, run=run)
    row["listener_is_service_pid"] = info["pid"] is not None and pids == [info["pid"]]
    token_file = environment.get("MEMORY_V4_TOKEN_FILE", str(home / ".config/memory-os/http.token"))
    token_inside = str(Path(token_file).resolve()).startswith(str(home.resolve()) + os.sep)
    ready = {}
    if token_inside and row["listener_is_service_pid"]:
        ready = (fetch or fetch_ready)(port, token_file)
    capabilities = ready.get("capabilities") or {}
    row.update(ready=ready.get("ready") is True, version=ready.get("version"), commit=ready.get("commit"),
               retrieval_profile=capabilities.get("retrieval_profile"),
               store_backend=(capabilities.get("store") or {}).get("backend"))
    if info["pid"] is not None:
        opened = run(["/usr/sbin/lsof", "-p", str(info["pid"])], capture_output=True, text=True, timeout=30)
        if opened.returncode != 0:
            raise Unobservable("lsof -p failed")
        row["ml_libraries_open"] = sum(1 for line in opened.stdout.splitlines() if ML_LIBRARIES.search(line))
    row["belongs_to_run"] = bool(row["plist_under_home"] and row["program_is_run_venv"] and token_inside
                                 and row["listener_is_service_pid"])
    return row


def fetch_ready(port, token_file):
    """Authenticated /ready of the TEST service (disposable token, never printed)."""
    token = read_text(token_file).strip()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request("http://127.0.0.1:%d/ready" % int(port), headers={"Authorization": "Bearer " + token})
    with opener.open(request, timeout=10) as response:
        return json.loads(response.read())


# ---------- inventories with content hashes ----------

def inventory(root):
    """Every entry below ``root``: type, mode, size AND content SHA-256 (names/sizes/dates are not enough)."""
    rows = {}
    for folder, names, files in guarded_walk(root):  # links to folders are recorded, never followed
        for name in sorted(names + files):
            path = Path(folder) / name
            rel = str(path.relative_to(os.path.abspath(os.fspath(root))))
            info = path.lstat()
            if path.is_symlink():
                rows[rel] = {"type": "link", "target": os.readlink(path)}
            elif path.is_dir():
                rows[rel] = {"type": "dir", "mode": info.st_mode & 0o7777}
            elif path.is_file():
                rows[rel] = {"type": "file", "mode": info.st_mode & 0o7777, "bytes": info.st_size,
                             "sha256": sha256_file(path)}
            else:
                rows[rel] = {"type": "other"}  # sockets, fifos: recorded, never read
    return rows


def inventory_digest(rows):
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


def diff(before, after):
    return {"added": sorted(set(after) - set(before)), "removed": sorted(set(before) - set(after)),
            "changed": sorted(p for p in set(before) & set(after) if before[p] != after[p])}


# ---------- installed runtime against the locks ----------

def canonical(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def constraints_check(constraints_text, freeze_text, profile, variant="autonomous"):
    """Installed versions against the locks. Autonomous: no package outside the list (bootstrap apart).

    ``.pth`` variant (decision of Romain, reduced coverage): the reused environment may expose other
    packages; they are listed (``unpinned``), every lock must still be present at its exact version.
    """
    pins = {}
    for raw in constraints_text.splitlines():
        found = re.fullmatch(r"([A-Za-z0-9_.\-]+)==([^\s;]+)", raw.split("#")[0].strip())
        if found:
            pins[canonical(found.group(1))] = found.group(2)
    out = {"profile": profile, "locks": len(pins), "installed": 0, "pinned_and_equal": 0, "unpinned": [],
           "mismatch": [], "ml_packages": [], "bootstrap": [], "missing": []}
    seen = set()
    for raw in freeze_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, _, version = (line.split(" @ ")[0], "", "") if " @ " in line else line.partition("==")
        key = canonical(name)
        if key == "memory-os-ai":
            continue
        if key in BOOTSTRAP:
            out["bootstrap"].append(key)
            continue
        seen.add(key)
        out["installed"] += 1
        if profile == "lexical" and key in ML_PACKAGES:
            out["ml_packages"].append(name)
        if key not in pins:
            out["unpinned"].append(name)
        elif pins[key] != version:
            out["mismatch"].append("%s %s != %s" % (name, version, pins[key]))
        else:
            out["pinned_and_equal"] += 1
    out["missing"] = sorted(set(pins) - seen)
    out["variant"] = variant
    outside = out["unpinned"] if variant == "autonomous" else []
    out["ok"] = not (outside or out["mismatch"] or out["ml_packages"] or out["missing"])
    return out


def pip_distributions(report):
    """Name, version, URL and SHA-256 of every wheel pip installed (from ``pip install --report``)."""
    rows, wheels_only = [], True
    for item in report.get("install", []):
        info = item.get("download_info", {})
        url = info.get("url", "")
        name = item["metadata"]["name"]
        if canonical(name) == "memory-os-ai":
            continue
        wheels_only = wheels_only and url.endswith(".whl")
        rows.append({"name": name, "version": item["metadata"]["version"], "url": url,
                     "sha256": info.get("archive_info", {}).get("hashes", {}).get("sha256")})
    return sorted(rows, key=lambda r: r["name"].lower()), wheels_only


def tree_bytes(root):
    total = 0
    for folder, _, files in guarded_walk(root):
        for name in files:
            path = Path(folder) / name
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
    return total


# ---------- resources ----------

def verify_granite(cache, catalog):
    """Pre-placed model checked file by file against provision_v1.json (size and SHA-256)."""
    cache = refuse_forbidden(cache)
    folder = refuse_forbidden(cache / "snapshots")  # resolved before it is listed
    snapshots = sorted(folder.iterdir()) if folder.is_dir() else []
    rows, ok = [], len(snapshots) == 1
    for record in catalog.get("files", []):
        path = refuse_forbidden(snapshots[0] / record["path"]) if snapshots else None  # link target included
        good = bool(path and path.is_file() and path.stat().st_size == record["bytes"]
                    and sha256_file(path) == record["sha256"])
        rows.append({"path": record["path"], "ok": good})
        ok = ok and good
    return {"ok": ok and bool(rows), "files": rows, "bytes": sum(r["bytes"] for r in catalog.get("files", [])),
            "revision": snapshots[0].name if len(snapshots) == 1 else None}


def hf_cache_files_present(model_dir):
    """Local Hugging Face model cache PRESENT, with non-empty files: nothing more is claimed.

    refs/main names one snapshot; every entry of it reaches a non-empty blob inside ``blobs/``; the
    configuration, tokenizer and weights files exist. This proves neither that the files are valid nor
    that the model loads: the cached MiniLM test of G4 remains the real proof. Read-only, no download.
    """
    model_dir = refuse_forbidden(model_dir)
    problems = []
    ref = refuse_forbidden(model_dir / "refs/main")  # a link here is judged by its target BEFORE the read
    revision = ref.read_text().strip() if ref.is_file() else ""
    snapshot = model_dir / "snapshots" / revision if re.fullmatch(r"[0-9a-f]{40}", revision) else None
    blobs = (model_dir / "blobs").resolve()
    files = {}
    if snapshot is None or not refuse_forbidden(snapshot).is_dir():
        problems.append("refs/main does not name an existing snapshot")
    else:
        for folder, _, names in guarded_walk(snapshot):
            for name in names:
                path = Path(folder) / name
                rel = str(path.relative_to(snapshot))
                target = refuse_forbidden(path.resolve())
                if path.is_symlink() and blobs not in target.parents:
                    problems.append("snapshot entry outside blobs/: " + rel)
                elif not target.is_file() or target.stat().st_size == 0:
                    problems.append("snapshot entry without a non-empty blob: " + rel)
                else:
                    files[rel] = target.stat().st_size
        for required in ("config.json", "modules.json", "tokenizer.json", "tokenizer_config.json"):
            if required not in files:
                problems.append("missing " + required)
        if not any(name in files for name in ("model.safetensors", "pytorch_model.bin")):
            problems.append("missing model weights")
    return {"ok": not problems, "label": "cache présent, fichiers non vides (ne prouve pas le chargement : G4)",
            "revision": revision or None, "files": len(files), "problems": problems}


# ---------- production: read only ----------

MCP_PROJECTION_VERSION = 1
# Claude Code MCP perimeter (QUALIFY_4_1_0 §2): server definitions and their enable / disable / approval
# settings, global and per project. Versioned selection; the rest of the file is NOT compared.
MCP_KEYS = ("mcpServers", "disabledMcpServers", "enabledMcpjsonServers", "disabledMcpjsonServers",
            "enableAllProjectMcpServers")


def mcp_projection(document):
    """Full JSON path, presence and value of every MCP key, at the top level and in every project."""
    if not isinstance(document, dict):
        raise Unobservable("client configuration is not a JSON object")
    rows = []

    def take(node, trail):
        for key in MCP_KEYS:  # absent and present-but-empty are different observations; {} is not []
            rows.append({"path": trail + [key], "present": key in node, "value": node.get(key)})

    take(document, [])
    projects = document.get("projects")
    if projects is not None:
        if not isinstance(projects, dict) or not all(isinstance(v, dict) for v in projects.values()):
            raise Unobservable("client configuration: projects is not an object of objects")
        for name in sorted(projects):
            take(projects[name], ["projects", name])
    return rows


def lstat_state(path):
    """Explicit observation of one path: its ``lstat`` result, or None when its absence is ESTABLISHED.

    Only "no such entry" (ENOENT) and "a parent is not a folder" (ENOTDIR) establish absence. A permission
    error, or any other failure, means the state could NOT be observed: that is a refusal, never "absent"
    (``os.path.lexists`` would silently turn such an error into False).
    """
    try:
        return os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as exc:
        raise Unobservable("protected path cannot be observed: %s (%s, errno %s)"
                           % (path, type(exc).__name__, exc.errno))


def observe(path, volatile=False, _seen=()):
    """One protected path: absent / file / dir / link, with its CONTENT fingerprint. Unreadable = refusal.

    A link is never fingerprinted by its target path alone: its target is checked (allowed) and its
    content observed too. A folder is walked entirely; any listing error is blocking.
    """
    path = os.fspath(refuse_forbidden(path))
    info = lstat_state(path)
    if info is None:
        return {"state": "absent"}
    try:
        if stat.S_ISLNK(info.st_mode):
            resolved = os.path.realpath(path)
            if resolved in _seen:
                raise Unobservable("link cycle at " + path)
            return {"state": "link", "target": os.readlink(path),
                    "content": observe(resolved, volatile, _seen + (resolved,))}
        if stat.S_ISDIR(info.st_mode):
            entries = {}
            for folder, names, files in guarded_walk(path):
                for name in names:
                    full = os.path.join(folder, name)
                    entries[os.path.relpath(full, path)] = (observe(full, False, _seen) if os.path.islink(full)
                                                            else {"state": "dir"})
                for name in files:
                    full = os.path.join(folder, name)
                    entries[os.path.relpath(full, path)] = observe(full, False, _seen)
            return {"state": "dir", "entries": entries}
        if stat.S_ISREG(info.st_mode):
            if volatile:
                rows = mcp_projection(json.loads(read_text(path)))  # in memory only; the digest alone is kept
                return {"state": "file", "scope": "MCP perimeter only", "mcp_projection_version": MCP_PROJECTION_VERSION,
                        "mcp_sha256": hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()}
            return {"state": "file", "sha256": sha256_file(path)}
        raise Unobservable("protected path is neither a file nor a folder: " + path)
    except (OSError, ValueError) as exc:
        raise Unobservable("protected path unreadable: %s (%s)" % (path, type(exc).__name__))


def production_snapshot(real_home, uid=None, run=subprocess.run):
    """Content fingerprints and observation states of what must never change (read only).

    Every client configuration of the catalogue, EVERY entry of the real LaunchAgents (content, not
    names; links with their target's content), the real ``~/.config/memory-os`` tree, and the
    production pid. Absent, file, folder and link are distinct states; unreadable is a refusal.
    """
    real_home = Path(real_home)
    clients = {rel: observe(real_home / rel, volatile=rel in VOLATILE_JSON_CONFIGS) for rel in REAL_CLIENT_CONFIGS}
    agents = observe(real_home / "Library/LaunchAgents")
    trees = {rel: observe(real_home / rel) for rel in PROTECTED_TREES}
    state, text = label_state(PRODUCTION_LABEL, uid=uid, run=run)
    row = {"client_configs": clients, "launch_agents": agents, "protected_trees": trees,
           "production_plist": (agents.get("entries") or {}).get(PRODUCTION_LABEL + ".plist"),
           "production_label_state": state, "production_pid": parse_print(text)["pid"] if state == "present" else None}
    row["sha256"] = hashlib.sha256(json.dumps(
        {k: row[k] for k in ("client_configs", "launch_agents", "protected_trees")}, sort_keys=True).encode()).hexdigest()
    return row


def production_unchanged(before, after):
    """The exit booleans, over the whole protected perimeter. Something never observed is not "unchanged"."""
    def observed(row, states):
        return isinstance(row, dict) and row.get("state") in states

    plist = before.get("production_plist")
    return {
        "production_plist_unchanged": observed(plist, ("file", "link")) and plist == after.get("production_plist"),
        "production_pid_unchanged": before["production_pid"] is not None
        and before["production_pid"] == after["production_pid"],
        "real_launch_agents_unchanged": observed(before.get("launch_agents"), ("dir",))
        and before["launch_agents"] == after.get("launch_agents"),
        "real_client_configs_unchanged": set(before["client_configs"]) == set(REAL_CLIENT_CONFIGS)
        and all(observed(row, ("absent", "file", "link")) for row in before["client_configs"].values())
        and before["client_configs"] == after.get("client_configs"),
        "real_memory_os_config_unchanged": set(before["protected_trees"]) == set(PROTECTED_TREES)
        and all(observed(row, ("dir",)) for row in before["protected_trees"].values())
        and before["protected_trees"] == after.get("protected_trees"),
    }


# ---------- client entries and user data (G8) ----------

def store_logical(store_dir):
    """Identity and logical content of a SQLite store, read only; survives WAL checkpoint/close."""
    db = refuse_forbidden(Path(store_dir) / "store.sqlite3")
    connection = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=30)
    try:
        digest = hashlib.sha256()
        tables = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "AND sql NOT LIKE 'CREATE VIRTUAL%' ORDER BY name")]
        counts, table_digests, receipts, counter_rows = {}, {}, None, []
        for table in tables:
            digest.update(table.encode())
            counts[table] = 0
            own = hashlib.sha256()
            cursor = connection.execute('SELECT * FROM "%s" ORDER BY rowid' % table.replace('"', '""'))
            names = [column[0] for column in cursor.description]
            if table == RECEIPTS_TABLE:
                receipts = []
            for row in cursor:
                raw = repr(row).encode()
                digest.update(raw)
                own.update(raw)
                counts[table] += 1
                if table == RECEIPTS_TABLE:
                    receipts.append(receipt_row(dict(zip(names, row)), raw))
                elif table == COUNTER_TABLE:
                    counter_rows.append(dict(zip(names, row)))
            table_digests[table] = own.hexdigest()
        identity = None
        for row in connection.execute("SELECT * FROM store_metadata"):
            if row and row[0] == "metadata":
                identity = json.loads(row[1]).get("store_instance_id")
        counter = counter_rows[0].get("value") if len(counter_rows) == 1 and counter_rows[0].get("id") == 1 else None
        # ``logical_sha256`` is the raw digest of every row of every table (unchanged definition). The
        # per-table digests and the receipt lines let ``store_compare`` tell WHICH rows differ.
        return {"store_instance_id": identity, "logical_sha256": digest.hexdigest(), "tables": counts,
                "user_version": connection.execute("PRAGMA user_version").fetchone()[0],
                "table_sha256": table_digests, "request_receipts": receipts, "receipt_counter": counter,
                "product_receipts": product_receipts(connection)}
    finally:
        connection.close()


RECEIPTS_TABLE, COUNTER_TABLE = "request_receipts", "receipt_counter"
ENGINE_SCOPE = "__engine_internal__"  # scope of the engine's own implicit receipts (plain text, not a JSON list)
# Exactly the keys of the implicit receipt the engine writes for its own flush: no result, no attestation
# (``raw_args_sha256`` belongs to attested tool calls only) -- memory_os_v4/sqlite_engine.py, ``complete``.
ENGINE_FLUSH_KEYS = ["committed_at", "explicit", "fingerprint", "operation", "request_id", "scope", "sequence", "state"]
# ``fingerprint("flush", {"args": [], "kwargs": {}})`` of the product (sqlite_engine.py ``fingerprint``, canonical
# JSON of sqlite_store.py ``_json``): a flush takes no argument, so this value is the same in every store.
ENGINE_FLUSH_FINGERPRINT = hashlib.sha256(
    b'{"operation":"flush","payload":{"args":[],"kwargs":{}}}').hexdigest()
IMPLICIT_REQUEST_ID = re.compile(r"[0-9a-f]{32}\Z")  # ``uuid.uuid4().hex`` (memory.py, ``_sql_mutation``)
RECORD_FIELDS = ("scope", "request_id", "sequence", "operation", "explicit", "state", "fingerprint", "committed_at")


def _unique_keys(pairs):
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate key")
    return dict(pairs)


def canonical_json(value):
    """``_json`` of the product (sqlite_store.py): the only text the engine writes for a record."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def receipt_row(row, raw):
    """One ``request_receipts`` line: its columns, and its record READ AS JSON (never searched as text).

    The VALUES of the fields the comparison judges are kept (never the result of a request). A record with
    a duplicate key, or that is not the canonical text the engine writes, is flagged: its shape is not exact.
    """
    text = row.get("record_json")
    try:
        record = json.loads(text, object_pairs_hook=_unique_keys)
        canonical = isinstance(record, dict) and canonical_json(record) == text
    except (TypeError, ValueError, RecursionError):
        record, canonical = None, False
    record = record if isinstance(record, dict) else {}
    return {"sequence": row.get("sequence"), "scope": row.get("scope"), "request_id": row.get("request_id"),
            "record_sha256": row.get("record_sha256"), "row_sha256": hashlib.sha256(raw).hexdigest(),
            "record_sha256_valid": isinstance(text, str)
            and hashlib.sha256(text.encode()).hexdigest() == row.get("record_sha256"),
            "record_canonical": canonical,
            "record": {key: record.get(key) for key in RECORD_FIELDS},
            "record_keys": sorted(record)}


def product_receipts(connection):
    """End to end: the product's OWN reader of receipts, the one every reopening runs, on this store (read only).

    ``SQLiteRequests.load`` only SELECTs on the connection it is given. Not importable (``/usr/bin/python3``,
    no product installed): ``checked`` is false and nothing is claimed.
    """
    try:
        from memory_os_v4.sqlite_engine import SQLiteRequests
    except Exception as exc:  # absent or unusable product: an observation not made, said as such
        return {"checked": False, "valid": None, "detail": type(exc).__name__}
    reader = SQLiteRequests.__new__(SQLiteRequests)

    class Store:
        _connection, db_path, _poisoned = connection, None, False

    reader.store = Store()
    try:
        reader.load()
    except Exception as exc:
        return {"checked": True, "valid": False, "detail": "%s: %s" % (type(exc).__name__, exc)}
    return {"checked": True, "valid": True, "detail": "%d receipts read" % len(reader.records)}


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _integer(value):
    return type(value) is int


def engine_flush_receipts(before, after):
    """The ONLY store difference admitted across the stop of the service: ``(rows, None)`` or ``([], reason)``.

    On a clean stop the engine flushes and writes ONE implicit receipt of its own (``flush``, scope
    ``__engine_internal__``, ``explicit: false``), which also advances ``receipt_counter``. Admitted: such
    lines APPENDED to ``request_receipts`` (sequences right after the counter of before, above every earlier
    sequence), every earlier line unchanged and in the same order, the counter advanced by exactly that
    number, every other table identical row for row. Anything else is a data modification.
    """
    for side in (before, after):
        if not (isinstance(side.get("tables"), dict) and isinstance(side.get("table_sha256"), dict)
                and isinstance(side.get("request_receipts"), list) and _integer(side.get("receipt_counter"))
                and set(side["tables"]) == set(side["table_sha256"])
                and side["tables"].get(RECEIPTS_TABLE) == len(side["request_receipts"])
                and side["tables"].get(COUNTER_TABLE) == 1
                and all(isinstance(row, dict) and _integer(row.get("sequence")) for row in side["request_receipts"])):
            return [], "per-table evidence missing or inconsistent"
    if set(before["tables"]) != set(after["tables"]):
        return [], "set of tables changed"
    for table in sorted(before["tables"]):
        if table not in (RECEIPTS_TABLE, COUNTER_TABLE) and (
                before["tables"][table] != after["tables"][table]
                or before["table_sha256"][table] != after["table_sha256"][table]):
            return [], "table changed: " + table
    old, new = before["request_receipts"], after["request_receipts"]
    if new[:len(old)] != old:
        return [], "an earlier receipt was changed, removed or reordered"
    added = new[len(old):]
    if not added:
        return [], "no appended receipt explains the difference"
    reader = after.get("product_receipts")
    if not (isinstance(reader, dict) and reader.get("checked") is True and reader.get("valid") is True):
        return [], "the receipts of the stopped store were not accepted by the product's own reader"
    highest = max([row["sequence"] for row in old] + [before["receipt_counter"]])
    # ``committed_at`` is ``time.time()`` at the commit (not monotonic): requiring it never before the last receipt
    # of before is a qualification requirement, stricter than the product's own reader.
    latest = max([(row.get("record") or {}).get("committed_at") for row in old
                  if _number((row.get("record") or {}).get("committed_at"))] + [0])
    rows = []
    for offset, row in enumerate(added, 1):
        record = row.get("record") or {}
        if not (record.get("fingerprint") == ENGINE_FLUSH_FINGERPRINT and _number(record.get("committed_at"))
                and record["committed_at"] >= latest and row.get("record_canonical") is True
                and isinstance(row.get("request_id"), str) and IMPLICIT_REQUEST_ID.match(row["request_id"])):
            return [], "appended receipt %r is not an internal engine flush" % (row.get("sequence"),)
        latest = record["committed_at"]
        if not (row["sequence"] == before["receipt_counter"] + offset and row["sequence"] > highest
                and row.get("scope") == ENGINE_SCOPE and record.get("scope") == ENGINE_SCOPE
                and record.get("operation") == "flush" and record.get("explicit") is False
                and record.get("state") == "complete" and _integer(record.get("sequence"))
                and record["sequence"] == row["sequence"]
                and isinstance(row.get("request_id"), str) and row["request_id"]
                and record.get("request_id") == row["request_id"]
                and row.get("record_keys") == ENGINE_FLUSH_KEYS and row.get("record_sha256_valid") is True
                and isinstance(row.get("record_sha256"), str)):
            return [], "appended receipt %r is not an internal engine flush" % (row.get("sequence"),)
        rows.append({"sequence": row["sequence"], "request_id": row["request_id"], "operation": "flush",
                     "record_sha256": row["record_sha256"]})
    if after["receipt_counter"] != before["receipt_counter"] + len(added):
        return [], "receipt counter does not match the appended receipts"
    return rows, None


def store_compare(before, after):
    """Logical comparison of the store around uninstall; the raw digest verdict is kept apart (transparency).

    ``store_logical_unchanged`` is true when the raw digests are equal, or when the only difference is the
    engine's own flush receipt(s) of the stop (``engine_flush_receipts``), listed in
    ``store_tolerated_engine_receipts``. ``user_version`` must be equal in both cases.
    """
    row = {"store_logical_unchanged": False, "store_raw_hash_identical": False,
           "store_tolerated_engine_receipts": [], "store_comparison_refusal": None}
    if not (isinstance(before, dict) and isinstance(after, dict)):
        return {**row, "store_comparison_refusal": "store observation missing"}
    row["store_raw_hash_identical"] = bool(before.get("logical_sha256")) \
        and before.get("logical_sha256") == after.get("logical_sha256") \
        and isinstance(before.get("tables"), dict) and before.get("tables") == after.get("tables")
    if before.get("user_version") != after.get("user_version"):
        return {**row, "store_comparison_refusal": "user_version changed"}
    if row["store_raw_hash_identical"]:
        return {**row, "store_logical_unchanged": True}
    tolerated, reason = engine_flush_receipts(before, after)
    return {**row, "store_logical_unchanged": reason is None, "store_tolerated_engine_receipts": tolerated,
            "store_comparison_refusal": reason}


IMMUTABLE = {
    "token": (".config/memory-os/http.token",),
    "resources": (".config/memory-os/cl100k_base.tiktoken", ".cache/huggingface/"),
    "backups": ("memory-os-backups/",),
    "key": (".config/memory-os/backup.key",),
}
STORE_PREFIX = ".local/share/memory-os/store/"
STATE_PREFIX = ".local/state/memory-os/"
QUICKSTART_PREFIX = ".local/state/memory-os/quickstart/"
UNINSTALL_PREFIX = ".local/state/memory-os/uninstall/"
WITNESS_BACKUP = "memory-os-backups/qualify-410-witness.mosb"


def kind_of(rel):
    for kind, prefixes in IMMUTABLE.items():
        if any(rel == p or (p.endswith("/") and rel.startswith(p)) for p in prefixes):
            return kind
    return None


def revocation_only(before_text, after_text, stamp):
    """The ONLY journal rewrite uninstall may do: records gaining ``revoked: true, revoked_by: <its stamp>``.

    Field by field: same document, same records in the same order, nothing else added, removed or changed.
    """
    try:
        before, after = json.loads(before_text), json.loads(after_text)
    except (TypeError, ValueError):
        return False
    if not (isinstance(before, dict) and isinstance(after, dict) and isinstance(before.get("records"), list)
            and isinstance(after.get("records"), list) and len(before["records"]) == len(after["records"])):
        return False
    if {k: v for k, v in before.items() if k != "records"} != {k: v for k, v in after.items() if k != "records"}:
        return False
    revoked = 0
    for old, new in zip(before["records"], after["records"]):
        if old == new:
            continue
        if not (isinstance(old, dict) and isinstance(new, dict) and not old.get("revoked")
                and new.get("revoked") is True and new.get("revoked_by") == stamp and stamp
                and {k: v for k, v in new.items() if k not in ("revoked", "revoked_by")}
                == {k: v for k, v in old.items() if k not in ("revoked", "revoked_by")}):
            return False
        revoked += 1
    return revoked > 0


def data_conservation(before, after, store_before, store_after, label, created_clients, *, stamp, text_before, text_after):
    """Only the expected, inventoried mutations of the uninstall protocol are admitted.

    ``text_before`` / ``text_after`` give the content of a state file (copies taken by the script) so a
    journal change is checked field by field. Anything not recognised refuses the gate.
    """
    delta = diff(before, after)
    plist = "Library/LaunchAgents/%s.plist" % label
    expected, unexpected = [], []
    immutable_changed = []

    def note(target, rel, change, why):
        target.append({"path": rel, "change": change, "why": why})

    for change, paths in delta.items():
        for rel in paths:
            kind = kind_of(rel)
            old, new = before.get(rel) or {}, after.get(rel) or {}
            is_dir = (new or old).get("type") == "dir"
            is_log = rel.endswith(".log") or ("/" + rel).startswith("/Library/Logs/")
            if kind:
                immutable_changed.append(rel)
                note(unexpected, rel, change, "immutable " + kind)
            elif change == "removed" and rel == plist:
                note(expected, rel, change, "plist moved out of its active place")
            elif change == "added" and rel.startswith(UNINSTALL_PREFIX):
                note(expected, rel, change, "new uninstall journal")
            elif is_dir and change in ("changed", "added") and (change == "changed" or rel.startswith(STATE_PREFIX)):
                note(expected, rel, change, "directory metadata")
            elif change == "changed" and rel.startswith(QUICKSTART_PREFIX) and rel.endswith("/journal.json") \
                    and revocation_only(text_before(rel), text_after(rel), stamp):
                note(expected, rel, change, "proof revocation, field by field")
            elif rel in created_clients and change in ("changed", "removed"):
                note(expected, rel, change, "our client entry removed (structure checked apart)")
            elif rel.startswith(STORE_PREFIX) and rel[len(STORE_PREFIX):] in ("store.sqlite3", "store.sqlite3-wal",
                                                                             "store.sqlite3-shm"):
                note(expected, rel, change, "SQLite close transition (logical content compared)")
            elif is_log and change == "changed" and new.get("bytes", -1) >= old.get("bytes", 0):
                note(expected, rel, change, "log still present, not shortened")
            elif is_log and change == "added":
                note(expected, rel, change, "new log")
            else:
                note(unexpected, rel, change, "not part of the uninstall protocol")
    kinds = {kind: sorted(rel for rel in before if kind_of(rel) == kind and before[rel].get("type") == "file"
                          and before[rel].get("bytes", 0) > 0) for kind in IMMUTABLE}
    journals_before = [rel for rel in before if rel.startswith(QUICKSTART_PREFIX)]
    logs_before = [rel for rel in before if rel.endswith(".log") and before[rel].get("type") == "file"]
    original = (before.get(plist) or {}).get("sha256")
    copies = [rel for rel, row in after.items() if rel.startswith(UNINSTALL_PREFIX) and row.get("type") == "file"
              and original and row.get("sha256") == original]
    return {
        "immutable_kinds": sorted(IMMUTABLE),
        "immutable_files": {kind: len(paths) for kind, paths in kinds.items()},
        # Witnesses: every kind has at least one non-empty file, so "unchanged" is a real observation.
        "immutable_witnessed": all(kinds[kind] for kind in IMMUTABLE),
        "immutable_unchanged": not immutable_changed and all(kinds[kind] for kind in IMMUTABLE),
        "store_identity_unchanged": bool(store_before["store_instance_id"])
        and store_before["store_instance_id"] == store_after["store_instance_id"],
        # Tolerant verdict (internal flush receipts of the stop only); the raw digest verdict stays visible,
        # and both observations are kept so the release rules recompute the comparison themselves.
        **store_compare(store_before, store_after),
        "store_before": store_before, "store_after": store_after,
        "journals_present": bool(journals_before) and all(rel in after for rel in journals_before)
        and all(rel in after for rel in logs_before),
        "plist_removed_from_active": plist in before and plist not in after,
        "plist_journal_copy_kept": bool(copies),  # a copy with exactly the bytes of the original plist
        "plist_journal_copies": copies,
        "expected_mutations": expected,
        "unexpected_changes": unexpected,
    }


# ---------- M1 ----------

def m1(timeline, install_json, profile, variant, pip_cache_bytes):
    """T0 = launch of ``python3.11 -m venv``; T1 = return of ``quickstart --apply --demo`` with ok: true."""
    spans = {name: end - start for name, (start, end) in timeline["spans"].items()}
    t0, t1 = timeline["spans"]["venv"][0], timeline["spans"]["apply"][1]
    inside = {name: value for name, value in spans.items() if t0 <= timeline["spans"][name][0] and timeline["spans"][name][1] <= t1}
    pip_s = sum(v for k, v in inside.items() if k.startswith("pip_"))
    controls_s = sum(v for k, v in inside.items() if k.startswith("control_"))
    timing = install_json.get("timing") or {}
    steps_ms = timing.get("steps_ms") or {}
    total = t1 - t0
    deviations = ["non_blank_machine", "local_wheel"] + (["model_pre_placed"] if profile == "semantic" else [])
    return {
        "t0_epoch": t0, "t1_epoch": t1, "total_s": total, "pip_s": pip_s, "controls_s": controls_s,
        "net_s": total - controls_s, "spans_s": spans, "install_json_timing": timing,
        "outside_model_s": total - steps_ms.get("resources", 0) / 1000.0,
        # Not measured: pip reports no transferred byte count. The size of the run's own (fresh) pip cache
        # is reported under its real name; it includes index responses and is NOT a download measure.
        "pip_downloaded_bytes": None, "pip_downloaded_bytes_measured": False,
        "pip_cache_bytes": int(pip_cache_bytes),
        "pip_cache_bytes_nature": "size of the run's own pip cache (wheels and index responses); not transferred bytes",
        "quickstart_download_bytes": timing.get("download_bytes"),
        "protocol_deviations": deviations,
        "label": "reused-dependencies variant" if variant == "pth" else "first installation",
        "not_a_threshold": True,
    }


def timeline(text):
    """Lines ``name start end`` (epoch seconds) written by the shell -> spans; a name appears once."""
    spans = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        name, start, end = line.split()
        if name in spans or float(end) < float(start):
            raise ValueError("Bad timeline span: " + name)
        spans[name] = [float(start), float(end)]
    if "venv" not in spans or "apply" not in spans:
        raise ValueError("Timeline without T0 (venv) or T1 (apply)")
    return {"spans": spans}


# ---------- command line ----------

def out(value, path=None):
    text = json.dumps(value, indent=2, sort_keys=True) + "\n"
    if path:
        with open(refuse_forbidden(path), "x") as stream:  # evidence is never overwritten
            stream.write(text)
    else:
        sys.stdout.write(text)


def load(path):
    return json.loads(read_text(path))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("service-state")
    s.add_argument("--label", default=TEST_LABEL)
    s.add_argument("--port", type=int, default=TEST_PORT)
    s.add_argument("--expect", choices=("absent",), required=True)
    s.add_argument("--output")
    s = sub.add_parser("wait-absent")
    s.add_argument("--label", default=TEST_LABEL)
    s.add_argument("--port", type=int, default=TEST_PORT)
    s.add_argument("--timeout", type=float, required=True)
    s.add_argument("--interval", type=float, required=True)
    s.add_argument("--output-prefix", required=True)
    s = sub.add_parser("attest-service")
    for name in ("--home", "--venv", "--profile", "--output"):
        s.add_argument(name, required=True)
    s = sub.add_parser("inventory")
    s.add_argument("--root", required=True)
    s.add_argument("--output", required=True)
    s = sub.add_parser("same-inventory")
    s.add_argument("before")
    s.add_argument("after")
    s.add_argument("--output", required=True)
    s = sub.add_parser("constraints-check")
    for name in ("--constraints", "--freeze", "--profile", "--output"):
        s.add_argument(name, required=True)
    s.add_argument("--variant", choices=("autonomous", "pth"), default="autonomous")
    s = sub.add_parser("hf-cache")
    s.add_argument("--model-dir", required=True)
    s = sub.add_parser("verify-granite")
    for name in ("--cache", "--catalog", "--output"):
        s.add_argument(name, required=True)
    s = sub.add_parser("production-snapshot")
    s.add_argument("--real-home", required=True)
    s.add_argument("--output", required=True)
    s = sub.add_parser("refuse-forbidden")
    s.add_argument("paths", nargs="+")
    s = sub.add_parser("store-logical")
    s.add_argument("--store", required=True)
    s.add_argument("--output", required=True)
    s = sub.add_parser("client-files")
    s.add_argument("--home", required=True)
    s.add_argument("--output", required=True)
    s = sub.add_parser("tree-bytes")
    s.add_argument("--root", required=True)
    s.add_argument("--output", required=True)
    s = sub.add_parser("timeline")
    s.add_argument("--input", required=True)
    s.add_argument("--output", required=True)
    s = sub.add_parser("meta")
    for name in ("--profile", "--variant", "--source-commit", "--run-tag", "--version", "--wheel", "--wheel-sha256",
                 "--venv", "--output"):
        s.add_argument(name, required=True)
    s = sub.add_parser("assemble")
    s.add_argument("--proof-dir", required=True)
    args = parser.parse_args(argv)
    # One gate for every path received from outside, before any read or traversal.
    for name in ("root", "home", "venv", "cache", "catalog", "store", "proof_dir", "constraints", "freeze", "input",
                 "wheel", "before", "after", "real_home", "output", "output_prefix"):
        if getattr(args, name, None):
            refuse_forbidden(getattr(args, name))
    for value in getattr(args, "paths", []):
        refuse_forbidden(value)
    try:
        if args.command == "service-state":
            check_test_identity(args.label, args.port)
            row = service_state(args.label, args.port)
            out(row, args.output)
            return 0 if row["label_state"] == "absent" and row["port_state"] == "free" else 1
        if args.command == "wait-absent":
            check_test_identity(args.label, args.port)
            if not (0 < args.timeout <= 600 and 0 < args.interval <= args.timeout):
                raise SystemExit("REFUS: 0 < interval <= timeout <= 600")
            return wait_absent(args.label, args.port, args.output_prefix, args.timeout, args.interval)
        if args.command == "attest-service":
            row = attest_service(TEST_LABEL, TEST_PORT, args.home, args.venv, args.profile)
            out(row, args.output)
            return 0 if row["present"] and row["belongs_to_run"] else 1
        if args.command == "inventory":
            rows = inventory(args.root)
            out({"root": str(args.root), "entries": len(rows), "sha256": inventory_digest(rows), "rows": rows}, args.output)
            return 0
        if args.command == "same-inventory":
            a, b = load(args.before), load(args.after)
            same = a["rows"] == b["rows"]
            out({"inventory_identical": same, "inventory_hashes_content": True, "inventory_entries": a["entries"],
                 "inventory_before_sha256": a["sha256"], "inventory_after_sha256": b["sha256"],
                 "difference": diff(a["rows"], b["rows"])}, args.output)
            return 0 if same else 1
        if args.command == "constraints-check":
            if args.variant == "pth" and args.profile != "semantic":
                raise SystemExit("REFUS: the .pth variant exists for the semantic profile only")
            row = constraints_check(read_text(args.constraints), read_text(args.freeze), args.profile,
                                    args.variant)
            row["constraints_sha256"] = sha256_file(args.constraints)
            out(row, args.output)
            return 0 if row["ok"] else 1
        if args.command == "hf-cache":
            row = hf_cache_files_present(args.model_dir)
            out(row)
            return 0 if row["ok"] else 1
        if args.command == "verify-granite":
            row = verify_granite(args.cache, load(args.catalog))
            out(row, args.output)
            return 0 if row["ok"] else 1
        if args.command == "production-snapshot":
            out(production_snapshot(args.real_home), args.output)
            return 0
        if args.command == "refuse-forbidden":
            return 0  # every path was checked above, lexically then resolved
        if args.command == "store-logical":
            out(store_logical(args.store), args.output)
            return 0
        if args.command == "client-files":
            out(client_files(args.home), args.output)
            return 0
        if args.command == "tree-bytes":
            out({"root": args.root, "bytes": tree_bytes(args.root)}, args.output)
            return 0
        if args.command == "timeline":
            out(timeline(read_text(args.input)), args.output)
            return 0
        if args.command == "meta":
            if sha256_file(args.wheel) != args.wheel_sha256:
                raise SystemExit("REFUS: wheel SHA-256 differs")
            out({"profile": args.profile, "variant": args.variant, "source_commit": args.source_commit,
                 "run_tag": args.run_tag, "version": args.version, "venv": args.venv, "label": TEST_LABEL,
                 "port": TEST_PORT, "wheel": {"name": Path(args.wheel).name, "sha256": args.wheel_sha256}}, args.output)
            return 0
        if args.command == "assemble":
            from_dir = Path(args.proof_dir)
            report = assemble(from_dir)
            out(report, from_dir / "report.json")
            return 0 if report["ok"] else 1
    except Unobservable as exc:
        print("OBSERVATION IMPOSSIBLE: %s" % exc, file=sys.stderr)
        return EXIT_UNOBSERVABLE
    return 2


REQUIRED_EVIDENCE = (
    "meta.json", "constraints_check.json", "locations.json", "pip_check.json", "production_before.json",
    "production_after.json", "timeline.json", "pip_cache_bytes.json", "safety_net.json", "shell_verdict.json",
    *("install%s/%s" % (i, name) for i in ("1", "2") for name in (
        "pre_state.json", "post_state.json", "apply.json", "apply_rc.json", "install.json", "service.json",
        "doctor.json", "doctor_rc.json", "clients_after_apply.json")),
    "install1/preview.json", "install1/preview_inventory.json", "install1/undo.json", "install1/undo_state.json",
    "install1/clients_before_apply.json", "install1/clients_after_undo.json", "install1/client_files_after_undo.json",
    "install2/uninstall_preview.json", "install2/uninstall_preview_inventory.json", "install2/uninstall.json",
    "install2/uninstall_state.json", "install2/clients_after_uninstall.json", "install2/data_before.json",
    "install2/data_after.json", "install2/store_before.json", "install2/store_after.json", "install2/witness.json",
)


def doctor_summary(checks, returncode):
    areas = sorted({c.get("area") for c in checks if isinstance(c, dict)})
    return {"returncode": returncode, "checks": len(checks), "areas": areas,
            "fail": sum(1 for c in checks if c.get("status") == "FAIL"),
            "statuses": {c.get("area"): c.get("status") for c in checks if c.get("area") in REQUIRED_DOCTOR_AREAS},
            "missing_areas": sorted(set(REQUIRED_DOCTOR_AREAS) - set(areas))}


CLIENT_FORMATS = {"codex": "toml", "cursor": "json"}  # read with the parser of the format, never as text
NEUTRAL_MAX_BYTES = 4096  # a file created empty by an installation is tiny; a bigger one is never "neutral"
MISSING_CLIENT = {"entry_present": False, "file_sha256": None, "kind": None, "state": "missing-config"}


def neutral_content(data, fmt):
    """Is this client file EMPTY of any setting? Structured reading only; ``None`` = this Python cannot tell.

    TOML: the parsed document has no key and no table (blanks and comments only). JSON: the document is
    the object ``{}`` or ``{"mcpServers": {}}``. Anything else, unreadable bytes included, is not neutral.
    """
    try:
        text = data.decode("utf-8")
        if fmt == "json":
            document = json.loads(text, object_pairs_hook=_unique_keys)
            return type(document) is dict and (document == {} or (
                list(document) == ["mcpServers"] and type(document["mcpServers"]) is dict and not document["mcpServers"]))
        if fmt == "toml":
            try:
                import tomllib  # Python 3.11+: the run's venv and the release rules; absent from /usr/bin/python3
            except ImportError:
                return None
            return tomllib.loads(text) == {}
    except (ValueError, RecursionError):  # UnicodeDecodeError, JSONDecodeError and TOMLDecodeError are ValueErrors
        return False
    return False


def client_files(home):
    """The two requested client files as they are on disk: SHA-256, size, and whether their content is neutral.

    The content itself is kept (base64) only when it is neutral: such a file holds no setting at all.
    """
    rows = {}
    for client, rel in TEST_CLIENTS.items():
        path = refuse_forbidden(Path(home) / rel)
        row = {"client": client, "path": rel, "format": CLIENT_FORMATS[client], "regular_file": False,
               "sha256": None, "bytes": None, "neutral": False, "content_b64": None}
        try:
            regular = stat.S_ISREG(os.lstat(path).st_mode)
        except FileNotFoundError:
            regular = False
        except OSError as exc:
            raise Unobservable("client file %s: %s" % (rel, exc.strerror or exc))
        if regular:
            data = path.read_bytes()
            neutral = neutral_content(data, row["format"]) if len(data) <= NEUTRAL_MAX_BYTES else False
            row.update(regular_file=True, sha256=hashlib.sha256(data).hexdigest(), bytes=len(data), neutral=neutral,
                       content_b64=base64.b64encode(data).decode() if neutral is True else None)
        rows[client] = row
    return rows


def neutral_file(client, old, new, observed, strict):
    """Case (ii) of ``clients_restored`` for one client: the attested row, or ``None``."""
    rel, fmt = TEST_CLIENTS[client], CLIENT_FORMATS[client]
    if old != {**MISSING_CLIENT, "path": rel}:
        return None  # the file existed before the installation: it had to come back identical
    if not (set(new) == set(old) and new.get("state") == "ok" and new.get("entry_present") is False
            and new.get("kind") is None and isinstance(observed, dict)
            and set(observed) == {"client", "path", "format", "regular_file", "sha256", "bytes", "neutral", "content_b64"}):
        return None
    try:
        data = base64.b64decode(observed["content_b64"], validate=True)
    except (TypeError, ValueError):
        return None
    verdict = neutral_content(data, fmt)
    if not (observed["client"] == client and observed["path"] == rel and observed["format"] == fmt
            and observed["regular_file"] is True and observed["neutral"] is True
            and type(observed["bytes"]) is int and observed["bytes"] == len(data) <= NEUTRAL_MAX_BYTES
            and observed["sha256"] == hashlib.sha256(data).hexdigest() == new.get("file_sha256")
            and (verdict is True or (verdict is None and not strict))):
        return None
    return dict(observed)


def clients_restored(before, after, files, *, strict=False):
    """Rollback of the client entries: ``(restored, neutral_files)``.

    For each requested client, either (i) the state after undo is the state before apply, or (ii) the file
    did not exist before apply and what is left is a NEUTRAL file created by the installation: no entry at
    all, and a content without any setting, parsed (``neutral_content``) from the very bytes whose SHA-256
    the installed product observed after undo. A relay entry left behind, a foreign entry, any other
    content, or a pre-existing file that changed, is a failure. ``strict`` (release rules) also refuses a
    content this interpreter cannot parse; without it the verdict recorded by ``client_files`` (written by
    the run's Python 3.11) is used, bound by the SHA-256.
    """
    if not (isinstance(before, dict) and isinstance(after, dict) and set(before) == set(after) == set(TEST_CLIENTS)
            and all(isinstance(side[client], dict) and side[client].get("path") == TEST_CLIENTS[client]
                    for side in (before, after) for client in TEST_CLIENTS)) or relay_entries(after):
        return False, []
    neutral = []
    for client in sorted(TEST_CLIENTS):
        if before[client] == after[client]:
            continue
        row = neutral_file(client, before[client], after[client], (files or {}).get(client), strict)
        if row is None:
            return False, []
        neutral.append(row)
    return True, neutral


def relay_entries(state):
    """Files whose STRUCTURED entry is ours (parsed by the installed product, not searched as text)."""
    return sorted(row["path"] for row in state.values() if row and row.get("entry_present") and row.get("kind") == "relay")


def assemble(d):
    """Build ``report.json`` from the files the shell wrote. A missing or truncated piece is a failure."""
    meta = load(d / "meta.json")
    profile, variant = meta["profile"], meta["variant"]
    report = {"format": "qualify-410-install-v1", "ok": False, **{k: meta[k] for k in (
        "profile", "variant", "source_commit", "run_tag", "version", "wheel", "label", "port")}}
    try:
        check = load(d / "constraints_check.json")
        located = load(d / "locations.json")
        env = {**check, "venv": meta["venv"], "pip_check_ok": load(d / "pip_check.json")["returncode"] == 0,
               "locator_under_site_packages": located["locator_under_site_packages"],
               "package_under_site_packages": located["package_under_site_packages"],
               "package_path": located["package_path"], "package_version": located["version"],
               "candidate_origin_sha256": located["candidate_origin_sha256"]}
        if variant == "autonomous":
            env["distributions"], env["wheels_only"] = pip_distributions(load(d / "pip_install_report.json"))
        else:
            env["reused_paths"] = located["reused_paths"]
        report["environment"] = env
        prod_before, prod_after = load(d / "production_before.json"), load(d / "production_after.json")
        flags = production_unchanged(prod_before, prod_after)
        report["production"] = {
            **flags, "unchanged": all(flags.values()), "pid_before": prod_before["production_pid"],
            "pid_after": prod_after["production_pid"], "snapshot_before_sha256": prod_before["sha256"],
            "snapshot_after_sha256": prod_after["sha256"]}
        for index in ("1", "2"):
            p = d / ("install" + index)
            apply_json = load(p / "apply.json")
            install_json = load(p / "install.json")
            doctor = load(p / "doctor.json")
            pre, post = load(p / "pre_state.json"), load(p / "post_state.json")
            block = {
                "home": load(p / "home.json")["home"],
                "pre": {"label_absent": pre["label_state"] == "absent", "port_free": pre["port_state"] == "free"},
                "post": {"label_absent": post["label_state"] == "absent", "port_free": post["port_state"] == "free"},
                "apply": {"ok": apply_json.get("ok") is True, "returncode": load(p / "apply_rc.json")["returncode"],
                          "demo": True, "stamp": apply_json.get("stamp"), "profile": (install_json.get("timing") or {}).get("profile"),
                          "download_bytes": (install_json.get("timing") or {}).get("download_bytes"),
                          "timing": install_json.get("timing")},
                "service": load(p / "service.json"),
                "doctor": doctor_summary(doctor, load(p / "doctor_rc.json")["returncode"]),
                "clients_after_apply": load(p / "clients_after_apply.json"),
            }
            block["client_entries_created"] = relay_entries(block["clients_after_apply"])
            report["install_" + index] = block
        one, d1 = report["install_1"], d / "install1"
        one["preview"] = {**load(d1 / "preview_inventory.json"), "ok": load(d1 / "preview.json").get("ok") is True,
                          "returncode": load(d1 / "preview_rc.json")["returncode"],
                          "profile": load(d1 / "preview.json").get("profile")}
        undo, after_undo = load(d1 / "undo.json"), load(d1 / "undo_state.json")
        one["undo"] = {"ok": undo.get("ok") is True, "returncode": load(d1 / "undo_rc.json")["returncode"],
                       "with_service": True, "label_absent": after_undo["label_state"] == "absent",
                       "port_free": after_undo["port_state"] == "free",
                       "plist_removed": not (Path(one["home"]) / "Library/LaunchAgents" / (meta["label"] + ".plist")).exists(),
                       "clients_before_apply": load(d1 / "clients_before_apply.json"),
                       "clients_after_undo": load(d1 / "clients_after_undo.json")}
        restored, neutral = clients_restored(one["undo"]["clients_before_apply"], one["undo"]["clients_after_undo"],
                                             load(d1 / "client_files_after_undo.json"))
        # Files the installation created and undo left EMPTY of any setting: listed, never silently accepted.
        one["undo"].update(client_entries_restored=restored, client_entries_neutral_files=neutral)
        two, d2 = report["install_2"], d / "install2"
        created = two["client_entries_created"]
        after_uninstall = load(d2 / "clients_after_uninstall.json")
        still = relay_entries(after_uninstall)
        present = sorted(row["path"] for row in after_uninstall.values() if row and row.get("entry_present"))
        removed = sorted(rel for rel in created if rel not in still and rel not in present)
        uninstall, state = load(d2 / "uninstall.json"), load(d2 / "uninstall_state.json")

        compared = []  # the state documents actually used for the field-by-field comparison

        def text(folder):
            def read(rel):
                try:
                    content = read_text(d2 / folder / rel[len(STATE_PREFIX):])
                except (OSError, ValueError):
                    return None
                compared.append("install2/%s/%s" % (folder, rel[len(STATE_PREFIX):]))
                return content
            return read

        data = data_conservation(load(d2 / "data_before.json")["rows"], load(d2 / "data_after.json")["rows"],
                                 load(d2 / "store_before.json"), load(d2 / "store_after.json"), meta["label"], created,
                                 stamp=uninstall.get("stamp"), text_before=text("state_before"),
                                 text_after=text("state_after"))
        data["checked_after_stop"] = state["label_state"] == "absent" and state["port_state"] == "free"
        data["compared_documents"] = sorted(set(compared))
        data["witness"] = load(d2 / "witness.json")
        two["uninstall_preview"] = {**load(d2 / "uninstall_preview_inventory.json"),
                                    "ok": load(d2 / "uninstall_preview.json").get("ok") is True,
                                    "returncode": load(d2 / "uninstall_preview_rc.json")["returncode"]}
        two["uninstall"] = {
            "ok": uninstall.get("ok") is True, "partial": uninstall.get("partial"),
            "complete": uninstall.get("complete"), "data_deleted": uninstall.get("data_deleted"),
            "stamp": uninstall.get("stamp"),
            "returncode": load(d2 / "uninstall_rc.json")["returncode"], "entries_removed": removed,
            "label_absent": state["label_state"] == "absent", "port_free": state["port_state"] == "free",
            "plist_removed_from_active": data["plist_removed_from_active"],
            "plist_journal_copy_kept": data["plist_journal_copy_kept"], "clients": uninstall.get("clients")}
        two["data_conservation"] = data
        report["m1"] = m1(load(d / "timeline.json"), load(d1 / "install.json"), profile, variant,
                          load(d / "pip_cache_bytes.json")["bytes"])
        report["safety_net_fired"] = load(d / "safety_net.json")["fired"]
        if profile == "semantic":
            report["model_pre_placed"] = [load(d / ("install%s" % i) / "granite.json") for i in ("1", "2")]
        # Sealed pieces, sub-folders included: re-verified (presence and bytes) at G15 and --check-final from
        # this folder. The copies of the state folder used for the G8 comparison are sealed whole. proof.log
        # is still being written while this runs: it stays out.
        sealed = {}
        for folder, names, files in guarded_walk(d):
            parts = Path(folder).relative_to(d).parts
            if not parts:
                names[:] = [n for n in names if n not in ("venv", "pip-cache", "tmp") and not n.startswith("home")]
            state_copy = len(parts) >= 2 and parts[0] == "install2" and parts[1] in ("state_before", "state_after")
            for name in files:
                p = Path(folder) / name
                if p.is_file() and not p.is_symlink() and name != "report.json" and (
                        state_copy or p.suffix in (".json", ".txt")):
                    sealed[str(p.relative_to(d))] = sha256_file(p)
        report["evidence_sha256"] = sealed
        missing = sorted((set(REQUIRED_EVIDENCE) | set(data["compared_documents"])) - set(sealed))
        if not any("/state_before/" in rel for rel in data["compared_documents"]) or \
                not any("/state_after/" in rel for rel in data["compared_documents"]):
            raise ValueError("the state documents of the G8 comparison were not kept")
        if missing:
            raise ValueError("required evidence missing: " + ", ".join(missing))
        report["ok"] = load(d / "shell_verdict.json")["ok"] is True
    except (OSError, KeyError, ValueError, TypeError, AttributeError) as exc:
        report["error"] = type(exc).__name__ + ": " + str(exc)
    return report


if __name__ == "__main__":
    sys.exit(main())

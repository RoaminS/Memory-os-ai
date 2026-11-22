#!/usr/bin/env python3
"""Standard-library helpers of ``qualify_install_410.sh`` and of the 4.1.0 chain (QUALIFY_4_1_0 §3, G7, G8, M1).

Observation only: this file never loads, unloads or signals anything. ``launchctl print`` and a
local ``bind`` are its only probes. An observation that cannot be made is a refusal (exit 3),
never "absent". Runs under ``python3 -I`` (3.9+), imports nothing from the repository.
"""
from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import socket
import sqlite3
import subprocess
import sys
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
REAL_CLIENT_CONFIGS = (
    ".cursor/mcp.json", ".codex/config.toml", ".gemini/config/mcp_config.json",
    "Library/Application Support/Claude/claude_desktop_config.json", "Library/Application Support/Code/User/mcp.json",
)
TEST_CLIENT_FILES = (".codex/config.toml", ".cursor/mcp.json")
EXIT_UNOBSERVABLE = 3


class Unobservable(RuntimeError):
    """The state could not be observed: the caller must stop and keep its lock."""


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def refuse_forbidden(path):
    text = os.path.abspath(os.fspath(path))
    if any(name in Path(text).parts for name in FORBIDDEN_NAMES):
        raise SystemExit("REFUS: sealed validation folder (never read or listed)")
    return Path(text)


def check_test_identity(label, port):
    """Never a label starting with the production one, never the production port."""
    if label != TEST_LABEL or label.startswith(PRODUCTION_LABEL) or int(port) != TEST_PORT or int(port) == PRODUCTION_PORT:
        raise SystemExit("REFUS: test label/port must be %s / %d" % (TEST_LABEL, TEST_PORT))


# ---------- launchd and port observation ----------

def label_state(label, uid=None, run=subprocess.run):
    """'absent' only when launchctl explicitly says the service does not exist; else 'present' or a refusal."""
    uid = os.getuid() if uid is None else uid
    try:
        done = run(["/bin/launchctl", "print", "gui/%d/%s" % (uid, label)], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Unobservable("launchctl print failed: " + type(exc).__name__)
    text = (done.stdout or "") + (done.stderr or "")
    if done.returncode == 0 and (label + " = {") in text.replace("gui/%d/" % uid, ""):
        return "present", text
    if done.returncode == 113 and "Could not find service" in text:
        return "absent", text
    raise Unobservable("launchctl print: unexpected answer (rc=%d)" % done.returncode)


def port_state(port, bind=None):
    """'free' when 127.0.0.1:port can be bound right now, 'busy' on EADDRINUSE, otherwise a refusal."""
    def default_bind(number):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", number))

    try:
        (bind or default_bind)(int(port))
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            return "busy"
        raise Unobservable("port probe failed: errno %s" % exc.errno)
    return "free"


def service_state(label, port, **kw):
    state, _ = label_state(label, uid=kw.get("uid"), run=kw.get("run", subprocess.run))
    return {"label": label, "port": int(port), "label_state": state, "port_state": port_state(port, kw.get("bind")),
            "label_absent": state == "absent"}


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
    token = Path(token_file).read_text().strip()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request("http://127.0.0.1:%d/ready" % int(port), headers={"Authorization": "Bearer " + token})
    with opener.open(request, timeout=10) as response:
        return json.loads(response.read())


# ---------- inventories with content hashes ----------

def inventory(root):
    """Every entry below ``root``: type, mode, size AND content SHA-256 (names/sizes/dates are not enough)."""
    root = refuse_forbidden(root)
    rows = {}
    for folder, names, files in os.walk(root):
        names.sort()
        for name in sorted(names + files):
            path = Path(folder) / name
            rel = str(path.relative_to(root))
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
    for folder, _, files in os.walk(root):
        for name in files:
            path = Path(folder) / name
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
    return total


# ---------- resources ----------

def verify_granite(cache, catalog):
    """Pre-placed model checked file by file against provision_v1.json (size and SHA-256)."""
    cache = Path(cache)
    snapshots = sorted((cache / "snapshots").iterdir()) if (cache / "snapshots").is_dir() else []
    rows, ok = [], len(snapshots) == 1
    for record in catalog.get("files", []):
        path = snapshots[0] / record["path"] if snapshots else None
        good = bool(path and path.is_file() and path.stat().st_size == record["bytes"]
                    and sha256_file(path) == record["sha256"])
        rows.append({"path": record["path"], "ok": good})
        ok = ok and good
    return {"ok": ok and bool(rows), "files": rows, "bytes": sum(r["bytes"] for r in catalog.get("files", [])),
            "revision": snapshots[0].name if len(snapshots) == 1 else None}


# ---------- production: read only ----------

def production_snapshot(real_home, uid=None, run=subprocess.run):
    """Fingerprints of what must never change: prod plist, real LaunchAgents, real client configs, prod pid."""
    real_home = Path(real_home)
    files = {}
    for rel in (*REAL_CLIENT_CONFIGS, "Library/LaunchAgents/%s.plist" % PRODUCTION_LABEL):
        path = real_home / rel
        files[rel] = {"sha256": sha256_file(path), "mtime_ns": path.stat().st_mtime_ns} if path.is_file() else None
    agents = real_home / "Library/LaunchAgents"
    state, text = label_state(PRODUCTION_LABEL, uid=uid, run=run)
    row = {"files": files, "launch_agents": sorted(os.listdir(agents)) if agents.is_dir() else None,
           "production_label_state": state, "production_pid": parse_print(text)["pid"] if state == "present" else None}
    row["sha256"] = hashlib.sha256(json.dumps({k: row[k] for k in ("files", "launch_agents")}, sort_keys=True).encode()).hexdigest()
    return row


# ---------- client entries and user data (G8) ----------

def client_state(home, port):
    home = Path(home)
    rows = {}
    for rel in TEST_CLIENT_FILES:
        path = home / rel
        if path.is_file():
            text = path.read_text(errors="replace")
            rows[rel] = {"sha256": sha256_file(path), "mentions_test_service": (":%d/" % int(port)) in text
                         or "memory-os" in text}
        else:
            rows[rel] = None
    return rows


def store_logical(store_dir):
    """Identity and logical content of a SQLite store, read only; survives WAL checkpoint/close."""
    db = Path(store_dir) / "store.sqlite3"
    connection = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=30)
    try:
        digest = hashlib.sha256()
        tables = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "AND sql NOT LIKE 'CREATE VIRTUAL%' ORDER BY name")]
        counts = {}
        for table in tables:
            digest.update(table.encode())
            counts[table] = 0
            for row in connection.execute('SELECT * FROM "%s" ORDER BY rowid' % table.replace('"', '""')):
                digest.update(repr(row).encode())
                counts[table] += 1
        identity = None
        for row in connection.execute("SELECT * FROM store_metadata"):
            if row and row[0] == "metadata":
                identity = json.loads(row[1]).get("store_instance_id")
        return {"store_instance_id": identity, "logical_sha256": digest.hexdigest(), "tables": counts,
                "user_version": connection.execute("PRAGMA user_version").fetchone()[0]}
    finally:
        connection.close()


IMMUTABLE = {
    "token": (".config/memory-os/http.token",),
    "resources": (".config/memory-os/cl100k_base.tiktoken", ".cache/huggingface/"),
    "backups": ("memory-os-backups/",),
    "key": (".config/memory-os/backup.key",),
}
STORE_PREFIX = ".local/share/memory-os/store/"
JOURNAL_PREFIX = ".local/state/memory-os/"
UNINSTALL_PREFIX = ".local/state/memory-os/uninstall/"


def kind_of(rel):
    for kind, prefixes in IMMUTABLE.items():
        if any(rel == p or (p.endswith("/") and rel.startswith(p)) for p in prefixes):
            return kind
    return None


def data_conservation(before, after, store_before, store_after, label, created_clients):
    """Only the expected, inventoried mutations of the uninstall protocol are admitted."""
    delta = diff(before, after)
    plist = "Library/LaunchAgents/%s.plist" % label
    expected, unexpected = [], []
    immutable_changed = []
    for change, paths in delta.items():
        for rel in paths:
            kind = kind_of(rel)
            is_dir = (after.get(rel) or before.get(rel) or {}).get("type") == "dir"
            if kind:
                immutable_changed.append(rel)
                unexpected.append({"path": rel, "change": change, "why": "immutable " + kind})
            elif change == "removed" and rel == plist:
                expected.append({"path": rel, "change": change, "why": "plist moved out of its active place"})
            elif change == "added" and rel.startswith(UNINSTALL_PREFIX):
                expected.append({"path": rel, "change": change, "why": "new uninstall journal"})
            elif change != "removed" and rel.startswith(JOURNAL_PREFIX):
                expected.append({"path": rel, "change": change, "why": "journal update (revocation)"})
            elif change == "changed" and rel in created_clients:
                expected.append({"path": rel, "change": change, "why": "our client entry removed"})
            elif rel.startswith(STORE_PREFIX) and rel[len(STORE_PREFIX):] in ("store.sqlite3", "store.sqlite3-wal",
                                                                             "store.sqlite3-shm"):
                expected.append({"path": rel, "change": change, "why": "SQLite close transition (logical content compared)"})
            elif change != "removed" and (rel.endswith(".log") or "/Logs/" in "/" + rel):
                expected.append({"path": rel, "change": change, "why": "log still present"})
            elif change == "changed" and is_dir:
                expected.append({"path": rel, "change": change, "why": "directory metadata"})
            else:
                unexpected.append({"path": rel, "change": change, "why": "not part of the uninstall protocol"})
    kinds = {kind: sorted(rel for rel in before if kind_of(rel) == kind and before[rel].get("type") == "file")
             for kind in IMMUTABLE}
    journals_before = [rel for rel in before if rel.startswith(JOURNAL_PREFIX)]
    logs_before = [rel for rel in before if rel.endswith(".log") and before[rel].get("type") == "file"]
    return {
        "immutable_kinds": sorted(IMMUTABLE),
        "immutable_files": {kind: len(paths) for kind, paths in kinds.items()},
        "immutable_unchanged": not immutable_changed and bool(kinds["token"]) and bool(kinds["resources"]),
        "store_identity_unchanged": bool(store_before["store_instance_id"])
        and store_before["store_instance_id"] == store_after["store_instance_id"],
        "store_logical_unchanged": store_before["logical_sha256"] == store_after["logical_sha256"]
        and store_before["tables"] == store_after["tables"],
        "journals_present": bool(journals_before) and all(rel in after for rel in journals_before)
        and all(rel in after for rel in logs_before),
        "plist_removed_from_active": plist in before and plist not in after,
        "plist_journal_copy_kept": any(rel.startswith(JOURNAL_PREFIX) and rel.endswith(".plist") for rel in after),
        "expected_mutations": expected,
        "unexpected_changes": unexpected,
    }


# ---------- M1 ----------

def m1(timeline, install_json, profile, variant, pip_download_bytes):
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
        "pip_download_bytes": int(pip_download_bytes), "pip_download_bytes_nature": "bytes of the run's own pip cache",
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
        with open(path, "x") as stream:  # evidence is never overwritten
            stream.write(text)
    else:
        sys.stdout.write(text)


def load(path):
    return json.loads(Path(path).read_text())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("service-state")
    s.add_argument("--label", default=TEST_LABEL)
    s.add_argument("--port", type=int, default=TEST_PORT)
    s.add_argument("--expect", choices=("absent",), required=True)
    s.add_argument("--output")
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
    s = sub.add_parser("verify-granite")
    for name in ("--cache", "--catalog", "--output"):
        s.add_argument(name, required=True)
    s = sub.add_parser("production-snapshot")
    s.add_argument("--real-home", required=True)
    s.add_argument("--output", required=True)
    s = sub.add_parser("client-state")
    s.add_argument("--home", required=True)
    s.add_argument("--output", required=True)
    s = sub.add_parser("store-logical")
    s.add_argument("--store", required=True)
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
    try:
        if args.command == "service-state":
            check_test_identity(args.label, args.port)
            row = service_state(args.label, args.port)
            out(row, args.output)
            return 0 if row["label_state"] == "absent" and row["port_state"] == "free" else 1
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
            row = constraints_check(Path(args.constraints).read_text(), Path(args.freeze).read_text(), args.profile,
                                    args.variant)
            row["constraints_sha256"] = sha256_file(args.constraints)
            out(row, args.output)
            return 0 if row["ok"] else 1
        if args.command == "verify-granite":
            row = verify_granite(args.cache, load(args.catalog))
            out(row, args.output)
            return 0 if row["ok"] else 1
        if args.command == "production-snapshot":
            out(production_snapshot(args.real_home), args.output)
            return 0
        if args.command == "client-state":
            out(client_state(args.home, TEST_PORT), args.output)
            return 0
        if args.command == "store-logical":
            out(store_logical(args.store), args.output)
            return 0
        if args.command == "tree-bytes":
            out({"root": args.root, "bytes": tree_bytes(args.root)}, args.output)
            return 0
        if args.command == "timeline":
            out(timeline(Path(args.input).read_text()), args.output)
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
               "package_path": located["package_path"]}
        if variant == "autonomous":
            env["distributions"], env["wheels_only"] = pip_distributions(load(d / "pip_install_report.json"))
        else:
            env.update(reused_paths=located["reused_paths"], candidate_origin_sha256=located["candidate_origin_sha256"])
        report["environment"] = env
        prod_before, prod_after = load(d / "production_before.json"), load(d / "production_after.json")
        report["production"] = {
            "unchanged": prod_before == prod_after, "pid_before": prod_before["production_pid"],
            "pid_after": prod_after["production_pid"], "snapshot_before_sha256": prod_before["sha256"],
            "snapshot_after_sha256": prod_after["sha256"]}
        for index in ("1", "2"):
            p = d / ("install" + index)
            apply_json = load(p / "apply.json")
            install_json = load(p / "install.json")
            doctor = load(p / "doctor.json")
            checks = doctor if isinstance(doctor, list) else doctor.get("checks", [])
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
                "doctor": {"returncode": load(p / "doctor_rc.json")["returncode"], "checks": len(checks),
                           "fail": sum(1 for c in checks if c.get("status") == "FAIL")},
                "clients_after_apply": load(p / "clients_after_apply.json"),
            }
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
                       "client_entries_restored": load(d1 / "clients_before_apply.json") == load(d1 / "clients_after_undo.json")}
        two, d2 = report["install_2"], d / "install2"
        created = sorted(rel for rel, row in two["clients_after_apply"].items() if row and row["mentions_test_service"])
        after_uninstall = load(d2 / "clients_after_uninstall.json")
        removed = sorted(rel for rel in created
                         if not after_uninstall.get(rel) or not after_uninstall[rel]["mentions_test_service"])
        uninstall, state = load(d2 / "uninstall.json"), load(d2 / "uninstall_state.json")
        data = data_conservation(load(d2 / "data_before.json")["rows"], load(d2 / "data_after.json")["rows"],
                                 load(d2 / "store_before.json"), load(d2 / "store_after.json"), meta["label"], created)
        data["checked_after_stop"] = state["label_state"] == "absent" and state["port_state"] == "free"
        two["client_entries_created"] = created
        two["uninstall_preview"] = {**load(d2 / "uninstall_preview_inventory.json"),
                                    "ok": load(d2 / "uninstall_preview.json").get("ok") is True,
                                    "returncode": load(d2 / "uninstall_preview_rc.json")["returncode"]}
        two["uninstall"] = {
            "ok": uninstall.get("ok") is True, "partial": uninstall.get("partial"),
            "complete": uninstall.get("complete"), "data_deleted": uninstall.get("data_deleted"),
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
        report["evidence_sha256"] = {str(p.relative_to(d)): sha256_file(p) for p in sorted(d.rglob("*"))
                                     if p.is_file() and p.suffix in (".json", ".log", ".txt") and p.name != "report.json"
                                     and not any(part in ("venv", "pip-cache", "tmp") or part.startswith("home")
                                                 for part in p.relative_to(d).parts)}
        report["ok"] = load(d / "shell_verdict.json")["ok"] is True
    except (OSError, KeyError, ValueError, TypeError) as exc:
        report["error"] = type(exc).__name__ + ": " + str(exc)
    return report


if __name__ == "__main__":
    sys.exit(main())

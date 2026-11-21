"""``memory-os-admin quickstart``: guided first install (D1, owner Claude, Codex review/integration).

Target location: ``memory_os_v4/ops/quickstart.py``. Contract: QUICKSTART_CONTRACT_v1.md + Codex amendments Q1-Q6
(DISTRIBUTION_D1a_v1.md §1):
- Q1 ``preview`` writes NOTHING (no directory, token, journal, model, cache, download); reads only the configs of the
  chosen clients and the state of resources (provision.plan), never an assistant auth file.
- Q2 a download requires separate consent (``download_approved``); ``apply`` alone downloads nothing.
- Q3 ``undo`` restores only OUR entry, and only if it is still the one we wrote; otherwise refusal.
- Q4 never act on a service that is not ours (label + executable + token checked).
- Q5 backups 0700/0600; reports show only our entry, masked, without foreign configuration.
- Q6 demo on a unique ``memory-os-demo-<id>`` project, no collision, no replay of an uncertain write.
JSON clients: semantic merge of the ``memory-os-ai`` key only, atomic write preserving the mode. Zed (JSONC) and the
TOML clients (Codex, Grok): written only when there is no existing entry and the file is safe; otherwise refusal with
the snippet to apply by hand. Claude Code: ``claude mcp add --scope user`` (the CLI maintains ~/.claude.json).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import re
import uuid
import platform
from pathlib import Path

from . import sqlite_runtime
from .clients import CLIENTS, SERVER, Client, classify, read_entry, relay_command

DEFAULT_CLIENTS = ("claude-code", "codex")
JOURNAL_ROOT = Path("~/.local/state/memory-os/quickstart")
JOURNAL_SUBDIR = ".local/state/memory-os/quickstart"  # below the selected HOME
SENSITIVE = ("TOKEN", "KEY", "SECRET", "PASSWORD", "BEARER")


class QuickstartError(RuntimeError):
    """Refusal with a stable ``code`` (SETUP_4_1_0.md §11); the message never carries foreign text."""

    def __init__(self, message: str, code: str = "quickstart_refused"):
        super().__init__(message)
        self.code = code


# ---------- pure helpers ----------

def _sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def _safe_path(path: Path, home: Path) -> None:
    """Reject redirection outside HOME and special files, including dangling links."""
    if not path.absolute().is_relative_to(home.absolute()):
        raise QuickstartError("Config path outside selected HOME")
    for parent in [path, *path.parents]:
        if parent == home.parent:
            break
        if parent.is_symlink():
            raise QuickstartError("Symlink config path: manual setup required")
    if path.exists() and not path.is_file():
        raise QuickstartError("Config is not a regular file")
    if path.exists() and (path.stat().st_nlink != 1 or path.stat().st_uid != os.getuid()):
        raise QuickstartError("Config must be owned by this user with one link")


def _mask(entry: dict | None) -> dict | None:
    if not isinstance(entry, dict):
        return entry
    out = dict(entry)
    env = out.get("env")
    if isinstance(env, dict):
        out["env"] = {k: ("***" if any(s in k.upper() for s in SENSITIVE) else v) for k, v in env.items()}
    return out


def desired_entry(client: Client, python: str, env: dict[str, str]) -> dict:
    """Semantic entry we want for this client (no token: the relay reads its 0600 file)."""
    command, args = relay_command(python)
    env = {"MEMORY_V4_CLIENT_ID": client.key, **env}
    if client.fmt == "zed":
        return {"source": "custom", "command": command, "args": args, "env": env}
    if client.fmt in {"vscode", "claude-user"}:
        return {"type": "stdio", "command": command, "args": args, "env": env}
    if client.fmt == "grok-toml":
        return {"command": command, "args": args, "env": env, "enabled": True}
    return {"command": command, "args": args, "env": env}


def _same(a: dict | None, b: dict | None) -> bool:
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def _entry_hash(entry: dict) -> str:
    return hashlib.sha256(json.dumps(entry, sort_keys=True).encode()).hexdigest()


def installation_identity(env: dict) -> str:
    """Destination identity: WHICH service an entry points to (URL + token file).

    It is not a proof of ownership: anyone can write these two values in a configuration.
    """
    document = {"url": env.get("MEMORY_V4_URL"), "token_file": env.get("MEMORY_V4_TOKEN_FILE")}
    return hashlib.sha256(json.dumps(document, sort_keys=True).encode()).hexdigest()


def _port_state(port: int) -> str:
    with socket.socket() as s:
        s.settimeout(0.5)
        return "listening" if s.connect_ex(("127.0.0.1", port)) == 0 else "free"


# ---------- preview (Q1: zero writes) ----------

def preview(*, home: Path, python: str, env: dict[str, str], clients: tuple[str, ...] = DEFAULT_CLIENTS,
            port: int = 8765, token_file: Path | None = None, provision_plan: dict | None = None,
            replace_existing: tuple[str, ...] = (), journal_root: Path | None = None) -> dict:
    """Read-only plan. ``provision_plan`` = result of provision.plan() (computed by the caller, without network)."""
    selected = [c for c in CLIENTS if c.key in clients]
    unknown = sorted(set(clients) - {c.key for c in selected})
    token_file = token_file or home / ".config/memory-os/http.token"
    token_state = "absent"
    if token_file.exists():
        mode = token_file.stat().st_mode & 0o777
        token_state = "ok" if mode == 0o600 else f"bad-permissions:{oct(mode)}"
    disk_free = shutil.disk_usage(home).free
    plan_clients = []
    for c in selected:
        path = c.config_path(home)
        _safe_path(path, home)
        state, current = read_entry(c, home)
        want = desired_entry(c, python, env)
        path = c.config_path(home)
        action = "unchanged" if _same(current, want) else ("create" if current is None else "replace")
        if state == "unreadable":
            action = "refuse-unreadable"
        elif c.fmt == "zed" and path.exists() and action != "unchanged":
            raw = path.read_text(encoding="utf-8")
            try:
                json.loads(raw or "{}")
            except ValueError:
                action = "refuse-jsonc-manual"  # comments: never rewrite the user's file
        elif c.fmt.endswith("toml") and current is not None and action != "unchanged":
            action = "refuse-toml-existing-manual"
        plan_clients.append({"client": c.key, "name": c.name, "path": str(path), "file_sha256": _sha(path),
                             "current_class": classify(current), "desired": _mask(want),
                             "desired_sha256": _entry_hash(want),
                             "action": action, "note": c.note})
    return {"version": 1, "created_at": time.time(), "platform": sys.platform, "python": python,
            "unknown_clients": unknown, "disk_free_bytes": disk_free, "port": port, "port_state": _port_state(port),
            "token": {"path": str(token_file), "state": token_state},
            "provision": provision_plan, "clients": plan_clients,
            "downloads_required_bytes": (provision_plan or {}).get("download_bytes", 0),
            "writes": "none (preview)"}


# ---------- client writes ----------

def _atomic_write(path: Path, text: str) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise QuickstartError("Unsafe write target")
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _json_key(client: Client) -> str:
    return {"vscode": "servers", "zed": "context_servers"}.get(client.fmt, "mcpServers")


def _write_client(client: Client, home: Path, entry: dict, *, run=subprocess.run) -> str:
    path = client.config_path(home)
    _safe_path(path, home)
    if client.fmt == "claude-user":
        command, args = entry["command"], entry["args"]
        flags = [x for k, v in entry.get("env", {}).items() for x in ("--env", f"{k}={v}")]
        _, existing = read_entry(client, home)
        if existing is not None:
            removed = run(["claude", "mcp", "remove", "--scope", "user", SERVER],
                          capture_output=True, text=True, timeout=30, env={**os.environ, "HOME": str(home)})
            if removed.returncode:
                raise QuickstartError("claude mcp remove refused; previous entry retained")
        res = run(["claude", "mcp", "add", "--scope", "user", "--transport", "stdio", *flags, SERVER, "--", command, *args],
                  capture_output=True, text=True, timeout=30, env={**os.environ, "HOME": str(home)})
        if res.returncode:
            raise QuickstartError(f"claude mcp add failed ({res.returncode})")
        return "claude-cli"
    if client.fmt.endswith("toml"):
        lines = [f"\n[mcp_servers.{SERVER}]", f"command = {json.dumps(entry['command'])}", f"args = {json.dumps(entry['args'])}"]
        if client.fmt == "grok-toml":
            lines.append("enabled = true")
        lines.append(f"[mcp_servers.{SERVER}.env]")
        lines += [f"{json.dumps(k)} = {json.dumps(v)}" for k, v in entry.get("env", {}).items()]
        prior = path.read_text(encoding="utf-8") if path.exists() else ""
        _atomic_write(path, prior.rstrip("\n") + "\n" + "\n".join(lines) + "\n")
        return "toml-append"
    data = json.loads(path.read_text(encoding="utf-8") or "{}") if path.exists() else {}
    data.setdefault(_json_key(client), {})[SERVER] = entry
    _atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return "json-merge"


def apply_clients(plan: dict, *, home: Path, python: str, env: dict[str, str], journal_root: Path | None = None,
                  run=subprocess.run, journal_dir: Path | None = None) -> dict:
    """Writes the client entries from the preview (Q3/Q5). Refuses any file modified since the preview."""
    if plan.get("unknown_clients"):
        raise QuickstartError("Unknown clients; re-preview with explicit supported choices")
    for item in plan["clients"]:
        client = next(c for c in CLIENTS if c.key == item["client"])
        if _entry_hash(desired_entry(client, python, env)) != item['desired_sha256']:
            raise QuickstartError("Desired entry changed since preview")
    jdir = journal_dir or new_journal(journal_root or home / ".local/state/memory-os/quickstart")
    stamp = jdir.name
    journal = jdir / "journal.json"
    records = []
    def persist():
        _atomic_write(journal, json.dumps({"version": 1, "stamp": stamp, "records": records},
                                         indent=1, ensure_ascii=False))
        journal.chmod(0o600)
    persist()
    for item in plan["clients"]:
        client = next(c for c in CLIENTS if c.key == item["client"])
        path = client.config_path(home)
        if str(path) != item["path"]:
            raise QuickstartError("Preview path differs from client registry")
        _safe_path(path, home)
        rec = {"client": client.key, "path": str(path), "action": item["action"], "status": "skipped"}
        if item["action"] == "unchanged" or item["action"].startswith("refuse"):
            rec["status"] = item["action"]
            records.append(rec)
            continue
        if _sha(path) != item["file_sha256"]:
            rec["status"] = "conflict-file-changed-since-preview"
            records.append(rec)
            continue
        _, before = read_entry(client, home)
        if path.exists():
            backup = jdir / f"{client.key}.bak"
            shutil.copy2(path, backup)
            os.chmod(backup, 0o600)
            with backup.open('rb') as stream:
                os.fsync(stream.fileno())
            rec["backup"] = str(backup)
        want = desired_entry(client, python, env)
        rec["previous_entry"] = before
        rec["applied_entry"] = want
        records.append(rec)
        rec["status"] = "pending"  # write-ahead evidence survives CLI/write failure
        persist()
        if _sha(path) != item["file_sha256"]:
            rec["status"] = "conflict-file-changed-since-preview"
            persist()
            continue
        try:
            rec["method"] = _write_client(client, home, want, run=run)
            state, after = read_entry(client, home)
            rec["status"] = "applied" if state == "ok" and _same(after, want) else f"verify-failed:{state}"
        except (OSError, ValueError, QuickstartError, subprocess.SubprocessError):
            rec["status"] = "failed-or-partial"
            persist()
            raise QuickstartError(f"Client {client.key} failed; private journal retained: {stamp}") from None
        persist()
    persist()
    return {"stamp": stamp, "journal": str(journal),
            "records": [{k: v for k, v in r.items() if not k.endswith("_entry")} for r in records]}


def new_journal(root: Path) -> Path:
    if any(p.is_symlink() for p in [root, *root.parents]):
        raise QuickstartError("Symlink journal path refused")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    path = root / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex)
    path.mkdir(mode=0o700)
    return path


def undo(stamp: str, *, home: Path, journal_root: Path | None = None, run=subprocess.run) -> dict:
    """Q3: restores the previous entry ONLY if our entry is still the one applied; otherwise refusal."""
    if not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[a-f0-9]{32}", stamp):
        raise QuickstartError("Invalid journal stamp")
    journal_root = journal_root or home / JOURNAL_SUBDIR
    journal = journal_root / stamp / "journal.json"
    _safe_path(journal, journal_root)
    if stat.S_IMODE(journal.stat().st_mode) != 0o600:
        raise QuickstartError("Unsafe journal permissions")
    data = json.loads(journal.read_text(encoding="utf-8"))
    out = []
    for rec in data["records"]:
        if rec.get("status") not in {"applied", "pending", "failed-or-partial"}:
            out.append({"client": rec["client"], "undo": "nothing-to-undo"})
            continue
        client = next(c for c in CLIENTS if c.key == rec["client"])
        state, current = read_entry(client, home)
        if state != "ok" or not _same(current, rec["applied_entry"]):
            out.append({"client": client.key, "undo": "refused-entry-changed-since-apply"})
            continue
        previous = rec.get("previous_entry")
        path = client.config_path(home)
        if str(path) != rec["path"]:
            raise QuickstartError("Journal config path differs from registry")
        _safe_path(path, home)
        if client.fmt == "claude-user":
            removed = run(["claude", "mcp", "remove", "--scope", "user", SERVER], capture_output=True,
                          text=True, timeout=30, env={**os.environ, "HOME": str(home)})
            if removed.returncode:
                raise QuickstartError("Claude undo failed; journal retained")
            if previous:
                _write_client(client, home, previous, run=run)
        elif client.fmt.endswith("toml"):
            # Only absent entries can be appended; remove their exact table spans,
            # leaving unrelated TOML/application edits intact.
            if previous is not None:
                raise QuickstartError("Existing TOML entry was not eligible for automatic apply")
            lines = path.read_text().splitlines(keepends=True)
            retained, skipping = [], False
            for line in lines:
                if line.lstrip().startswith("["):
                    header = line.strip().split("#", 1)[0].strip()
                    skipping = header in {f"[mcp_servers.{SERVER}]", f"[mcp_servers.{SERVER}.env]"}
                if not skipping:
                    retained.append(line)
            _atomic_write(path, "".join(retained))
        else:
            data_cfg = json.loads(path.read_text(encoding="utf-8") or "{}")
            servers = data_cfg.setdefault(_json_key(client), {})
            if previous is None:
                servers.pop(SERVER, None)
            else:
                servers[SERVER] = previous
            _atomic_write(path, json.dumps(data_cfg, indent=2, ensure_ascii=False) + "\n")
        out.append({"client": client.key, "undo": "restored"})
    return {"stamp": stamp, "results": out}


def report(plan: dict) -> dict:
    """Q5: anonymized report (paths relative to HOME, entries masked, no foreign configuration)."""
    home = str(Path.home())
    def anon(x):
        return x.replace(home, "~") if isinstance(x, str) else x
    return {"platform": plan["platform"], "port_state": plan["port_state"], "token": plan["token"]["state"],
            "backend": plan.get('backend'), "work_state": plan.get('work_state'),
            "runtime_qualified": plan.get('runtime_qualification', {}).get('ok'),
            "disk_free_gb": round(plan["disk_free_bytes"] / 1e9, 1), "downloads_required_bytes": plan["downloads_required_bytes"],
            **{key: plan[key] for key in ('disk_min_free_bytes', 'disk_required_free_bytes') if key in plan},
            "clients": [{"client": c["client"], "path": anon(c["path"]), "current_class": c["current_class"],
                         "action": c["action"]} for c in plan["clients"]]}


# ---------- orchestration: all mutations require explicit apply ----------

def _selected_backend(settings, home, requested=None):
    """A persisted backend is a hint only: ownership still requires the full preflight."""
    if requested is not None:
        if requested not in {'sqlite', 'jsonl'}:
            raise QuickstartError('Select sqlite or jsonl')
        return requested
    import plistlib
    label = settings.service_label or 'local.memory-os-v4'
    path = home / 'Library/LaunchAgents' / (label + '.plist')
    _safe_path(path, home)
    if path.exists():
        try:
            value = plistlib.loads(path.read_bytes()).get('EnvironmentVariables', {}).get('MEMORY_V4_STORE_BACKEND')
            if value in {'sqlite_s1', 'jsonl'}:
                return 'sqlite' if value == 'sqlite_s1' else 'jsonl'
        except (ValueError, AttributeError):
            pass  # full service preflight refuses an invalid/different plist
    return 'sqlite'


def settings_for_install(settings, *, backend=None, home=None):
    """Only new default SQLite installs get a new directory; never migrate legacy data."""
    from dataclasses import replace
    home = home or Path.home()
    chosen = _selected_backend(settings, home, backend)
    legacy = home / '.memory-os-v4'
    label = settings.service_label or 'local.memory-os-v4'
    existing_plist = home / 'Library/LaunchAgents' / (label + '.plist')
    if (chosen == 'sqlite' and settings.store_dir == legacy and 'MEMORY_V4_DIR' not in os.environ
            and not existing_plist.exists() and not legacy.exists()):
        settings = replace(settings, store_dir=home / '.local/share/memory-os/store')
    return settings, chosen


PROFILES = ('lexical', 'semantic')
# Encoder settings a lexical service must not carry (Config.validate_retrieval_profile refuses them).
ENCODER_ENV = ('MEMORY_V4_EMBED_MODEL', 'MEMORY_V4_EMBED_MAX_SEQ_LENGTH', 'MEMORY_V4_DEVICE',
               'MEMORY_V4_EMBED_DTYPE', 'MEMORY_V4_EMBED_BATCH_SIZE', 'HF_HUB_CACHE')
PROFILE_INSTALL = {
    'lexical': ('memory-os-ai[ops,brief]', 'qualified_runtime_constraints_lexical_v1.txt'),
    'semantic': ('memory-os-ai[ops,brief,semantic]', 'qualified_runtime_constraints_v1.txt'),
}


def _service_plan(settings, home, python, resource_env, *, backend=None, profile='semantic'):
    """Service definition of one profile. ``semantic`` is the frozen 4.0.9 definition, byte for byte;
    ``lexical`` is that definition minus the encoder variables, plus the profile selector."""
    from .cli import _plist
    if profile not in PROFILES:
        raise QuickstartError('Unknown install profile', code='setup_profile_unknown')
    label = settings.service_label or "local.memory-os-v4"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", label):
        raise QuickstartError("Invalid service label")
    if settings.url != f"http://127.0.0.1:{settings.port}/mcp/":
        raise QuickstartError("Quickstart supports the explicit 127.0.0.1 loopback URL")
    backend = _selected_backend(settings, home, backend)
    env = {"MEMORY_V4_DIR": str(settings.store_dir), "MEMORY_V4_TOKEN_FILE": str(settings.token_file),
           "MEMORY_V4_URL": settings.url, "MEMORY_V4_SERVICE_LABEL": label,
           "MEMORY_V4_STORE_BACKEND": 'sqlite_s1' if backend == 'sqlite' else 'jsonl',
           "MEMORY_V4_WORK_STATE": 'true' if backend == 'sqlite' else 'false'}
    if profile == 'semantic':
        from .provision import catalog
        env.update({"MEMORY_V4_EMBED_MODEL": catalog()['model_id'], "MEMORY_V4_EMBED_MAX_SEQ_LENGTH": "512"})
    env["MEMORY_V4_MAINTENANCE_MODE"] = "disabled"
    if profile == 'semantic':
        env.update({"MEMORY_V4_DEVICE": "auto", "MEMORY_V4_EMBED_DTYPE": "native", "MEMORY_V4_EMBED_BATCH_SIZE": "32"})
    env.update({"MEMORY_V4_BM25_PROFILE": "scoped_literal", "MEMORY_V4_LEXICAL_GUARD_K": "3",
                "TOKENIZERS_PARALLELISM": "false", "MEMORY_V4_OPS_DIR": str(settings.ops_dir), **resource_env})
    if profile == 'lexical':
        env = {key: value for key, value in env.items() if key not in ENCODER_ENV}
        env["MEMORY_V4_RETRIEVAL_PROFILE"] = "lexical"
    argv = [python, "-m", "memory_os_v4.server", "--transport", "http", "--host", "127.0.0.1",
            "--port", str(settings.port), "--warm"]
    xml = _plist(label, argv, env=env, keepalive=True, log=settings.ops_dir / "logs/server.err")
    path = home / "Library/LaunchAgents" / (label + ".plist")
    _safe_path(path, home)
    import plistlib
    state = "absent"
    if path.exists():
        try:
            current = plistlib.loads(path.read_bytes())
            expected = plistlib.loads(xml.encode())
            state = "owned" if current == expected else "foreign-or-different"
        except (ValueError, plistlib.InvalidFileException):
            state = "invalid"
    return {"label": label, "path": str(path), "argv": argv, "env": env, "plist": xml,
            "state": state, "sha256": _sha(path)}


def _installed_profile(settings, home):
    """Profile recorded by an existing service definition (absent variable = semantic); None without one."""
    import plistlib
    label = settings.service_label or 'local.memory-os-v4'
    path = home / 'Library/LaunchAgents' / (label + '.plist')
    try:
        if path.is_symlink() or not path.is_file():
            return None
        env = plistlib.loads(path.read_bytes()).get('EnvironmentVariables', {})
        value = env.get('MEMORY_V4_RETRIEVAL_PROFILE', 'semantic') if isinstance(env, dict) else None
    except (OSError, ValueError, AttributeError, plistlib.InvalidFileException):
        return None
    return value if value in PROFILES else None


def _job_run(argv, *, run, timeout):
    """One system command reduced to (return code, stdout, stderr); a timeout or OS error gives None.

    Callers only ever copy the command name and its return code into what we produce (§8).
    """
    try:
        result = run(argv, capture_output=True, text=True, timeout=timeout)
    except (subprocess.SubprocessError, OSError):
        return None, '', ''
    return result.returncode, result.stdout or '', result.stderr or ''


def _job_observation(service, *, run):
    code, stdout, stderr = _job_run(["launchctl", "print", f"gui/{os.getuid()}/{service['label']}"], run=run, timeout=5)
    if code is None:
        return {'state': 'unknown'}  # a timeout is never evidence of absence
    if code == 0:
        # A plist on disk alone cannot attest the currently loaded launchd job.
        # Check the launchctl output's program and first argument, not just a label.
        program = re.search(r"(?m)^\s*program = (.+?)\s*$", stdout)
        args = re.search(r"arguments = \{\s*\n([\s\S]*?)\n\s*\}", stdout)
        def unquote(text):
            return text.strip().strip('"')
        actual_args = [unquote(line) for line in args.group(1).splitlines() if line.strip()] if args else []
        if not program or not args or unquote(program.group(1)) != service['argv'][0] or actual_args != service['argv']:
            return {'state': 'foreign-or-unattested'}
        for key in ('MEMORY_V4_TOKEN_FILE', 'MEMORY_V4_DIR'):
            match = re.search(r"(?m)^\s*" + re.escape(key) + r" => (.+?)\s*$", stdout)
            if not match or unquote(match.group(1)) != service['env'][key]:
                return {'state': 'foreign-or-unattested'}
        def number(key):
            match = re.search(r'(?m)^\s*' + re.escape(key) + r' = (-?\d+)\s*$', stdout)
            return int(match[1]) if match else None
        process_state = re.search(r'(?m)^\s*state = (.+?)\s*$', stdout)
        return {'state': 'loaded', 'runs': number('runs'), 'last_exit_code': number('last exit code'),
                'pid': number('pid'), 'process_state': process_state[1] if process_state else None}
    if "Could not find service" in (stdout + stderr):
        return {'state': 'absent'}
    return {'state': 'unknown'}  # permissions/syntax/IPC failures are not evidence of absence


def _job_state(service, *, run):
    return _job_observation(service, run=run)['state']


def _startup_failure(observed, settings, home):
    """Structured, allow-listed facts about a crashing service (§8): never a line of its log.

    The raw log stays where the server wrote it, bytes unchanged; only its path, its size and
    class names of the closed list leave it.
    """
    from .diagnostics import exception_types
    path = settings.ops_dir / 'logs/server.err'
    facts = {key: observed.get(key) for key in ('state', 'runs', 'last_exit_code', 'pid', 'process_state')}
    facts.update(exception_types=[], log_path=str(path), log_bytes=None)
    try:
        _safe_path(path, home)
        with path.open('rb') as stream:
            stream.seek(0, 2)
            facts['log_bytes'] = stream.tell()
            stream.seek(max(0, facts['log_bytes'] - 8192))
            facts['exception_types'] = exception_types(stream.read(8192).decode('utf-8', errors='replace'))
    except (OSError, ValueError, RuntimeError):
        pass
    return facts


def _install_journals(root):
    """``install.json`` documents below a journal root, newest first; unreadable ones are skipped."""
    found = []
    try:
        directories = sorted((p for p in root.iterdir() if p.is_dir() and not p.is_symlink()), reverse=True)
    except OSError:
        return found
    for directory in directories:
        try:
            document = json.loads((directory / 'install.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if isinstance(document, dict):
            found.append((directory, document))
    return found


def prepare(settings, *, clients=DEFAULT_CLIENTS, home=None, python=None, backend=None, run=subprocess.run,
            profile=None, replace_existing=(), which=shutil.which):
    """Pure read-only preflight. Does not invoke setup/create_token or use client auth.

    ``profile``: explicit for a new install (R1: no default); an owned service gives its own
    profile, which is read and never converted.
    """
    from .. import readiness
    from . import provision, qualified_runtime
    from .auth import read_token
    import importlib.util
    if profile is not None and profile not in PROFILES:
        raise QuickstartError('Unknown install profile: choose lexical or semantic', code='setup_profile_unknown')
    minimum = provision.min_free_bytes()
    home = home or Path.home()
    python = python or str(Path(sys.executable).absolute())
    backend = _selected_backend(settings, home, backend)
    client_env = {"MEMORY_V4_URL": settings.url, "MEMORY_V4_TOKEN_FILE": str(settings.token_file),
                  "MEMORY_V4_OPS_DIR": str(settings.ops_dir)}
    blockers, blocker_codes = [], []

    def block(code, message):
        blocker_codes.append(code)
        blockers.append(message)
    installed = _installed_profile(settings, home)
    effective = profile or installed
    if effective is None:
        # New install without --profile: refused, never defaulted, and nothing else is evaluated.
        result = preview(home=home, python=python, env=client_env, clients=clients, port=settings.port,
                         token_file=settings.token_file, provision_plan=None, replace_existing=replace_existing)
        block('setup_profile_required', 'setup_profile_required: a new install needs --profile lexical or '
              '--profile semantic; no default is chosen')
        return {**result, 'ok': False, 'profile': None, 'blockers': blockers, 'blocker_codes': blocker_codes,
                'service': None, 'backend': backend, 'work_state': backend == 'sqlite', 'notices': [],
                'client_env': client_env, 'writes': 'none (preview)', 'stage': 'preview'}

    def resources_for(name):
        return provision.plan(resources=('cl100k',)) if name == 'lexical' else provision.plan()
    resource_plan = resources_for(effective)
    service = _service_plan(settings, home, python, resource_plan['env'], backend=backend, profile=effective)
    if service['state'] == 'foreign-or-different' and installed and installed != effective:
        # The existing definition may be ours under ITS profile: a conversion request, not a foreign service.
        other_plan = resources_for(installed)
        other = _service_plan(settings, home, python, other_plan['env'], backend=backend, profile=installed)
        if other['state'] == 'owned':
            block('setup_profile_conflict', f'setup_profile_conflict: this installation is {installed}; '
                  f'--profile {effective} is refused (a profile is read, never converted)')
            resource_plan, service = other_plan, other
    result = preview(home=home, python=python, env=client_env, clients=clients, port=settings.port,
                     token_file=settings.token_file, provision_plan=resource_plan,
                     replace_existing=replace_existing)
    if effective == 'lexical' and backend != 'sqlite':
        block('setup_profile_backend_conflict', 'setup_profile_backend_conflict: the lexical profile requires the sqlite backend')
    sqlite_report = sqlite_runtime.inspect()
    if backend == 'sqlite' and not sqlite_report.get('ok'):
        block(sqlite_report.get('code') or 'sqlite_runtime_unavailable',
              f"{sqlite_report.get('code')}: SQLite loaded by this interpreter is unfit "
              f"(version {sqlite_report.get('version')}, minimum {sqlite_report.get('minimum')}); nothing written")
    identity = readiness.build_identity()
    runtime = qualified_runtime.inspect_current(profile=effective, extras=('ops', 'brief'))
    if sys.version_info[:2] != (3, 11):
        block('runtime_unqualified', 'runtime_unqualified: macOS Python 3.11 closure required for quickstart')
    if sys.platform != 'darwin' or platform.machine().lower() not in {'arm64', 'aarch64'}:
        block('runtime_unqualified', 'macOS Apple Silicon launchd onboarding only; other platforms remain manual')
    if identity.get('identity_source') != 'embedded_descriptor' or not identity.get('commit') or identity.get('source_dirty'):
        block('build_unqualified', 'build_unqualified: install a qualified wheel; source/env identity is insufficient')
    if not runtime['ok']:
        from .. import __version__
        requirement, constraints = PROFILE_INSTALL[effective]
        block('runtime_unqualified', f"runtime_unqualified ({runtime.get('state', 'drift')}): package versions differ: "
              + ', '.join(sorted(runtime['mismatches']))
              + f'; install the exact qualified dependencies of the {effective} profile '
              + f'(pip install -c {constraints} "{requirement}=={__version__}"), then re-preview')
    if not Path(python).is_file() or not os.access(python, os.X_OK) or any(s in python for s in ('/cache/uv/', 'pip-build-env', 'uvx')):
        block('runtime_unqualified', 'absolute durable Python executable required (no uvx ephemeral runtime)')
    if importlib.util.find_spec('tiktoken') is None:
        block('runtime_unqualified', 'install memory-os-ai[ops,brief] before apply; no implicit dependency install')
    if result['unknown_clients']:
        block('client_unknown', 'unknown clients')
    conflicts = [c['client'] for c in result['clients'] if c['action'].startswith('refuse-existing')]
    if conflicts:
        block('client_entry_conflict', 'client_entry_conflict: an existing memory-os-ai entry is not proven to be ours for '
              + ', '.join(conflicts) + '; nothing is overwritten silently (name the client with --replace-existing, '
              'or edit it by hand)')
    if any(c['action'].startswith('refuse') and not c['action'].startswith('refuse-existing') for c in result['clients']):
        block('client_config_manual', 'manual client setup required; select only eligible clients')
    client_cli = {}
    if any(c['client'] == 'claude-code' and c['action'] in {'create', 'replace'} for c in result['clients']):
        client_cli['claude'] = which('claude')
        if not client_cli['claude']:
            block('client_cli_missing', 'client_cli_missing: the claude CLI is not on PATH; Claude Code is configured '
                  'only through claude mcp add --scope user')
    if 'claude-desktop' in clients:
        desktop, _, _ = _job_run(['pgrep', '-x', 'Claude'], run=run, timeout=5)
        if desktop != 1:
            block('client_app_running', 'quit Claude Desktop before apply; live config may be rewritten by the app')
    token = read_token(settings.token_file)  # strict permissions/owner/format, never returned
    required = max(minimum, resource_plan['peak_additional_bytes_upper_bound'] + 1_000_000_000)
    if result['disk_free_bytes'] < required:
        block('disk_guard', f'disk guard: {result["disk_free_bytes"]} bytes free < required {required} bytes '
              f'(effective minimum {minimum} bytes; provisioning peak + 1000000000 bytes margin)')
    loaded = _job_state(service, run=run) if sys.platform == 'darwin' else 'unsupported'
    service['loaded_state'] = loaded
    if service['state'] not in {'absent', 'owned'} or loaded in {'unknown', 'foreign-or-unattested'}:
        block('service_foreign', 'foreign/different/unattested service: no repair, stop or overwrite')
    if service['state'] == 'absent' and (loaded != 'absent' or result['port_state'] != 'free'):
        block('port_or_label_occupied', 'port or launchd label occupied; nothing activated')
    if service['state'] == 'owned' and loaded == 'absent':
        # An interrupted install (plist written, job never loaded) is said here, not discovered at apply.
        stamp = next((directory.name for directory, document in _install_journals(home / JOURNAL_SUBDIR)
                      if document.get('steps', {}).get('service', {}).get('created') is True
                      and document.get('service', {}).get('path') == service['path']), '<stamp>')
        block('service_not_loaded', 'service_not_loaded: the service definition exists but its job is not loaded '
              f'(interrupted install); roll it back with quickstart --undo {stamp} --with-service, then re-preview')
    if service['state'] == 'owned' and token is None:
        block('service_foreign', 'existing service token missing; no replacement token')
    if service['state'] == 'absent' and settings.store_dir.exists() and any(settings.store_dir.iterdir()):
        block('store_not_empty', 'store_not_empty: non-empty store: use the qualified upgrade runbook, never reset/migrate here')
    if backend == 'sqlite':
        from ..sqlite_store import validate_sqlite_target
        try:
            validate_sqlite_target(settings.store_dir)
        except ValueError:
            block('store_not_empty', 'SQLite target refused: choose a fresh non-legacy directory; no implicit migration')
    # Reject unexpected path redirects before any download/token write.
    for path in (settings.token_file, settings.store_dir / ('store.sqlite3' if backend == 'sqlite' else 'items.jsonl'),
                 settings.ops_dir / 'logs/server.err'):
        _safe_path(path, home)
    notices = []
    if 'claude-code' in clients and (Path.cwd() / '.mcp.json').exists():
        notices.append('Project .mcp.json can override user scope; inspect it before claiming a connection')
    if 'gemini-cli' in clients:
        notices.append('Gemini CLI folder trust is a separate user choice; configuration does not prove a connection')
    return {**result, 'ok': not blockers, 'profile': effective, 'identity': identity, 'service': service,
            'blockers': blockers, 'blocker_codes': blocker_codes, 'sqlite_runtime': sqlite_report, 'notices': notices,
            'disk_min_free_bytes': minimum, 'disk_required_free_bytes': required,
            'backend': backend, 'work_state': backend == 'sqlite', 'runtime_qualification': runtime,
            'client_env': client_env, 'client_cli': client_cli, 'replace_existing': sorted(replace_existing),
            'writes': 'none (preview)', 'stage': 'preview'}


def _sqlite_triplet(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', value):
        return None
    return tuple(int(part) for part in value.split('.'))


def _ready(settings, identity, *, backend='sqlite', profile='semantic'):
    """Readiness of the started service, exactly for the expected profile; None while it is not ready."""
    from .auth import http_get
    status, body = http_get(settings.ready_url, settings, timeout=2)
    if status not in (200, 503):
        return None
    try:
        response = json.loads(body)
    except (ValueError, TypeError):
        raise QuickstartError('Invalid readiness response') from None
    if response.get('commit') != identity['commit'] or response.get('version') != identity['version']:
        raise QuickstartError('Service build identity differs; no client config written')
    if status != 200:
        return None
    mismatch = 'service_profile_mismatch'
    if response.get('ready') is not True or response.get('reason') != 'ready':
        raise QuickstartError('Retrieval not ready (work-only is insufficient)')
    caps = response.get('capabilities') or {}
    store = caps.get('store') if isinstance(caps.get('store'), dict) else {}
    expected_backend = 'sqlite_s1' if backend == 'sqlite' else 'jsonl'
    if (store.get('backend') != expected_backend or not store.get('instance_id')
            or caps.get('work_state') is not (backend == 'sqlite')):
        raise QuickstartError('Existing store/profile differs; use qualified upgrade runbook', code=mismatch)
    policies = caps.get('policies') if isinstance(caps.get('policies'), dict) else {}
    encoding = caps.get('encoding') if isinstance(caps.get('encoding'), dict) else {}
    tokenizer_ready = bool((caps.get('brief') or {}).get('tokenizer', {}).get('ready'))
    if profile == 'lexical':
        from ..config import LEXICAL_MARKER, LEXICAL_POLICY
        if (caps.get('retrieval_profile') != LEXICAL_MARKER or policies.get('ranking_policy') != LEXICAL_POLICY
                or policies.get('bm25_profile') != 'scoped_literal' or policies.get('lexical_guard_k') != 3
                or encoding.get('state') != 'absent_by_profile' or encoding.get('profile') is not None
                or not tokenizer_ready):
            raise QuickstartError('Service is not exactly the lexical profile; no client config written', code=mismatch)
    else:
        encoder = encoding.get('profile') or {}
        from .provision import catalog
        from .qualified_runtime import compare
        if not compare(encoder.get('runtime') or {}, encoding_only=True)['ok']:
            raise QuickstartError('Server encoder runtime differs from qualified package versions', code=mismatch)
        if (encoding.get('requested_device') != 'auto' or encoder.get('dtype') != 'native'
                or encoder.get('batch_size') != 32 or encoder.get('model_revision') != catalog()['revision']
                or encoder.get('max_seq_length') != 512 or encoding.get('model') != catalog()['model_id']
                or policies.get('bm25_profile') != 'scoped_literal' or policies.get('lexical_guard_k') != 3
                or not tokenizer_ready):
            raise QuickstartError('Service profile or exact tokenizer differs', code=mismatch)
    if backend == 'sqlite':
        # The SERVER process attests the library it loaded; this interpreter's own check is not enough.
        version = _sqlite_triplet(store.get('sqlite_version'))
        if version is None:
            raise QuickstartError('The running server does not attest a readable SQLite version',
                                  code='sqlite_runtime_unattested')
        if version < tuple(sqlite_runtime.MIN_VERSION):
            raise QuickstartError('The running server loaded a SQLite older than the engine floor',
                                  code='sqlite_runtime_too_old')
    return response


async def _relay_probe(python, env, *, demo=False, expected=None):
    """Prove the packaged relay, not just direct HTTP. No mutation unless demo is explicit."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    import anyio
    with anyio.fail_after(60):
        params = StdioServerParameters(command=python, args=['-m', 'memory_os_v4.ops.relay'],
            env={**os.environ, **env, 'MEMORY_V4_CLIENT_ID': 'quickstart', 'MEMORY_V4_PROJECT': '*',
                 'PYTHONPATH': '', 'PYTHONNOUSERSITE': '1'})
        async with stdio_client(params) as (r, w), ClientSession(r, w) as session:
            init = await session.initialize()
            if expected and init.serverInfo.version != expected['version']:
                raise QuickstartError('Relay handshake version differs')
            tools = await session.list_tools()
            required = {'memory_status', 'memory_chat_save', 'memory_search', 'memory_request_status'}
            if expected and expected['capabilities'].get('work_state'):
                required |= {'memory_task_list', 'memory_task_create', 'memory_task_update'}
            if not required <= {t.name for t in tools.tools}:
                raise QuickstartError('Relay tool contract missing')
            status = await session.call_tool('memory_status', {'project': '*'})
            if status.isError:
                raise QuickstartError('Relay memory_status failed')
            deployed = json.loads(status.content[0].text)
            if expected and deployed.get('capabilities', {}).get('store', {}).get('instance_id') != expected['capabilities']['store']['instance_id']:
                raise QuickstartError('Relay connected to a different store identity')
            if expected and expected.get('_data_dir') and deployed.get('data_dir') != expected['_data_dir']:
                raise QuickstartError('Relay store directory differs from selected installation')
            result = {'connected': True, 'tool_count': len(tools.tools), 'real_client_usage': 'unmeasured'}
            if expected and expected['capabilities'].get('work_state'):
                listed = await session.call_tool('memory_task_list',
                    {'project': 'memory-os-quickstart-check-' + uuid.uuid4().hex, 'limit': 1})
                task_result = json.loads(listed.content[0].text) if listed.content else {}
                if listed.isError or task_result.get('ok') is not True or not isinstance(task_result.get('tasks'), list):
                    raise QuickstartError('SQLite work state list failed; no automatic task mutation')
                result['work_state_read_verified'] = True
            if not demo:
                return result
            project = 'memory-os-demo-' + uuid.uuid4().hex
            request = 'quickstart-demo-' + uuid.uuid4().hex
            key = 'handoff-' + uuid.uuid4().hex
            saved = await session.call_tool('memory_chat_save', {
                'project_label': project, 'request_id': request,
                'messages': [{'role': 'user', 'content': f'Demo decision: {key}.'}],
                'facts': [{'text': f'The demo project decision is {key}.'}]})
            if saved.isError:
                # Never retry: relay journals uncertain mutations; operator resolves status.
                raise QuickstartError(f'Demo not confirmed; inspect memory_request_status for {request}; never replay')
            searched = await session.call_tool('memory_search', {'project': project, 'query': key, 'top_k': 10})
            if searched.isError or key not in json.dumps(searched.model_dump()):
                raise QuickstartError('Demo saved but not found; no replay or automatic cleanup')
            result['demo'] = {'project': project, 'request_id': request, 'saved_and_found': True,
                              'handoff_instruction': 'Ask a second chosen assistant to search this demo project; real cross-client use is not measured.'}
            return result


def _bootout(service, *, run):
    """One ``launchctl bootout``; returns 'ok', 'failed' (non-zero code) or 'timeout'. Output is never kept."""
    try:
        result = run(['launchctl', 'bootout', f"gui/{os.getuid()}/{service['label']}"],
                     capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        return 'timeout'
    except (subprocess.SubprocessError, OSError):
        return 'failed'
    return 'ok' if result.returncode == 0 else 'failed'


def apply_install(plan, settings, *, download_approved=False, demo=False, home=None, run=subprocess.run,
                  probe=None, doctor_run=None, timeout=180, clock=time.monotonic):
    """Explicit apply. Journal every stage; failure preserves evidence, never repairs a foreign job.

    Stages: sqlite (WAL probe) -> resources -> token -> service -> service_ready -> clients -> checks
    -> demo (on request). Every duration comes from ``clock``. Diagnostics are structured and
    allow-listed (§8): no log line, subprocess output, doctor detail or foreign exception text.
    """
    from dataclasses import replace
    from . import provision, doctor
    from .auth import create_token, read_token
    from .diagnostics import exception_label
    import anyio
    home = home or Path.home()
    if not plan.get('ok'):
        raise QuickstartError('Preflight refused: ' + '; '.join(plan.get('blockers', [])), code='preflight_refused')
    if plan['provision']['download_bytes'] and not download_approved:
        raise QuickstartError('Resource download needs --download-approved; no token/service/config changed',
                              code='download_not_approved')
    profile = plan.get('profile') or 'semantic'
    # Revalidate every mutable input before resources or setup writes.
    cli_paths = plan.get('client_cli') or {}
    current = prepare(settings, clients=tuple(c['client'] for c in plan['clients']), home=home,
                      python=plan['python'], backend=plan['backend'], run=run, profile=profile,
                      replace_existing=tuple(plan.get('replace_existing', ())),
                      which=lambda name: cli_paths.get(name))
    if not current['ok'] or any(current[k] != plan[k] for k in ('identity', 'service', 'client_env')):
        raise QuickstartError('Preflight changed; re-preview, nothing activated', code='preflight_changed')
    if any(a['file_sha256'] != b['file_sha256'] for a, b in zip(current['clients'], plan['clients'])):
        raise QuickstartError('Client file changed; re-preview before apply', code='preflight_changed')
    if plan['service']['state'] == 'owned' and _ready(settings, plan['identity'], backend=plan['backend'],
                                                      profile=profile) is None:
        raise QuickstartError('Existing owned service not ready: no repair or config writes', code='service_not_ready')
    provision.guard(home)  # includes swap attestation even when all resources are already present
    journal = new_journal(home / JOURNAL_SUBDIR)
    service = plan['service']
    began = clock()
    progress = {'version': 1, 'stamp': journal.name, 'ok': False, 'stage': 'sqlite', 'steps': {},
                'profile': profile,
                'retained_on_undo': ['resources', 'token', 'service', 'store'], 'started_at': time.time(),
                'backend': plan['backend'], 'work_state': plan['work_state'],
                'runtime_qualification': plan['runtime_qualification'],
                'installation_identity': installation_identity(plan['client_env']),
                'service': {'label': service['label'], 'path': service['path'], 'argv': service['argv'],
                            'env': {key: service['env'][key] for key in ('MEMORY_V4_TOKEN_FILE', 'MEMORY_V4_DIR')},
                            'state_at_preview': service['state']},
                'paths': {'store': str(settings.store_dir), 'backups': str(settings.backup_dir),
                          'backup-key': str(settings.backup_key), 'token': str(settings.token_file),
                          'resources': {key: plan['provision']['env'][key]
                                        for key in ('HF_HUB_CACHE', 'MEMORY_V4_CL100K_PATH')
                                        if key in plan['provision'].get('env', {})},
                          'logs': str(settings.ops_dir / 'logs')},
                'ops_env': {**service['env'], 'MEMORY_V4_BACKUP_DIR': str(settings.backup_dir),
                            'MEMORY_V4_BACKUP_KEY': str(settings.backup_key)},
                'ops_notice': 'Use ops_env for doctor/health/backup in a fresh shell; quickstart never changes shell defaults.'}
    def record():
        _atomic_write(journal / 'install.json', json.dumps(progress, indent=1))
        (journal / 'install.json').chmod(0o600)

    def enter(stage):
        progress['stage'] = stage
        record()
        return clock()

    def leave(step, started, **facts):
        progress['steps'].setdefault(step, {}).update(elapsed_ms=1000 * (clock() - started), **facts)
    record()
    failure_code = 'sqlite_wal_unavailable'  # stable code of the step in progress, used for a foreign exception
    try:
        started = clock()
        if plan['backend'] == 'sqlite':
            # Functional proof, in the private journal directory, before resources, token and service.
            wal = sqlite_runtime.wal_probe(journal)
            if not wal.get('ok'):
                raise QuickstartError('SQLite cannot open a WAL database here; nothing activated',
                                      code=wal.get('code') or 'sqlite_wal_unavailable')
        leave('sqlite', started)
        failure_code = 'resources_failed'
        started = enter('resources')
        resources = provision.apply(plan['provision'], download_approved=download_approved)
        if not resources.get('ok') or not resources.get('ready'):
            raise QuickstartError('Resources not ready; no service activation', code='resources_failed')
        leave('resources', started, download_bytes=plan['provision']['download_bytes'])
        failure_code = 'token_failed'
        started = enter('token')
        created = create_token(settings.token_file)
        leave('token', started, created=created)
        failure_code = 'service_bootstrap_failed'
        started = enter('service')
        plist = Path(service['path'])
        if service['state'] == 'absent':
            if _sha(plist) != service['sha256'] or _port_state(settings.port) != 'free' or _job_state(service, run=run) != 'absent':
                raise QuickstartError('Port/job/plist changed; token retained, nothing overwritten', code='preflight_changed')
            if settings.store_dir.exists() and any(settings.store_dir.iterdir()):
                raise QuickstartError('Store changed; nothing activated', code='preflight_changed')
            settings.store_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            (settings.ops_dir / 'logs').mkdir(parents=True, exist_ok=True, mode=0o700)
            # No previous plist exists: create exclusively, never replace a race winner.
            from .auth import write_secret_file
            write_secret_file(plist, service['plist'].encode())
            progress['steps']['service'] = {'created': True, 'plist_sha256': _sha(plist)}
            record()
            code, _, _ = _job_run(['launchctl', 'bootstrap', f'gui/{os.getuid()}', str(plist)], run=run, timeout=15)
            if code != 0:
                raise QuickstartError('launchd bootstrap failed; plist/journal retained; no automatic repair '
                                      f'(roll back with quickstart --undo {journal.name} --with-service)',
                                      code='service_bootstrap_failed')
        leave('service', started)
        failure_code = 'service_startup_failed'
        start = enter('service')  # the stage stays "service" until the service answers ready
        ready = None
        for _ in range(int(timeout / .25) + 2):
            if clock() - start >= timeout:
                break
            try:
                provision.guard(home)
            except provision.ProvisionError:
                if (service['state'] == 'absent' and _sha(plist) == progress['steps']['service']['plist_sha256']
                        and _job_state(service, run=run) == 'loaded'):
                    # Only our newly created, still attested job is stopped on a resource breach.
                    progress['guard_stop'] = {'attempted': True, 'stopped': _bootout(service, run=run) == 'ok'}
                raise
            ready = _ready(settings, plan['identity'], backend=plan['backend'], profile=profile)
            if ready:
                break
            observed = _job_observation(service, run=run)
            if (observed['state'] == 'loaded' and (observed.get('runs') or 0) >= 1
                    and observed.get('last_exit_code') not in (None, 0)
                    and ((observed.get('runs') or 0) >= 2 or
                         (not observed.get('pid') and observed.get('process_state') != 'running'))):
                progress['startup_failure'] = _startup_failure(observed, settings, home)
                # Only our new, unchanged plist and re-attested job can be stopped.
                if (service['state'] == 'absent' and _sha(plist) == progress['steps']['service']['plist_sha256']
                        and _job_state(service, run=run) == 'loaded'):
                    progress['startup_failure']['stop_attempted'] = True
                    progress['startup_failure']['stopped'] = _bootout(service, run=run) == 'ok'
                raise QuickstartError('Server startup failed: ' + str(observed.get('runs')) +
                                      ' runs, last exit code ' + str(observed.get('last_exit_code')) +
                                      '; private server log kept, content not copied: ' +
                                      progress['startup_failure']['log_path'], code='service_startup_failed')
            time.sleep(.25)
        if not ready:
            raise QuickstartError('Readiness timeout; preserve logs/plist; no client config written',
                                  code='service_startup_failed')
        leave('service_ready', start)
        failure_code = 'client_write_failed'
        started = enter('clients')
        clients = apply_clients(plan, home=home, python=plan['python'], env=plan['client_env'],
                                journal_dir=journal, run=run)
        progress['clients'] = clients
        leave('clients', started)
        if any(r['status'] not in ('applied', 'unchanged') for r in clients['records']):
            raise QuickstartError('Client configuration refused; review retained journal, no forced write',
                                  code='client_write_failed')
        failure_code = 'doctor_failed'
        started = enter('checks')
        effective = replace(settings, service_label=service['label'])
        checks = (doctor_run or doctor.run)(effective)
        selected = {c['client'] for c in plan['clients']}
        checks = [c for c in checks if not c.area.startswith('client:') or c.area.split(':', 1)[1] in selected]
        # Areas and statuses only: details and hints may quote anything (run doctor again to read them).
        progress['doctor'] = [{'area': c.area, 'status': c.status} for c in checks]
        failed = [c.area for c in checks if c.status == 'FAIL']
        if failed:
            raise QuickstartError('Doctor failed in: ' + ', '.join(failed) + '; journal retained, connection not '
                                  'qualified (run memory-os-admin doctor for the details)', code='doctor_failed')
        if read_token(settings.token_file) is None:
            raise QuickstartError('Token vanished; no handshake attempted', code='doctor_failed')
        failure_code = 'relay_probe_failed'
        ready = {**ready, '_data_dir': str(settings.store_dir)}
        async def connect():
            return await (probe or _relay_probe)(plan['python'], plan['client_env'], demo=False, expected=ready)
        progress['relay'] = anyio.run(connect)
        leave('checks', started)
        if progress['relay'].get('connected') is not True:
            raise QuickstartError('Relay connection not proven', code='relay_probe_failed')
        if demo:
            started = enter('demo')
            async def demonstrate():
                return await (probe or _relay_probe)(plan['python'], plan['client_env'], demo=True, expected=ready)
            progress['demo'] = anyio.run(demonstrate)
            leave('demo', started)
        progress.update(ok=True, stage='complete', clients_restart_required=True, real_client_usage='unmeasured')
        progress['client_next_steps'] = [{'client': item['client'], 'state': 'configured',
                                        'action': 'restart client, check MCP tools, then perform an explicit save/search',
                                        'note': item['note']} for item in plan['clients']]
    except Exception as exc:  # MCP/AnyIO can wrap a failure in an ExceptionGroup; keep journal, never replay
        ours = isinstance(exc, (QuickstartError, provision.ProvisionError))
        # Our own messages are built without foreign content; anything else is reduced to a listed class name.
        progress['error'] = str(exc) if ours else exception_label(exc)
        code = getattr(exc, 'code', None) if isinstance(exc, QuickstartError) else None
        progress['code'] = code if code and code != 'quickstart_refused' else failure_code
    finally:
        steps = progress['steps']
        progress['timing'] = {'profile': profile, 'download_bytes': plan['provision']['download_bytes'],
                              'steps_ms': {name: steps[name]['elapsed_ms'] for name in steps if 'elapsed_ms' in steps[name]},
                              'total_s': clock() - began, 'clock': 'monotonic'}
        progress['elapsed_s'] = progress['timing']['total_s']
        record()
    return progress

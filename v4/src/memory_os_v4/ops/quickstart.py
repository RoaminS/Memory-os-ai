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
import stat
import subprocess
import sys
import tempfile
import time
import re
import uuid
import platform  # noqa: F401  (read through ops/cell.py; install fixtures patch qs.platform.machine)
from pathlib import Path

from . import cell, sqlite_runtime
from . import service as managers
from .clients import (
    CLIENTS,
    SERVER,
    Client,
    ClientPathError,
    classify,
    client_notices,
    read_entry,
    relay_command,
    strict_loads,
)

DEFAULT_CLIENTS = ("claude-code", "codex")
JOURNAL_ROOT = Path("~/.local/state/memory-os/quickstart")
JOURNAL_SUBDIR = ".local/state/memory-os/quickstart"  # below the selected HOME
SENSITIVE = ("TOKEN", "KEY", "SECRET", "PASSWORD", "BEARER")


def parse_clients_arg(value, default=DEFAULT_CLIENTS):
    """The single ``--clients`` rule: absent or empty -> ``default``; otherwise an exact ``split(',')``.

    Nothing is stripped or case-folded: an inexact name stays in the tuple and is refused afterwards as
    an unknown client. ``scripts/memory_os_bootstrap.py`` (stdlib only) carries a copy of this rule.
    """
    return tuple(value.split(',')) if value else default


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
    except OSError:
        return "unreadable"  # e.g. mode 000: a refusal for the caller, never a crash of a preview


def _read(path: Path) -> str:
    """Text of a client file with its line endings untouched (CRLF stays CRLF)."""
    return path.read_bytes().decode("utf-8")


def _eol(text: str | None) -> str:
    return "\r\n" if text and "\r\n" in text else "\n"


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
    return json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)


def _entry_hash(entry: dict) -> str:
    # default=str: a TOML entry may hold a date; it is foreign, it only has to be hashable.
    return hashlib.sha256(json.dumps(entry, sort_keys=True, default=str).encode()).hexdigest()


def installation_identity(env: dict) -> str:
    """Destination identity: WHICH service an entry points to (URL + token file).

    It is not a proof of ownership: anyone can write these two values in a configuration.
    """
    document = {"url": env.get("MEMORY_V4_URL"), "token_file": env.get("MEMORY_V4_TOKEN_FILE")}
    return hashlib.sha256(json.dumps(document, sort_keys=True).encode()).hexdigest()


def _port_state(port: int, *, timeout: float = 0.5) -> str:
    """``listening`` | ``free`` | ``unknown`` (``ops/service.py``: ``free`` is only a refused connection).

    A timeout, a permission error or any other failure of the probe is ``unknown``: it is never
    a free port, so it never lets an install start nor a stop be called confirmed.
    """
    return managers.port_state(port, timeout=timeout)


# ---------- recognised form, destination identity, proof of ownership (SETUP_4_1_0.md §5.2) ----------

RELAY_ARGS = ["-m", "memory_os_v4.ops.relay"]
RELAY_ENV = frozenset({"MEMORY_V4_CLIENT_ID", "MEMORY_V4_URL", "MEMORY_V4_TOKEN_FILE", "MEMORY_V4_OPS_DIR"})
INTERPRETER = re.compile(r"python(3(\.[0-9]+)?)?")
# Transport keys a client adds around command/args/env, with the only value we write for each.
TRANSPORT_KEYS = {"type": "stdio", "source": "custom", "enabled": True}


def relay_form(entry) -> bool:
    """Strict recognised form: an ABSOLUTE interpreter running exactly ``-m memory_os_v4.ops.relay``.

    Anything else is a loose form, even when ``clients.classify`` calls it ``relay``: wrapper,
    foreign program, extra or leading argument, relative interpreter, console script, legacy
    script, an entry also carrying a URL, an extra variable. A form is not a proof of ownership.
    """
    if not isinstance(entry, dict) or set(entry) - {"command", "args", "env", *TRANSPORT_KEYS}:
        return False
    if any(key in entry and entry[key] != value for key, value in TRANSPORT_KEYS.items()):
        return False
    command = entry.get("command")
    if not isinstance(command, str) or not os.path.isabs(command) or not INTERPRETER.fullmatch(os.path.basename(command)):
        return False
    if entry.get("args") != RELAY_ARGS:
        return False
    env = entry.get("env", {})
    return isinstance(env, dict) and not set(env) - RELAY_ENV and all(isinstance(v, str) for v in env.values())


def entry_identity(entry) -> str | None:
    """Destination identity of an entry carrying both variables, else None. Not a proof of ownership."""
    env = entry.get("env") if isinstance(entry, dict) else None
    if not isinstance(env, dict):
        return None
    if not all(isinstance(env.get(key), str) for key in ("MEMORY_V4_URL", "MEMORY_V4_TOKEN_FILE")):
        return None
    return installation_identity(env)


def _private(path: Path, mode: int, *, directory: bool = False) -> bool:
    """Owned by this user, exact mode, not a link; a file must also have a single hard link."""
    try:
        info = path.lstat()
    except OSError:
        return False
    kind_ok = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    return (kind_ok and stat.S_IMODE(info.st_mode) == mode and info.st_uid == os.getuid()
            and (directory or info.st_nlink == 1))


def journal_trusted(directory: Path) -> bool:
    """Trusted journal (§6): private directory, private single-link files, no symbolic link on the path."""
    root = directory.parent
    if directory.is_symlink() or any(p.is_symlink() for p in [root, *root.parents]):
        return False
    try:
        root_info = root.lstat()
    except OSError:
        return False
    if not stat.S_ISDIR(root_info.st_mode) or root_info.st_uid != os.getuid() or root_info.st_mode & 0o022:
        return False
    if not _private(directory, 0o700, directory=True):
        return False
    names = [name for name in ("journal.json", "install.json") if (directory / name).exists() or (directory / name).is_symlink()]
    return bool(names) and all(_private(directory / name, 0o600) for name in names)


def _trusted_journals(journal_root: Path):
    """(path of journal.json, document) for every trusted client journal, in path order."""
    for path, document, _ in _trusted_journal_files(journal_root):
        yield path, document


def _trusted_journal_files(journal_root: Path):
    """(path, document, SHA-256 of the bytes read) for every trusted client journal, in path order."""
    try:
        directories = sorted(p for p in journal_root.iterdir())
    except OSError:
        return
    if journal_root.is_symlink():
        return
    for directory in directories:
        path = directory / "journal.json"
        if not journal_trusted(directory) or not path.is_file():
            continue
        try:
            data = path.read_bytes()
            document = json.loads(data.decode("utf-8"))
        except (OSError, ValueError, RecursionError):
            continue
        if isinstance(document, dict) and isinstance(document.get("records"), list):
            yield path, document, hashlib.sha256(data).hexdigest()


def _record_digest(record: dict) -> str | None:
    """Digest of the entry a journal record applied; a 4.0.9 record holds the entry itself."""
    if "applied_entry_sha256" in record:
        value = record["applied_entry_sha256"]
        return value if isinstance(value, str) else None
    applied = record.get("applied_entry")
    return _entry_hash(applied) if isinstance(applied, dict) else None


def _record_proves(record: dict, client_key: str, digest: str, identity: str) -> bool:
    if not isinstance(record, dict) or record.get("client") != client_key or record.get("status") != "applied":
        return False
    if record.get("revoked") or _record_digest(record) != digest:
        return False
    if "applied_entry_sha256" in record:
        return record.get("installation_identity") == identity
    return entry_identity(record.get("applied_entry")) == identity  # 4.0.9 journal: the exact entry is the binding


def proof_of_ownership(journal_root: Path, client_key: str, entry, identity: str) -> list:
    """Trusted journals proving that THIS installation wrote ``entry`` for THIS client (maybe none)."""
    if not isinstance(entry, dict) or not relay_form(entry) or entry_identity(entry) != identity:
        return []
    digest = _entry_hash(entry)
    return [path for path, document in _trusted_journals(journal_root)
            if any(_record_proves(record, client_key, digest, identity) for record in document["records"])]


def revoke_proofs(journal_root: Path, client_key: str, digest: str, by: str, *, progress: list | None = None) -> int:
    """Durably revoke every record (same client, same digest) of every trusted journal; returns their count.

    A proof is consumed by the removal it authorises. Journals are rewritten in path order through
    ``_atomic_write`` (mode kept). A write failure propagates: revocations already written stay
    (they are never undone) and are counted in ``progress``.
    """
    done = progress if progress is not None else []
    for path, document, read_sha256 in _trusted_journal_files(journal_root):
        changed = False
        for record in document["records"]:
            if (isinstance(record, dict) and record.get("client") == client_key
                    and _record_digest(record) == digest and not record.get("revoked")):
                record.update(revoked=True, revoked_by=by)
                changed = True
        if changed:
            # The journal must still be the bytes this rewrite was built from (another run of the tool).
            _atomic_write(path, json.dumps(document, indent=1, ensure_ascii=False), expected=read_sha256)
            done.append(str(path))
    return len(done)


# ---------- client formats ----------

class _DuplicateKey(ValueError):
    pass


def _no_duplicates(pairs):
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise _DuplicateKey("duplicate key")
    return dict(pairs)


def _json_has_duplicate_keys(text: str) -> bool:
    try:
        json.loads(text or "{}", object_pairs_hook=_no_duplicates)
    except _DuplicateKey:
        return True
    except ValueError:
        return False
    return False


def _dump_json(data, original: str | None) -> str:
    """Serialise keeping the indentation unit, the final newline and non-ASCII text of ``original``."""
    if original is None or not original.strip():
        return json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    unit = re.search(r"\n([ \t]+)\S", original)
    text = json.dumps(data, indent=unit.group(1) if unit else None, ensure_ascii=False)
    return (text + ("\n" if original.endswith("\n") else "")).replace("\n", _eol(original))


def _toml_block(client: Client, entry: dict) -> str:
    lines = [f"[mcp_servers.{SERVER}]", f"command = {json.dumps(entry['command'])}", f"args = {json.dumps(entry['args'])}"]
    if client.fmt == "grok-toml":
        lines.append("enabled = true")
    lines.append(f"[mcp_servers.{SERVER}.env]")
    lines += [f"{json.dumps(k)} = {json.dumps(v)}" for k, v in entry.get("env", {}).items()]
    return "\n".join(lines) + "\n"


def _toml_candidate(client: Client, prior: str, entry: dict) -> tuple[str, str] | None:
    """(new text, appended text) when appending keeps the file valid, else None.

    The candidate is parsed BEFORE any write and accepted only if its data equal the previous
    data plus our entry; the previous bytes are a prefix of the new file.
    """
    import copy
    import tomllib
    appended = ("" if not prior or prior.endswith("\n") else "\n") + ("\n" if prior else "") + _toml_block(client, entry)
    appended = appended.replace("\n", _eol(prior))
    try:
        before = tomllib.loads(prior)
        after = tomllib.loads(prior + appended)
    except (ValueError, TypeError):
        return None
    expected = copy.deepcopy(before)
    servers = expected.setdefault("mcp_servers", {})
    if not isinstance(servers, dict) or SERVER in servers:
        return None
    servers[SERVER] = entry
    return (prior + appended, appended) if after == expected else None


def _json_key(client: Client) -> str:
    return {"vscode": "servers", "zed": "context_servers"}.get(client.fmt, "mcpServers")


def _entry_in_text(client: Client, text: str) -> dict | None:
    """Our server entry inside a copy of a JSON client file (used to read a private backup)."""
    from .clients import _jsonc
    data = _jsonc(text) if client.fmt == "zed" else strict_loads(text or "{}")
    servers = data.get(_json_key(client), {}) if isinstance(data, dict) else {}
    entry = servers.get(SERVER) if isinstance(servers, dict) else None
    return entry if isinstance(entry, dict) else None


# ---------- preview (Q1: zero writes) ----------

def preview(*, home: Path, python: str, env: dict[str, str], clients: tuple[str, ...] = DEFAULT_CLIENTS,
            port: int = 8765, token_file: Path | None = None, provision_plan: dict | None = None,
            replace_existing: tuple[str, ...] = (), journal_root: Path | None = None) -> dict:
    """Read-only plan. ``provision_plan`` = result of provision.plan() (computed by the caller, without network).

    An existing entry that is not exactly ours is never replaced silently (§5.2): only a strict
    relay form pointing to this installation AND proven by a trusted journal is replaced without
    asking; everything else needs the client named in ``replace_existing``. TOML is never rewritten.
    """
    selected = [c for c in CLIENTS if c.key in clients]
    unknown = sorted(set(clients) - {c.key for c in selected})
    stray = sorted(set(replace_existing) - set(clients))
    if stray:
        raise QuickstartError("Consent names a client that is not selected: " + ", ".join(stray),
                              code="client_consent_unselected")
    journal_root = journal_root or home / JOURNAL_SUBDIR
    token_file = token_file or home / ".config/memory-os/http.token"
    token_state = "absent"
    if token_file.exists():
        mode = token_file.stat().st_mode & 0o777
        token_state = "ok" if mode == 0o600 else f"bad-permissions:{oct(mode)}"
    disk_free = shutil.disk_usage(home).free
    identity = installation_identity(env)
    plan_clients = []
    for c in selected:
        want = desired_entry(c, python, env)
        try:
            path = c.config_path(home)
        except ClientPathError as exc:
            plan_clients.append({"client": c.key, "name": c.name, "path": None, "file_sha256": None,
                                 "current_class": "absent", "desired": _mask(want),
                                 "desired_sha256": _entry_hash(want),
                                 "installation_identity": identity, "current_identity": None,
                                 "ownership": "unproven",
                                 "action": "refuse-" + exc.code.replace("_", "-"),
                                 "refusal_code": exc.code, "refusal": str(exc), "note": c.note})
            continue
        _safe_path(path, home)
        state, current = read_entry(c, home)
        kind = classify(current)
        current_identity = entry_identity(current)
        proven = bool(current) and bool(proof_of_ownership(journal_root, c.key, current, identity))
        raw = None
        if path.exists():
            try:
                raw = _read(path)
            except (OSError, ValueError):
                raw = None
        if raw is not None and not c.fmt.endswith("toml") and c.fmt != "zed" and _json_has_duplicate_keys(raw):
            action = "refuse-json-duplicate-keys"  # at any level: a duplicate could mask an entry
        elif state == "unreadable":
            action = "refuse-unreadable"
        elif _same(current, want):
            action = "unchanged"
        elif c.fmt == "zed" and raw is not None and _is_jsonc(raw):
            action = "refuse-jsonc-manual"  # comments: never rewrite the user's file
        elif c.fmt.endswith("toml"):
            if current is not None:
                action = "refuse-toml-existing-manual"  # never rewritten, no consent possible
            elif _toml_candidate(c, raw or "", want) is None:
                action = "refuse-toml-structure-manual"
            else:
                action = "create"
        elif raw is not None and c.fmt != "zed" and _json_has_duplicate_keys(raw):
            action = "refuse-json-duplicate-keys"
        elif current is None:
            action = "create"
        elif c.key in replace_existing:
            action = "replace"  # named consent for this client only
        elif kind == "relay" and current_identity == identity:
            action = "replace" if proven else "refuse-existing-relay-unproven"
        elif kind == "relay":
            action = "refuse-existing-relay-other-installation"
        else:
            action = "refuse-existing-" + kind
        plan_clients.append({"client": c.key, "name": c.name, "path": str(path), "file_sha256": _sha(path),
                             "current_class": kind, "desired": _mask(want),
                             "desired_sha256": _entry_hash(want),
                             "installation_identity": identity, "current_identity": current_identity,
                             "ownership": "journal" if proven else "unproven",
                             "action": action, "note": c.note})
    return {"version": 1, "created_at": time.time(), "platform": sys.platform, "python": python,
            "unknown_clients": unknown, "disk_free_bytes": disk_free, "port": port, "port_state": _port_state(port),
            "token": {"path": str(token_file), "state": token_state},
            "provision": provision_plan, "clients": plan_clients,
            "downloads_required_bytes": (provision_plan or {}).get("download_bytes", 0),
            "writes": "none (preview)"}


def _is_jsonc(raw: str) -> bool:
    try:
        json.loads(raw or "{}")
    except ValueError:
        return True
    return False


# ---------- client writes ----------

class _Sentinel:
    def __init__(self, name: str):
        self.name = name

    def __repr__(self) -> str:
        return self.name


ABSENT = _Sentinel("ABSENT")        # the target must not exist at the last moment
UNGUARDED = _Sentinel("UNGUARDED")  # only a journal file inside a fresh private directory created by THIS run
_ANY = _Sentinel("ANY")             # (callers of _write_client only) no preview digest to compare with
JOURNAL_FILES = frozenset({"journal.json", "install.json", "uninstall.json"})
_FRESH_JOURNALS: set[str] = set()   # directories created by new_journal() in this process; only ever added to


class RemovalUnconfirmed(QuickstartError):
    """The ``claude`` CLI returned zero but the file read back does not hold the expected state."""

    def __init__(self, message: str = "claude CLI returned zero but the expected entry state is not confirmed"):
        super().__init__(message)


class EntryChanged(QuickstartError):
    """The target is no longer the one the caller was authorised to change; nothing was written."""

    def __init__(self, message: str = "The memory-os-ai entry changed; it is not removed"):
        super().__init__(message, code="client_entry_changed")


def _expected_of(digest: str | None):
    """Expectation for a target whose bytes were read with digest ``digest`` (None: there was no file)."""
    return ABSENT if digest is None else digest


def _read_verified(path: Path) -> bytes | None:
    """THE last-look read, shared by every mutation path. It NEVER follows a link.

    Returns None when ``lstat`` says the name does not exist (a dangling symbolic link is not
    absent), or the bytes of a regular file with a single hard link, read through a descriptor
    opened with ``O_NOFOLLOW | O_NONBLOCK`` whose identity (device, inode) is the one ``lstat`` saw
    before and still sees after the read. Any other object (symbolic link, directory, FIFO,
    several hard links, a file swapped during the read) raises ``EntryChanged``.
    """
    changed = EntryChanged("The target is not the plain file expected; nothing written")
    try:
        before = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError:
        raise changed from None
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise changed
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)  # never blocks on a FIFO swapped in
    except OSError:
        raise changed from None
    try:
        opened = os.fstat(fd)
        blocks = []
        while True:
            block = os.read(fd, 1 << 16)
            if not block:
                break
            blocks.append(block)
    except OSError:
        raise changed from None
    finally:
        os.close(fd)
    try:
        after = os.lstat(path)
    except OSError:
        raise changed from None
    if (len({(info.st_dev, info.st_ino) for info in (before, opened, after)}) != 1
            or not stat.S_ISREG(after.st_mode) or after.st_nlink != 1):
        raise changed
    return b"".join(blocks)


def _verify_target(path: Path, expected) -> None:
    """Last look at the target, immediately before ``os.replace`` (see ``_read_verified``).

    ``ABSENT``: the name must not exist. A digest: a plain single-link file with exactly these
    bytes. ``UNGUARDED``: absent, or a plain single-link file. Otherwise ``EntryChanged``.
    """
    data = _read_verified(path)
    if expected is UNGUARDED:
        return
    found = ABSENT if data is None else hashlib.sha256(data).hexdigest()
    if found != expected:
        raise EntryChanged("The target changed while the write was prepared; nothing written")


_UNSET = _Sentinel("UNSET")


def _claude_guard(client: Client, home: Path, *, file_sha256=_UNSET, entry_sha256=_UNSET, name_absent=False):
    """Pre-command check for Claude Code, immediately before EACH mutating ``claude mcp`` command.

    Same last look as every other mutation (``_read_verified``: never follows a link; a dangling
    link, a directory, a FIFO or a multi-link file is refused), then, on the bytes just read:
    ``file_sha256`` (None: the file must not exist), ``entry_sha256`` (our entry must be exactly
    this one), ``name_absent`` (our name must not be in the user-scope table; true absence of the
    file qualifies). Returns our current entry. Any mismatch raises ``EntryChanged``: no further
    command is run and what is there is left as it is.
    """
    path = client.config_path(home)
    data = _read_verified(path)
    if file_sha256 is not _UNSET and (None if data is None else hashlib.sha256(data).hexdigest()) != file_sha256:
        raise EntryChanged("The client file changed since the preview; nothing written")
    entry = None
    if data is not None and data.strip():
        try:
            # Strict: a duplicated key at ANY level is refused (a second ``mcpServers`` table or a second
            # ``memory-os-ai`` key could mask an entry behind the last value).
            document = strict_loads(data.decode("utf-8"))
            servers = document.get(_json_key(client), {}) if isinstance(document, dict) else None
            if not isinstance(servers, dict):
                raise ValueError("unexpected server table")
            present = SERVER in servers  # KEY MEMBERSHIP: a null value is a present name, not an absence
            entry = servers.get(SERVER)
        except (ValueError, RecursionError):
            raise EntryChanged("The client file is no longer readable; nothing written") from None
    else:
        present = False
    if name_absent and present:
        raise EntryChanged("A memory-os-ai entry appeared before the claude command; nothing written")
    if present and not isinstance(entry, dict):
        raise EntryChanged("The memory-os-ai entry is not an object; nothing written")
    if entry_sha256 is not _UNSET and (not isinstance(entry, dict) or _entry_hash(entry) != entry_sha256):
        raise EntryChanged()
    return entry


def _atomic_write(path: Path, text: str, *, expected) -> None:
    """Prepare first, verify last, then replace. ``expected`` is REQUIRED and explicit:

    - a SHA-256 hex string: the target must still have exactly these bytes;
    - ``ABSENT``: the target must not exist;
    - ``UNGUARDED``: accepted only for ``journal.json`` / ``install.json`` / ``uninstall.json`` inside a
      private 0700 directory that ``new_journal`` created in this very run; anywhere else it is refused.

    There is no default and no state shared between calls: a forgotten expectation is a
    ``TypeError``, never an unprotected write. The temporary file is fully prepared (write, flush,
    fsync, close, chmod) BEFORE the target is looked at one last time; the comparison is the very
    last step, with nothing but ``os.replace`` after it. A mismatch unlinks the temporary file
    and raises ``EntryChanged``. A target that is a symbolic link, not a regular file, or has
    several hard links is refused before the preparation AND at the last look (``_verify_target``,
    which uses ``lstat`` and ``O_NOFOLLOW``): every alias of a file is either the same single-link
    path or a refusal, including one that appears while the temporary file is prepared.
    """
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise QuickstartError("Unsafe write target")
    if path.exists() and path.stat().st_nlink != 1:
        raise QuickstartError("Unsafe write target: several hard links")
    if expected is UNGUARDED:
        directory = path.parent
        if (path.name not in JOURNAL_FILES or str(directory.resolve()) not in _FRESH_JOURNALS
                or not _private(directory, 0o700, directory=True)):
            raise QuickstartError("Unguarded write refused outside a fresh private journal of this run")
    elif expected is not ABSENT and not (isinstance(expected, str) and re.fullmatch(r"[0-9a-f]{64}", expected)):
        raise QuickstartError("Invalid write expectation")
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        _verify_target(path, expected)  # never follows a link; nothing but os.replace after it
        os.replace(tmp, path)
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _claude(argv: list[str], home: Path, *, run) -> int | None:
    """Run the ``claude`` CLI against the selected HOME; only its return code is kept (None: timeout)."""
    try:
        return run(["claude", *argv], capture_output=True, text=True, timeout=30,
                   env={**os.environ, "HOME": str(home)}).returncode
    except subprocess.SubprocessError:
        return None


def _claude_add(client: Client, home: Path, entry: dict, *, run, file_sha256=_UNSET) -> int | None:
    """``claude mcp add`` of ``entry``, guarded immediately before the command: our name must be ABSENT
    from a verified plain file, or the file truly absent (an entry or an object put there by someone
    else is never replaced)."""
    _claude_guard(client, home, file_sha256=file_sha256, name_absent=True)
    flags = [x for k, v in entry.get("env", {}).items() for x in ("--env", f"{k}={v}")]
    return _claude(["mcp", "add", "--scope", "user", "--transport", "stdio", *flags, SERVER, "--",
                    entry["command"], *entry["args"]], home, run=run)


def _write_client(client: Client, home: Path, entry: dict, *, run=subprocess.run,
                  expected_file_sha256=_ANY, produced: list | None = None) -> str:
    """Write OUR entry; the target is verified immediately before each mutation.

    JSON / TOML: the candidate is built from the bytes read here, whose digest must be
    ``expected_file_sha256`` when one is given (None: no file); the temporary file is prepared,
    and the target must still have the digest of those bytes at the last moment before
    ``os.replace`` (``_atomic_write``). Claude Code: ``_claude_guard`` runs immediately before
    ``claude mcp remove`` (file digest of the preview, else the entry found) and before every
    ``claude mcp add`` (our name absent): the add of a create, the add after a remove, and the
    best-effort return to a previous entry. Any mismatch raises ``EntryChanged``: no further command.
    """
    path = client.config_path(home)
    _safe_path(path, home)
    if client.fmt == "claude-user":
        preview_sha = _UNSET if expected_file_sha256 is _ANY else expected_file_sha256
        existing = _claude_guard(client, home, file_sha256=preview_sha)
        if existing is not None:
            # Immediately before the remove: the file of the preview, and the entry just read.
            _claude_guard(client, home, file_sha256=preview_sha, entry_sha256=_entry_hash(existing))
            if _claude(["mcp", "remove", "--scope", "user", SERVER], home, run=run) != 0:
                raise QuickstartError("claude mcp remove refused; previous entry retained")
            code = _claude_add(client, home, entry, run=run)
        else:
            code = _claude_add(client, home, entry, run=run, file_sha256=preview_sha)
        if code != 0:
            if existing is not None and relay_form(existing) and existing != entry:
                # Best effort: put back a previous entry of OUR strict form (no secret, plain shape).
                # Any other previous entry stays in the private backup only: the CLI cannot express it.
                try:
                    _claude_add(client, home, existing, run=run)
                except EntryChanged:
                    pass  # someone wrote an entry or put another object meanwhile: theirs stays
            raise QuickstartError(f"claude mcp add failed ({code})")
        return "claude-cli"
    try:
        data_bytes = path.read_bytes()
    except FileNotFoundError:
        data_bytes = None
    built_from = None if data_bytes is None else hashlib.sha256(data_bytes).hexdigest()
    if expected_file_sha256 is not _ANY and built_from != expected_file_sha256:
        raise EntryChanged("The client file changed since the preview; nothing written")
    raw = None if data_bytes is None else data_bytes.decode("utf-8")
    if client.fmt.endswith("toml"):
        candidate = _toml_candidate(client, raw or "", entry)
        if candidate is None:
            raise QuickstartError("TOML structure cannot be extended safely; apply the snippet by hand")
        if produced is not None:  # digest of the bytes WE produce, taken before they are written
            produced.append(hashlib.sha256(candidate[0].encode("utf-8")).hexdigest())
        _atomic_write(path, candidate[0], expected=_expected_of(built_from))
        return "toml-append"
    if raw is not None and _json_has_duplicate_keys(raw):
        raise QuickstartError("JSON config with duplicate keys; edit it by hand")
    data = strict_loads(raw or "{}") if raw is not None else {}
    servers = data.setdefault(_json_key(client), {}) if isinstance(data, dict) else None
    if not isinstance(servers, dict) or (SERVER in servers and not isinstance(servers[SERVER], dict)):
        raise QuickstartError("Unexpected server table or entry; edit the file by hand")
    servers[SERVER] = entry
    text = _dump_json(data, raw)
    if produced is not None:  # digest of the bytes WE produce, taken before they are written
        produced.append(hashlib.sha256(text.encode("utf-8")).hexdigest())
    _atomic_write(path, text, expected=_expected_of(built_from))
    return "json-merge"


def _expect(entry, expected_sha256: str | None) -> None:
    if expected_sha256 is not None and (not isinstance(entry, dict) or _entry_hash(entry) != expected_sha256):
        raise EntryChanged()


def removal_text(client: Client, home: Path, *, previous: dict | None = None,
                 toml_appended: str | None = None, expected_sha256: str | None = None) -> str | None:
    return _removal(client, home, previous=previous, toml_appended=toml_appended,
                    expected_sha256=expected_sha256)[1]


def _removal(client: Client, home: Path, *, previous: dict | None = None, toml_appended: str | None = None,
             expected_sha256: str | None = None) -> tuple[str | None, str | None]:
    """(text read, text to write): the file after OUR entry is removed (or ``previous`` put back).

    ``expected_sha256``: digest of the entry the caller may remove. It is compared with the entry
    found IN THE TEXT READ HERE, the very text the candidate is built from: an entry that changed
    since the authorisation raises ``EntryChanged`` and nothing is written. Both are None for Claude Code.

    Computed and VALIDATED before anything is written or revoked; raises ``QuickstartError`` when
    the removal cannot be done without touching foreign content: JSON with duplicate keys, a TOML
    whose result does not parse back to the previous data minus our entry, a previous entry the
    ``claude`` CLI cannot express (only our own strict relay form is put back through it).
    """
    path = client.config_path(home)
    _safe_path(path, home)
    if client.fmt == "claude-user":
        if previous is not None and not relay_form(previous):
            raise QuickstartError("The previous Claude Code entry cannot be restored through the claude CLI; "
                                  "it is kept in the private backup of the journal: restore it by hand")
        _claude_guard(client, home, **({} if expected_sha256 is None else {"entry_sha256": expected_sha256}))
        return None, None
    text = _read(path)
    if client.fmt.endswith("toml"):
        # Only absent entries can be appended; remove their exact text or table spans,
        # leaving unrelated TOML/application edits intact.
        import copy
        import tomllib
        if previous is not None:
            raise QuickstartError("Existing TOML entry was not eligible for automatic apply")
        if toml_appended and text.count(toml_appended) == 1:
            candidate = text.replace(toml_appended, "", 1)
        else:
            retained, skipping = [], False
            for line in text.splitlines(keepends=True):
                if line.lstrip().startswith("["):
                    header = line.strip().split("#", 1)[0].strip()
                    skipping = header in {f"[mcp_servers.{SERVER}]", f"[mcp_servers.{SERVER}.env]"}
                if not skipping:
                    retained.append(line)
            candidate = "".join(retained)
        try:
            expected = copy.deepcopy(tomllib.loads(text))
            servers = expected.get("mcp_servers")
            if not isinstance(servers, dict) or SERVER not in servers:
                raise ValueError("entry not in the expected table")
            _expect(servers[SERVER], expected_sha256)
            del servers[SERVER]
            after = tomllib.loads(candidate)
            if not servers and "mcp_servers" not in after:
                del expected["mcp_servers"]
            if after != expected:
                raise ValueError("removal would change foreign content")
        except EntryChanged:
            raise
        except (ValueError, TypeError):
            raise QuickstartError("TOML entry cannot be removed safely; edit the file by hand") from None
        return text, candidate
    if _json_has_duplicate_keys(text):
        raise QuickstartError("JSON config with duplicate keys; edit it by hand")
    data_cfg = strict_loads(text or "{}")
    servers = data_cfg.setdefault(_json_key(client), {}) if isinstance(data_cfg, dict) else None
    if not isinstance(servers, dict):
        raise QuickstartError("Unexpected server table; edit the file by hand")
    _expect(servers.get(SERVER), expected_sha256)
    if previous is None:
        servers.pop(SERVER, None)
    else:
        servers[SERVER] = previous
    return text, _dump_json(data_cfg, text)


def remove_client_entry(client: Client, home: Path, *, run=subprocess.run, previous: dict | None = None,
                        toml_appended: str | None = None, expected_sha256: str | None = None) -> None:
    """Semantic removal of OUR entry (or return to ``previous``); everything else in the file is kept.

    Never a restore of the backup: what was written to the file since is preserved. JSON keeps
    its formatting; TOML loses exactly the text we appended when it is still there, else the
    span of our two tables. The candidate is validated before the write, and the entry is
    re-checked against ``expected_sha256`` on the bytes the write is built from (compare, then
    write): an entry changed meanwhile, even during the revocation, raises ``EntryChanged``.
    """
    original, candidate = _removal(client, home, previous=previous, toml_appended=toml_appended,
                                   expected_sha256=expected_sha256)
    if client.fmt == "claude-user":
        # ``_removal`` has just run the guard on the expected entry: immediately before the remove.
        if _claude(["mcp", "remove", "--scope", "user", SERVER], home, run=run) != 0:
            raise QuickstartError("claude mcp remove failed; entry retained")
        if previous:
            # Revalidated immediately before the restoring add: our name must be absent.
            if _claude_add(client, home, previous, run=run) != 0:
                raise QuickstartError("claude mcp add failed; previous entry kept in the private backup")
        # A zero return code is not the state: the file is read back, and only the expected state
        # (our entry gone, or exactly ``previous`` back) is a removal or a restoration.
        state, now = read_entry(client, home)
        gone = previous is None and now is None and state in {"ok", "missing-config"}
        back = (previous is not None and state == "ok" and isinstance(now, dict)
                and _entry_hash(now) == _entry_hash(previous))
        if not (gone or back):
            raise RemovalUnconfirmed()
        return
    path = client.config_path(home)
    # Prepare first, verify last: the target must still be the bytes the candidate was built from.
    _atomic_write(path, candidate, expected=hashlib.sha256(original.encode("utf-8")).hexdigest())


def _missing_parents(path: Path, home: Path) -> list:
    """Directories below HOME that do not exist yet on the way to ``path``, deepest first."""
    missing = []
    for parent in path.parents:
        if parent == home or not parent.is_relative_to(home):
            break
        try:
            os.lstat(parent)
        except FileNotFoundError:
            missing.append(str(parent))
        except OSError:
            break
        else:
            break
    return missing


def created_file(record, path: Path) -> str | None:
    """Digest of the client file THIS record created, when the file on disk is still exactly that file.

    Undo option (b), 4.1.1: a client file is removed only when the install journal attests both
    facts: the file did not exist before the install (``file_absent_before``, written before the
    write) and the bytes on disk are the bytes the install itself PRODUCED
    (``file_created_sha256``: computed from the text before it was written, journaled only if
    the file read back after the write was exactly that text). A file that existed before, a
    record of an older journal, a file written by the ``claude`` CLI, a file changed by anyone
    since our write: None, and the entry is removed semantically as before, the file staying
    in place.
    """
    if not isinstance(record, dict) or record.get("status") != "applied" or record.get("file_absent_before") is not True:
        return None
    if record.get("method") not in {"json-merge", "toml-append"}:
        return None  # only a file whose every byte this code produced
    digest = record.get("file_created_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or record.get("path") != str(path):
        return None
    return digest if _plist_is(path, digest) else None


def remove_created_file(record, path: Path, home: Path, journal_dir: Path, name: str) -> dict | None:
    """Take the client file this record created out of the client's directory; None when it is not removable.

    The file is MOVED into the private journal (same last look and rename as a service
    definition: plain single-link file with the journaled digest, not following a link), so
    it is gone for the client and its bytes are kept. The directories the install created for
    it are removed when, and only when, they are empty real directories.

    Residual window, declared: the digest is checked by path and the rename follows it, as two
    system calls. A writer that replaces the file between that check and the rename has its
    file moved into the journal (recoverable there, not destroyed). This is the same window as
    for a service definition; it is narrowed, not closed.
    """
    digest = created_file(record, path)
    if digest is None:
        return None
    try:
        _safe_path(path, home)
    except QuickstartError:
        return None
    moved = _move_plist(path, journal_dir, digest, name)
    if moved is None:
        return None
    removed = []
    recorded = record.get("dirs_absent_before")
    for index, text in enumerate(recorded if isinstance(recorded, list) else []):
        expected = path.parents[index] if index < len(path.parents) else None
        if not isinstance(text, str) or expected is None or text != str(expected) or expected == home \
                or not expected.is_relative_to(home):
            break  # only the chain of parents of this very file, below HOME
        try:
            info = os.lstat(expected)
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
                break
            os.rmdir(expected)  # refuses a directory that is not empty: whatever was added since stays
        except OSError:
            break
        removed.append(text)
    return {"file": "removed-created-by-install", "file_moved_to": moved, "dirs_removed": removed}


def created_file_record(journal_root: Path, client_key: str, digest: str, identity: str, path: Path):
    """The trusted, unrevoked journal record proving this entry AND attesting the file as created by us."""
    for _, document in _trusted_journals(journal_root):
        for record in document["records"]:
            if _record_proves(record, client_key, digest, identity) and created_file(record, path) is not None:
                return record
    return None


def backup_client_file(path: Path, backup: Path) -> str:
    """Verbatim private copy (0600, fsync) taken before a write; returns its SHA-256.

    The only place where a pre-existing secret of the file may survive: never shown, never
    copied into a journal, quoted by path with ``backup_may_hold_secrets``.
    """
    shutil.copy2(path, backup)
    os.chmod(backup, 0o600)
    with backup.open('rb') as stream:
        os.fsync(stream.fileno())
    return _sha(backup)


def apply_clients(plan: dict, *, home: Path, python: str, env: dict[str, str], journal_root: Path | None = None,
                  run=subprocess.run, journal_dir: Path | None = None) -> dict:
    """Writes the client entries from the preview (Q3/Q5). Refuses any file modified since the preview.

    The journal binds client, entry and installation (``applied_entry_sha256``,
    ``installation_identity``) and never holds a previous entry in clear: only its class and digest.
    """
    if plan.get("unknown_clients"):
        raise QuickstartError("Unknown clients; re-preview with explicit supported choices")
    for item in plan["clients"]:
        client = next(c for c in CLIENTS if c.key == item["client"])
        if _entry_hash(desired_entry(client, python, env)) != item['desired_sha256']:
            raise QuickstartError("Desired entry changed since preview")
    jdir = journal_dir or new_journal(journal_root or home / JOURNAL_SUBDIR)
    stamp = jdir.name
    journal = jdir / "journal.json"
    records = []
    identity = installation_identity(env)
    def persist():
        _atomic_write(journal, json.dumps({"version": 1, "stamp": stamp, "records": records},
                                         indent=1, ensure_ascii=False), expected=UNGUARDED)
        journal.chmod(0o600)
    persist()
    for item in plan["clients"]:
        client = next(c for c in CLIENTS if c.key == item["client"])
        if item["action"].startswith("refuse") and item.get("path") is None:
            records.append({"client": client.key, "path": None, "action": item["action"],
                            "status": item["action"]})
            continue
        try:
            path = client.config_path(home)
        except ClientPathError as exc:
            raise QuickstartError(str(exc), code=exc.code) from None
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
            rec["backup"] = str(backup)
            rec["backup_sha256"] = backup_client_file(path, backup)
            rec["backup_may_hold_secrets"] = True
        elif client.fmt != "claude-user":
            # 4.1.1, undo option (b): the file does not exist before this install writes it. Journaled
            # BEFORE the write, with the directories the write will create. The digest added after
            # the write is the digest of the bytes THIS code produced, not of whatever is on disk.
            # Claude Code is excluded: ``~/.claude.json`` is written by the ``claude`` CLI, the
            # provenance of its whole content is not established here, so it keeps the semantic
            # removal (``claude mcp remove``) and is not taken away as a file.
            rec["file_absent_before"] = True
            rec["dirs_absent_before"] = _missing_parents(path, home)
        want = desired_entry(client, python, env)
        if before is not None:
            rec["previous_class"] = classify(before)
            rec["previous_entry_sha256"] = _entry_hash(before)
        rec["applied_entry"] = want  # our own entry: no secret, and the binding a 4.0.9 journal relied on
        rec["applied_entry_sha256"] = _entry_hash(want)
        rec["installation_identity"] = identity
        if client.fmt.endswith("toml"):
            candidate = _toml_candidate(client, _read(path) if path.exists() else "", want)
            if candidate is not None:
                rec["toml_appended"] = candidate[1]
        records.append(rec)
        rec["status"] = "pending"  # write-ahead evidence survives CLI/write failure
        persist()
        if _sha(path) != item["file_sha256"]:
            rec["status"] = "conflict-file-changed-since-preview"
            persist()
            continue
        if before is not None:
            # A replace REMOVES the entry in place: the proof of that entry is consumed here, like by an
            # undo or an uninstall, so the old entry re-created later by hand is never ours again.
            try:
                rec["revoked_journals"] = revoke_proofs(jdir.parent, client.key, _entry_hash(before), stamp)
            except (OSError, ValueError, QuickstartError):
                rec["status"] = "revocation-failed-entry-untouched"
                persist()
                continue
        try:
            # The file must still be the one the preview showed, on the bytes the write is built from:
            # a writer that slipped in since the last check (during the revocation of a replace
            # included) keeps its entry; this client is a conflict, the others are still handled.
            produced = []
            rec["method"] = _write_client(client, home, want, run=run, expected_file_sha256=item["file_sha256"],
                                          produced=produced)
            state, after = read_entry(client, home)
            rec["status"] = "applied" if state == "ok" and _same(after, want) else f"verify-failed:{state}"
            if (rec["status"] == "applied" and rec.get("file_absent_before") is True and len(produced) == 1
                    and _plist_is(path, produced[0])):
                # The proof of creation is the digest of the bytes we produced, and it is journaled only
                # if the file read back right after the write is exactly those bytes. A file that
                # another writer already changed carries no proof: its entry is removed semantically.
                rec["file_created_sha256"] = produced[0]
        except EntryChanged:
            rec["status"] = "conflict-file-changed-since-preview"
            persist()
            continue
        except (OSError, ValueError, QuickstartError, subprocess.SubprocessError):
            rec["status"] = "failed-or-partial"
            persist()
            raise QuickstartError(f"Client {client.key} failed; private journal retained: {stamp}",
                                  code="client_write_failed") from None
        persist()
    persist()
    hidden = ("applied_entry", "previous_entry", "toml_appended")
    return {"stamp": stamp, "journal": str(journal),
            "records": [{k: v for k, v in r.items() if k not in hidden} for r in records]}


def new_journal(root: Path) -> Path:
    if any(p.is_symlink() for p in [root, *root.parents]):
        raise QuickstartError("Symlink journal path refused")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    path = root / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex)
    path.mkdir(mode=0o700)  # exclusive creation: a fresh directory nobody else has
    _FRESH_JOURNALS.add(str(path.resolve()))
    return path


# ---------- stopping OUR service: confirmed stop first, plist moved last (SETUP_4_1_0.md §6) ----------

STOP_CODES = {"stop-failed": "service_stop_failed", "stop-timeout": "service_stop_timeout",
              "stop-unconfirmed": "service_stop_unconfirmed",
              "refused-changed-since-install": "service_changed_since_install",
              # The definition is no longer the plain file the install wrote: a link, a directory or any
              # other object stands in its place, or it is gone while its job is loaded or cannot be read.
              "refused-definition-not-a-file": "service_changed_since_install",
              "refused-job-without-definition": "service_changed_since_install",
              # The manager attests the absence of the job but the port is not attested free: nothing
              # was stopped by this run, and nothing is moved nor reported as finished.
              "absence-unconfirmed": "service_stop_unconfirmed",
              "refused-job-not-attested": "service_changed_since_install"}


def _free_name(journal_dir: Path, name: str) -> Path:
    """An unused name inside the private journal directory (``lstat`` only)."""
    index, target = 0, journal_dir / name
    while True:
        try:
            os.lstat(target)
        except FileNotFoundError:
            return target
        index += 1
        target = journal_dir / f"{name}.{index}"


def _move_plist(plist: Path, journal_dir: Path, sha256: str, name: str | None = None) -> str | None:
    """The definition is MOVED into the private journal, never deleted; None when it is no longer ours.

    Order: the destination is chosen FIRST (private directory, unused name); the check of the
    definition (``_plist_is``: plain single-link file with the journaled digest, never following
    a link) is the LAST step; then the rename, with no other I/O in between. The same move takes
    a systemd unit, and a client file this installation created (4.1.1, undo option (b)).
    """
    if journal_dir.is_symlink() or not _private(journal_dir, 0o700, directory=True):
        return None
    target = _free_name(journal_dir, name or plist.name)
    if not _plist_is(plist, sha256):
        return None
    try:
        os.rename(plist, target)
    except OSError:
        return None  # e.g. another volume: the definition stays where it is, reported as not moved
    return str(target)


def _plist_is(plist: Path, sha256: str) -> bool:
    """The definition is still the plain single-link file with the journaled digest (never follows a link)."""
    try:
        data = _read_verified(plist)
    except EntryChanged:
        return False
    return data is not None and hashlib.sha256(data).hexdigest() == sha256


def _definition_kind(path: Path) -> str:
    """``file`` | ``missing`` | ``other`` | ``unknown`` by ``lstat`` alone (a link is never followed).

    ``missing`` is only the name not existing at all; a symbolic link (dangling or not), a
    directory or any special file is ``other``; an ``lstat`` that fails otherwise is ``unknown``.
    """
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return "missing"
    except (OSError, ValueError):
        return "unknown"
    return "file" if stat.S_ISREG(mode) else "other"


def _service_port(service) -> int | None:
    """The TCP port the planned command listens on (``--port N`` of its arguments), or None."""
    argv = service.get("argv") if isinstance(service, dict) else None
    if not isinstance(argv, list) or argv.count("--port") != 1:
        return None
    index = argv.index("--port") + 1
    value = argv[index] if index < len(argv) else None
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,4}", value) or int(value) > 65535:
        return None
    return int(value)


def _await_stop(service, port, *, run, stop_timeout, sleep, clock) -> tuple:
    """THE confirmation of a stop, shared by every path: ``(job state, port state)`` as last observed.

    Confirmed means, in ONE round of observation: the manager attests the absence by its strict
    predicate AND the port is ``free`` (a refused connection, nothing else). The wait is bounded
    by ``clock`` (monotonic): ``stop_timeout`` is the TOTAL time, observations included, not a
    sum of pauses. No round starts after the deadline, and each observation gets what remains as
    its own limit (at most 5 s for the manager, 0.5 s for the port). A round that started is
    completed, so both facts always come from the same round: the port probe keeps at least
    0.1 s, the only overrun of a wait longer than a second. One complete round is always made,
    even with a zero ``stop_timeout`` (limits then: 1 s for the manager, 0.1 s for the port).
    """
    deadline = clock() + max(0.0, float(stop_timeout))
    state, port_state, first = 'unknown', 'unknown', True
    while True:
        remaining = deadline - clock()
        if not first and remaining <= 0:
            break
        state = _job_state(service, run=run, timeout=min(5.0, max(remaining, 1.0) if first else remaining))
        port_state = _port_state(port, timeout=min(0.5, max(deadline - clock(), 0.1)))
        first = False
        if state == 'absent' and port_state == 'free':
            break
        remaining = deadline - clock()
        if remaining <= 0:
            break
        sleep(min(0.25, remaining))
    return state, port_state


def stop_owned_service(service: dict, plist_sha256: str | None, journal_dir: Path, *, run,
                       stop_timeout: float = 10.0, sleep=time.sleep, port: int | None = None,
                       clock=None) -> dict:
    """Stop the job this installation owns and move its plist into ``journal_dir``.

    Sequence: the plist still has the journaled digest; the loaded job is re-attested (program,
    arguments, token file, directory) right before the stop; ONE ``launchctl bootout``, no retry,
    no forced kill; the stop is CONFIRMED within ``stop_timeout`` (``_await_stop``: the job
    attested absent AND the port free, total time on a monotonic clock); only then the plist
    moves. Only an explicit "Could not find service" is absence: an error or a timeout of an
    observation is indeterminate, never absent, never a free port. Returns
    ``{"outcome": ..., ["code"], ...}``.

    EVERY successful outcome rests on the same confirmation (``_await_stop``: in one round, the
    manager attests the absence AND the port refuses the connection), whatever stands at the
    path of the definition and whether or not this run sent a stop:
    - a symbolic link, a directory or any object that is not a plain file: refusal
      (``refused-definition-not-a-file``), nothing stopped, nothing moved;
    - no file at all: ``<noun>-already-absent`` only after that confirmation; a job still
      loaded, foreign or unreadable is ``refused-job-without-definition``;
    - the file is ours and the job is already absent (interrupted install, or a second run
      after a ``stop-unconfirmed``): the definition moves only after that confirmation;
    - job absent but port listening or unreadable: ``absence-unconfirmed`` (with what was
      seen): nothing moves, the caller fails, it never reports a finished rollback.
    A stop command that fails or does not answer is not retried, and it is no proof that
    nothing stopped: the state is observed once and reported with the refusal (``observation``,
    ``port``); it stays a refusal, the definition stays in place.

    4.1.1: the same sequence through the service interface (``ops/service.py``). With
    ``systemd --user`` the stop is ONE ``systemctl --user disable --now``, absence is read from
    ``systemctl --user show`` and the outcomes say ``unit`` where launchd says ``plist``.
    ``port`` is the port of the service (default: ``--port`` of the planned command); a plan
    naming no port cannot be confirmed stopped, so nothing is stopped.
    """
    manager = managers.manager_of(service)
    noun = manager.noun if manager else "plist"
    clock = clock or time.monotonic

    def refused(outcome, **facts):
        return {"outcome": outcome, "code": STOP_CODES[outcome], **facts}

    def seen(state):
        return "indeterminate" if state == "unknown" else state
    if manager is None or not isinstance(service.get("path"), str):
        return refused("refused-job-not-attested")  # a manager this build does not know: nothing is stopped
    if port is None:
        port = _service_port(service)
    if not isinstance(port, int) or isinstance(port, bool):
        return refused("refused-job-not-attested")  # no port to confirm a stop with: nothing is stopped
    plist = Path(service["path"])
    kind = _definition_kind(plist)
    if kind not in {"file", "missing"}:
        return refused("refused-definition-not-a-file")
    def one_round():
        # One complete round of THE confirmation (manager AND port), without any wait.
        return _await_stop(service, port, run=run, stop_timeout=0, sleep=sleep, clock=clock)
    if kind == "missing":
        # The absence of the FILE says nothing about the JOB nor about the port: both are observed.
        state, port_state = one_round()
        if state != "absent":
            return refused("refused-job-without-definition", observation=seen(state), port=port_state)
        if port_state != "free":
            return refused("absence-unconfirmed", observation="absent", port=port_state)
        if _definition_kind(plist) != "missing":
            # The path is read again AFTER the probes: a definition that appeared meanwhile is not "absent".
            return refused("refused-changed-since-install", observation="absent", port=port_state)
        # Declared TOCTOU limit (contract rev. 5, D6 bis): nothing excludes a definition or a job
        # appearing after this last look.
        return {"outcome": f"{noun}-already-absent"}
    if plist_sha256 is None or not _plist_is(plist, plist_sha256):
        return refused("refused-changed-since-install")
    state = _job_state(service, run=run)
    if state == "absent":  # never loaded (interrupted install), or stopped by an earlier, unconfirmed run
        state, port_state = one_round()
        if state != "absent" or port_state != "free":
            return refused("absence-unconfirmed", observation=seen(state), port=port_state)
        moved = _move_plist(plist, journal_dir, plist_sha256)
        if moved is None:
            return refused("refused-changed-since-install")
        # Declared TOCTOU limit (contract rev. 5, D6 bis): the absence was observed BEFORE the move and
        # before ``after_remove`` (systemd ``daemon-reload``); a job reappearing during them is not seen.
        return {"outcome": f"{noun}-moved-job-was-absent", f"{noun}_moved_to": moved,
                **manager.after_remove(service, run=run)}
    if state != "loaded":
        return refused("refused-job-not-attested")
    if not _plist_is(plist, plist_sha256):
        # Read again AFTER the attestation (an observation takes time), right before the stop: the
        # job attested above is stopped only while its definition is still the journaled file.
        return refused("refused-changed-since-install")
    # Declared TOCTOU limit (contract rev. 5, D6 bis): between this re-reading and the stop command
    # a substitution is not excluded (no atomic transaction over the file and the manager).
    stop = _bootout(service, run=run)
    if stop != "ok":
        # Not retried, and no proof that nothing stopped: observed once, reported, still refused.
        state, port_state = one_round()
        return refused("stop-timeout" if stop == "timeout" else "stop-failed",
                       observation=seen(state), port=port_state)
    state, port_state = _await_stop(service, port, run=run, stop_timeout=stop_timeout, sleep=sleep, clock=clock)
    if state != "absent" or port_state != "free":
        # The definition stays in place: the job may still run, or something still holds the port.
        return refused("stop-unconfirmed", observation=seen(state), port=port_state)
    moved = _move_plist(plist, journal_dir, plist_sha256)
    if moved is None:
        return refused("refused-changed-since-install")
    # Declared TOCTOU limit (contract rev. 5, D6 bis): the stop was confirmed BEFORE the move and
    # before ``after_remove`` (systemd ``daemon-reload``); a job reappearing during them is not seen.
    return {"outcome": f"stopped-and-{noun}-moved", f"{noun}_moved_to": moved,
            **manager.after_remove(service, run=run)}


def _definition_sha(step) -> str | None:
    """Digest of the service definition an install journal recorded (plist or systemd unit)."""
    if not isinstance(step, dict):
        return None
    value = step.get("plist_sha256") or step.get("unit_sha256")
    return value if isinstance(value, str) else None


def _created_service(installed) -> bool:
    steps = installed.get("steps") if isinstance(installed, dict) else None
    step = steps.get("service") if isinstance(steps, dict) else None
    return isinstance(step, dict) and step.get("created") is True


def _attestable_service(installed: dict) -> bool:
    """The journal names the service precisely enough to re-attest the job and find who depends on it."""
    from urllib.parse import urlsplit
    service, env = installed.get("service"), installed.get("ops_env")
    if not isinstance(service, dict) or not isinstance(env, dict) or not isinstance(env.get("MEMORY_V4_URL"), str):
        return False
    if not (isinstance(service.get("label"), str) and isinstance(service.get("path"), str)
            and isinstance(service.get("argv"), list) and isinstance(service.get("env"), dict)
            and all(isinstance(service["env"].get(key), str) for key in ("MEMORY_V4_TOKEN_FILE", "MEMORY_V4_DIR"))):
        return False
    try:
        return bool(urlsplit(env["MEMORY_V4_URL"]).port)
    except ValueError:
        return False


def retained_items(paths: dict) -> list:
    """What is never removed, restored, replaced or even opened: ``{kind, path, exists}``."""
    items = []
    for kind in ("store", "backups", "backup-key", "token", "resources", "journals", "logs"):
        value = paths.get(kind)
        if not value:
            continue
        values = sorted(value.values()) if isinstance(value, dict) else [value]
        if not values:
            continue
        item = {"kind": kind, "path": values[0], "exists": any(Path(v).exists() for v in values)}
        if len(values) > 1:
            item["paths"] = values
        items.append(item)
    return items


def undo(stamp: str, *, home: Path, journal_root: Path | None = None, run=subprocess.run,
         with_service: bool = False, stop_timeout: float = 10.0, sleep=time.sleep) -> dict:
    """Q3: restores the previous entry ONLY if our entry is still the one applied; otherwise refusal.

    Clients: semantic removal of our entry (never the backup put back). The proof that
    authorised the removal is revoked first, durably. Service: only with ``with_service`` and
    only the one THIS install created, through ``stop_owned_service``. Data: never touched.

    4.1.1, option (b): a client file this install CREATED (absent before, still the bytes the
    install left; both attested by this journal) leaves the client's directory instead of
    staying there as an empty shell: it is moved into this journal. A file that existed before,
    or was changed since, keeps the semantic removal above.
    """
    if not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[a-f0-9]{32}", stamp):
        raise QuickstartError("Invalid journal stamp")
    journal_root = journal_root or home / JOURNAL_SUBDIR
    directory = journal_root / stamp
    if not journal_trusted(directory):
        raise QuickstartError("Journal is not private or its path is not safe; nothing done", code="journal_unsafe")
    journal, install = directory / "journal.json", directory / "install.json"
    try:
        data = json.loads(journal.read_text(encoding="utf-8")) if journal.is_file() else {"records": []}
        installed = json.loads(install.read_text(encoding="utf-8")) if install.is_file() else None
    except (OSError, ValueError):
        raise QuickstartError("Journal unreadable; nothing done", code="journal_unsafe") from None
    out = []
    if not isinstance(data, dict) or not isinstance(data.get("records"), list):
        raise QuickstartError("Journal malformed; nothing done", code="journal_unsafe")
    for rec in data["records"]:
        client = next((c for c in CLIENTS if isinstance(rec, dict) and c.key == rec.get("client")), None)
        if client is None:
            continue  # not a record of a known client: nothing we may act on
        if rec.get("status") not in {"applied", "pending", "failed-or-partial"}:
            out.append({"client": rec["client"], "undo": "nothing-to-undo"})
            continue
        digest = _record_digest(rec)
        try:
            path = client.config_path(home)
        except ClientPathError as exc:
            out.append({"client": client.key, "undo": "refused-" + exc.code.replace("_", "-")})
            continue
        state, current = read_entry(client, home)
        file_gone = (state == "missing-config" and isinstance(rec.get("file_created_sha256"), str)
                     and not os.path.lexists(path))  # option (b): the file we created has left
        if (state == "ok" or file_gone) and current is None and rec.get("revoked"):
            # Already removed by an earlier undo or uninstall (the proof is revoked, the entry is gone):
            # a second run has nothing to do here, so a rollback whose stop failed can be finished later.
            out.append({"client": client.key, "undo": "nothing-to-undo"})
            continue
        if state != "ok" or not isinstance(current, dict) or digest is None or _entry_hash(current) != digest:
            out.append({"client": client.key, "undo": "refused-entry-changed-since-apply"})
            continue
        if rec.get("revoked"):
            # The proof was consumed by an earlier removal: the same entry, back by hand, is not ours to remove.
            out.append({"client": client.key, "undo": "refused-proof-revoked"})
            continue
        if str(path) != rec["path"]:
            raise QuickstartError("Journal config path differs from registry")
        _safe_path(path, home)
        previous = rec.get("previous_entry")  # 4.0.9 journals only; newer ones never hold it in clear
        if "previous_entry_sha256" in rec:
            # The exact previous entry is read back from the private backup, after both digests are checked.
            previous = None
            try:
                backup = Path(rec["backup"])
                if _private(backup, 0o600) and _sha(backup) == rec.get("backup_sha256"):
                    previous = _entry_in_text(client, backup.read_text(encoding="utf-8"))
            except (OSError, ValueError, KeyError, TypeError):
                previous = None
            if previous is None or _entry_hash(previous) != rec["previous_entry_sha256"]:
                out.append({"client": client.key, "undo": "refused-backup-changed"})
                continue
        try:
            # Validated before anything is revoked or written: an unsafe removal leaves entry AND proof intact.
            removal_text(client, home, previous=previous, toml_appended=rec.get("toml_appended"),
                         expected_sha256=digest)
        except EntryChanged:
            out.append({"client": client.key, "undo": "refused-entry-changed-since-apply"})
            continue
        except (OSError, ValueError, QuickstartError, RecursionError):
            out.append({"client": client.key, "undo": "refused-removal-manual"})
            continue
        # Undo option (b): attested BEFORE the revocation rewrites the journal (the record in hand is kept).
        ours_alone = created_file(rec, path) is not None
        file_facts = {}
        if rec.get("file_absent_before") is True and not ours_alone:
            # Changed since, or not proven to be only our bytes: the file stays, our entry leaves.
            file_facts = {"file": "kept-not-proven-ours-alone"}
        try:
            # Revocation first, removal second: a failed removal leaves the entry unproven, never removable by default.
            revoke_proofs(journal_root, client.key, digest, stamp)
        except (OSError, ValueError, QuickstartError):
            out.append({"client": client.key, "undo": "revocation-failed-entry-untouched"})
            continue
        if ours_alone:
            # The file holds nothing but what this install wrote: it leaves the client's directory whole.
            gone = remove_created_file(rec, path, home, directory, f"{client.key}.created-by-install")
            if gone is not None:
                out.append({"client": client.key, "undo": "restored", **gone})
                continue
            file_facts = {"file": "kept-not-proven-ours-alone"}  # changed at the last look: semantic removal
        try:
            remove_client_entry(client, home, run=run, previous=previous, toml_appended=rec.get("toml_appended"),
                                expected_sha256=digest)
        except EntryChanged:
            # Changed DURING the revocation: it is someone else's entry now, and it is left alone.
            out.append({"client": client.key, "undo": "refused-entry-changed-since-apply"})
            continue
        except RemovalUnconfirmed:
            # The CLI said zero and the file read back is not the expected one: never "restored".
            out.append({"client": client.key, "undo": "unconfirmed-entry-state-after-claude-cli"})
            continue
        except (OSError, ValueError, KeyError, QuickstartError, subprocess.SubprocessError):
            out.append({"client": client.key, "undo": "failed-entry-kept-unproven"})
            continue
        out.append({"client": client.key, "undo": "restored", **file_facts})
    clients_done = all(item["undo"] in {"restored", "nothing-to-undo"} for item in out)
    result = {"stamp": stamp, "ok": clients_done, "results": out, "data_restored_from_copy": False}
    if not clients_done:
        result["code"] = "undo_partial"
    if not with_service:
        result["service"] = {"undo": "not-requested"}
    elif not _created_service(installed):
        result["service"] = {"undo": "not-created-by-this-install"}
    elif not _attestable_service(installed):
        # A journal that says it created the service but cannot describe it (4.0.9 journal, no usable
        # URL): the job cannot be attested, so it is never stopped from here.
        result.update(ok=False, code="service_changed_since_install")
        result["service"] = {"undo": "refused-job-not-attested"}
    elif not clients_done:
        # A client still configured must not be left pointing to a stopped service.
        result["service"] = {"undo": "kept-clients-incomplete"}
    else:
        from . import uninstall
        from urllib.parse import urlsplit
        port = urlsplit(installed["ops_env"]["MEMORY_V4_URL"]).port  # checked by _attestable_service
        # Same rule as the uninstall (§7): the service outlives every entry that depends, or may depend, on it.
        remaining = uninstall.remaining_dependencies(home, port)
        if remaining:
            result["service"] = {"undo": "kept-clients-remaining", "remaining_clients": sorted(remaining),
                                 "remaining_dependencies": dict(sorted(remaining.items()))}
        else:
            stopped = stop_owned_service(installed["service"], _definition_sha(installed["steps"]["service"]),
                                         directory, run=run, stop_timeout=stop_timeout, sleep=sleep, port=port)
            code = stopped.pop("code", None)
            result["service"] = {"undo": stopped.pop("outcome"), **stopped}
            if code:
                result.update(ok=False, code=code)
    if isinstance(installed, dict) and isinstance(installed.get("paths"), dict):
        result["retained"] = retained_items({**installed["paths"], "journals": str(journal_root)})
    else:
        result["retained"] = []
    return result


def attestation_summary(attestation: dict | None) -> dict | None:
    """Both SQLite read-back values without any path: what every report carries, failures included."""
    if not attestation:
        return None
    interpreter, server = attestation.get('interpreter') or {}, attestation.get('server') or {}
    return {'minimum': attestation.get('minimum'), 'route': attestation.get('route'), 'ok': attestation.get('ok'),
            'code': attestation.get('code'),
            'interpreter': {key: interpreter.get(key) for key in ('version', 'ok', 'code', 'read_by', 'python')},
            'server': {key: server.get(key) for key in ('version', 'ok', 'state', 'code')}}


def report(plan: dict) -> dict:
    """Q5: anonymized report (paths relative to HOME, entries masked, no foreign configuration)."""
    home = str(Path.home())
    def anon(x):
        return x.replace(home, "~") if isinstance(x, str) else x
    return {"platform": plan["platform"], "port_state": plan["port_state"], "token": plan["token"]["state"],
            "backend": plan.get('backend'), "work_state": plan.get('work_state'),
            **({"sqlite_attestation": attestation_summary(plan['sqlite_attestation'])}
               if plan.get('sqlite_attestation') else {}),
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
    manager = managers.host_manager()
    if manager is None:
        return 'sqlite'  # no service manager here: no definition to read (the preflight refuses the install)
    label = settings.service_label or 'local.memory-os-v4'
    path = manager.definition_path(label, home)
    _safe_path(path, home)
    if path.exists():
        try:
            value = manager.read_environment(path).get('MEMORY_V4_STORE_BACKEND')
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
    manager = managers.host_manager()
    existing = manager is not None and manager.definition_path(label, home).exists()
    if (chosen == 'sqlite' and settings.store_dir == legacy and 'MEMORY_V4_DIR' not in os.environ
            and not existing and not legacy.exists()):
        settings = replace(settings, store_dir=home / '.local/share/memory-os/store')
    return settings, chosen


PROFILES = ('lexical', 'semantic')
# Decision R1 (pending, SETUP_4_1_0.md §16): profile of a NEW install when --profile is absent.
# None = no default, the preview is refused with setup_profile_required (the contract's recommendation).
# Flipping R1 to "default lexical" is this one constant ('lexical'), plus its test.
NEW_INSTALL_DEFAULT_PROFILE = None
# Encoder settings a lexical service must not carry (Config.validate_retrieval_profile refuses them).
ENCODER_ENV = ('MEMORY_V4_EMBED_MODEL', 'MEMORY_V4_EMBED_MAX_SEQ_LENGTH', 'MEMORY_V4_DEVICE',
               'MEMORY_V4_EMBED_DTYPE', 'MEMORY_V4_EMBED_BATCH_SIZE', 'HF_HUB_CACHE')
PROFILE_INSTALL = {
    'lexical': ('memory-os-ai[ops,brief]', 'qualified_runtime_constraints_lexical_v1.txt'),
    'semantic': ('memory-os-ai[ops,brief,semantic]', 'qualified_runtime_constraints_v1.txt'),
}


def profile_install(profile: str, *, cell_id: str | None = None) -> tuple[str, str]:
    """``(requirement, constraints_filename)`` for ``(profile, cell_id)`` (detected cell when None)."""
    from . import qualified_runtime
    requirement, fallback = PROFILE_INSTALL[profile]
    resolved = cell_id if cell_id is not None else cell.detect().get('cell_id')
    if resolved is not None:
        entry = qualified_runtime.CELL_LOCKS.get((profile, resolved))
        if isinstance(entry, tuple) and len(entry) >= 2:
            return requirement, entry[1].name
    return requirement, fallback


_HOST = _Sentinel("HOST")  # "the manager of this machine": distinct from None, which is "no manager"


def _service_plan(settings, home, python, resource_env, *, backend=None, profile='semantic', manager=_HOST):
    """Service definition of one profile. ``semantic`` is the frozen 4.0.9 definition, byte for byte;
    ``lexical`` is that definition minus the encoder variables, plus the profile selector.

    The definition is rendered by the service manager of this machine (plist or systemd unit;
    same program, arguments and environment). Without a manager the plan describes the service
    and has no definition (``state: unsupported``): nothing can be installed.
    """
    if manager is _HOST:
        manager = managers.host_manager()
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
    if manager is None:
        return {"label": label, "path": None, "argv": argv, "env": env, "state": "unsupported", "sha256": None,
                "manager": None}
    try:
        xml = manager.render(label, argv, env, log=settings.ops_dir / "logs/server.err")
    except ValueError as exc:
        raise QuickstartError(f"The service definition cannot be written safely: {exc}") from None
    path = manager.definition_path(label, home)
    if not path.absolute().is_relative_to(home.absolute()):
        # Declared limit (4.1.1 L4): e.g. an absolute XDG_CONFIG_HOME outside the HOME. Nothing is written there.
        raise QuickstartError("The service definition directory is outside the selected HOME (XDG_CONFIG_HOME "
                              "outside HOME is not supported); nothing written", code="service_definition_outside_home")
    _safe_path(path, home)
    state = manager.state(path, xml)
    return {"label": label, "path": str(path), "argv": argv, "env": env, manager.text_key: xml,
            "state": state, "sha256": _sha(path), **manager.plan_extras(label)}


def _installed_profile(settings, home):
    """Profile recorded by an existing service definition (absent variable = semantic); None without one."""
    manager = managers.host_manager()
    if manager is None:
        return None
    label = settings.service_label or 'local.memory-os-v4'
    path = manager.definition_path(label, home)
    try:
        if path.is_symlink() or not path.is_file():
            return None
        env = manager.read_environment(path)
        value = env.get('MEMORY_V4_RETRIEVAL_PROFILE', 'semantic') if isinstance(env, dict) else None
    except (OSError, ValueError, AttributeError):
        return None
    return value if value in PROFILES else None


def _job_run(argv, *, run, timeout):
    """One system command reduced to (return code, stdout, stderr); a timeout or OS error gives None.

    Callers only ever copy the command name and its return code into what we produce (§8).
    """
    return managers.job_run(argv, run=run, timeout=timeout)


def _job_observation(service, *, run, timeout: float = 5):
    """Attested state of the job of a service plan, through its manager (launchd when the plan names none)."""
    manager = managers.manager_of(service)
    if manager is None:
        return {'state': 'unknown'}
    return manager.observe(service, run=run, timeout=timeout)


def _job_state(service, *, run, timeout: float = 5):
    return _job_observation(service, run=run, timeout=timeout)['state']


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
    """``install.json`` of the TRUSTED journals below a root, newest first, with dict-shaped sections."""
    found = []
    try:
        directories = sorted((p for p in root.iterdir() if p.is_dir() and not p.is_symlink()), reverse=True)
    except OSError:
        return found
    for directory in directories:
        if not journal_trusted(directory):
            continue  # an untrusted directory names nothing and attests nothing
        try:
            document = json.loads((directory / 'install.json').read_text(encoding='utf-8'))
        except (OSError, ValueError, RecursionError):
            continue
        if isinstance(document, dict):
            for key in ('steps', 'service', 'paths'):
                if not isinstance(document.get(key), dict):
                    document[key] = {}
            if not isinstance(document['steps'].get('service'), dict):
                document['steps']['service'] = {}
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
    # 4.1.1 L4: the service manager comes from the detected cell (launchd, systemd --user, or none).
    host = cell.detect()
    manager = managers.select(host)
    installed = _installed_profile(settings, home)
    effective = profile or installed or NEW_INSTALL_DEFAULT_PROFILE
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
    service = _service_plan(settings, home, python, resource_plan['env'], backend=backend, profile=effective,
                            manager=manager)
    if service['state'] == 'foreign-or-different' and installed and installed != effective:
        # The existing definition may be ours under ITS profile: a conversion request, not a foreign service.
        other_plan = resources_for(installed)
        other = _service_plan(settings, home, python, other_plan['env'], backend=backend, profile=installed,
                              manager=manager)
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
    # 4.1.1 §3: only the value read back BY THE INTERPRETER OF THE VENV counts. When ``python`` is
    # not the interpreter running this command, it is executed to read back its own library (and
    # its own version, hence its own cell): this command never answers for another interpreter.
    target_report, target_facts = sqlite_report, {}
    if (os.path.abspath(python) != os.path.abspath(sys.executable) and Path(python).is_file()
            and os.access(python, os.X_OK) and not sqlite_runtime.is_ephemeral(python)):
        target_report = sqlite_runtime.probe_target(python)
        probed = target_report.get('target')
        if probed:
            target_facts = {'python_version': tuple(int(part) for part in probed['python'].split('.')),
                            'implementation': probed['implementation'] or 'unknown',
                            'sqlite_version': target_report.get('version')}
    detected = cell.detect(**target_facts)
    # Route (a) or (i), else an actionable refusal.
    sqlite_attestation = sqlite_runtime.attestation(target_report, python=python, cell_id=detected.get('cell_id'))
    sqlite_attestation['command_interpreter'] = {'version': sqlite_report.get('version'), 'ok': bool(sqlite_report.get('ok')),
                                                 'code': sqlite_report.get('code'), 'executable': sys.executable}
    if backend == 'sqlite' and not target_report.get('ok'):
        block(target_report.get('code') or 'sqlite_runtime_unavailable', sqlite_attestation['message'])
    elif backend == 'sqlite' and not sqlite_report.get('ok'):
        # The target is fit but this command (which runs the WAL probe and the checks) is not.
        block(sqlite_report.get('code') or 'sqlite_runtime_unavailable', sqlite_runtime.refusal_message(
            sqlite_report, cell_id=detected.get('cell_id'),
            remedy=sqlite_runtime.standalone_plan(detected.get('cell_id'))))
    identity = readiness.build_identity()
    # 4.1.1: one of the retained cells, with its attested embedded lock for this profile; semantic stays on its cell.
    cell_acceptance = cell.acceptance(detected, profile=effective)
    if not cell_acceptance['ok']:
        block(cell_acceptance['code'], cell_acceptance['message'])
        runtime = {'ok': False, 'state': cell_acceptance['reason'], 'cell_id': detected.get('cell_id'),
                   'profile_id': None, 'expected': {}, 'actual': {}, 'mismatches': {},
                   'attestation': 'cell not accepted; lock not applied'}
    else:
        runtime = qualified_runtime.inspect_current(profile=effective, extras=('ops', 'brief'),
                                                    cell_id=detected['cell_id'])
    if identity.get('identity_source') != 'embedded_descriptor' or not identity.get('commit') or identity.get('source_dirty'):
        block('build_unqualified', 'build_unqualified: install a qualified wheel; source/env identity is insufficient')
    if cell_acceptance['ok'] and not runtime['ok']:
        from .. import __version__
        requirement, constraints = profile_install(effective, cell_id=detected.get('cell_id'))
        block('runtime_unqualified', f"runtime_unqualified ({runtime.get('state', 'drift')}): package versions differ: "
              + ', '.join(sorted(runtime['mismatches']))
              + f'; install the exact qualified dependencies of the {effective} profile '
              + f'(pip install -c {constraints} "{requirement}=={__version__}"), then re-preview')
    if not Path(python).is_file() or not os.access(python, os.X_OK) or sqlite_runtime.is_ephemeral(python):
        block('runtime_unqualified', 'absolute durable Python executable required (no uvx ephemeral runtime)')
    if importlib.util.find_spec('tiktoken') is None:
        block('runtime_unqualified', 'install memory-os-ai[ops,brief] before apply; no implicit dependency install')
    if result['unknown_clients']:
        block('client_unknown', 'unknown clients')
    path_refusal_codes = ('client_unsupported_on_os', 'client_env_override',
                          'client_xdg_outside_home', 'client_xdg_invalid')
    for code in path_refusal_codes:
        refused = [c for c in result['clients'] if c.get('refusal_code') == code]
        if refused:
            block(code, '; '.join(c['refusal'] for c in refused))
    conflicts = [c['client'] for c in result['clients'] if c['action'].startswith('refuse-existing')]
    if conflicts:
        block('client_entry_conflict', 'client_entry_conflict: an existing memory-os-ai entry is not proven to be ours for '
              + ', '.join(conflicts) + '; nothing is overwritten silently (name the client with --replace-existing, '
              'or edit it by hand)')
    if any(c['action'].startswith('refuse') and not c['action'].startswith('refuse-existing')
           and c.get('refusal_code') not in path_refusal_codes for c in result['clients']):
        block('client_config_manual', 'manual client setup required; select only eligible clients')
    client_cli = {}
    if any(c['client'] == 'claude-code' and c['action'] in {'create', 'replace'} for c in result['clients']):
        client_cli['claude'] = which('claude')
        if not client_cli['claude']:
            block('client_cli_missing', 'client_cli_missing: the claude CLI is not on PATH; Claude Code is configured '
                  'only through claude mcp add --scope user')
    if any(c['client'] == 'claude-desktop' and c.get('refusal_code') != 'client_unsupported_on_os'
           for c in result['clients']):
        desktop, _, _ = _job_run(['pgrep', '-x', 'Claude'], run=run, timeout=5)
        if desktop != 1:
            block('client_app_running', 'quit Claude Desktop before apply; live config may be rewritten by the app')
    token = read_token(settings.token_file)  # strict permissions/owner/format, never returned
    required = max(minimum, resource_plan['peak_additional_bytes_upper_bound'] + 1_000_000_000)
    if result['disk_free_bytes'] < required:
        block('disk_guard', f'disk guard: {result["disk_free_bytes"]} bytes free < required {required} bytes '
              f'(effective minimum {minimum} bytes; provisioning peak + 1000000000 bytes margin)')
    loaded = _job_state(service, run=run) if manager is not None else 'unsupported'
    service['loaded_state'] = loaded
    if manager is None:
        # No launchd and no usable systemd --user: said as such, never as an occupied port or label.
        block('service_manager_unavailable', managers.unavailable(host) + '; nothing written')
    elif service['state'] not in {'absent', 'owned'} or loaded in {'unknown', 'foreign-or-unattested'}:
        block('service_foreign', 'foreign/different/unattested service: no repair, stop or overwrite')
    if service['state'] == 'absent' and (loaded != 'absent' or result['port_state'] != 'free'):
        # A port that could not be probed is not a free port: same refusal, said as what it is.
        block('port_or_label_occupied', manager.occupied + (
            ' (the port could not be probed: its state is unknown, which is never a free port)'
            if result['port_state'] == 'unknown' else ''))
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
    notices += client_notices(clients, home)
    if manager is not None:
        notices += manager.notices(service['label'])
    return {**result, 'ok': not blockers, 'profile': effective, 'identity': identity, 'service': service,
            'blockers': blockers, 'blocker_codes': blocker_codes, 'sqlite_runtime': sqlite_report, 'notices': notices,
            'sqlite_attestation': sqlite_attestation,
            **({'sqlite_runtime_target': target_report} if target_report is not sqlite_report else {}),
            'disk_min_free_bytes': minimum, 'disk_required_free_bytes': required,
            'backend': backend, 'work_state': backend == 'sqlite', 'runtime_qualification': runtime,
            'cell': detected, 'cell_acceptance': cell_acceptance,
            'client_env': client_env, 'client_cli': client_cli, 'replace_existing': sorted(replace_existing),
            'writes': 'none (preview)', 'stage': 'preview'}


def _sqlite_triplet(value):
    return sqlite_runtime.triplet(value)


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
        served = sqlite_runtime.server_attestation(store.get('sqlite_version'))
        refusal = None
        if served['state'] == 'unattested':
            refusal = QuickstartError('The running server does not attest a readable SQLite version',
                                      code='sqlite_runtime_unattested')
        elif not served['ok']:
            refusal = QuickstartError('The running server loaded a SQLite older than the engine floor (version '
                                      f"{served['version']}, minimum {sqlite_runtime.MIN_VERSION[0]}."
                                      f"{sqlite_runtime.MIN_VERSION[1]}.{sqlite_runtime.MIN_VERSION[2]}); start it "
                                      'from a durable interpreter whose loaded SQLite meets the minimum',
                                      code='sqlite_runtime_too_old')
        if refusal is not None:
            refusal.sqlite_server = served  # the value read back travels with the refusal, into the report
            raise refusal
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
    """One ``launchctl bootout``; returns 'ok', 'failed' (non-zero code) or 'timeout'. Output is never kept.

    Through the manager of the plan: ONE ``systemctl --user disable --now`` with ``systemd --user``.
    """
    manager = managers.manager_of(service)
    return manager.stop(service, run=run) if manager else 'failed'


def _confirmed_stop(service, port, *, run, stop_timeout=10.0, sleep=None, clock=None) -> dict:
    """ONE stop of our attested job, then a BOUNDED confirmation: ``{"stopped": bool, "outcome": ...}``.

    A zero return code of the stop command is not a stop. ``stopped`` is true only when, within
    ``stop_timeout``, the manager attests the absence by its strict predicate (launchd: return
    code 113 AND "Could not find service"; systemd: a complete, valid ``show`` with no main
    process, no activity and no activation, which is also how "no process" is read) AND the
    port of the service is ``free``: a refused connection. A port that cannot be read (timeout,
    permission, any other error) is ``unknown`` and confirms nothing. ``stop_timeout`` is the
    total time of the confirmation on a monotonic clock, observations included (``_await_stop``,
    the same confirmation as ``stop_owned_service``). Outcomes: ``confirmed``, ``stop-failed``,
    ``stop-timeout``, ``stop-not-confirmed`` (with what was last seen). No retry, no forced kill.
    """
    sent = _bootout(service, run=run)
    if sent != 'ok':
        # A stop command that failed or did not answer proves nothing either way: the state is
        # observed once and reported; it is never a confirmed stop (no retry, no second command).
        state, port_state = _await_stop(service, port, run=run, stop_timeout=0, sleep=sleep or time.sleep,
                                        clock=clock or time.monotonic)
        return {'stopped': False, 'outcome': 'stop-timeout' if sent == 'timeout' else 'stop-failed',
                'observation': 'indeterminate' if state == 'unknown' else state, 'port': port_state}
    state, port_state = _await_stop(service, port, run=run, stop_timeout=stop_timeout,
                                    sleep=sleep or time.sleep, clock=clock or time.monotonic)
    if state == 'absent' and port_state == 'free':
        return {'stopped': True, 'outcome': 'confirmed'}
    return {'stopped': False, 'outcome': 'stop-not-confirmed',
            'observation': 'indeterminate' if state == 'unknown' else state, 'port': port_state}


def apply_install(plan, settings, *, download_approved=False, demo=False, home=None, run=subprocess.run,
                  probe=None, doctor_run=None, timeout=180, clock=time.monotonic, stop_timeout=10.0):
    """Explicit apply. Journal every stage; failure preserves evidence, never repairs a foreign job.

    Stages: sqlite (WAL probe) -> resources -> token -> service -> service_ready -> clients -> checks
    -> demo (on request). Every duration comes from ``clock``. Diagnostics are structured and
    allow-listed (§8): no log line, subprocess output, doctor detail or foreign exception text.
    """
    from dataclasses import replace
    from . import provision, doctor
    from .auth import create_token, read_token, responder_pid
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
                            'state_at_preview': service['state'],
                            **({'manager': service['manager']} if service.get('manager') else {})},
                'paths': {'store': str(settings.store_dir), 'backups': str(settings.backup_dir),
                          'backup-key': str(settings.backup_key), 'token': str(settings.token_file),
                          'resources': {key: plan['provision']['env'][key]
                                        for key in ('HF_HUB_CACHE', 'MEMORY_V4_CL100K_PATH')
                                        if key in plan['provision'].get('env', {})},
                          'logs': str(settings.ops_dir / 'logs')},
                'ops_env': {**service['env'], 'MEMORY_V4_BACKUP_DIR': str(settings.backup_dir),
                            'MEMORY_V4_BACKUP_KEY': str(settings.backup_key)},
                'ops_notice': 'Use ops_env for doctor/health/backup in a fresh shell; quickstart never changes shell defaults.'}
    if 'sqlite_attestation' in plan:
        # §3: both values in EVERY report, a failed one included. The server value stays
        # ``not_running`` until the service itself publishes it (service_ready below).
        progress['sqlite_attestation'] = dict(plan['sqlite_attestation'])
    def record():
        _atomic_write(journal / 'install.json', json.dumps(progress, indent=1), expected=UNGUARDED)
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
        manager = managers.manager_of(service)
        if manager is None:  # a plan without a manager never passes the preflight; kept as a last guard
            raise QuickstartError('No service manager; nothing activated', code='service_manager_unavailable')
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
            write_secret_file(plist, service[manager.text_key].encode())
            progress['steps']['service'] = {'created': True, manager.step_key: _sha(plist)}
            record()
            if _sha(plist) != hashlib.sha256(service[manager.text_key].encode()).hexdigest():
                # Read again right before the start: only the definition this run produced is started.
                raise QuickstartError('Service definition changed before its start; nothing activated',
                                      code='preflight_changed')
            # Declared TOCTOU limit (contract rev. 5, D6 bis): a substitution between this reading and the
            # start (systemd: also during ``daemon-reload``, which precedes ``enable --now``) is not excluded.
            if manager.start(service, run=run) != 0:
                raise QuickstartError(manager.start_failed(journal.name), code='service_bootstrap_failed')
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
                if (service['state'] == 'absent' and _job_state(service, run=run) == 'loaded'
                        and _sha(plist) == _definition_sha(progress['steps']['service'])):
                    # Only our newly created, still attested job is stopped on a resource breach. The
                    # digest is read AFTER the attestation, right before the stop; what remains between
                    # it and the command is the declared TOCTOU limit (contract rev. 5, D6 bis).
                    # ``stopped`` is a CONFIRMED stop (job attested absent and port free, bounded wait).
                    progress['guard_stop'] = {'attempted': True, **_confirmed_stop(
                        service, settings.port, run=run, stop_timeout=stop_timeout)}
                raise
            ready = _ready(settings, plan['identity'], backend=plan['backend'], profile=profile)
            if ready:
                # A ready answer proves an application, not that OUR job provides it (D6 bis): the
                # responder must be the process the manager attests for the installed job (launchd
                # pid / systemd MainPID), say ``ok`` and name itself ``memory-os-v4``.
                serving = _job_observation(service, run=run)
                answered = responder_pid(ready)
                if (serving['state'] != 'loaded' or serving.get('running') is not True
                        or not isinstance(serving.get('pid'), int) or answered != serving['pid']):
                    progress['responder'] = {'job_state': serving['state'], 'job_running': serving.get('running') is True,
                                             'identified': answered is not None,
                                             'same_process': False}
                    raise QuickstartError('Readiness was answered, but not by the attested process of the installed '
                                          'service (no running job, or another process holds the port); no client '
                                          'config written', code='service_startup_failed')
                break
            observed = _job_observation(service, run=run)
            if (observed['state'] == 'loaded' and (observed.get('runs') or 0) >= 1
                    and observed.get('last_exit_code') not in (None, 0)
                    and ((observed.get('runs') or 0) >= 2 or
                         (not observed.get('pid') and observed.get('process_state') != 'running'))):
                progress['startup_failure'] = _startup_failure(observed, settings, home)
                # Only our new, unchanged plist and re-attested job can be stopped.
                # Digest read AFTER the attestation, right before the stop (declared TOCTOU limit beyond
                # that: contract rev. 5, D6 bis).
                if (service['state'] == 'absent' and _job_state(service, run=run) == 'loaded'
                        and _sha(plist) == _definition_sha(progress['steps']['service'])):
                    progress['startup_failure']['stop_attempted'] = True
                    confirmed = _confirmed_stop(service, settings.port, run=run, stop_timeout=stop_timeout)
                    progress['startup_failure']['stopped'] = confirmed['stopped']
                    progress['startup_failure']['stop_outcome'] = confirmed['outcome']
                    if 'observation' in confirmed:
                        progress['startup_failure']['stop_observation'] = confirmed['observation']
                    if 'port' in confirmed:
                        progress['startup_failure']['stop_port'] = confirmed['port']
                unconfirmed = progress['startup_failure'].get('stop_attempted') and not progress['startup_failure']['stopped']
                raise QuickstartError('Server startup failed: ' + str(observed.get('runs')) +
                                      ' runs, last exit code ' + str(observed.get('last_exit_code')) +
                                      '; private server log kept, content not copied: ' +
                                      progress['startup_failure']['log_path'] +
                                      ('; stop not confirmed (' + progress['startup_failure']['stop_outcome'] +
                                       '): the service may still run, roll back with quickstart --undo ' +
                                       journal.name + ' --with-service' if unconfirmed else ''),
                                      code='service_startup_failed')
            time.sleep(.25)
        if not ready:
            raise QuickstartError('Readiness timeout; preserve logs/plist; no client config written',
                                  code='service_startup_failed')
        if plan['backend'] == 'sqlite' and 'sqlite_attestation' in plan:
            # Second read-back (§3): the value published by the server process itself, next to the first.
            store = ((ready.get('capabilities') or {}).get('store') or {}) if isinstance(ready, dict) else {}
            progress['sqlite_attestation'] = {**plan['sqlite_attestation'], 'server':
                                              sqlite_runtime.server_attestation(store.get('sqlite_version'))}
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
        attested = progress.get('sqlite_attestation')
        if (attested and progress['code'] in ('sqlite_runtime_too_old', 'sqlite_runtime_unattested')
                and progress['stage'] == 'service' and attested['server'].get('state') == 'not_running'):
            # The readiness check refused the value published by the server: the report keeps the very
            # value that was read back (version included), never "not running".
            served = getattr(exc, 'sqlite_server', None)
            progress['sqlite_attestation'] = {**attested, 'server': dict(served) if isinstance(served, dict) else {
                'version': None, 'ok': False, 'state': 'unattested', 'code': progress['code']}}
    finally:
        steps = progress['steps']
        progress['timing'] = {'profile': profile, 'download_bytes': plan['provision']['download_bytes'],
                              'steps_ms': {name: steps[name]['elapsed_ms'] for name in steps if 'elapsed_ms' in steps[name]},
                              'total_s': clock() - began, 'clock': 'monotonic'}
        progress['elapsed_s'] = progress['timing']['total_s']
        record()
    return progress

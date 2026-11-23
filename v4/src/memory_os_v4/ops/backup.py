"""Encrypted backups and restore drills for the JSONL memory-os-v4 store (runbook RB-04).

A backup is *qualified* only with positive evidence that no writer can touch the store during the copy:
either the store's ``writer.lock`` is held exclusively by this process, or the launchd service was loaded, booted
out by this operation, and is verifiably gone (no launchd job, no ``memory_os_v4`` server process, port closed).
Otherwise the operation refuses (use the attested maintenance tooling) — a ``--no-stop`` copy is only a labelled,
non-qualified *diagnostic snapshot*. Only a service stopped by this operation is restarted.

Archives: strict snapshot (no links or special files, strict JSONL), tar of regular files only, AES-256-GCM
(``pip install 'memory-os-ai[ops]'``), exclusive 0600 publication without overwrite, fsync, full read-back
verification (decrypt, safe extract, exact inventory, hashes, item counts) BEFORE rotation. Rotation keeps the
newest archive of the 7 most recent distinct days and of the 4 most recent distinct ISO weeks. SQLite stores are
refused until the online backup API (ADR-001 S4).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import os
import stat
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath

from .auth import SecretFileError, read_secret_file, write_secret_file
from .settings import OpsSettings
from .storecheck import StoreCheckError, check_dir, check_files

MAGIC = b"MOSB1"  # MAGIC | nonce(12) | AESGCM(tar.gz, aad=MAGIC)
ROOT = "memory-os-v4"
ARCHIVE_GLOB = "memory-os-v4-*.mosb"
EXCLUDED = {"server.log"}
SQLITE_MARKERS = (".sqlite", ".sqlite3", ".db", "-wal", "-shm")


class BackupError(RuntimeError):
    pass


# ---------------------------------------------------------------------------------------------- crypto / key
def _aesgcm(key: bytes):
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:  # optional dependency
        raise BackupError("Encrypted backups need: pip install 'memory-os-ai[ops]'") from exc
    return AESGCM(key)


def load_key(path: Path) -> bytes:
    """Strict read of an existing key (owner-only regular 0600 file, one link, exactly 32 bytes)."""
    try:
        return read_secret_file(path, max_bytes=32, exact=32)
    except FileNotFoundError:
        raise SecretFileError(f"{path}: backup key missing (never recreated to decrypt)") from None


def load_or_create_key(path: Path) -> bytes:
    """Backups only: create the key exclusively when absent, otherwise validate it strictly."""
    if not path.exists() and not path.is_symlink():
        write_secret_file(path, os.urandom(32))
    return load_key(path)


def encrypt(data: bytes, key: bytes) -> bytes:
    nonce = os.urandom(12)
    return MAGIC + nonce + _aesgcm(key).encrypt(nonce, data, MAGIC)


def decrypt(blob: bytes, key: bytes) -> bytes:
    if blob[:5] != MAGIC or len(blob) < 17 + 16:
        raise ValueError("not a memory-os backup archive")
    return _aesgcm(key).decrypt(blob[5:17], blob[17:], MAGIC)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------------------------- snapshot
def snapshot(store: Path) -> dict[str, bytes]:
    store = store.resolve(strict=True)
    files: dict[str, bytes] = {}
    for path in sorted(store.rglob("*")):
        rel = path.relative_to(store).as_posix()
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise BackupError(f"link or special file refused in store: {rel}")
        if rel.endswith(SQLITE_MARKERS):
            raise BackupError("SQLite store: use the online backup API (ADR-001 S4), not a file copy")
        if stat.S_ISREG(mode) and path.name not in EXCLUDED and not rel.endswith(".tmp"):
            files[rel] = path.read_bytes()
    if "items.jsonl" not in files:
        raise BackupError("items.jsonl missing from store")
    return files


def build_archive(files: dict[str, bytes], manifest: dict) -> bytes:
    payload = dict(files)
    payload["MANIFEST.json"] = json.dumps(manifest, indent=1, ensure_ascii=False).encode()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for rel in sorted(payload):
            info = tarfile.TarInfo(f"{ROOT}/{rel}")
            info.size, info.mode, info.mtime = len(payload[rel]), 0o600, 0
            tar.addfile(info, io.BytesIO(payload[rel]))
    return buf.getvalue()


def extract(archive: Path, key: bytes, target: Path) -> Path:
    """Decrypt and extract safely: regular files and directories only, relative paths under ``memory-os-v4/``,
    no traversal, no duplicates."""
    with tarfile.open(fileobj=io.BytesIO(decrypt(archive.read_bytes(), key)), mode="r:gz") as tar:
        members, seen = tar.getmembers(), set()
        for m in members:
            p = PurePosixPath(m.name)
            if not (m.isfile() or m.isdir()) or p.is_absolute() or ".." in p.parts:
                raise BackupError(f"unsafe archive member: {m.name!r}")
            if p.parts[0] != ROOT or m.name in seen:
                raise BackupError(f"unexpected or duplicate archive member: {m.name!r}")
            seen.add(m.name)
        tar.extractall(target, members=members, filter="data")
    return target / ROOT


def _safe_rel(key) -> bool:
    if not isinstance(key, str) or not key or key == "MANIFEST.json" or "\\" in key or "\x00" in key:
        return False
    p = PurePosixPath(key)
    return not p.is_absolute() and str(p) == key and all(part not in ("", ".", "..") for part in p.parts)


def loads_manifest(data: bytes) -> dict:
    from .storecheck import loads_strict

    manifest = loads_strict(data)
    if not isinstance(manifest, dict):
        raise BackupError("manifest is not an object")
    return manifest


def verify_manifest(root: Path) -> tuple[dict, list[str]]:
    """Exact inventory. Manifest keys are validated BEFORE any filesystem access (canonical relative POSIX paths,
    no traversal); the set of regular files under ``root`` must equal the manifest; links or special files are
    refused; sizes, hashes and the strict store check must match."""
    manifest = loads_manifest((root / "MANIFEST.json").read_bytes())
    expected = manifest.get("files")
    if not isinstance(expected, dict):
        return manifest, ["manifest:files missing"]
    bad_keys = [k for k in expected if not _safe_rel(k)]
    if bad_keys:
        return manifest, [f"manifest:unsafe path {k!r}" for k in bad_keys]
    problems: list[str] = []
    actual: set[str] = set()
    for p in root.rglob("*"):
        rel = p.relative_to(root).as_posix()
        mode = p.lstat().st_mode
        if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            problems.append(f"special:{rel}")
        elif stat.S_ISREG(mode) and rel != "MANIFEST.json":
            actual.add(rel)
    problems += sorted(f"extra:{f}" for f in actual - set(expected))
    problems += sorted(f"missing:{f}" for f in set(expected) - actual)
    for rel in sorted(actual & set(expected)):
        meta, path = expected[rel], root / rel
        if (not isinstance(meta, dict) or path.stat().st_size != meta.get("bytes")
                or sha256(path) != meta.get("sha256")):
            problems.append(f"altered:{rel}")
    if not problems:
        try:
            checked = check_dir(root, sorted(actual))
        except (StoreCheckError, OSError) as exc:
            problems.append(f"store_check:{exc}")
        else:
            if checked["items"] != manifest.get("items"):
                problems.append("items:counts differ from manifest")
    return manifest, problems


# ---------------------------------------------------------------------------------------------- service control
def _launchctl(*args: str) -> int:
    try:
        return subprocess.run(["launchctl", *args], capture_output=True, check=False, timeout=120).returncode
    except (OSError, subprocess.SubprocessError):
        return -1  # a command that did not answer is a failure, never a success


def _manager():
    """The service manager of this machine (``ops/service.py``); launchd where there is none, as before 4.1.1."""
    from . import service
    return service.host_manager() or service.Launchd()


def _command(argv: list) -> int:
    """Return code of one manager command; launchd commands keep going through ``_launchctl``."""
    if argv[0] == "launchctl":
        return _launchctl(*argv[1:])
    try:
        return subprocess.run(argv, capture_output=True, check=False, timeout=120).returncode
    except (OSError, subprocess.SubprocessError):
        return -1


def _service_status(label: str, *, timeout: float = 5.0) -> str:
    """``running`` | ``stopped`` | ``unknown``, used only to WAIT for the stop this backup already sent,
    never to decide a stop nor a restart (those need the attested state, ``_attested_state``).

    ``stopped`` needs a positive answer of the manager (``status`` of ``ops/service.py``):
    launchd's documented absence (return code 113 AND "Could not find service"), or a complete
    and valid ``systemctl show`` saying stopped. Anything else is indeterminate: it keeps the
    wait going, then fails it.
    """
    try:
        return _manager().status(label, run=subprocess.run, timeout=timeout)
    except Exception:  # an observation that fails is indeterminate, whatever the failure
        return "unknown"


def _service_loaded(label: str) -> bool:
    """True unless the manager positively says stopped (an indeterminate answer counts as loaded)."""
    return _service_status(label) != "stopped"


def _attested_state(settings: OpsSettings, manager) -> dict:
    """Attested state of OUR service before (and, for systemd, after) a backup; refuses whatever is not established.

    The reference is INDEPENDENT of the definition being attested: the definition on disk must
    be the one ``render`` produces for the settings of this command (program = the Python
    running it, arguments, whole environment, log; for a unit, compared by the closed parser,
    which refuses any directive or section ``render`` does not write), or the file whose digest
    the trusted install journal recorded when it created it (``uninstall._service``, the rule
    the uninstall already applies). There is no other way in: a definition written by hand
    (``setup --write-launchd``) is refused, whatever it contains. The manager must then attest
    that the loaded job is that very definition (program, arguments, token file, store:
    ``observe``, as quickstart does).
    Indeterminate or foreign is a refusal raised BEFORE any mutation: nothing is stopped.
    Returns ``{"active", "running", "pid", "unit_file_state"}`` (the last is None with launchd;
    ``pid`` is the process of the attested job, launchd ``pid`` / systemd ``MainPID``, else None).
    ``active``: the job is loaded / the unit is not in a terminal stopped state, so it must be
    stopped before the copy. ``running``: a PROCESS of that attested job exists now (launchd: PID
    > 0 and ``state = running``; systemd: ``ActiveState=active``, ``SubState=running``, ``MainPID``
    > 0). Only ``running`` may ever be reported as a service that runs or was put back.
    """
    import sys

    from . import uninstall
    label = settings.service_label
    home = Path.home()
    seen: dict = {}  # the FULL observation of the job attested against the owned definition (one command)
    try:
        facts = uninstall._service(settings, home, str(Path(sys.executable).absolute()), run=subprocess.run,
                                   observation=seen)
    except Exception:  # an unreadable definition or journal establishes nothing
        raise BackupError("the service definition of this installation cannot be established; nothing stopped") from None
    path = manager.definition_path(label, home)
    launchd = manager.kind == "launchd"
    if facts.get("state") == "absent" or facts.get("path") is None:
        # No definition of ours: the name must be free too, else something else runs under it.
        observed = manager.observe({"label": label, "path": str(path), "argv": [""], "env": {}}, run=subprocess.run)
        if observed["state"] != "absent":
            raise BackupError("a service of this label exists but this installation has no definition, or its "
                              "state cannot be read; nothing stopped")
        return {"active": False, "running": False, "pid": None, "unit_file_state": None if launchd else "not-found"}
    if facts["state"] != "owned":
        raise BackupError(f"{manager.noun} not written by this installation; nothing stopped; reinstall with "
                          "quickstart or stop the service yourself (the definition on disk is neither the one "
                          "quickstart writes for these settings nor one an install journal attests)")
    if facts["env"].get("MEMORY_V4_DIR") != str(settings.store_dir):
        raise BackupError("the service does not serve the store being backed up; nothing stopped")
    observed = seen if seen.get("state") == facts.get("loaded_state") else {"state": "unknown"}
    if observed["state"] == "absent":
        return {"active": False, "running": False, "pid": None, "unit_file_state": None if launchd else "disabled"}
    if observed["state"] != "loaded":
        raise BackupError("the running service cannot be attested as the one of this installation ("
                          + str(observed["state"]) + "); nothing stopped")
    running = observed.get("running") is True
    pid = observed.get("pid") if running and isinstance(observed.get("pid"), int) else None
    if launchd:
        return {"active": True, "running": running, "pid": pid, "unit_file_state": None}
    return {"active": bool(observed["active"]), "running": running, "pid": pid,
            "unit_file_state": observed["unit_file_state"]}


def _definition_digest(settings: OpsSettings, manager) -> str | None:
    """SHA-256 of the service definition read WITHOUT following a link (plain single-link file), else None.

    Taken when the service is attested before the stop and compared right before a restart: the
    definition started again must be, byte for byte, the one that was attested.
    """
    from . import quickstart
    try:
        data = quickstart._read_verified(manager.definition_path(settings.service_label, Path.home()))
    except Exception:  # a link, a directory, an unreadable path: not the attested file
        return None
    return None if data is None else sha256_bytes(data)


def _same_service(after, before) -> bool:
    """Same attested activity and activation (the PID and the ``running`` fact are judged apart)."""
    return (isinstance(after, dict) and isinstance(before, dict)
            and after.get("active") == before.get("active")
            and after.get("unit_file_state") == before.get("unit_file_state"))


def _restart_decision(settings: OpsSettings, manager, before: dict, digest_before, stop_confirmed: bool) -> str:
    """What to do about the service after OUR stop command, decided on observations only.

    Never from the return code of the stop, and never from the unattested ``status``: the
    definition must still be the attested file (same bytes, no link), the complete attestation
    (``_attested_state``: definition ours, job observed by the strict ``observe``) must succeed
    again, and a fresh writer/port check (``_no_writer``) must positively return ``none`` — even
    when the earlier stop had been confirmed (a writer or port listener may have appeared during
    the snapshot). A substituted/foreign definition, a live writer/port, or any failed/indeterminate
    observation forbids the restart. Returns the ``restart`` value of the record; only the two
    ``after-…`` values allow the start command.

    Order: definition and attestation are read first (a cheap refusal, and the only way to say
    "still running"), then the writer and port probes, which may be long. EVERYTHING that
    authorises the start is then read AGAIN, after those probes: the attestation, and last the
    bytes of the definition. An ``after-…`` value is returned only by the statement that follows
    this second reading; the caller sends the start command immediately after it and never
    starts a definition that was not revalidated at that moment.
    """
    def identity():
        """Bytes then attestation of the definition, now; a refusal value, or None with the attested state."""
        if digest_before is None or _definition_digest(settings, manager) != digest_before:
            return "not-restarted-definition-changed", None
        try:
            after = _attested_state(settings, manager)
        except Exception:  # foreign, unattested or unreadable: nothing is started
            return "not-attempted-state-unknown", None
        if after.get("active"):
            # Exact wording (review round 11): "still running" only with a process of the attested job; a
            # job that is loaded, starting or waiting for a restart is neither running nor restartable here.
            return ("not-needed-service-still-running" if after.get("running") is True
                    else "not-restarted-service-not-running"), None
        if after.get("unit_file_state") != before.get("unit_file_state"):
            return "not-restarted-definition-changed", None  # the activation is no longer the attested one
        return None, after
    refusal, _ = identity()
    if refusal:
        return refusal
    writers = _no_writer(settings)
    if writers == "alive":
        return "not-restarted-writer-alive"
    if writers != "none":
        return "not-restarted-writer-unknown"
    # The probes above took time: what they found says nothing about the definition NOW.
    refusal, _ = identity()
    if refusal:
        return refusal
    # Declared TOCTOU limit (contract rev. 5, D6 bis): a writer appearing, or the definition being
    # replaced, between these last readings and the start command is not excluded; there is no
    # atomic transaction over the manager, the port and the file. The readings are as late as possible.
    if _definition_digest(settings, manager) != digest_before:  # last look, right before the start
        return "not-restarted-definition-changed"
    return "after-confirmed-stop" if stop_confirmed else "after-manager-attested-stopped"


def _restoration(settings: OpsSettings, manager, before: dict, digest_before) -> tuple:
    """``(attested state now | None, restored)``: THE final proof of a service put back, read LAST.

    Called after the health wait, never before it: whatever happened while waiting (the service
    vanished, its definition was replaced) is seen here. ``restored`` needs the definition to be
    the attested bytes before AND after a complete attestation, that attestation to say the same
    activity and activation as ``before``, a PROCESS of the attested job to exist now
    (``running``), AND the HTTP responder to BE that process: ``/health`` must say ``ok``, name
    itself ``memory-os-v4`` and give the very PID the manager attests (``_responder_pid``). A
    loaded job, a unit that is starting or waiting for a restart, an attested process that does
    not serve the port yet, or an HTTP 200 of any other process is never a service put back.
    """
    if digest_before is None or _definition_digest(settings, manager) != digest_before:
        attested = None
        try:
            attested = _attested_state(settings, manager)
        except Exception:
            pass
        return attested, False
    try:
        attested = _attested_state(settings, manager)
    except Exception:
        return None, False
    unchanged = _definition_digest(settings, manager) == digest_before
    proven = bool(unchanged and attested.get("running") is True and _same_service(attested, before))
    if proven:
        pid = attested.get("pid")
        proven = isinstance(pid, int) and pid > 0 and _responder_pid(settings) == pid
    return attested, proven


def _responder_pid(settings: OpsSettings) -> int | None:
    """PID announced by the ``memory-os-v4`` server answering ``/health`` now, or None (``ops/auth.py``)."""
    from .auth import health_responder_pid
    try:
        return health_responder_pid(settings)
    except Exception:  # an identification that fails identifies nobody
        return None


def _service_stop(label: str) -> int:
    manager = _manager()
    # systemd: ``stop`` / ``start`` leave the activation (enabled or disabled) exactly as it was.
    return _command(manager.stop_argv(label) if manager.kind == "launchd" else manager.pause_argv(label))


def _service_start(settings: OpsSettings) -> int:
    manager = _manager()
    if manager.kind != "launchd":
        return _command(manager.resume_argv(settings.service_label))
    return _command(manager.start_argv(settings.service_label, manager.definition_path(settings.service_label, Path.home())))


WRITER_PATTERN = "memory_os_v4.server|memory-os-(ai|v4) .*--transport"


def _writer_processes(*, timeout: float = 10.0) -> list[str] | None:
    """Lines of the writer processes seen by ``pgrep``; ``[]`` when it says there is none; None when
    the observation failed.

    ``pgrep`` documents its return codes: 0 (at least one match), 1 (no match), 2 (syntax error),
    3 (fatal error). ``[]`` is ONLY return code 1 with no line printed. Code 0 gives the lines
    (each must start with a process number). Any other code, a line of another shape, a timeout
    or a command that cannot be run is None: indeterminate, never "no writer".
    """
    try:
        result = subprocess.run(["pgrep", "-fl", WRITER_PATTERN], capture_output=True, text=True, check=False,
                                timeout=timeout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    lines = [line for line in (result.stdout or "").splitlines() if line.strip()]
    if result.returncode == 1:
        return [] if not lines else None
    if result.returncode != 0 or not lines or any(not line.split()[0].isdigit() for line in lines):
        return None
    return [line for line in lines if line.split()[0] != str(os.getpid())]


def _port_state(port: int, *, timeout: float = 0.5) -> str:
    """``listening`` | ``free`` | ``unknown`` (``ops/service.py``): ``free`` is only a refused connection;
    a timeout, a permission error or any other failure is ``unknown``, never a closed port."""
    from . import service
    return service.port_state(port, timeout=timeout)


def _no_writer(settings: OpsSettings, *, timeout: float | None = None) -> str:
    """``none`` | ``alive`` | ``unknown``: writer processes and port of the service, read strictly.

    ``none`` needs BOTH observations to succeed and to be negative: ``pgrep`` answering "no
    match" and the port refusing the connection. ``alive`` is a positive sighting (a process, or
    a listening port). Everything else is ``unknown``. When ``timeout`` is given, ONE shared
    monotonic deadline bounds BOTH observations together (never applied twice).
    """
    try:
        if timeout is None:
            writers = _writer_processes()
            if writers:
                return "alive"
            port = _port_state(settings.port)
        else:
            deadline = time.monotonic() + max(0.0, float(timeout))
            writers = _writer_processes(timeout=max(0.05, min(10.0, deadline - time.monotonic())))
            if writers:
                return "alive"
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return "unknown"
            port = _port_state(settings.port, timeout=min(0.5, remaining))
    except Exception:
        return "unknown"
    if writers or port == "listening":
        return "alive"
    return "none" if writers == [] and port == "free" else "unknown"


def _wait_stopped(settings: OpsSettings, label: str, timeout: float = 30.0) -> bool:
    """True only when, in one round that ENDS before ``timeout`` (monotonic clock), the manager says
    stopped AND no writer process is seen AND the port refuses the connection.

    ``timeout`` is the total time. Each observation is limited by what remains of it (never
    below 0.05 s, the smallest limit a command can be given), no round starts at or after the
    deadline except the first one, and a round that ends after the deadline is never a
    success, whatever it saw. An observation that fails, times out or is incomplete never ends
    the wait with True.
    """
    deadline = time.monotonic() + timeout

    def left():
        return max(0.05, deadline - time.monotonic())
    while True:
        stopped = _service_status(label, timeout=min(5.0, left())) == "stopped"
        confirmed = stopped and _no_writer(settings, timeout=left()) == "none"
        remaining = deadline - time.monotonic()
        if confirmed:
            return remaining >= 0  # seen in time; a success obtained after the deadline is refused
        if remaining <= 0:
            return False
        time.sleep(min(1.0, remaining))
        if deadline - time.monotonic() <= 0:
            return False


def _try_writer_lock(store: Path):
    """``None`` when the store has no writer.lock (4.0.0 engines), ``"busy"`` when another process holds it,
    else an open file holding an exclusive lock (candidate engines hold it while open)."""
    lock_path = store / "writer.lock"
    if not lock_path.exists() and not lock_path.is_symlink():
        return None
    if lock_path.is_symlink() or not lock_path.is_file():
        raise BackupError("writer.lock is not a regular file")
    import fcntl

    fh = lock_path.open("r+b")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return "busy"
    return fh


# ---------------------------------------------------------------------------------------------- backup
def backup(settings: OpsSettings | None = None, *, stop_service: bool = True, now: dt.datetime | None = None) -> dict:
    settings = settings or OpsSettings.from_env()
    key = load_or_create_key(settings.backup_key)
    stamp = (now or dt.datetime.now()).strftime("%Y%m%d-%H%M%S")
    record: dict = {"stamp": stamp, "qualified": False, "verified": False, "archive_ok": False, "ok": False}
    stop_sent = False       # our stop command was sent (whatever it returned): NOT a stop
    stop_confirmed = False  # the stop was observed (manager, processes, port): the only "stopped by us"
    systemd_before = None
    before = digest_before = None
    lock = None
    try:
        files = None
        if stop_service:
            label = settings.service_label
            if not label:
                raise BackupError("a qualified backup needs MEMORY_V4_SERVICE_LABEL")
            # A free writer.lock is NOT proof: a legacy 4.0.0 writer ignores it (A6 R1b). Establish the service
            # state first; a loaded service is always stopped and verified gone; an already-stopped one keeps its
            # state and must show no writer process and a closed port.
            manager = _manager()
            # Identity and state attested first, on both managers; indeterminate or foreign is raised here,
            # before any mutation (4.1.1: a loaded launchd job is attested ours before any bootout).
            # The bytes are read BEFORE and AFTER the attestation: what was attested is exactly the file
            # whose digest the restart will require (a file replaced in between is refused here).
            digest_first = _definition_digest(settings, manager)
            before = _attested_state(settings, manager)
            digest_before = _definition_digest(settings, manager)  # the bytes attested, for the restart
            if digest_before != digest_first:
                raise BackupError("the service definition changed while it was being attested; nothing stopped")
            if manager.kind != "launchd":
                systemd_before = before
                record["service_state_before"] = before
            running = before["active"]
            if running:
                # Set BEFORE the command: a stop that fails or does not answer may still have taken
                # effect, so what follows it is decided by observing the manager, never by its code.
                stop_sent = True
                if _service_stop(label) != 0:
                    raise BackupError("launchctl bootout failed" if manager.kind == "launchd"
                                      else "systemctl --user stop failed")
                stop_confirmed = bool(_wait_stopped(settings, label))
                if not stop_confirmed:
                    raise BackupError("stop not confirmed after bootout: the service, a writer process or the "
                                      "port is still alive, or their state could not be observed")
                evidence = "verified_bootout"
            else:
                writers = _no_writer(settings)
                if writers == "alive":
                    raise BackupError("service not loaded but a writer process or the port is alive: cannot prove "
                                      "that no writer is running; use the attested offline maintenance backup")
                if writers != "none":
                    raise BackupError("service not loaded but the writer processes or the port could not be "
                                      "observed (pgrep or the connection failed): an observation that fails is "
                                      "not the absence of a writer; nothing qualified")
                evidence = "verified_no_writer"
            lock = _try_writer_lock(settings.store_dir)
            if lock == "busy":
                raise BackupError("writer.lock is held although no managed writer runs")
            record["evidence"] = evidence + ("+writer_lock" if lock else "")
            files = snapshot(settings.store_dir)
            # The absence of a writer must hold over the WHOLE copy (D6 bis): processes and port are
            # probed again AFTER it. A writer seen, or a probe that fails, qualifies nothing: no
            # archive is published, so nothing enters the rotation nor ``verified_archives``.
            writers_after = _no_writer(settings)
            if writers_after != "none":
                files = None
                raise BackupError(
                    "a writer process or the port is alive after the copy: the snapshot may not be consistent; "
                    "nothing qualified" if writers_after == "alive" else
                    "the writer processes or the port could not be observed after the copy (an observation "
                    "that fails is not the absence of a writer); nothing qualified")
            record["qualified"] = True
        else:
            files = snapshot(settings.store_dir)
            record["evidence"] = "none (diagnostic snapshot, writers may be running)"
        try:
            checked = check_files(files)
        except StoreCheckError as exc:
            raise BackupError(f"store failed strict validation: {exc}") from None
        items = checked["items"]
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
        items = None
    finally:
        if lock not in (None, "busy"):
            lock.close()
        if stop_sent:
            # The restart is founded on OBSERVATIONS (``_restart_decision``), never on the return
            # code of the stop (zero or not) nor on an unattested status: the definition is still
            # the attested file, byte for byte, the complete attestation succeeds again, the
            # service is seen stopped, and a fresh check confirms no writer process or listening
            # port. In every other case nothing is started, and the report says why, with the
            # command to run by hand. A service put back is ATTESTED, after the health wait
            # (``_restoration``): definition unchanged, attestation equal to ``before``, and a
            # process of our job (never a loaded job or an HTTP answer alone).
            record["stop_confirmed"] = stop_confirmed
            manager = _manager()
            manual_cmd = (manager.manual_start(settings.service_label) if manager.kind == "launchd"
                          else " ".join(manager.resume_argv(settings.service_label)))
            record["restart"] = _restart_decision(settings, manager, before or {}, digest_before, stop_confirmed)
            if record["restart"].startswith("after-"):
                # Sent right after the revalidation that ends ``_restart_decision``: nothing in between.
                record["restart_rc"] = _service_start(settings)
                healthy = (_wait_healthy(settings) if record["restart_rc"] == 0
                           else _wait_healthy(settings, timeout=10.0))
            else:
                healthy = _wait_healthy(settings, timeout=10.0)
            record["server_restarted_healthy"] = healthy
            # Final proof, read AFTER the health wait in every branch (restarted or found still
            # running): definition unchanged, attestation equal to ``before``, a process of OUR job.
            attested_after, proven = _restoration(settings, manager, before or {}, digest_before)
            if systemd_before is not None:
                record["service_state_after"] = attested_after
                record["service_state_restored"] = proven
            expected = (record.get("restart_rc") == 0 if record["restart"].startswith("after-")
                        else record["restart"] == "not-needed-service-still-running")  # never "…-not-running"
            record["service_restored"] = bool(expected and healthy is True and proven)
            if not record["service_restored"]:
                record["restart_manual"] = manual_cmd
    if items is None:
        _log(settings, "backup.jsonl", record)
        return record
    manifest = {"format": "memory-os-v4-backup/2", "created": stamp, "qualified": record["qualified"],
                "evidence": record["evidence"],
                "files": {rel: {"sha256": sha256_bytes(b), "bytes": len(b)} for rel, b in files.items()},
                "items": items, "store_check": checked}
    blob = encrypt(build_archive(files, manifest), key)
    name = f"memory-os-v4-{stamp}{'' if record['qualified'] else '-diagnostic'}.mosb"
    try:
        final = _publish(settings.backup_dir, name, blob)
        record.update(archive=name, bytes=len(blob), archive_sha256=sha256_bytes(blob), items=items)
        with tempfile.TemporaryDirectory(prefix="mosv-") as tmp:
            _, problems = verify_manifest(extract(final, key, Path(tmp)))
        record["verified"] = not problems
        record["verify_problems"] = problems
        if record["verified"] and record["qualified"]:
            eligible = verified_archives(settings)
            eligible[name] = (record["archive_sha256"], stamp)  # pin the archive just verified
            record["rotated_out"] = rotate(settings.backup_dir, eligible, pinned=name)
            if not final.is_file() or sha256(final) != record["archive_sha256"]:
                raise BackupError("current archive missing or altered after rotation")
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
        record["verified"] = False
    # Archive validity (``archive_ok``) is separated from operational restoration of the service
    # (``service_restored``): a qualified, verified archive stays eligible in ``verified_archives``
    # even when the service failed to restart afterward, while ``ok`` requires BOTH so an
    # un-restored service never passes as an overall success.
    record["archive_ok"] = bool(record["qualified"] and record["verified"])
    record["ok"] = bool(record["verified"] and record.get("service_restored", True))
    _log(settings, "backup.jsonl", record)
    return record


def _publish(dest: Path, name: str, blob: bytes) -> Path:
    dest.mkdir(parents=True, exist_ok=True, mode=0o700)
    final, part = dest / name, dest / (name + ".part")
    fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, blob)
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.link(part, final)  # fails if the name exists: never overwrite an archive
    finally:
        part.unlink()
    dfd = os.open(dest, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)
    return final


def _wait_healthy(settings: OpsSettings, timeout: float = 90.0) -> bool:
    from .auth import health_ok

    deadline = time.time() + timeout
    while time.time() < deadline:
        if health_ok(settings):
            return True
        time.sleep(2)
    return False


def archive_valid(rec: dict) -> bool:
    """THE validity of a logged archive, for every reader (rotation, health, doctor).

    ``archive_ok`` when the record has it (4.1.1: the archive itself, whatever happened to the
    service afterwards); records without it (older ones, SQLite snapshots) keep ``ok``.
    """
    return rec.get("archive_ok") is True if "archive_ok" in rec else bool(rec.get("ok"))


def verified_archives(settings: OpsSettings) -> dict[str, tuple[str, str]]:
    """{archive name: (sha256, stamp)} for archives logged as qualified and verified.

    Archive validity (``archive_ok``) is separate from post-backup service restoration: a qualified,
    verified archive stays eligible for rotation/restore even if restarting the service afterward
    failed (``ok=False``). Legacy/SQLite records without ``archive_ok`` keep requiring ``ok``.
    """
    out: dict[str, tuple[str, str]] = {}
    log = settings.ops_dir / "logs/backup.jsonl"
    if not log.exists():
        return out
    for line in log.read_text().splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        if (archive_valid(rec) and rec.get("qualified") and rec.get("verified")
                and isinstance(rec.get("archive"), str) and isinstance(rec.get("archive_sha256"), str)):
            out[rec["archive"]] = (rec["archive_sha256"], str(rec.get("stamp", "")))
    return out


def rotate(dest: Path, eligible: dict[str, tuple[str, str]], *, pinned: str, days: int = 7,
           weeks: int = 4, fault_hook=None) -> list[str]:
    """Retention among archives logged as verified AND still intact on disk: keep the newest of the ``days`` most
    recent distinct days and of the ``weeks`` most recent distinct ISO weeks, plus ``pinned``. Unknown, unverified,
    altered or diagnostic files are never deleted (and never count as a backup)."""
    candidates: list[tuple[dt.date, str, Path]] = []
    for name, (digest, stamp) in eligible.items():
        path = dest / name
        try:
            day = dt.datetime.strptime(stamp, "%Y%m%d-%H%M%S").date()
        except ValueError:
            continue
        if path.is_file() and not path.is_symlink() and sha256(path) == digest:
            candidates.append((day, stamp, path))
    candidates.sort(key=lambda c: c[1])
    newest_per_day: dict[dt.date, Path] = {}
    for day, _, path in candidates:
        newest_per_day[day] = path
    keep = {newest_per_day[d] for d in sorted(newest_per_day)[-days:]}
    newest_per_week: dict[tuple, Path] = {}
    for d in sorted(newest_per_day):
        newest_per_week[d.isocalendar()[:2]] = newest_per_day[d]
    keep |= {newest_per_week[w] for w in sorted(newest_per_week)[-weeks:]}
    keep.add(dest / pinned)
    removed = []
    for _, _, path in candidates:
        if path not in keep:
            if fault_hook:
                fault_hook('before_rotation_unlink')
            # Revalidate immediately before deleting; altered evidence is retained.
            if path.is_symlink() or path.stat().st_nlink != 1 or sha256(path) != eligible[path.name][0]:
                continue
            path.unlink()
            removed.append(path.name)
            if fault_hook:
                fault_hook('after_rotation_unlink')
    return removed


# ---------------------------------------------------------------------------------------------- restore drill
def restore_drill(settings: OpsSettings | None = None, archive: Path | None = None, *,
                  start_server: bool = True, port: int = 8799) -> dict:
    """Decrypt into a throw-away directory, verify the exact inventory; with ``start_server`` also serve it on a
    temporary port and compare item counts (``level='service'``), else ``level='files'`` only. Never touches the
    live store; never recreates a missing key."""
    settings = settings or OpsSettings.from_env()
    t0 = time.time()
    record: dict = {"ok": False, "level": "service" if start_server else "files"}
    try:
        key = load_key(settings.backup_key)
        candidates = sorted(p for p in settings.backup_dir.glob(ARCHIVE_GLOB) if "-diagnostic" not in p.name)
        archive = archive or (candidates[-1] if candidates else None)
        if archive is None:
            raise BackupError("no qualified archive found")
        record["archive"] = archive.name
        with tempfile.TemporaryDirectory(prefix="mosr-") as tmp:
            root = extract(archive, key, Path(tmp))
            manifest, problems = verify_manifest(root)
            created = dt.datetime.strptime(manifest["created"], "%Y%m%d-%H%M%S")
            record.update(manifest_problems=problems, qualified=manifest.get("qualified", False),
                          backup_age_hours=round((dt.datetime.now() - created).total_seconds() / 3600, 2),
                          expected_live_total=manifest["items"]["live_total"])
            if start_server and not problems:
                record["server_check"] = _serve_and_count(root, settings, port, manifest["items"]["live_total"])
            record["ok"] = not problems and (not start_server or record.get("server_check", {}).get("match") is True)
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
    record["duration_s"] = round(time.time() - t0, 1)
    _log(settings, "restore_drill.jsonl", record)
    return record


def _serve_and_count(root: Path, settings: OpsSettings, port: int, expected: int) -> dict:
    import sys

    import anyio

    from .auth import call_tool

    env = dict(os.environ, MEMORY_V4_DIR=str(root), MEMORY_V4_PROJECT="*", MEMORY_V4_TOKEN_FILE=str(settings.token_file))
    proc = subprocess.Popen([sys.executable, "-m", "memory_os_v4.server", "--transport", "http", "--host",
                             "127.0.0.1", "--port", str(port)], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    tmp_settings = OpsSettings(**{**settings.__dict__, "url": f"http://127.0.0.1:{port}/mcp/"})
    witness = _witness(root)
    try:
        from .auth import http_get

        deadline = time.time() + 120
        while time.time() < deadline:
            if proc.poll() is not None:
                return {"error": f"temporary server exited ({proc.returncode})"}
            try:
                status = anyio.run(call_tool, "memory_status", {}, tmp_settings)
            except Exception:
                time.sleep(3)
                continue
            ready, body = http_get(tmp_settings.ready_url, tmp_settings)
            try:
                reason = json.loads(body or b"{}").get("reason")
            except ValueError:
                reason = None
            found = None
            if witness:
                hits = anyio.run(call_tool, "memory_search",
                                 {"query": witness[1], "project": witness[2], "top_k": 5}, tmp_settings)
                found = witness[0] in {h.get("id") for h in hits.get("hits", [])}
            # 404: server predates /ready; 503 build_unqualified concerns the code identity, not the restored data
            ready_ok = ready in (200, 404) or (ready == 503 and reason == "build_unqualified")
            return {"status_total": status["total"], "expected_total": expected, "ready_status": ready,
                    "ready_reason": reason, "witness_found": found,
                    "match": status["total"] == expected and ready_ok and found is not False}
        return {"error": "temporary server did not answer"}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()


def _witness(root: Path) -> tuple[str, str, str] | None:
    """(id, query, project) of the newest live item with enough text: restore must find it again by search."""
    best = None
    for line in (root / "items.jsonl").read_bytes().splitlines():
        row = json.loads(line)
        if row.get("deleted") or not isinstance(row.get("text"), str) or len(row["text"].split()) < 4:
            continue
        best = (row["id"], " ".join(row["text"].split()[:12]), row.get("project", "default"))
    return best


def _log(settings: OpsSettings, name: str, record: dict) -> None:
    logs = settings.ops_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    with (logs / name).open("a") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")

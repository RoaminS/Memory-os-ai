"""ADR-001 S2: SQLite authority, with injectable write boundaries.

The existing sqlite_s1 flag remains experimental. No implicit JSONL migration.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from threading import RLock
import time
from urllib.parse import quote

from .config import LEXICAL_MARKER, PROFILE_LEXICAL, PROFILE_SEMANTIC
from .store import MemoryItem, Store, VALID_KINDS

MIN_SQLITE = (3, 51, 3)
APPLICATION_ID = 0x4D4F5334
SCHEMA_VERSION = 8

SCHEMA = (
    "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, checksum TEXT NOT NULL, applied_at REAL NOT NULL)",
    "CREATE TABLE store_metadata(key TEXT PRIMARY KEY, value_json TEXT NOT NULL)",
    """CREATE TABLE items(
        id TEXT PRIMARY KEY CHECK(length(id)>0), project TEXT NOT NULL CHECK(length(project)>0 AND project<>'*'),
        kind TEXT NOT NULL, current_revision INTEGER NOT NULL CHECK(current_revision>0),
        FOREIGN KEY(id,current_revision) REFERENCES item_versions(item_id,revision) DEFERRABLE INITIALLY DEFERRED)""",
    """CREATE TABLE item_versions(
        item_id TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision>0), record_json TEXT NOT NULL,
        record_sha256 TEXT NOT NULL, recorded_at REAL NOT NULL, PRIMARY KEY(item_id,revision),
        FOREIGN KEY(item_id) REFERENCES items(id) DEFERRABLE INITIALLY DEFERRED)""",
    "CREATE INDEX items_project_kind ON items(project,kind)",
    "CREATE TRIGGER item_versions_no_update BEFORE UPDATE ON item_versions BEGIN SELECT RAISE(ABORT,'immutable version'); END",
    "CREATE TRIGGER item_versions_no_delete BEFORE DELETE ON item_versions BEGIN SELECT RAISE(ABORT,'immutable version'); END",
)

SCHEMA_S2 = (
    """CREATE TABLE block_versions(
        sequence INTEGER PRIMARY KEY, block_key TEXT NOT NULL,
        record_json TEXT NOT NULL, record_sha256 TEXT NOT NULL)""",
    "CREATE INDEX block_versions_key ON block_versions(block_key,sequence)",
    "CREATE TRIGGER block_versions_no_update BEFORE UPDATE ON block_versions BEGIN SELECT RAISE(ABORT,'immutable version'); END",
    "CREATE TRIGGER block_versions_no_delete BEFORE DELETE ON block_versions BEGIN SELECT RAISE(ABORT,'immutable version'); END",
    """CREATE TABLE request_receipts(
        sequence INTEGER PRIMARY KEY, scope TEXT NOT NULL, request_id TEXT NOT NULL,
        record_json TEXT NOT NULL, record_sha256 TEXT NOT NULL, UNIQUE(scope,request_id))""",
    "CREATE TRIGGER receipts_no_update BEFORE UPDATE ON request_receipts BEGIN SELECT RAISE(ABORT,'immutable receipt'); END",
    "CREATE TRIGGER receipts_no_delete BEFORE DELETE ON request_receipts BEGIN SELECT RAISE(ABORT,'immutable receipt'); END",
)

SCHEMA_CACHE = (
    """CREATE TABLE embedding_cache(
        cache_key TEXT PRIMARY KEY, dim INTEGER NOT NULL CHECK(dim>0),
        vector BLOB NOT NULL, vector_sha256 TEXT NOT NULL)""",
)

SCHEMA_RETENTION = (
    "CREATE TABLE receipt_counter(id INTEGER PRIMARY KEY CHECK(id=1), value INTEGER NOT NULL)",
    "INSERT INTO receipt_counter SELECT 1, COALESCE(max(sequence),0) FROM request_receipts",
    "DROP TRIGGER receipts_no_delete",
    """CREATE TRIGGER receipts_no_delete BEFORE DELETE ON request_receipts
        WHEN COALESCE(json_extract(OLD.record_json,'$.explicit'),1)<>0
        BEGIN SELECT RAISE(ABORT,'immutable explicit receipt'); END""",
    "CREATE INDEX receipts_explicit ON request_receipts(COALESCE(json_extract(record_json,'$.explicit'),1),sequence)",
)

SCHEMA_LEGACY = (
    "CREATE TABLE legacy_files(path TEXT PRIMARY KEY, contents BLOB NOT NULL, sha256 TEXT NOT NULL)",
    "CREATE TABLE legacy_request_log(ordinal INTEGER PRIMARY KEY, record_json TEXT NOT NULL, record_sha256 TEXT NOT NULL)",
    """CREATE TABLE legacy_pending_requests(scope TEXT NOT NULL, request_id TEXT NOT NULL,
        record_json TEXT NOT NULL, record_sha256 TEXT NOT NULL, PRIMARY KEY(scope,request_id))""",
    *tuple(f"CREATE TRIGGER {table}_no_{op} BEFORE {op.upper()} ON {table} BEGIN SELECT RAISE(ABORT,'immutable legacy'); END"
           for table in ("legacy_files", "legacy_request_log", "legacy_pending_requests") for op in ("update", "delete")),
)

SCHEMA_WORK = (
    '''CREATE TABLE task_versions(
        task_id TEXT NOT NULL,revision INTEGER NOT NULL CHECK(revision>0),project TEXT NOT NULL,
        record_json TEXT NOT NULL,record_sha256 TEXT NOT NULL,PRIMARY KEY(task_id,revision))''',
    '''CREATE TABLE task_current(task_id TEXT PRIMARY KEY,revision INTEGER NOT NULL,
        FOREIGN KEY(task_id,revision) REFERENCES task_versions(task_id,revision))''',
    'CREATE INDEX task_versions_project ON task_versions(project,task_id,revision)',
    "CREATE TRIGGER tasks_no_update BEFORE UPDATE ON task_versions BEGIN SELECT RAISE(ABORT,'immutable task'); END",
    "CREATE TRIGGER tasks_no_delete BEFORE DELETE ON task_versions BEGIN SELECT RAISE(ABORT,'immutable task'); END",
)

# Rebuildable current-task lookup; immutable task_versions remain the authority.
TASK_LOOKUP_SELECT = """SELECT c.task_id,c.revision,v.project,
    json_extract(v.record_json,'$.status'),
    CAST(strftime('%s',substr(json_extract(v.record_json,'$.updated_at'),1,19)||'+00:00') AS REAL) +
    CASE WHEN instr(json_extract(v.record_json,'$.updated_at'),'.')>0
    THEN CAST('0.'||substr(json_extract(v.record_json,'$.updated_at'),21) AS REAL) ELSE 0 END,
    json_extract(v.record_json,'$.owner.client_id')
    FROM task_current c JOIN task_versions v ON v.task_id=c.task_id AND v.revision=c.revision"""
SCHEMA_WORK_LOOKUP = (
    "CREATE TABLE task_lookup(task_id TEXT PRIMARY KEY,revision INTEGER NOT NULL,project TEXT NOT NULL,"
    "status TEXT NOT NULL,updated_epoch REAL NOT NULL,owner_id TEXT,"
    "FOREIGN KEY(task_id,revision) REFERENCES task_versions(task_id,revision))",
    "INSERT INTO task_lookup " + TASK_LOOKUP_SELECT,
    "CREATE INDEX task_lookup_page ON task_lookup(project,status,updated_epoch DESC,task_id)",
    "CREATE INDEX task_lookup_owner_page ON task_lookup(project,status,owner_id,updated_epoch DESC,task_id)",
    "CREATE INDEX task_versions_cut ON task_versions(project)",
    *tuple("CREATE TRIGGER task_lookup_" + operation.lower() + " AFTER " + operation +
           " ON task_current BEGIN INSERT INTO task_lookup " + TASK_LOOKUP_SELECT +
           " WHERE c.task_id=NEW.task_id ON CONFLICT(task_id) DO UPDATE SET "
           "revision=excluded.revision,project=excluded.project,status=excluded.status,"
           "updated_epoch=excluded.updated_epoch,owner_id=excluded.owner_id; END" for operation in ("INSERT", "UPDATE")),
)
SCHEMA_CHANGES = (
    'CREATE TABLE change_events(sequence INTEGER PRIMARY KEY,event_id TEXT NOT NULL UNIQUE,operation TEXT NOT NULL,project TEXT NOT NULL,record_json TEXT NOT NULL,record_sha256 TEXT NOT NULL,committed_at REAL NOT NULL)',
    'CREATE INDEX change_events_page ON change_events(project,sequence)',
    "CREATE TRIGGER change_events_no_update BEFORE UPDATE ON change_events BEGIN SELECT RAISE(ABORT,'immutable event'); END",
)
MIGRATIONS = (SCHEMA, SCHEMA_S2, SCHEMA_CACHE, SCHEMA_RETENTION, SCHEMA_LEGACY, SCHEMA_WORK, SCHEMA_WORK_LOOKUP, SCHEMA_CHANGES)


def schema_checksums():
    return [(version, hashlib.sha256("\n".join(statements).encode()).hexdigest())
            for version, statements in enumerate(MIGRATIONS, 1)]


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def validate_sqlite_target(directory: Path) -> None:
    directory = directory.expanduser().resolve()
    for forbidden in (Path.home() / ".memory-os-v4", Path.home() / "Memory-os-ai"):
        forbidden = forbidden.resolve()
        if directory == forbidden or forbidden in directory.parents:
            raise ValueError("Experimental SQLite S1 is forbidden on production/original-repository paths")
    db = directory / "store.sqlite3"
    if (directory / "items.jsonl").exists():
        raise ValueError("No implicit JSONL migration or dual item authority; use a fresh test store")
    for name in ("blocks.jsonl", "requests.jsonl"):
        path = directory / name
        if path.exists() and path.stat().st_size:
            raise ValueError("JSONL blocks/receipts present: offline migration required; no dual authority")
    if not db.exists() and any((directory / name).exists()
                              for name in ("blocks.jsonl", "requests.jsonl", "meta.json")):
        raise ValueError("SQLite S1 requires a fresh test store; migration is not implemented")
    for name in ("store.sqlite3", "store.sqlite3-wal", "store.sqlite3-shm"):
        path = directory / name
        if path.is_symlink() or (path.exists() and path.stat().st_nlink != 1):
            raise ValueError("Linked SQLite files are forbidden")


# --------------------------------------------------------------------------------------
# Retrieval-profile probe (docs/LEXICAL_PROFILE_4_1_0.md §5). Strictly read-only: it runs
# before SQLiteStore, whose constructor already mutates (WAL pragma, migrations, journal).
SEMANTIC_TRACE_KEYS = ("embed_model", "dim", "embed_compute_profile")
_LEXICAL_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
_LEXICAL_MIN_VERSION = (4, 1, 0)


def lexical_version_ok(version) -> bool:
    """``X.Y.Z`` >= 4.1.0 compared as an integer triple; absent, non-text or malformed is refused."""
    if not isinstance(version, str) or _LEXICAL_VERSION.fullmatch(version) is None:
        return False
    return tuple(int(part) for part in version.split(".")) >= _LEXICAL_MIN_VERSION


@dataclass(frozen=True)
class ProfileFinding:
    """What one read-only probe saw. Two probes of an unchanged store compare equal."""
    state: str  # "new" | "existing" | "unverifiable" | "deferred_to_lease" (probe 1 only)
    reason: str | None = None  # why the profile cannot be determined
    has_marker: bool = False
    marker: object = None
    traces: tuple = ()  # semantic traces, computed only under a valid lexical marker
    store_instance_id: object = None
    application_id: int | None = None
    user_version: int | None = None

    @property
    def marker_valid(self) -> bool:
        return self.has_marker and type(self.marker) is dict and self.marker == LEXICAL_MARKER

    @property
    def contaminated(self) -> bool:
        return self.marker_valid and bool(self.traces)

    @property
    def profile(self) -> str | None:
        """Profile of a determined, valid store: marker absent means semantic."""
        if self.state != "existing" or (self.has_marker and not self.marker_valid):
            return None
        return PROFILE_LEXICAL if self.has_marker else PROFILE_SEMANTIC


class ReadOnlyStoreError(ValueError):
    """A read-only open refused; ``code`` names the reason. A refusal never deletes a file; only
    ``store_side_files_recreated`` follows an open that created side files (the declared 4.1.3 window)."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


_held_sides: dict = {}            # database -> [count, wal_fd, shm_fd]: shared by the live readers of this process
_held_lock = RLock()


def _hold_side_files(database: Path):
    """Keep -wal and -shm open read-only so their inodes cannot be reused while a live read is checked.

    POSIX caveat: closing ANY descriptor of a file drops every fcntl lock the process holds on it, SQLite's
    included. The descriptors are therefore shared by the live readers of this process and closed only after the
    LAST of their connections is closed (``_release_side_files``). Returns None if a side file vanished."""
    key = str(database)
    with _held_lock:
        if key in _held_sides:
            _held_sides[key][0] += 1
            return _held_sides[key]
        fds = []
        try:
            for suffix in ("-wal", "-shm"):
                fds.append(os.open(key + suffix, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)))
        except OSError:
            for fd in fds:
                os.close(fd)
            return None
        _held_sides[key] = [1, *fds]
        return _held_sides[key]


def _release_side_files(database: Path) -> None:
    key = str(database)
    with _held_lock:
        entry = _held_sides.get(key)
        if entry is None:
            return
        entry[0] -= 1
        if entry[0] == 0:
            del _held_sides[key]
            for fd in entry[1:]:
                os.close(fd)


def _sides_unchanged(database: Path, entry) -> bool:
    """The held -wal/-shm are still the files at those paths (not unlinked, not replaced)."""
    for fd, suffix in zip(entry[1:], ("-wal", "-shm")):
        held = os.fstat(fd)
        if held.st_nlink == 0:
            return False
        try:
            current = os.lstat(str(database) + suffix)
        except FileNotFoundError:
            return False
        if (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino):
            return False
    return True


def _windows() -> bool:
    return os.name == "nt"


def _try_shared_lock(fd: int) -> bool:
    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _recreated(cause=None) -> ReadOnlyStoreError:
    error = ReadOnlyStoreError(
        "store_side_files_recreated",
        "the service closed SQLite during this read; SQLite may have recreated empty side files "
        "(declared 4.1.3 limit; a reader never deletes them); retry")
    error.__cause__ = cause
    return error


@contextmanager
def open_readonly(database, *, timeout: float = 5.0):
    """THE read-only open of a store database by a diagnostic (doctor, health). It never deletes a file.

    4.1.3 (note NOTE_4_1_3_WAL_MODE_RO_v1, decision of 2026-11-26, option B): a ``mode=ro`` open of a WAL
    database whose -wal/-shm the last writer deleted RECREATES them, and fails on a read-only directory. The
    engine holds ``writer.lock`` exclusively from before it opens SQLite until after it has closed every store.

    * ``writer.lock`` must exist as a regular file (opened ``O_RDONLY | O_NOFOLLOW``); missing or linked:
      ``writer_lock_missing`` (a reader never creates it).
    * A SHARED non-blocking lock is attempted. Obtained, no writer can start or close during the read, so the
      observed state is stable and nothing is created: no side file, ``mode=ro&immutable=1``; a complete (stale)
      pair, ``mode=ro`` (nobody can delete it); an incomplete pair, ``store_side_files_incomplete``.
    * Lock refused, a writer (the service) runs: no side file, ``store_busy_retry`` (the writer is starting);
      incomplete pair, ``store_side_files_incomplete``; a process that itself holds this store's writer lease
      (an engine running in it), ``readonly_open_in_writer_process``; otherwise a LIVE read with ``mode=ro``.
    * In one process, readers and the engine of a store exclude each other for their whole lifetime: a reader is
      refused while the engine runs, and the engine refuses to start while a reader is open (``WriterLock``).
      Every reader whose SQLite connection opens the pair holds shared descriptors on it, closed after the LAST
      such connection (closing a descriptor drops every fcntl lock of the process on that file). Raw SQLite
      connections opened outside this function are not part of this protocol: the store's other readers
      (backup, migration, profile probe) run under the writer lease or in their own command.
    * DECLARED RESIDUAL WINDOW of the live read: if the service closes SQLite between this reader's look and its
      open, SQLite recreates two side files without data. The window is short in practice but NOT bounded (a
      suspended reader process lengthens it); it only opens while the service shuts down. The files are never
      deleted by a reader (another reader or a new writer may use them); the next engine start uses them as its
      pair. Both side files are held open read-only from the look onwards, so their inodes cannot be reused, and
      their identity is compared after the first read, including when that read fails: a recreation is refused
      with ``store_side_files_recreated``, never silently. A read-only mount of the DIAGNOSTIC does not prevent a
      service from writing through another mount of the same store.

    Windows is not a retained cell: refused (``readonly_open_unqualified_platform``) rather than risking a write.
    """
    database = Path(database)
    if _windows():
        raise ReadOnlyStoreError("readonly_open_unqualified_platform", "no lock-protected read-only open on Windows")
    lock = database.parent / "writer.lock"
    if lock.is_symlink() or not lock.is_file() or lock.stat().st_nlink != 1:
        raise ReadOnlyStoreError("writer_lock_missing", "existing regular writer.lock required; never created by a reader")
    fd = os.open(lock, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        locked = _try_shared_lock(fd)
        has_wal, has_shm = (os.path.lexists(str(database) + suffix) for suffix in ("-wal", "-shm"))
        if has_wal != has_shm:
            raise ReadOnlyStoreError("store_side_files_incomplete", "incomplete -wal/-shm pair; nothing opened")
        if not locked and not has_wal:
            raise ReadOnlyStoreError("store_busy_retry", "the store writer is starting; retry")
        key = _register_reader(lock)
        try:
            # Every reader that lets SQLite open the pair (live, or offline with a stale pair) shares the held
            # descriptors: none of them closes them while another one's connection is still open.
            held = _hold_side_files(database) if has_wal else None
            if has_wal and held is None:
                raise ReadOnlyStoreError("store_busy_retry", "the side files vanished before the open; retry")
            try:
                if held is not None and not _sides_unchanged(database, held):
                    raise ReadOnlyStoreError("store_busy_retry", "the side files changed before the open; retry")
                uri = "file:" + quote(str(database)) + ("?mode=ro" if has_wal else "?mode=ro&immutable=1")
                connection = sqlite3.connect(uri, uri=True, timeout=timeout)
                try:
                    if not locked:
                        try:
                            connection.execute("SELECT count(*) FROM sqlite_master").fetchone()   # attaches to the WAL
                        except Exception as exc:
                            if not _sides_unchanged(database, held):
                                raise _recreated(exc)
                            raise
                        if not _sides_unchanged(database, held):
                            raise _recreated()
                    yield connection
                finally:
                    connection.close()            # BEFORE the held descriptors (POSIX fcntl caveat)
            finally:
                if held is not None:
                    _release_side_files(database)
        finally:
            _unregister_reader(key)
    finally:
        os.close(fd)


def _register_reader(lock: Path) -> Path:
    """Exclusion between diagnostic readers and an engine IN THE SAME PROCESS, for their whole lifetime.

    A reader is refused while this process owns the store's writer lease, and ``WriterLock`` refuses to start an
    engine while a reader of that store is registered (``writer_lock._active_readers``), both under the registry
    lock: the held descriptors can never be closed under an engine's SQLite connection."""
    from .writer_lock import _active_readers, _owned, _registry_lock

    key = lock.resolve()
    with _registry_lock:
        if key in _owned:
            raise ReadOnlyStoreError("readonly_open_in_writer_process",
                                     "this process runs the store's engine: read through it, not around it")
        _active_readers[key] = _active_readers.get(key, 0) + 1
    return key


def _unregister_reader(key: Path) -> None:
    from .writer_lock import _active_readers, _registry_lock

    with _registry_lock:
        count = _active_readers.get(key, 0) - 1
        if count > 0:
            _active_readers[key] = count
        else:
            _active_readers.pop(key, None)


def probe_retrieval_profile(data_dir, *, under_lease: bool = False) -> ProfileFinding:
    """Read the retrieval profile of ``data_dir`` without creating or modifying any file.

    No -wal/-shm: ``mode=ro&immutable=1`` (SQLite creates no side file in immutable mode).
    Incomplete pair or orphaned side file: ``unverifiable`` from ``stat`` alone, never opened.
    Complete -wal/-shm pair: the WAL must be read with ``mode=ro``, and a ``mode=ro`` open of a
    WAL database whose side files have just been deleted by the last writer closing RECREATES
    them. That open is therefore only made ``under_lease`` (probe 2: no engine writer can close
    concurrently, the pair is stable; only the transient -shm bookkeeping of a reader may
    change). Without the lease (probe 1) nothing is opened: ``deferred_to_lease``.
    """
    database = Path(data_dir).expanduser().resolve() / "store.sqlite3"
    has_wal, has_shm = (os.path.lexists(str(database) + suffix) for suffix in ("-wal", "-shm"))
    if not os.path.lexists(database):
        if has_wal or has_shm:
            return ProfileFinding("unverifiable", "a -wal/-shm file exists without store.sqlite3")
        return ProfileFinding("new")
    if has_wal != has_shm:
        return ProfileFinding("unverifiable", "incomplete -wal/-shm pair")
    if database.is_symlink() or not database.is_file():
        return ProfileFinding("unverifiable", "store.sqlite3 is not a regular file")
    if has_wal and not under_lease:
        return ProfileFinding("deferred_to_lease")
    uri = "file:" + quote(str(database)) + ("?mode=ro" if has_wal else "?mode=ro&immutable=1")
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=5)
    except sqlite3.Error as error:
        return ProfileFinding("unverifiable", f"cannot open read-only: {error}")
    try:
        application_id = connection.execute("PRAGMA application_id").fetchone()[0]
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('store_metadata','embedding_cache')")}
        row = (connection.execute("SELECT value_json FROM store_metadata WHERE key='metadata'").fetchone()
               if "store_metadata" in tables else None)
        # The single read of embedding_cache allowed to the lexical profile: is there at least one row?
        cached = ("embedding_cache" in tables
                  and connection.execute("SELECT 1 FROM embedding_cache LIMIT 1").fetchone() is not None)
    except sqlite3.Error as error:
        return ProfileFinding("unverifiable", f"cannot read: {error}")
    finally:
        connection.close()
    meta = {}
    if row is not None:
        try:
            meta = json.loads(row[0])
        except (TypeError, ValueError):
            return ProfileFinding("unverifiable", "metadata is not JSON")
        if not isinstance(meta, dict):
            return ProfileFinding("unverifiable", "metadata is not an object")
    finding = ProfileFinding("existing", has_marker="retrieval_profile" in meta,
                             marker=meta.get("retrieval_profile"),
                             store_instance_id=meta.get("store_instance_id"),
                             application_id=application_id, user_version=user_version)
    if not finding.marker_valid:
        return finding
    traces = [key for key in SEMANTIC_TRACE_KEYS if key in meta]
    if cached:
        traces.append("embedding_cache")
    if not lexical_version_ok(meta.get("version")):
        traces.append("version")
    return replace(finding, traces=tuple(traces))


def profile_refusal(finding: ProfileFinding, requested: str) -> tuple[str, str] | None:
    """(code, message) refusing ``finding`` for the requested profile, or None.

    Priority: unverifiable, then invalid marker, then contamination, then the comparison
    with the requested profile. Same verdict for both probes of an open.
    """
    if finding.state == "unverifiable":
        return ("store_profile_unverifiable",
                f"The retrieval profile of this existing store cannot be determined ({finding.reason}); "
                "it is not opened in any profile")
    if finding.state in ("new", "deferred_to_lease"):
        return None  # deferred: probe 1 opened nothing, probe 2 under the lease decides alone
    if finding.has_marker and not finding.marker_valid:
        return ("store_profile_mismatch",
                "The store carries an unknown or invalid retrieval_profile marker; it is never interpreted")
    if finding.contaminated:
        return ("store_profile_contaminated",
                "Lexical store with semantic traces (" + ", ".join(finding.traces) + "): a lexical store has "
                "no 4.0.x rollback. No automatic repair; restore the last 4.1.0 backup. Going back to a "
                "4.0.x means resuming the semantic store or the backup taken before 4.1.0")
    if finding.profile != requested:
        return ("store_profile_mismatch",
                f"The store belongs to the {finding.profile} profile, the {requested} profile was requested; "
                "no implicit conversion: create a new store and rewrite the sources through the public API")
    return None


class SQLiteStore(Store):
    """Store-compatible item/history/metadata layer with nested transactions.

    Item mutations and metadata inside transaction() commit as one unit. On
    rollback, readers must refetch items (old object references may be stale).
    Embedding files are derived and explicitly outside these transactions.
    """

    def __init__(self, data_dir: Path, *, owned: list | None = None) -> None:
        """``owned``: the opener's list of stores to close before it releases its writer lease.
        The store joins it as soon as its connection exists, BEFORE any further initialization: if
        that initialization fails and the fallback close below fails too, the opener still knows
        a connection may be open."""
        if sqlite3.sqlite_version_info < MIN_SQLITE:
            raise RuntimeError("SQLite S1 requires runtime >= 3.51.3 (WAL-reset fix)")
        data_dir = Path(data_dir).expanduser().resolve()
        validate_sqlite_target(data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = data_dir / "store.sqlite3"
        fresh = not self.db_path.exists()
        if fresh:
            fd = os.open(self.db_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        version = 0
        self._sql_lock = RLock()
        self._depth = 0
        self._readiness_generation = 0
        self._poisoned = False
        self.fault_hook = None  # Test-only callback; receives every mutation boundary.
        self._connection = sqlite3.connect(self.db_path, isolation_level=None, timeout=5,
                                           check_same_thread=False)
        if owned is not None:
            owned.append(self)
        try:
            if not fresh:
                app_id = self._connection.execute("PRAGMA application_id").fetchone()[0]
                version = self._connection.execute("PRAGMA user_version").fetchone()[0]
                if app_id != APPLICATION_ID or version not in range(1, SCHEMA_VERSION + 1):
                    raise ValueError("Unknown/future SQLite schema; no implicit downgrade")
                if self._connection.execute(
                    "SELECT version,checksum FROM schema_migrations ORDER BY version"
                ).fetchall() != schema_checksums()[:version]:
                    raise ValueError("SQLite schema checksum mismatch; upgrade refused")
            self._connection.execute("PRAGMA foreign_keys=ON")
            mode = self._connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            self._connection.execute("PRAGMA synchronous=FULL")
            self._connection.execute("PRAGMA fullfsync=ON")
            self._connection.execute("PRAGMA checkpoint_fullfsync=ON")
            self._connection.execute("PRAGMA busy_timeout=5000")
            settings = {key: self._connection.execute(f"PRAGMA {key}").fetchone()[0]
                        for key in ("foreign_keys", "synchronous", "fullfsync", "checkpoint_fullfsync")}
            if mode != "wal" or settings != {"foreign_keys": 1, "synchronous": 2,
                                            "fullfsync": 1, "checkpoint_fullfsync": 1}:
                raise RuntimeError("Required SQLite WAL/durability settings unavailable")
            if fresh or version < SCHEMA_VERSION:
                self._connection.execute("BEGIN IMMEDIATE")
                try:
                    for migration, statements in enumerate(MIGRATIONS, 1):
                        if not fresh and migration <= version:
                            continue
                        for statement in statements:
                            self._connection.execute(statement)
                        self._connection.execute("INSERT INTO schema_migrations VALUES(?,?,?)",
                                                 (*schema_checksums()[migration - 1], time.time()))
                    self._connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
                    self._connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                    self._connection.execute("COMMIT")
                except BaseException:
                    self._connection.execute("ROLLBACK")
                    raise
            if os.name != "nt":
                fd = os.open(data_dir, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            super().__init__(data_dir)
            from .changes import initialize
            initialize(self)
            if not self.check_integrity():
                raise RuntimeError("SQLite source integrity failed")
        except BaseException:
            self._connection.close()
            raise

    @contextmanager
    def transaction(self):
        """BEGIN IMMEDIATE / savepoints, serializing use across worker threads."""
        with self._sql_lock:
            if self._poisoned:
                raise RuntimeError("SQLite commit outcome uncertain; inspect/reopen before further writes")
            level = self._depth
            savepoint = f"store_{level}"
            if level == 0:
                # Invalidate even when BEGIN/action/COMMIT later fails or rolls back.
                self._readiness_generation += 1
                self._fault("before_begin")
            try:
                self._connection.execute("BEGIN IMMEDIATE" if level == 0 else f"SAVEPOINT {savepoint}")
            except BaseException:
                if self._connection.in_transaction:
                    self._poisoned = True
                raise
            self._depth += 1
            committing = False
            try:
                if level == 0:
                    self._fault("after_begin")
                yield self
                if level == 0:
                    self._fault("before_commit")
                committing = True
                self._connection.execute("COMMIT" if level == 0 else f"RELEASE SAVEPOINT {savepoint}")
                if level == 0:
                    self._fault("after_commit")
            except BaseException:
                if committing:
                    self._poisoned = True  # No blind retry after an ambiguous commit exception.
                try:
                    if self._connection.in_transaction:
                        if level == 0:
                            self._connection.execute("ROLLBACK")
                        else:
                            self._connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                            self._connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                    self.load()
                    self.load_chunk_map()
                    self._dirty_access.clear()
                except BaseException:
                    self._poisoned = True  # Recovery failure cannot expose uncommitted state as ready.
                    raise
                raise
            finally:
                self._depth -= 1

    def _fault(self, point: str) -> None:
        if self.fault_hook is not None:
            self.fault_hook(point)

    def write(self, label: str, sql: str, parameters=()):
        """All S2 DML passes here; injection runs before and after each write."""
        if not self._connection.in_transaction:
            raise RuntimeError("SQLite source write requires an explicit transaction")
        self._fault(f"before_{label}")
        result = self._connection.execute(sql, parameters)
        self._fault(f"after_{label}")
        return result

    def load(self) -> None:
        with self._sql_lock:
            self.items.clear()
            for item_id, project, kind, record_json in self._connection.execute(
                "SELECT i.id,i.project,i.kind,v.record_json FROM items i JOIN item_versions v "
                "ON v.item_id=i.id AND v.revision=i.current_revision"
            ):
                item = MemoryItem.from_dict(json.loads(record_json))
                if (item.id, item.project, item.kind) != (item_id, project, kind):
                    raise RuntimeError("SQLite head fields disagree with their record")
                self.items[item.id] = item
            row = self._connection.execute("SELECT value_json FROM store_metadata WHERE key='metadata'").fetchone()
            self.meta = json.loads(row[0]) if row else {}

    def append_many(self, items: list[MemoryItem]) -> None:
        if not items:
            return
        with self.transaction():
            for item in items:
                if (not item.id or not isinstance(item.text, str) or item.kind not in VALID_KINDS
                        or not isinstance(item.project, str) or not item.project or item.project == "*"):
                    raise ValueError("Invalid SQLite item identity/kind/project/text")
                old = self._connection.execute("SELECT project,kind,current_revision FROM items WHERE id=?",
                                               (item.id,)).fetchone()
                if old and (item.project, item.kind) != old[:2]:
                    raise ValueError("An item cannot change project/kind")
                revision = old[2] + 1 if old else 1
                previous = self._connection.execute(
                    "SELECT record_json FROM item_versions WHERE item_id=? AND revision=?",
                    (item.id, old[2])).fetchone() if old else None
                # Preserve unknown legacy fields through later corrections/forget/access flush.
                record = _json({**(json.loads(previous[0]) if previous else {}), **item.to_dict()})
                self.write("item_version", "INSERT INTO item_versions VALUES(?,?,?,?,?)",
                                         (item.id, revision, record, hashlib.sha256(record.encode()).hexdigest(), time.time()))
                self.write("item_head", "INSERT INTO items VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE "
                                         "SET current_revision=excluded.current_revision",
                                         (item.id, item.project, item.kind, revision))
                self.items[item.id] = item

    def save_meta(self, meta: dict) -> None:
        with self.transaction():
            self.write("metadata", "INSERT INTO store_metadata VALUES('metadata',?) "
                                     "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json", (_json(meta),))
            self.meta = dict(meta)

    def load_chunk_map(self) -> None:
        with self._sql_lock:
            row = self._connection.execute("SELECT value_json FROM store_metadata WHERE key='chunk_map'").fetchone()
            self.chunk_owner = json.loads(row[0]) if row else {}

    def save_chunk_map(self, chunk_owner: dict[str, str] | None = None) -> None:
        with self.transaction():
            value = self.chunk_owner if chunk_owner is None else chunk_owner
            self.write("chunk_map", "INSERT INTO store_metadata VALUES('chunk_map',?) "
                                     "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json", (_json(value),))
            self.chunk_owner = dict(value)

    def flush_access(self) -> None:
        with self.transaction():
            super().flush_access()

    def history(self, item_id: str) -> list[MemoryItem]:
        with self._sql_lock:
            return [MemoryItem.from_dict(json.loads(row[0])) for row in self._connection.execute(
                "SELECT record_json FROM item_versions WHERE item_id=? ORDER BY revision", (item_id,))]

    def raw_history(self, item_id: str) -> list[dict]:
        with self._sql_lock:
            return [json.loads(row[0]) for row in self._connection.execute(
                "SELECT record_json FROM item_versions WHERE item_id=? ORDER BY revision", (item_id,))]

    def check_integrity(self) -> bool:
        with self._sql_lock:
            try:
                if self._poisoned or not self.db_path.is_file():
                    return False
                if (self._connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
                        or self._connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION):
                    return False
                if self._connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    return False
                if self._connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    return False
                if self._connection.execute("SELECT version,checksum FROM schema_migrations ORDER BY version").fetchall() != schema_checksums():
                    return False
                for item_id, record, checksum in self._connection.execute(
                    "SELECT item_id,record_json,record_sha256 FROM item_versions"
                ):
                    if hashlib.sha256(record.encode()).hexdigest() != checksum:
                        return False
                    item = MemoryItem.from_dict(json.loads(record))
                    if (item.id != item_id or not isinstance(item.text, str) or item.kind not in VALID_KINDS
                            or not isinstance(item.project, str) or not item.project or item.project == "*"):
                        return False
                if self._connection.execute(
                    "SELECT i.id FROM items i WHERE i.current_revision <> "
                    "(SELECT max(v.revision) FROM item_versions v WHERE v.item_id=i.id) LIMIT 1"
                ).fetchone() is not None:
                    return False
                heads = {}
                for item_id, project, kind, record in self._connection.execute(
                    "SELECT i.id,i.project,i.kind,v.record_json FROM items i JOIN item_versions v "
                    "ON v.item_id=i.id AND v.revision=i.current_revision"
                ):
                    heads[item_id] = json.loads(record)
                    if (heads[item_id]["id"], heads[item_id]["project"], heads[item_id]["kind"]) != (item_id, project, kind):
                        return False
                if set(heads) != set(self.items):
                    return False
                for item_id, record in heads.items():
                    current = self.items[item_id].to_dict()
                    if any(current[key] != value for key, value in record.items()
                           if key in current and key not in {"access_count", "last_accessed"}):
                        return False
                for row in self._connection.execute("SELECT value_json FROM store_metadata"):
                    if not isinstance(json.loads(row[0]), dict):
                        return False
                for table in ("block_versions", "request_receipts", "legacy_request_log", "legacy_pending_requests", "change_events"):
                    for record, checksum in self._connection.execute(
                        f"SELECT record_json,record_sha256 FROM {table}"
                    ):
                        if hashlib.sha256(record.encode()).hexdigest() != checksum:
                            return False
                for contents, checksum in self._connection.execute("SELECT contents,sha256 FROM legacy_files"):
                    if hashlib.sha256(contents).hexdigest() != checksum:
                        return False
                counter = self._connection.execute("SELECT value FROM receipt_counter WHERE id=1").fetchone()
                maximum = self._connection.execute("SELECT COALESCE(max(sequence),0) FROM request_receipts").fetchone()[0]
                if counter is None or counter[0] < maximum:
                    return False
                from .work_state import check_integrity
                check_integrity(self)
                from .changes import check_integrity as check_changes
                check_changes(self)
                return True
            except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
                return False

    def close(self) -> None:
        with self._sql_lock:
            self._connection.close()

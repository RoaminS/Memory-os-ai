"""Lifetime writer lease for the local JSONL store.

The lock file stays in place: unlinking it would allow a new inode to be locked
while an older process still owns the original. Kernel locks release on exit.
"""
from __future__ import annotations

import errno
import os
from pathlib import Path
from threading import Lock

_registry_lock = Lock()
_owned: set[Path] = set()
# Raw duplicates of pinned lease descriptors (see ``WriterLock.pin``). Plain integers: no Python
# object owns them, so interpreter shutdown cannot close them; only ``WriterLock.close`` does.
_pinned_fds: set[int] = set()


class WriterConflictError(RuntimeError):
    pass


class WriterLock:
    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir.resolve() / "writer.lock"
        self._file = None
        self._pinned_fd = None
        self._pid = os.getpid()
        with _registry_lock:
            if self.path in _owned:
                raise self._conflict()
            fh = self.path.open("a+b")
            try:
                if os.name == "nt":  # pragma: no cover - Windows
                    import msvcrt
                    if self.path.stat().st_size == 0:
                        fh.write(b"\0")
                        fh.flush()
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                fh.close()
                if exc.errno in {errno.EACCES, errno.EAGAIN}:
                    raise self._conflict() from exc
                raise RuntimeError(f"Cannot acquire writer lease for {self.path.parent}: {exc}") from exc
            self._file = fh
            _owned.add(self.path)

    def _conflict(self) -> WriterConflictError:
        return WriterConflictError(
            f"Memory store {self.path.parent} already has an active writer. "
            "Use one shared HTTP server (memory-os-v4 --transport http) for multiple clients, "
            "or close the existing engine before opening another. Do not delete writer.lock."
        )

    def assert_owner(self) -> None:
        if os.getpid() != self._pid:
            raise RuntimeError("An inherited memory engine cannot be used after fork; connect to the shared HTTP server")

    def pin(self) -> None:
        """Make the lease outlive this object: it then ends only with ``close()`` or the process.

        The kernel lock belongs to the open file description, not to a descriptor: it lasts until
        an explicit unlock or until EVERY descriptor on it is closed. Duplicating the descriptor
        into a raw integer that no Python object owns means that destroying this lock or its file
        object (garbage collection, interpreter shutdown, in any order) only closes the original
        descriptor: the lease stays held until the operating system closes the duplicate, when the
        process is gone and nothing in it can still close a SQLite connection. Only ``close()``
        unlocks; nothing else in this module does. Idempotent.
        """
        with _registry_lock:
            if self._file is None or self._pinned_fd is not None or os.getpid() != self._pid:
                return
            self._pinned_fd = os.dup(self._file.fileno())
            _pinned_fds.add(self._pinned_fd)

    def close(self) -> None:
        with _registry_lock:
            if self._file is None:
                return
            fh, self._file = self._file, None
            pinned, self._pinned_fd = self._pinned_fd, None
            try:
                if os.getpid() != self._pid:
                    # A fork inherits the open file description. Explicitly
                    # unlocking it would also release the parent's lease.
                    return
                if os.name == "nt":  # pragma: no cover - Windows
                    import msvcrt
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            finally:
                try:
                    fh.close()
                finally:
                    _owned.discard(self.path)
                    if pinned is not None:  # after the explicit unlock above: the lease is released
                        _pinned_fds.discard(pinned)
                        os.close(pinned)

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
# Raw duplicates of the lease descriptors reserved right after acquisition (``WriterLock.reserve``).
# Plain integers: no Python object owns them, so neither garbage collection nor interpreter
# shutdown can close them; only ``WriterLock.close`` does. (Inventory only: the kernel keeps a
# descriptor open whether or not this set still exists.)
_reserved_fds: set[int] = set()


class WriterConflictError(RuntimeError):
    pass


class WriterLock:
    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir.resolve() / "writer.lock"
        self._file = None
        self._reserved_fd = None
        self._pinned = False
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

    def reserve(self) -> None:
        """Duplicate the lock descriptor into a raw integer, NOW: the memory engine calls this
        right after taking the lease and before it opens anything under it.

        The kernel lock belongs to the open file description, not to a descriptor: it lasts until
        an explicit unlock or until EVERY descriptor on it is closed. With the raw duplicate, the
        destruction of this object or of its file object (garbage collection, interpreter
        shutdown, in any order) only closes the original descriptor: the lease then ends only
        with ``close()`` or with the process itself. Reserving up front means that keeping the
        lease after a failed release needs no late allocation that could itself fail
        (descriptor exhaustion). If the reservation fails, the lease is released and the error
        raised: the caller has opened nothing yet and must not. A reserved lock MUST be closed
        explicitly to release the lease (the engine's release state does). Idempotent.
        """
        with _registry_lock:
            if self._file is None or self._reserved_fd is not None or os.getpid() != self._pid:
                return
            try:
                self._reserved_fd = os.dup(self._file.fileno())
                _reserved_fds.add(self._reserved_fd)
                return
            except BaseException as exc:
                failure = exc
        self.close()  # no guarantee: give the lease back (and any duplicate) and refuse
        if isinstance(failure, OSError):
            raise RuntimeError(f"Cannot reserve the writer lease descriptor for {self.path.parent}: "
                               f"{failure}; the store was not opened") from failure
        raise failure

    def pin(self) -> None:
        """Record that a failed release keeps this lease. Allocates nothing, calls nothing, cannot
        fail: the raw descriptor that makes the lease outlive this object was reserved at
        acquisition (``reserve()``); without a reservation this is only a mark."""
        self._pinned = True

    def close(self) -> None:
        with _registry_lock:
            if self._file is None:
                return
            fh, self._file = self._file, None
            reserved, self._reserved_fd = self._reserved_fd, None
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
                    self._pinned = False
                    if reserved is not None:  # after the explicit unlock above: the lease is released
                        _reserved_fds.discard(reserved)
                        os.close(reserved)

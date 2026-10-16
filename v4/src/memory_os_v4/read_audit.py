"""Bounded positive-only read audits; live state must still be checked by callers.

TTL is a detection bound for untracked in-memory faults. Disk/source changes and
writer generations invalidate immediately; failures are NEVER reused.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from threading import RLock
import time

MAX_AGE_SECONDS = 1.0


def file_stamp(path: Path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class ReadAudit:
    def __init__(self):
        self._lock = RLock()
        self._entry = None

    def run(self, stamp, check, valid, *, max_age=MAX_AGE_SECONDS):
        # Never let an operator/API silently stretch the readiness detection bound.
        max_age = min(MAX_AGE_SECONDS, max(0.0, max_age))
        with self._lock:
            before = stamp()
            now = time.monotonic()
            if self._entry is not None:
                old_stamp, at, result = self._entry
                if before == old_stamp and 0 <= now-at < max_age:
                    return deepcopy(result)
            self._entry = None
            result = check()
            after = stamp()
            if before != after:
                raise ValueError('Read audit changed during verification')
            if valid(result) and max_age:
                self._entry = (after, time.monotonic(), deepcopy(result))
            return result

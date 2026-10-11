"""Private, per-process relay ledger. No arguments/results/secrets are stored.

An orphan is read only under its exclusive companion lock. PID metadata is
informational: holding flock is the liveness proof (PID reuse is harmless).
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
import uuid

from ..idempotency import _json

RESOLVED = {"completed"}
STATES = {"prepared", "sending", "blocked", "completed"}


def directory(settings):
    return Path(os.environ.get("MEMORY_V4_RELAY_JOURNAL_DIR", str(settings.ops_dir / "relay"))).expanduser()


def _private(fd, mode=0o600):
    st = os.fstat(fd)
    if (not stat.S_ISREG(st.st_mode) or stat.S_IMODE(st.st_mode) != mode
            or st.st_uid != os.geteuid() or st.st_nlink != 1):
        raise ValueError("Unsafe relay journal/lock permissions or type")


def _open(path, flags):
    fd = os.open(path, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        _private(fd)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _parent(path, create=False):
    if create:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    st = path.lstat()
    if not stat.S_ISDIR(st.st_mode) or stat.S_IMODE(st.st_mode) != 0o700 or st.st_uid != os.geteuid():
        raise ValueError("Relay journal directory must be private, owned, nonsymlink, mode 0700")


def _sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def key(record):
    return tuple(record[k] for k in ("endpoint_sha256", "store_instance_id", "scope", "request_id", "raw_args_sha256", "operation"))


def _records(path):
    fd = _open(path, os.O_RDONLY)
    records = {}
    with os.fdopen(fd, "rb") as stream:
        previous = ""
        for line in stream:
            if len(line) > 32768 or not line.endswith(b"\n"):
                raise ValueError("Truncated/oversized relay journal record")
            row = json.loads(line)
            digest = row.pop("sha256")
            if row.pop("previous_sha256") != previous or digest != hashlib.sha256(_json(row).encode()).hexdigest():
                raise ValueError("Relay journal chain/checksum mismatch")
            previous = digest
            if row["state"] not in STATES or not isinstance(row["at"], (float, int)):
                raise ValueError("Invalid relay journal state")
            records[key(row)] = row
    return records, previous


def scan(path, *, exclude=None):
    """No writes, even to orphan journals. Busy owners are not adopted."""
    out = {"entries": [], "errors": [], "active_journals": 0, "bytes": 0, "oldest_age_s": 0.0}
    if not path.exists():
        return out
    try:
        _parent(path)
    except (OSError, ValueError) as exc:
        out["errors"].append(type(exc).__name__)
        return out
    for journal in sorted(path.glob("*.jsonl")):
        if journal == exclude:
            continue
        fd = None
        try:
            fd = _open(journal.with_suffix(".lock"), os.O_RDONLY)
            jfd = _open(journal, os.O_RDONLY)
            try:
                st = os.fstat(jfd)
                out["bytes"] += st.st_size
                out["oldest_age_s"] = max(out["oldest_age_s"], max(0, time.time() - st.st_mtime))
            finally:
                os.close(jfd)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                out["active_journals"] += 1
                continue
            records, _ = _records(journal)
            out["entries"].extend({**row, "journal": journal.name} for row in records.values())
        except (OSError, ValueError, KeyError, TypeError):
            out["errors"].append(journal.name)
        finally:
            if fd is not None:
                os.close(fd)
    return out


class RelayJournal:
    def __init__(self, path: Path, endpoint: str, client="unknown", *, max_bytes=10 * 1024**2, max_days=30):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", client):
            raise ValueError("MEMORY_V4_CLIENT_ID must be a short alphanumeric client label")
        if max_bytes < 1024 or max_days <= 0:
            raise ValueError("Invalid relay journal retention bounds")
        _parent(path, create=True)
        self.path = path / f"{client}-{uuid.uuid4().hex}.jsonl"
        self.endpoint = hashlib.sha256(endpoint.encode()).hexdigest()
        self.max_bytes, self.max_age = max_bytes, max_days * 86400
        self.records, self.previous, self.poisoned = {}, "", False
        self.samples = {"prepared": [], "sending": [], "completed": [], "blocked": []}
        self.counts = dict.fromkeys(self.samples, 0)
        self.fd = _open(self.path.with_suffix(".lock"), os.O_RDWR | os.O_CREAT | os.O_EXCL)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fd = _open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            os.close(fd)
            _sync_dir(path)
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def append(self, record, state):
        if self.poisoned or self.fd is None:
            raise RuntimeError("Relay journal unavailable; outcome uncertain, do not repeat")
        if state not in STATES:
            raise ValueError("Unknown relay journal state")
        # Whitelist: never accept arguments, response, token, query or arbitrary caller fields.
        row = {k: record[k] for k in ("request_id", "scope", "operation", "raw_args_sha256", "store_instance_id")}
        row.update(endpoint_sha256=self.endpoint, state=state, at=time.time(), pid=os.getpid())
        if state == "completed" and "result_sha256" in record:
            row["result_sha256"] = record["result_sha256"]
        started = time.perf_counter()
        digest = hashlib.sha256(_json(row).encode()).hexdigest()
        line = (_json({**row, "previous_sha256": self.previous, "sha256": digest}) + "\n").encode()
        try:
            fd = _open(self.path, os.O_WRONLY | os.O_APPEND)
            with os.fdopen(fd, "ab") as fh:
                fh.write(line)
                fh.flush()
                os.fsync(fh.fileno())
            self.previous = digest
            self.records[key(row)] = row
            self.rotate()
        except BaseException:
            self.poisoned = True
            # Preserve a RAM uncertainty signal even when fsync has an ambiguous outcome.
            self.records[key(row)] = {**row, "state": "blocked"}
            raise
        finally:
            values = self.samples[state]
            values.append((time.perf_counter() - started) * 1000)
            del values[:-256]
            self.counts[state] += 1
        return row

    def rotate(self):
        now = time.time()
        size = self.path.stat().st_size
        if size <= self.max_bytes and not any(r["state"] in RESOLVED and now - r["at"] > self.max_age for r in self.records.values()):
            return
        rows = [r for r in self.records.values() if r["state"] not in RESOLVED or now - r["at"] <= self.max_age]
        # Retain all uncertainties, even above the size cap. Completed oldest first are expendable.
        rows.sort(key=lambda r: r["at"])
        while sum(len(_json(r).encode()) + 180 for r in rows) > self.max_bytes:
            index = next((i for i, r in enumerate(rows) if r["state"] in RESOLVED), None)
            if index is None:
                break
            rows.pop(index)
        temporary = self.path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        previous = ""
        fd = _open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        with os.fdopen(fd, "wb") as fh:
            for row in rows:
                digest = hashlib.sha256(_json(row).encode()).hexdigest()
                fh.write((_json({**row, "previous_sha256": previous, "sha256": digest}) + "\n").encode())
                previous = digest
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, self.path)
        _sync_dir(self.path.parent)
        self.records, self.previous = {key(r): r for r in rows}, previous

    def status(self):
        report = scan(self.path.parent, exclude=self.path)
        combined = report.pop("entries") + list(self.records.values())
        resolved = {key(r) for r in combined if r["state"] in RESOLVED}
        unresolved = {key(r): r for r in combined if key(r) not in resolved and r["endpoint_sha256"] == self.endpoint}
        completed = {key(r): r for r in combined if r["state"] in RESOLVED and r["endpoint_sha256"] == self.endpoint}
        # Recover an ID even if the relay died AFTER journalling the response but
        # BEFORE delivering it to its stdio client. This is not an unresolved count.
        report.update(recent_completed=sorted(completed.values(), key=lambda r: r["at"], reverse=True)[:20],
                      local_journals=True, replay_permitted=False,
                      unresolved=list(unresolved.values()), unresolved_count=len(unresolved),
                      journal_poisoned=self.poisoned, bytes=report["bytes"] + self.path.stat().st_size,
                      fsync_ms={phase: {"count": self.counts[phase], "samples": len(v),
                                        "p95": sorted(v)[max(0, (95 * len(v) + 99) // 100 - 1)] if v else None}
                                for phase, v in self.samples.items()})
        return report


def prune_closed(path, *, apply=False, max_bytes=10 * 1024**2, max_days=30):
    """Separate retention operation, NOT adoption. No new ledger, no server access.

    Acquires the writer role only on closed journals, keeps ALL unresolved final
    states. Dry-run by default; invalid/active files are never rewritten.
    """
    if max_bytes < 1024 or max_days <= 0:
        raise ValueError("Invalid retention limits")
    _parent(path)
    report = {"apply": apply, "journals": [], "errors": [], "active": 0}
    for path_ in sorted(path.glob("*.jsonl")):
        fd = None
        try:
            fd = _open(path_.with_suffix(".lock"), os.O_RDWR)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                report["active"] += 1
                continue
            records, previous = _records(path_)
            # Reuse the rotation writer under the same exclusive lock. Adoption
            # via scan() never reaches this code and never modifies old files.
            writer = object.__new__(RelayJournal)
            writer.path, writer.fd = path_, fd
            writer.records, writer.previous = records, previous
            writer.max_bytes, writer.max_age = max_bytes, max_days * 86400
            before = path_.stat().st_size
            unresolved = sum(r["state"] not in RESOLVED for r in records.values())
            eligible = before > max_bytes or any(r["state"] in RESOLVED and time.time() - r["at"] > writer.max_age for r in records.values())
            if apply and eligible:
                writer.rotate()
            after = path_.stat().st_size
            report["journals"].append({"name": path_.name, "before_bytes": before, "after_bytes": after,
                                        "eligible": eligible, "unresolved_preserved": unresolved})
        except (OSError, ValueError, KeyError, TypeError):
            report["errors"].append(path_.name)
        finally:
            if fd is not None:
                os.close(fd)
    return report


def main():
    import argparse
    from .settings import OpsSettings
    parser = argparse.ArgumentParser(description="Relay journal retention only; no store or server access")
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--apply", action="store_true", help="Default: simulation, never modifies journals")
    parser.add_argument("--max-days", type=float, default=30)
    parser.add_argument("--max-bytes", type=int, default=10 * 1024**2)
    args = parser.parse_args()
    report = prune_closed(args.directory or directory(OpsSettings.from_env()), apply=args.apply,
                          max_days=args.max_days, max_bytes=args.max_bytes)
    print(json.dumps(report, indent=2))
    return int(bool(report["errors"]))


if __name__ == "__main__":
    raise SystemExit(main())

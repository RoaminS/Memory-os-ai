"""Explicit SQLite backup capacity, shared by every stage. Decimal env = bytes."""

from dataclasses import dataclass
import os
import re

DEFAULT_DB_BYTES = 4 * 1024**3
MAX_CONFIGURED_DB_BYTES = 16 * 1024**3
MIN_CONFIGURED_DB_BYTES = 64 * 1024**2
MANIFEST_BYTES = 2 * 1024**2


@dataclass(frozen=True)
class Limits:
    db: int
    manifest: int = MANIFEST_BYTES

    @property
    def unpacked(self):
        return self.db + self.manifest + 65536

    @property
    def compressed(self):
        # Conservative worst-case gzip/deflate overhead, not compression optimism.
        return self.unpacked + self.unpacked // 100 + 65536

    @property
    def encrypted(self):
        return self.compressed + 5 + 12 + 16  # MOSB1 + GCM nonce + tag

    def check(self, size, stage):
        limit = {
            "database": self.db,
            "wal": self.db,
            "shm": self.db,
            "member": self.db,
            "manifest": self.manifest,
            "decompressed": self.unpacked,
            "decrypted": self.compressed,
            "archive": self.encrypted,
        }[stage]
        if size > limit:
            raise ValueError(f"{stage.capitalize()} cap exceeded: {size} > {limit} bytes")

    def report(self):
        return {
            "database_bytes": self.db,
            "manifest_bytes": self.manifest,
            "decompressed_bytes": self.unpacked,
            "decrypted_bytes": self.compressed,
            "encrypted_archive_bytes": self.encrypted,
        }


def configured(default=DEFAULT_DB_BYTES, manifest=MANIFEST_BYTES):
    raw = os.environ.get("MEMORY_V4_SQLITE_BACKUP_MAX_BYTES")
    if raw is None:
        return Limits(default, manifest)
    message = "MEMORY_V4_SQLITE_BACKUP_MAX_BYTES must be integer bytes between 67108864 and 17179869184"
    if re.fullmatch(r"[0-9]+", raw) is None:
        raise ValueError(message)
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(message) from None
    if not MIN_CONFIGURED_DB_BYTES <= value <= MAX_CONFIGURED_DB_BYTES:
        raise ValueError(message)
    return Limits(value, manifest)


def capacity(directory):
    limits = configured()
    sizes = {
        suffix or "db": (directory / ("store.sqlite3" + suffix)).stat().st_size
        if (directory / ("store.sqlite3" + suffix)).exists()
        else 0
        for suffix in ("", "-wal", "-shm")
    }
    largest = max(sizes.values())
    return {
        "limits": limits.report(),
        "observed_bytes": sizes,
        "margin_bytes": limits.db - largest,
        "remaining_fraction": (limits.db - largest) / limits.db,
        "ok": largest <= limits.db,
        "warning": largest >= limits.db * 0.8,
        "setting": "MEMORY_V4_SQLITE_BACKUP_MAX_BYTES",
        "default_bytes": DEFAULT_DB_BYTES,
    }

"""Bounded-memory MOSB1 AES-256-GCM/tar.gz; byte-compatible with backup.encrypt.

Uses cryptography's streaming GCM implementation, the same nonce/AAD/tag layout.
No unauthenticated bytes are parsed or extracted. Scratch files are private and
unlinked automatically; no new crypto/file-format construction.
"""

from contextlib import contextmanager
import gzip
import json
import os
import stat
import tarfile
import tempfile

from .backup import BackupError, MAGIC, ROOT

CHUNK = 1024 * 1024


class EncryptWriter:
    def __init__(self, output, key, limits, guard):
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        nonce = os.urandom(12)
        self.output, self.limits, self.guard, self.size = output, limits, guard, 17
        self.encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
        self.encryptor.authenticate_additional_data(MAGIC)
        output.write(MAGIC + nonce)

    def write(self, data):
        self.guard()
        self.size += len(data)
        self.limits.check(self.size + 16, "archive")
        self.output.write(self.encryptor.update(data))
        return len(data)

    def flush(self):
        self.output.flush()

    def finish(self):
        self.output.write(self.encryptor.finalize())
        self.output.write(self.encryptor.tag)
        self.flush()
        os.fsync(self.output.fileno())


def build(database, manifest, output, key, limits, guard):
    payload = json.dumps(manifest, indent=1, ensure_ascii=False).encode()
    limits.check(len(payload), "manifest")
    limits.check(database.stat().st_size, "database")
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        writer = EncryptWriter(stream, key, limits, guard)
        with tarfile.open(fileobj=writer, mode="w|gz", format=tarfile.GNU_FORMAT) as tar:
            import io

            for name, size, source in [
                ("MANIFEST.json", len(payload), io.BytesIO(payload)),
                ("store.sqlite3", database.stat().st_size, database.open("rb")),
            ]:
                with source:
                    info = tarfile.TarInfo(ROOT + "/" + name)
                    info.size, info.mode, info.mtime = size, 0o600, 0
                    tar.addfile(info, source)
        writer.finish()


@contextmanager
def unpacked(archive, key, limits, guard, scratch):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.exceptions import InvalidTag

    fd = os.open(archive, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as source, tempfile.TemporaryFile(dir=scratch) as compressed:
        info = os.fstat(source.fileno())
        limits.check(info.st_size, "archive")
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size < 33:
            raise BackupError("Invalid archive file/header")
        header = source.read(17)
        if header[:5] != MAGIC:
            raise BackupError("Invalid archive magic")
        source.seek(-16, os.SEEK_END)
        tag = source.read(16)
        source.seek(17)
        dec = Cipher(algorithms.AES(key), modes.GCM(header[5:], tag)).decryptor()
        dec.authenticate_additional_data(MAGIC)
        remaining = info.st_size - 33
        total = 0
        while remaining:
            guard()
            block = source.read(min(CHUNK, remaining))
            if not block:
                raise BackupError("Truncated encrypted archive")
            remaining -= len(block)
            total += len(block)
            limits.check(total, "decrypted")
            compressed.write(dec.update(block))
        try:
            compressed.write(dec.finalize())
        except InvalidTag:
            raise BackupError("Archive authentication failed") from None
        final = os.fstat(source.fileno())
        if (info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (
            final.st_size,
            final.st_mtime_ns,
            final.st_ctime_ns,
        ):
            raise BackupError("Archive changed during authentication")
        compressed.seek(0)
        with tempfile.TemporaryFile(dir=scratch) as clear:
            total = 0
            with gzip.GzipFile(fileobj=compressed) as gzip_stream:
                while True:
                    guard()
                    block = gzip_stream.read(min(CHUNK, limits.unpacked - total + 1))
                    if not block:
                        break
                    total += len(block)
                    limits.check(total, "decompressed")
                    clear.write(block)
            clear.seek(0)
            yield clear


def extract(clear, target, limits, guard):
    """Parse fixed ustar headers without letting tarfile allocate PAX/GNU payloads."""
    allowed = {ROOT + "/store.sqlite3": limits.db, ROOT + "/MANIFEST.json": limits.manifest}
    target.mkdir(mode=0o700)
    seen = set()
    end = False
    while True:
        guard()
        header = clear.read(512)
        if len(header) != 512:
            raise BackupError("Truncated tar header")
        if header == bytes(512):
            if clear.read(512) != bytes(512):
                raise BackupError("Invalid tar terminator")
            end = True
            break
        try:
            member = tarfile.TarInfo.frombuf(header, "utf-8", "strict")
        except (tarfile.HeaderError, UnicodeError):
            raise BackupError("Invalid tar header") from None
        if (
            member.type not in {tarfile.REGTYPE, tarfile.AREGTYPE}
            or member.name not in allowed
            or member.name in seen
            or not 0 <= member.size <= allowed[member.name]
        ):
            raise BackupError("Unsafe/duplicate/oversized SQLite archive member")
        seen.add(member.name)
        fd = os.open(
            target / member.name.split("/")[-1], os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(fd, "wb") as output:
            remaining = member.size
            while remaining:
                guard()
                block = clear.read(min(CHUNK, remaining))
                if not block:
                    raise BackupError("Truncated tar member")
                output.write(block)
                remaining -= len(block)
            output.flush()
            os.fsync(output.fileno())
        padding = clear.read((-member.size) % 512)
        if len(padding) != (-member.size) % 512:
            raise BackupError("Truncated tar padding")
    while True:
        guard()
        padding = clear.read(CHUNK)
        if not padding:
            break
        if any(padding):
            raise BackupError("Unexpected tar trailing data")
    if not end or seen != set(allowed):
        raise BackupError("Missing SQLite archive member")

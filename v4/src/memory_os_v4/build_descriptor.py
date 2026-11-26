"""Verify installed payload coherence; hashes are not a publisher signature."""
from __future__ import annotations

from functools import lru_cache

import hashlib
import os
import json
from pathlib import Path
import re

NAME = "_build_identity.json"


@lru_cache(maxsize=4)
def _audit(root, version):
    from .read_audit import ReadAudit
    return ReadAudit()


def _stamp(root):
    from .read_audit import file_stamp
    # Include directory entries, permissions and symlinks: additions/removals
    # or replacements must invalidate without waiting for the TTL.
    payload = tuple((str(p.relative_to(root)), file_stamp(p))
                    for p in (root/'memory_os_v4', *sorted((root/'memory_os_v4').rglob('*')))
                    if '__pycache__' not in p.parts and p.suffix not in {'.pyc', '.pyo'})
    # 4.1.3: a memory_os_ai leftover is stamped on EXACTLY the entries _importable_legacy inspects (every entry,
    # __pycache__ included, and the link itself), so v3 code appearing after a pass does not wait for the TTL.
    legacy = root/'memory_os_ai'
    if not os.path.lexists(legacy):
        return payload
    if legacy.is_symlink() or not legacy.is_dir():
        return payload + (('memory_os_ai', file_stamp(legacy)),)
    return payload + tuple((str(p.relative_to(root)), file_stamp(p))
                           for p in (legacy, *sorted(legacy.rglob('*'))))


def _importable_legacy(path: Path) -> bool:
    """True when ``path`` (a ``memory_os_ai`` beside the payload) holds anything Python could import or run:
    a link, a regular file, or any file other than ``__pycache__/*.pyc`` caches."""
    if not os.path.lexists(path):
        return False
    if path.is_symlink() or not path.is_dir():
        return True
    for item in path.rglob("*"):
        if item.is_symlink():
            return True
        if item.is_file() and not (item.parent.name == "__pycache__" and item.suffix == ".pyc"):
            return True
    return False


def verify(root: Path, version: str, *, max_age=0.0) -> dict:
    if not max_age:
        return _verify(root, version)
    try:
        return _audit(root, version).run(lambda: _stamp(root), lambda: _verify(root, version),
                                         lambda result: result.get('ok') is True, max_age=max_age)
    except (OSError, ValueError):
        return {'ok': False, 'code': 'build_unqualified', 'error': 'Payload changed during verification'}


def _verify(root: Path, version: str) -> dict:
    path = root / "memory_os_v4" / NAME
    try:
        if path.is_symlink() or path.stat().st_size > 1_000_000:
            raise ValueError("unsafe descriptor")
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("schema_version") != 1 or record.get("version") != version:
            raise ValueError("descriptor version mismatch")
        if not re.fullmatch(r"[0-9a-f]{40}", str(record.get("source_commit", ""))):
            raise ValueError("invalid source commit")
        if not re.fullmatch(r"[0-9a-f]{64}", str(record.get("source_inventory_sha256", ""))):
            raise ValueError("invalid source inventory")
        files = record.get("files")
        if not isinstance(files, dict) or not files or any(
            not isinstance(name, str) or Path(name).is_absolute() or ".." in Path(name).parts
            or name.split("/")[0] != "memory_os_v4"
            or not re.fullmatch(r"[0-9a-f]{64}", str(value)) for name, value in files.items()
        ):
            raise ValueError("invalid payload inventory")
        canonical = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
        if hashlib.sha256(canonical).hexdigest() != record.get("payload_sha256"):
            raise ValueError("invalid payload root")
        if _importable_legacy(root / "memory_os_ai"):
            # 4.1.3: the legacy v3 package is no longer shipped; v3 CODE found beside memory_os_v4 is stale or
            # foreign code importable by the same interpreter. An empty leftover directory or orphan bytecode
            # caches (never imported without their sources, PEP 3147) are tolerated.
            raise ValueError("legacy v3 package beside the installed payload")
        actual = {}
        for package in ("memory_os_v4",):  # 4.1.3: no legacy v3 package
            directory = root / package
            if directory.is_symlink():
                raise ValueError("symlinked package")
            for item in directory.rglob("*"):
                if "__pycache__" in item.parts or item.suffix in {".pyc", ".pyo"} or item == path:
                    continue
                if item.is_symlink():
                    raise ValueError("symlinked payload")
                if item.is_file():
                    actual[str(item.relative_to(root))] = hashlib.sha256(item.read_bytes()).hexdigest()
        if actual != files:
            raise ValueError("payload missing, modified or added")
        return {"ok": True, "commit": record["source_commit"],
                "descriptor_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "payload_sha256": record["payload_sha256"]}
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        return {"ok": False, "code": "build_unqualified", "error": str(exc)}

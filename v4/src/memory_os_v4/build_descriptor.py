"""Verify installed payload coherence; hashes are not a publisher signature."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

NAME = "_build_identity.json"


def verify(root: Path, version: str) -> dict:
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
            or name.split("/")[0] not in {"memory_os_v4", "memory_os_ai"}
            or not re.fullmatch(r"[0-9a-f]{64}", str(value)) for name, value in files.items()
        ):
            raise ValueError("invalid payload inventory")
        canonical = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
        if hashlib.sha256(canonical).hexdigest() != record.get("payload_sha256"):
            raise ValueError("invalid payload root")
        actual = {}
        for package in ("memory_os_v4", "memory_os_ai"):
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

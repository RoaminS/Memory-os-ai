"""Explicit pinned resources for installed wheels. Preview has no side effects.

No Hugging Face/model import, inference, user credential or client config access.
Missing resources need BOTH apply and download approval. Failed staging is kept.
"""
from __future__ import annotations

from contextlib import contextmanager
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
import uuid

from . import cell, storage

CATALOG = Path(__file__).resolve().parents[1] / "resources/provision_v1.json"
CAP = 300_000_000


class ProvisionError(RuntimeError):
    pass


def min_free_bytes() -> int:
    """Conservative default; an explicit operator override cannot go below 3 GB."""
    raw = os.environ.get("MEMORY_V4_MIN_FREE_BYTES")
    if raw is None:
        return 5_000_000_000
    message = "MEMORY_V4_MIN_FREE_BYTES must be integer bytes >= 3000000000 (3 GB)"
    if not re.fullmatch(r"[0-9]+", raw):
        raise ProvisionError(message)
    try:
        minimum = int(raw)
    except ValueError as exc:
        raise ProvisionError(message) from exc
    if minimum < 3_000_000_000:
        raise ProvisionError(message)
    return minimum


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1_048_576), b""):
            digest.update(chunk)
    return digest.hexdigest()


def catalog() -> dict:
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def matches(path: Path, record: dict) -> bool:
    return path.is_file() and path.stat().st_size == record["bytes"] and sha256(path) == record["sha256"]


def checked_ref(path: Path, revision: str) -> bool:
    """HF reads refs verbatim. Never normalize or repair another cache's ref."""
    if not path.exists() and not path.is_symlink():
        return False
    if path.is_symlink() or not path.is_file():
        raise ProvisionError("Existing Granite refs/main is not a regular ref; preserve it and use a fresh owned cache")
    try:
        exact = path.read_bytes() == revision.encode('ascii')
    except OSError as exc:
        raise ProvisionError("Existing Granite refs/main unreadable; preserve it and investigate") from exc
    if not exact:
        raise ProvisionError("Existing Granite refs/main bytes differ from the pinned revision; "
                             "no silent profile change or repair; use a fresh owned cache")
    return True


def plan(*, resources=("granite", "cl100k"), cache_dir=None, table_path=None) -> dict:
    """Read-only plan used by quickstart. Environment returned contains paths, not secrets."""
    min_free_bytes()  # Invalid operator policies are refused even in a read-only preview.
    chosen = tuple(resources)
    if not chosen or len(set(chosen)) != len(chosen) or set(chosen) - {"granite", "cl100k"}:
        raise ProvisionError("Select granite and/or cl100k, without duplicate resources")
    record = catalog()
    cache = Path(cache_dir or os.environ.get("HF_HUB_CACHE") or
                 Path(os.environ.get("HF_HOME", Path.home() / ".cache/huggingface")) / "hub").expanduser().absolute()
    table = Path(table_path or os.environ.get("MEMORY_V4_CL100K_PATH") or
                 Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "memory-os/cl100k_base.tiktoken").expanduser().absolute()
    model = cache / ("models--" + record["model_id"].replace("/", "--"))
    snapshot = model / "snapshots" / record["revision"]
    files = []
    ref_present = False
    if "granite" in chosen:
        ref = model / "refs/main"
        ref_present = checked_ref(ref, record["revision"])
        for item in record["files"]:
            target = snapshot / item["path"]
            present = matches(target, item)
            if (target.exists() or target.is_symlink()) and not present:
                raise ProvisionError("Existing Granite file differs; preserve it and investigate: " + item["path"])
            files.append({**item, "resource": "granite", "target": str(target),
                          "url": f'https://huggingface.co/{record["model_id"]}/resolve/{record["revision"]}/{item["path"]}',
                          "present": present})
    if "cl100k" in chosen:
        item = record["cl100k"]
        present = matches(table, item)
        if (table.exists() or table.is_symlink()) and not present:
            raise ProvisionError("Existing cl100k table differs; never overwritten")
        files.append({**item, "path": "cl100k_base.tiktoken", "resource": "cl100k",
                      "target": str(table), "present": present})
    missing = sum(item["bytes"] for item in files if not item["present"])
    if sum(item["bytes"] for item in files) > CAP:
        raise ProvisionError("Resource manifest exceeds 300 MB cap")
    ready = not missing and ("granite" not in chosen or ref_present)
    return {"schema_version": 1, "ok": True, "mode": "preview", "ready": ready,
            "resources": list(chosen), "revision": record["revision"], "model_id": record["model_id"],
            "catalog_sha256": sha256(CATALOG), "cache_dir": str(cache), "table_path": str(table),
            "files": files, "download_bytes": missing, "peak_additional_bytes_upper_bound": 2 * missing + 1_000_000,
            "env": {**({"HF_HUB_CACHE": str(cache)} if "granite" in chosen else {}),
                    **({"MEMORY_V4_CL100K_PATH": str(table)} if "cl100k" in chosen else {}),
                    "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}}


def guard(path: Path) -> None:
    """Disk and swap guard. macOS: unchanged. Linux: retained cell with its lock, statvfs, /proc/meminfo."""
    minimum = min_free_bytes()
    existing = path
    while not existing.exists():
        existing = existing.parent
    platform_name = sys_platform()
    linux = platform_name.startswith("linux")
    free = storage.statvfs_free_bytes(existing) if linux else shutil.disk_usage(existing).free
    if free < minimum:
        raise ProvisionError(f"Disk guard: {free} bytes free < effective minimum {minimum} bytes")
    if platform_name == "darwin":
        try:
            result = subprocess.run(["/usr/sbin/sysctl", "vm.swapusage"], text=True, capture_output=True, timeout=3)
            match = re.search(r"used\s*=\s*([\d.]+)([KMGT])", result.stdout)
            if result.returncode or not match:
                raise ProvisionError("Swap measurement unavailable")
            used = float(match[1]) * 1024 ** ("KMGT".index(match[2]) + 1)
            if used > storage.SWAP_LIMIT_BYTES:
                raise ProvisionError("Swap guard: more than 8.5 GB")
        except (OSError, subprocess.SubprocessError) as exc:
            raise ProvisionError("Swap measurement unavailable") from exc
    elif linux:
        resource_gate(("cl100k",))
        try:
            used = storage.linux_swap_used_bytes()
        except (OSError, RuntimeError) as exc:
            raise ProvisionError("Swap measurement unavailable") from exc
        if used > storage.SWAP_LIMIT_BYTES:
            raise ProvisionError("Swap guard: more than 8.5 GB")
    else:
        raise ProvisionError("Resource acquisition qualified only for macOS and the retained Linux cell")


def resource_gate(resources) -> None:
    """Outside macOS (unchanged), resources are acquired only on a retained cell that embeds its lock.

    Granite belongs to the semantic profile, which stays limited to its own cell.
    """
    if sys_platform() == "darwin":
        return
    verdict = cell.acceptance(cell.detect(), profile="semantic" if "granite" in resources else "lexical")
    if not verdict["ok"]:
        raise ProvisionError(verdict["message"])


def sys_platform():
    import sys
    return sys.platform


def allowed_url(url: str) -> bool:
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and parsed.port in {None, 443} and not parsed.username and not parsed.password and (
        host in {"huggingface.co", "openaipublic.blob.core.windows.net"} or host.endswith(".hf.co")
        or host.endswith(".huggingface.co"))


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed_url(newurl):
            raise ProvisionError("Resource redirected to an unapproved host")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def opener():
    # Public files only. Never use Hugging Face auth helpers or credential files.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), SafeRedirect())


def upstream_check(source, record: dict) -> None:
    url = f'https://huggingface.co/api/models/{record["model_id"]}/revision/{record["revision"]}?blobs=true'
    with source.open(url, timeout=30) as response:
        data = response.read(1_000_001)
        if len(data) > 1_000_000:
            raise ProvisionError("Oversized upstream metadata")
    info = json.loads(data)
    if info.get("sha") != record["revision"] or info.get("cardData", {}).get("license") != "apache-2.0":
        raise ProvisionError("Upstream revision/licence differs from approved manifest")
    by_name = {item["rfilename"]: item for item in info["siblings"]}
    for item in record["files"]:
        upstream = by_name.get(item["path"], {})
        blob = upstream.get("lfs", {}).get("sha256") or upstream.get("blobId")
        if upstream.get("size") != item["bytes"] or blob != item["upstream_blob"]:
            raise ProvisionError("Upstream resource metadata differs: " + item["path"])


@contextmanager
def lock(cache: Path):
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / ".memory-os-provision.lock"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ProvisionError("Provisioning lock exists; no automatic adoption/removal") from exc
    try:
        os.close(fd)
        yield
    finally:
        path.unlink()


def _safe_parent(target: Path):
    if any(p.is_symlink() for p in (target, *target.parents)):
        raise ProvisionError("Refusing a symlinked write destination")
    target.parent.mkdir(parents=True, exist_ok=True)


def _sync_parent(path: Path):
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _install(partial: Path, target: Path):
    _safe_parent(target)
    try:
        os.link(partial, target)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        # A user may place the tokenizer table on another filesystem.
        local = target.parent / (".memory-os-" + uuid.uuid4().hex + ".part")
        with partial.open("rb") as source, local.open("xb") as out:
            shutil.copyfileobj(source, out, 1_048_576)
            out.flush()
            os.fsync(out.fileno())
        os.link(local, target)
        local.unlink()  # success only; failures preserve the staging file
    _sync_parent(target)


def apply(preview: dict, *, download_approved: bool = False) -> dict:
    """Revalidate, then provision sequentially. No service, token or client changes."""
    expected = plan(resources=preview["resources"], cache_dir=preview["cache_dir"], table_path=preview["table_path"])
    if preview != expected:
        raise ProvisionError("Resources changed since preview; create a new preview")
    if expected["ready"]:
        return {**expected, "mode": "apply", "downloaded_bytes": 0, "modified": False}
    if expected["download_bytes"] and not download_approved:
        raise ProvisionError("Missing resources: explicit --download-approved consent required")
    cache = Path(expected["cache_dir"])
    resource_gate(expected["resources"])
    guard(cache)
    for item in expected["files"]:
        guard(Path(item["target"]).parent)
    started = time.monotonic()
    received = 0
    stage = cache / (".memory-os-provision-" + uuid.uuid4().hex)
    report = {"ok": False, "mode": "apply",
              "staging_dir": str(stage), "downloaded_bytes": 0}
    try:
        _safe_parent(cache / "placeholder")
        with lock(cache):
            stage.mkdir(mode=0o700)
            source = opener()
            record = catalog()
            if any(x["resource"] == "granite" and not x["present"] for x in expected["files"]):
                upstream_check(source, record)
            staged = []
            for index, item in enumerate(expected["files"]):
                if item["present"]:
                    continue
                guard(cache)
                partial = stage / f"{index:02d}.part"
                h = hashlib.sha256()
                count = 0
                with source.open(item["url"], timeout=30) as response, partial.open("xb") as out:
                    if not allowed_url(response.geturl()):
                        raise ProvisionError("Unapproved resource URL")
                    length = response.headers.get("Content-Length")
                    if length is None or int(length) != item["bytes"]:
                        raise ProvisionError("Resource declared length differs from pinned manifest")
                    while True:
                        if time.monotonic() - started > 600:
                            raise ProvisionError("Provisioning wall-time budget exceeded (600 s)")
                        guard(cache)
                        data = response.read(min(4_194_304, item["bytes"] - count + 1))
                        if not data:
                            break
                        out.write(data)
                        count += len(data)
                        received += len(data)
                        h.update(data)
                        if count > item["bytes"] or received > CAP:
                            raise ProvisionError("Resource download cap exceeded")
                    out.flush()
                    os.fsync(out.fileno())
                if count != item["bytes"] or h.hexdigest() != item["sha256"]:
                    raise ProvisionError("Resource size/SHA-256 mismatch; staging preserved")
                blob = item.get("upstream_blob", "")
                if len(blob) == 40:
                    git_hash = hashlib.sha1(f"blob {count}\0".encode() + partial.read_bytes()).hexdigest()
                    if git_hash != blob:
                        raise ProvisionError("Upstream Git blob mismatch")
                staged.append((partial, item))
            # Activation only after every staged resource is verified.
            for partial, item in staged:
                target = Path(item["target"])
                # Hard-link creation is exclusive; never overwrite another writer.
                _install(partial, target)
            if "granite" in expected["resources"]:
                ref = cache / ("models--" + record["model_id"].replace("/", "--")) / "refs/main"
                _safe_parent(ref)
                if not checked_ref(ref, record["revision"]):
                    ready_ref = stage / "main.ref"
                    with ready_ref.open("xb") as out:
                        out.write(record["revision"].encode('ascii'))
                        out.flush()
                        os.fsync(out.fileno())
                    _install(ready_ref, ref)
            final = plan(resources=expected["resources"], cache_dir=cache, table_path=expected["table_path"])
            if not final["ready"]:
                raise ProvisionError("Provisioned resources failed final inventory")
            report = {**final, "ok": True, "mode": "apply", "modified": True, "staging_dir": str(stage)}
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ProvisionError) as exc:
        # No signed CDN URLs, credentials, or model content in the error report.
        report["code"] = "provision_failed"
        report["error"] = str(exc) if isinstance(exc, ProvisionError) else type(exc).__name__
    report.update(downloaded_bytes=received, elapsed_ms=round(1000 * (time.monotonic() - started), 3))
    if stage.exists():
        (stage / "PROVENANCE.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report

"""Read-only space estimates shared by health, doctor and SQLite S4 guards."""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

FLOOR = 5_000_000_000
SWAP_LIMIT_BYTES = 8_500_000_000  # one threshold for every swap guard (provision.guard, machine_guard)
PROC_MEMINFO = Path("/proc/meminfo")


def sys_platform():
    return sys.platform


def statvfs_free_bytes(path, *, statvfs=None):
    """Bytes available to this user on the volume of ``path`` (Linux guards)."""
    usage = (statvfs or os.statvfs)(path)
    return usage.f_bavail * usage.f_frsize


def linux_swap_used_bytes():
    """Swap in use according to ``/proc/meminfo``; a value that cannot be attested is refused."""
    rows = dict(re.findall(r"^(SwapTotal|SwapFree):\s+(\d+) kB$", PROC_MEMINFO.read_text(), re.M))
    if len(rows) != 2 or int(rows["SwapFree"]) > int(rows["SwapTotal"]):
        raise RuntimeError("Swap cannot be attested")
    return (int(rows["SwapTotal"]) - int(rows["SwapFree"])) * 1024


def nearest_existing(path):
    path = Path(path).expanduser().absolute()
    while not path.exists():
        if path == path.parent:
            raise ValueError("No existing storage parent")
        path = path.parent
    return path


def policy(footprint, *, sqlite=True):
    chosen = os.environ.get("MEMORY_V4_SQLITE_STORAGE_POLICY", "legacy5gb")
    if chosen not in {"legacy5gb", "adaptive_v1"}:
        raise ValueError("MEMORY_V4_SQLITE_STORAGE_POLICY must be legacy5gb or adaptive_v1")
    if chosen == "adaptive_v1" and not sqlite:
        raise ValueError("adaptive_v1 requires a SQLite store")
    reserve = FLOOR if chosen == "legacy5gb" else max(3_000_000_000, 2 * footprint)
    # Streaming archive + authenticated compressed/unpacked spools + extracted DB,
    # snapshot and headroom. Budget actual footprint, never the maximum cap.
    working = (6 * max(footprint, 1_048_576) + 32 * 1024**2) if sqlite else max(footprint, 1_048_576) * 3
    return chosen, reserve, working


def margin(settings):
    store = settings.store_dir
    if (store / "store.sqlite3").exists():
        paths = [store / ("store.sqlite3" + suffix) for suffix in ("", "-wal", "-shm")]
    else:
        paths = [
            store / name
            for name in ("items.jsonl", "blocks.jsonl", "requests.jsonl", "meta.json", "embeddings.npy")
        ]
    footprint = 0
    for path in paths:
        if path.is_symlink():
            raise ValueError("Linked storage refused")
        if path.exists():
            footprint += path.stat().st_size
    # Conservative db + encrypted archive + isolated drill; includes source WAL.
    chosen, reserve, working = policy(footprint, sqlite=(store / "store.sqlite3").exists())
    required = reserve + working
    volumes = {}
    for label, path in (("source", store), ("backup", settings.backup_dir), ("ops", settings.ops_dir)):
        existing = nearest_existing(path)
        free = shutil.disk_usage(existing).free
        need = required  # Conservative on every volume, even when devices differ.
        volumes[label] = {
            "free_bytes": free,
            "required_bytes": need,
            "margin_bytes": free - need,
            "device": existing.stat().st_dev,
        }
    return {
        "policy": chosen,
        "reserve_bytes": reserve,
        "non_negotiable_floor_bytes": 3_000_000_000,
        "source_bytes": footprint,
        "estimated_working_bytes": working,
        "required_bytes": required,
        "volumes": volumes,
        "ok": all(v["margin_bytes"] >= 0 for v in volumes.values()),
    }


def machine_guard(settings, *, require_nice=True):
    space = margin(settings)
    if not space["ok"]:
        raise RuntimeError(
            f"storage_margin_insufficient: policy={space['policy']} reserve={space['reserve_bytes']} required={space['required_bytes']} bytes"
        )
    if require_nice and os.getpriority(os.PRIO_PROCESS, 0) < 15:
        raise RuntimeError("SQLite S4 requires nice 15, concurrency 1")
    platform_name = sys_platform()
    if platform_name == "darwin":
        proc = subprocess.run(
            ["/usr/sbin/sysctl", "-n", "vm.swapusage"], capture_output=True, text=True, timeout=3
        )
        match = re.search(r"used\s*=\s*([0-9.]+)([KMG])", proc.stdout)
        if proc.returncode or not match:
            raise RuntimeError("Swap cannot be attested; operation refused")
        swap = float(match[1]) * {"K": 1024, "M": 1024**2, "G": 1024**3}[match[2]]
    elif platform_name.startswith("linux"):
        swap = linux_swap_used_bytes()
    else:
        raise RuntimeError("Unqualified swap measurement platform")
    if swap > SWAP_LIMIT_BYTES:
        raise RuntimeError("Swap exceeds 8.5 GB; operation refused")
    return {**space, "swap_bytes": int(swap)}

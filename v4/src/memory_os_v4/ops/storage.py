"""Read-only space estimates shared by health, doctor and SQLite S4 guards."""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

FLOOR = 5_000_000_000


def nearest_existing(path):
    path = Path(path).expanduser().absolute()
    while not path.exists():
        if path == path.parent:
            raise ValueError("No existing storage parent")
        path = path.parent
    return path


def margin(settings):
    store = settings.store_dir
    if (store / "store.sqlite3").exists():
        paths = [store / ("store.sqlite3" + suffix) for suffix in ("", "-wal", "-shm")]
    else:
        paths = [store / name for name in ("items.jsonl", "blocks.jsonl", "requests.jsonl", "meta.json", "embeddings.npy")]
    footprint = 0
    for path in paths:
        if path.is_symlink():
            raise ValueError("Linked storage refused")
        if path.exists():
            footprint += path.stat().st_size
    # Conservative db + encrypted archive + isolated drill; includes source WAL.
    working = max(footprint, 1_048_576) * 3
    required = max(FLOOR + working, 2 * working)
    volumes = {}
    for label, path in (("source", store), ("backup", settings.backup_dir), ("ops", settings.ops_dir)):
        existing = nearest_existing(path)
        free = shutil.disk_usage(existing).free
        need = FLOOR if label == "source" else required
        volumes[label] = {"free_bytes": free, "required_bytes": need, "margin_bytes": free - need,
                          "device": existing.stat().st_dev}
    return {"source_bytes": footprint, "estimated_working_bytes": working,
            "required_bytes": required, "volumes": volumes,
            "ok": all(v["margin_bytes"] >= 0 for v in volumes.values())}


def machine_guard(settings, *, require_nice=True):
    space = margin(settings)
    if not space["ok"]:
        raise RuntimeError("storage_margin_insufficient: need 5 GB reserve and backup/drill working space")
    if require_nice and os.getpriority(os.PRIO_PROCESS, 0) < 15:
        raise RuntimeError("SQLite S4 requires nice 15, concurrency 1")
    if sys.platform == "darwin":
        proc = subprocess.run(["/usr/sbin/sysctl", "-n", "vm.swapusage"], capture_output=True,
                              text=True, timeout=3)
        match = re.search(r"used\s*=\s*([0-9.]+)([KMG])", proc.stdout)
        if proc.returncode or not match:
            raise RuntimeError("Swap cannot be attested; operation refused")
        swap = float(match[1]) * {"K": 1024, "M": 1024 ** 2, "G": 1024 ** 3}[match[2]]
    elif sys.platform.startswith("linux"):
        rows = dict(re.findall(r"^(SwapTotal|SwapFree):\s+(\d+) kB$", Path("/proc/meminfo").read_text(), re.M))
        if len(rows) != 2:
            raise RuntimeError("Swap cannot be attested")
        swap = (int(rows["SwapTotal"]) - int(rows["SwapFree"])) * 1024
    else:
        raise RuntimeError("Unqualified swap measurement platform")
    if swap > 8_500_000_000:
        raise RuntimeError("Swap exceeds 8.5 GB; operation refused")
    return {**space, "swap_bytes": int(swap)}

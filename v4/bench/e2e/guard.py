"""Fail-closed machine guard; no override for an unknown swap measurement."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import subprocess


class ResourceStop(RuntimeError):
    pass


@dataclass(frozen=True)
class Limits:
    # Decimal GB (the user's Go thresholds); do not silently round up to GiB.
    swap_bytes: int = 8_500_000_000
    free_bytes: int = 5_000_000_000
    nice: int = 15


def swap_used() -> int:
    result = subprocess.run(["/usr/sbin/sysctl", "vm.swapusage"], capture_output=True,
                            text=True, timeout=3)
    if result.returncode:
        raise ResourceStop("Swap measurement denied/unavailable; workload not authorized by guard")
    match = re.search(r"used\s*=\s*([\d.]+)([KMGT])", result.stdout)
    if not match:
        raise ResourceStop("Unknown swap measurement format")
    return int(float(match[1]) * 1024 ** ("KMGT".index(match[2]) + 1))


def ensure_priority(limits: Limits) -> None:
    try:
        current = os.getpriority(os.PRIO_PROCESS, 0)
        if current < limits.nice:
            os.setpriority(os.PRIO_PROCESS, 0, limits.nice)
        if os.getpriority(os.PRIO_PROCESS, 0) < limits.nice:
            raise ResourceStop("nice >= 15 not effective")
    except OSError as e:
        raise ResourceStop("Cannot enforce nice >= 15; workload refused") from e


def sample(path: Path, limits: Limits = Limits()) -> dict:
    swap = swap_used()
    free = shutil.disk_usage(path).free
    if swap > limits.swap_bytes:
        raise ResourceStop(f"swap {swap} > {limits.swap_bytes} bytes")
    if free < limits.free_bytes:
        raise ResourceStop(f"free disk {free} < {limits.free_bytes} bytes")
    return {"swap_bytes": swap, "free_bytes": free}

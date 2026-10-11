#!/usr/bin/env python3
"""Measure prepared+sending fsync on a fresh synthetic local ledger; no server/model."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "v4/src"))
from memory_os_v4.ops.request_journal import RelayJournal  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=100)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / ".local-qualification") or not 20 <= args.samples <= 1000:
        parser.error("fresh output inside clone/.local-qualification; 20..1000 samples")
    if shutil.disk_usage(ROOT).free < 5_000_000_000:
        parser.error("Disk guard: <5 GB free")
    output.mkdir(parents=True, exist_ok=False)
    j = RelayJournal(output / "journal", "http://127.0.0.1:9/mcp/", "synthetic")
    values = []
    try:
        for i in range(args.samples):
            row = {"request_id": str(i), "scope": '["project", "synthetic"]', "operation": "memory_block_set",
                   "store_instance_id": "synthetic-store", "raw_args_sha256": "a" * 64}
            start = time.perf_counter()
            j.append(row, "prepared")
            j.append(row, "sending")
            values.append((time.perf_counter() - start) * 1000)
            j.append({**row, "result_sha256": "b" * 64}, "completed")
        ordered = sorted(values)
        report = {"synthetic_only": True, "neural_inference": False, "production_modified": False,
                  "samples": args.samples, "prepared_sending_ms": values,
                  "p50_ms": ordered[len(values) // 2], "p95_ms": ordered[(len(values) * 95 + 99) // 100 - 1],
                  "max_ms": max(values), "phase_metrics": j.status()["fsync_ms"],
                  "l9_250ms_durable_ack_qualified": False}
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({k: report[k] for k in ("samples", "p50_ms", "p95_ms", "max_ms")}))
    finally:
        j.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

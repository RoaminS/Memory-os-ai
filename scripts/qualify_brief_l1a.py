#!/usr/bin/env python3
"""Exact offline L1a qualification. Refuses missing table/counter; never downloads."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "v4/src"), str(ROOT / "v4/tests")]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--local-tiktoken-site",
        type=Path,
        help="Optional existing site-packages appended AFTER this interpreter dependencies; no install",
    )
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / ".local-qualification"):
        parser.error("fresh output must be inside clone .local-qualification")
    output.mkdir(parents=True, exist_ok=False)
    report = {
        "ok": False,
        "exact_tokens_qualified": False,
        "production_modified": False,
        "inference": False,
        "cases": [],
        "tokenizer": None,
    }
    try:
        if args.local_tiktoken_site:
            sys.path.append(str(args.local_tiktoken_site.resolve()))
        from memory_os_v4.brief_tokens import counter, status
        from memory_os_v4 import brief, server

        count = counter()  # BEFORE any measurement or store
        report["tokenizer"] = status()
        from test_brief_l1a import (
            CASES,
            FIXTURES,
            load_case,
            run_path,
            assert_case,
            ids_of,
            timestamp,
        )

        manifest = json.loads((FIXTURES / "manifest_v1.json").read_text())
        digest = hashlib.sha256(
            (FIXTURES / "scenarios_v1.jsonl").read_bytes()
        ).hexdigest()
        if digest != manifest["scenarios_sha256"] or len(CASES) != 100:
            raise RuntimeError("scenario seal mismatch")
        report["scenarios_sha256"] = digest
        for backend in ("jsonl", "sqlite_s1"):
            for case in CASES:
                if shutil.disk_usage(output).free < 5_000_000_000:
                    raise RuntimeError("disk guard < 5 GB")
                if sys.platform == "darwin":
                    import re

                    swap = subprocess.run(
                        ["/usr/sbin/sysctl", "vm.swapusage"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    found = re.search(r"used = ([0-9.]+)([MG])", swap.stdout)
                    if swap.returncode != 0 or not found:
                        raise RuntimeError(
                            "swap guard unavailable: execute outside sandbox"
                        )
                    used = float(found[1]) * (1024**2 if found[2] == "M" else 1024**3)
                    if used > 8.5 * 1024**3:
                        raise RuntimeError("swap guard > 8.5 GiB")
                root = output / (case["id"] + "-" + backend)
                root.mkdir()
                with patch(
                    "memory_os_v4.brief.time.time", return_value=timestamp(case["now"])
                ):
                    e = load_case(case, root, backend)
                    try:
                        with (
                            patch.object(server, "_engine", e),
                            patch.object(server, "get_engine", return_value=e),
                        ):
                            for path in ("server", "relay"):
                                start = time.perf_counter()
                                data = run_path(case, path)
                                elapsed = 1000 * (time.perf_counter() - start)
                                error = (
                                    case["expect"]
                                    .get("path_errors", {})
                                    .get(path, case["expect"].get("error_code"))
                                )
                                if error:
                                    assert data["code"] == error, data
                                else:
                                    assert_case(case, data, path)
                                    if case["expect"].get("deterministic"):
                                        assert data == run_path(case, path)
                                    if case["expect"].get("compare_paths"):
                                        assert run_path(case, "server") == run_path(
                                            case, "relay"
                                        )
                                    if case["expect"].get("compare_modes"):
                                        other = server.dispatch(
                                            "memory_session_brief",
                                            {**case["request"], "mode": "compact"},
                                        )
                                        assert set(
                                            case["expect"]["required_ids"]
                                        ) <= ids_of(other)
                                    if case["expect"]["expand_ids"]:
                                        detail = server.dispatch(
                                            "memory_session_brief",
                                            {
                                                "project": case["request"]["project"],
                                                "mode": "detailed",
                                                "expand": case["expect"]["expand_ids"],
                                            },
                                        )
                                        assert set(
                                            case["expect"]["expand_ids"]
                                        ) <= ids_of(detail)
                                size, tokens = brief.measures(data, count)
                                compact = case["request"]["mode"] == "compact"
                                if compact:
                                    assert size <= 6000 and tokens <= 1500, (
                                        case["id"],
                                        size,
                                        tokens,
                                    )
                                row = {
                                    "id": case["id"],
                                    "backend": backend,
                                    "path": path,
                                    "compact": compact,
                                    "bytes": size,
                                    "tokens_cl100k_base": tokens,
                                    "ms": elapsed,
                                    "ok": True,
                                }
                                report["cases"].append(row)
                                with (output / "measurements.jsonl").open("a") as file:
                                    file.write(json.dumps(row) + "\n")
                    finally:
                        e.close()
                        shutil.rmtree(
                            root
                        )  # disposable sources only; measured evidence stays
        assert len(report["cases"]) == 400
        compact = [r for r in report["cases"] if r["compact"]]
        durations = sorted(r["ms"] for r in report["cases"])
        report["latency_ms"] = {"p50": durations[len(durations)//2],
                                "p95": durations[int(.95*(len(durations)-1))], "max": durations[-1]}
        report.update(
            ok=True,
            exact_tokens_qualified=True,
            compact_max_bytes=max(r["bytes"] for r in compact),
            compact_max_tokens=max(r["tokens_cl100k_base"] for r in compact),
        )
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        report["source_sha256"] = {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((ROOT / "v4/src").rglob("*.py"))
        }
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                k: report.get(k)
                for k in (
                    "ok",
                    "exact_tokens_qualified",
                    "error",
                    "compact_max_bytes",
                    "compact_max_tokens",
                )
            }
        )
    )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    raise SystemExit(main())

#!/usr/bin/env python3
"""Verify the immutable local evidence for the published BEAM v2 summary."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from pathlib import Path


def load(path: Path):
    return json.loads(path.read_text())


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def same(actual, expected, label: str) -> None:
    if isinstance(expected, float):
        if not math.isclose(actual, expected, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError(f"{label}: {actual!r} != {expected!r}")
    elif actual != expected:
        raise ValueError(f"{label}: {actual!r} != {expected!r}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence_dir", type=Path)
    parser.add_argument(
        "--summary", type=Path,
        default=Path("v4/bench/results/beam_c4_retrieval_v2_summary.json"),
    )
    args = parser.parse_args()
    expected = load(args.summary)
    for relative, wanted in expected["files"].items():
        same(digest(args.evidence_dir / relative), wanted, relative + " sha256")

    report = load(args.evidence_dir / "retrieval/report.json")
    for key in ("complete", "questions", "recall_questions", "clusters",
                "bootstrap_replicates", "numerical_criterion_met", "claim"):
        same(report[key], expected[key], key)
    for system in ("memory", "bm25"):
        for metric in ("recall", "mrr"):
            same(report["systems"][system][metric], expected["systems"][system][metric],
                 f"{system}.{metric}")
    for metric in ("recall", "mrr"):
        same(report["paired"][metric], expected["paired"][metric], f"paired.{metric}")
    for key, wanted in expected["usage"].items():
        same(report["usage"][key], wanted, "usage." + key)

    rows = [json.loads(line) for line in
            (args.evidence_dir / "retrieval/predictions.jsonl").read_text().splitlines()]
    same(len(rows), expected["questions"], "prediction rows")
    conversations = {}
    for row in rows:
        conversations.setdefault(row["conversation"], row)
    same(len(conversations), expected["clusters"], "conversation count")
    ingest = [row["conversation_ingest_seconds"] for row in conversations.values()]
    searches = sorted(row["search_ms"]["memory"] for row in rows)
    observed = {
        "conversation_ingest_total_seconds": sum(ingest),
        "conversation_ingest_median_seconds": statistics.median(ingest),
        "conversation_ingest_max_seconds": max(ingest),
        "memory_search_p50_ms": statistics.median(searches),
        "memory_search_p95_ms": searches[int(0.95 * len(searches))],
    }
    for key, wanted in expected["timing"].items():
        same(observed[key], wanted, "timing." + key)
    print("BEAM v2 evidence verified: criterion NOT MET; 0 LLM calls; no leaderboard claim")


if __name__ == "__main__":
    main()

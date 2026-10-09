"""Guarded P1 lifecycle benchmark: hash only, sequential, no network/model downloads."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import time

from .e2e.guard import Limits, ResourceStop, ensure_priority, sample
from memory_os_v4 import Config, MemoryV4
from memory_os_v4 import memory as facade
from memory_os_v4.embed import HashEmbedder
from memory_os_v4.sqlite_store import SQLiteStore, validate_sqlite_target


def measure(directory, n, repeats, check):
    counts = {"texts": 0, "calls": 0, "seconds": 0.0}

    class CountedHash(HashEmbedder):
        def encode(self, texts):
            check()
            start = time.perf_counter()
            vectors = super().encode(texts)
            counts["texts"] += len(texts)
            counts["calls"] += 1
            counts["seconds"] += time.perf_counter() - start
            check()
            return vectors

    original = facade._make_embedder
    facade._make_embedder = lambda config: CountedHash(dim=config.hash_dim)
    config = Config(data_dir=str(directory), store_backend="sqlite_s1", embed_model="hash", hash_dim=384)
    try:
        check()
        with MemoryV4(config=config) as engine:
            for start in range(0, n, 250):
                check()
                engine.add_episodes_bulk(
                    [
                        {
                            "item_id": f"item-{i}",
                            "project": "synthetic",
                            "event_time": 100,
                            "text": f"Synthetic item {i}. "
                            + "Synthetic memory storage lifecycle test with explicit provenance. " * 6,
                        }
                        for i in range(start, min(n, start + 250))
                    ]
                )
        check()
        store = SQLiteStore(directory)
        try:
            with store.transaction():
                store.write("benchmark_cache_reset", "DELETE FROM embedding_cache")
        finally:
            store.close()
        counts.update(texts=0, calls=0, seconds=0.0)
        start = time.perf_counter()
        engine = MemoryV4(config=config)
        cold = {
            "total_s": time.perf_counter() - start,
            "encoder_texts": counts["texts"],
            "encoder_calls": counts["calls"],
            "encode_s": counts["seconds"],
        }
        engine.close()
        warm, rollbacks = [], []
        for _ in range(repeats):
            check()
            counts.update(texts=0, calls=0, seconds=0.0)
            start = time.perf_counter()
            engine = MemoryV4(config=config)
            try:
                warm.append({"total_s": time.perf_counter() - start, "encoder_texts": counts["texts"]})
                counts.update(texts=0, calls=0, seconds=0.0)
                rebuild = engine._rebuild_sql_indexes
                rebuild_seconds = []

                def timed_rebuild():
                    begin = time.perf_counter()
                    rebuild()
                    rebuild_seconds.append(time.perf_counter() - begin)

                engine._rebuild_sql_indexes = timed_rebuild

                def fail():
                    engine.forget("item-0", project="synthetic")
                    raise ValueError("Synthetic rollback after mutation")

                start = time.perf_counter()
                try:
                    engine.execute_request("benchmark_rollback", "synthetic", {}, "rollback", fail)
                except ValueError:
                    pass
                rollbacks.append(
                    {
                        "total_s": time.perf_counter() - start,
                        "rebuild_s": sum(rebuild_seconds),
                        "encoder_texts": counts["texts"],
                    }
                )
                assert not engine.store.get("item-0").deleted
                assert len(engine.store.items) == n
            finally:
                engine.close()
            check()
        assert cold["encoder_texts"] == n
        assert all(row["encoder_texts"] == 0 for row in [*warm, *rollbacks])
        return {
            "items": n,
            "dim": 384,
            "cold": cold,
            "warm": warm,
            "rollback": rollbacks,
            "warm_p50_s": statistics.median(row["total_s"] for row in warm),
            "rollback_p50_s": statistics.median(row["total_s"] for row in rollbacks),
        }
    finally:
        facade._make_embedder = original


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args(argv)
    if not 1 <= args.repeats <= 5:
        parser.error("repeats must be 1..5")
    output = Path(args.output).resolve()
    validate_sqlite_target(output)
    if output.exists():
        parser.error("output must be a new directory")
    output.mkdir(parents=True, mode=0o700)
    report = {
        "protocol": "sqlite-lifecycle-hash-v1",
        "counts": [1000, 10000],
        "repeats": args.repeats,
        "complete": False,
        "results": [],
        "resources": [],
        "granite": "extrapolation only; no granite measurements",
    }

    def check():
        report["resources"].append(sample(output, Limits()))

    try:
        ensure_priority(Limits())
        check()  # Fail closed before creating any store/workload if swap is unavailable.
        for n in report["counts"]:
            report["results"].append(measure(output / str(n), n, args.repeats, check))
            (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        report["complete"] = True
        status = 0
    except ResourceStop as exc:
        report["blocked"] = str(exc)
        status = 2
    except (OSError, ValueError, RuntimeError, AssertionError) as exc:
        report["failed"] = str(exc)
        status = 1
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return status


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Small, synthetic hash-encoder baseline. No model, production, token estimate or L1 verdict."""
import argparse
import json
from pathlib import Path

from memory_os_v4 import Config, MemoryV4, server
from memory_os_v4.qualification import QualificationMetrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    root = Path(__file__).resolve().parents[1] / ".local-qualification"
    if not output.is_relative_to(root):
        parser.error("output must be inside this clone's .local-qualification")
    output.mkdir(parents=True, exist_ok=False)
    metrics = QualificationMetrics(server.TOOL_HANDLERS, enabled=True)
    server._qualification = metrics
    with MemoryV4(config=Config(data_dir=str(output / "store"), embed_model="hash")) as engine:
        server._engine = engine
        for i in range(20):
            engine.add_episode(f"Synthetic task {i}: decision and evidence " + "synthetic context " * 30,
                               project="synthetic", event_time=100 + i)
        for _ in range(5):
            server.dispatch("memory_session_brief", {"project": "synthetic", "max_tokens": 1000})
            server.dispatch("memory_search", {"project": "synthetic", "query": "task decision", "top_k": 3})
        server.dispatch("memory_compact", {"request_id": "synthetic-refused"})
        snapshot = metrics.snapshot()
        assert snapshot["tools"]["memory_session_brief"]["calls"] == 5
        assert snapshot["tools"]["memory_compact"]["maintenance_denied"] == 1
        assert snapshot["observation_failures"] == 0
        (output / "metrics.json").write_text(json.dumps({
            "encoder": "hash", "synthetic_items": 20, "production_performance_claim": False,
            "l1_budget_qualified": False, "metrics": snapshot}, indent=2) + "\n")
    server._engine = None


if __name__ == "__main__":
    main()

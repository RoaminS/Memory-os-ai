"""One sequential system worker, supervised by the fail-closed resource guard."""
from __future__ import annotations

import argparse
import asyncio
import gc
import json
from pathlib import Path
import resource
import tempfile
import time

from .baselines import BASELINES
from .client import ToolClient, offline_env, project_for
from .data import cases
from .leaderboard import AddSearchAdapter
from .network import deny_ip_network
from .pipeline import extract, pack, respond
from .stats import score
from .planning import extra_queries
from .usage import install, read


def footprint(root: Path) -> int:
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())


async def evaluate(inputs, system, cfg, root):
    start = time.perf_counter()
    t = time.perf_counter()
    project = project_for(cfg["run_id"], inputs.id)
    quotes = extract(project, inputs.turns, cfg.get("chunk_chars", 1024))
    extraction_ms = (time.perf_counter() - t) * 1000
    quote_by_id = {q.id: q for q in quotes}
    if sum(len(q.text.encode()) for q in quotes) > cfg["ingest_bytes"]:
        raise ValueError("Shared ingestion budget exceeded")
    phase = {"extraction_ms": extraction_ms}
    calls = 0
    retrieval_k = max(cfg["top_k"], cfg.get("trace_k", 0))
    t = time.perf_counter()
    queries = extra_queries(inputs.question, cfg.get("query_mode", "none")) if system == "memory" else []
    phase["planning_ms"] = (time.perf_counter() - t) * 1000
    trace = []
    usage_phases = {}
    warm_times = []
    if system == "memory":
        t = time.perf_counter()
        async with ToolClient(root, cfg["model"], cfg["transport"],
                              cfg.get("engine_overrides"), cfg.get("reranker")) as client:
            phase["init_ms"] = (time.perf_counter() - t) * 1000
            usage_phases["after_init"] = read(root / "usage.json")
            adapter = AddSearchAdapter(client, cfg["run_id"])
            t = time.perf_counter()
            capture = await adapter._capture(inputs.id, inputs.turns, quotes)
            calls += capture["tool_calls"]
            phase["ingest_ms"] = (time.perf_counter() - t) * 1000
            usage_phases["after_ingest"] = read(root / "usage.json")
            t = time.perf_counter()
            result = await adapter.search(inputs.id, inputs.question, retrieval_k,
                                          queries=queries or None, rerank=bool(cfg.get("reranker")))
            calls += result["tool_calls"]
            phase["retrieval_ms"] = (time.perf_counter() - t) * 1000
            usage_phases["after_retrieval"] = read(root / "usage.json")
            # A returned id not produced by the client is an isolation/provenance
            # defect, not a silently ignored retrieval result.
            ranked = [quote_by_id[h["id"]] for h in result["results"]]
            trace = [{**h, "turn_id": quote_by_id[h["id"]].turn_id} for h in result["results"]]
            if cfg.get("reranker") and trace and not any("rerank" in h["signals"] for h in trace):
                raise RuntimeError("Configured reranker did not execute; silent fallback forbidden in evaluation")
            for _ in range(cfg.get("warm_searches", 0)):
                t = time.perf_counter()
                await adapter.search(inputs.id, inputs.question, retrieval_k,
                                     queries=queries or None, rerank=bool(cfg.get("reranker")))
                warm_times.append((time.perf_counter() - t) * 1000)
                calls += 1
            usage_phases["after_warm_searches"] = read(root / "usage.json")
            t = time.perf_counter()
        phase["shutdown_ms"] = (time.perf_counter() - t) * 1000
    else:
        t = time.perf_counter()
        if system == "dense":
            install(root / "usage.json", cfg["model"])
        retriever = BASELINES[system](quotes, root, cfg["model"])
        phase["init_ms"] = 0.0
        phase["ingest_ms"] = (time.perf_counter() - t) * 1000
        t = time.perf_counter()
        ranked = retriever.search(inputs.question, retrieval_k)
        phase["retrieval_ms"] = (time.perf_counter() - t) * 1000
        for _ in range(cfg.get("warm_searches", 0)):
            t = time.perf_counter()
            retriever.search(inputs.question, retrieval_k)
            warm_times.append((time.perf_counter() - t) * 1000)
        if system == "full-context" and sum(len(q.text.encode()) + 1 for q in quotes) > cfg["context_bytes"]:
            return {"status": "not_applicable", "reason": "Whole history exceeds shared context byte cap",
                    "end_to_end_ms": (time.perf_counter() - start) * 1000, **phase}
    t = time.perf_counter()
    context = pack(ranked, cfg["top_k"], cfg["context_bytes"], full=system == "full-context",
                   unique_turns=cfg.get("unique_turns", False))
    response = respond(inputs.question, context)
    phase["response_ms"] = (time.perf_counter() - t) * 1000
    context_bytes = sum(len(q.text.encode()) + 1 for q in context)
    return {"status": "ok", "retrieved_turn_ids": list(dict.fromkeys(q.turn_id for q in context)),
            "response": response, "tool_calls": calls, "llm_calls": 0, "api_usd": 0.0,
            "model_usage": read(root / "usage.json"),
            "model_usage_phase_snapshots": usage_phases,
            "warm_search_ms": warm_times,
            "queries": queries, "query_expansion_bytes": sum(len(q.encode()) for q in queries),
            "rank_trace": trace if cfg.get("trace_k", 0) else [],
            "ranked_quote_ids": [q.id for q in ranked] if cfg.get("trace_k", 0) else [],
            "selected_quote_ids": [q.id for q in context],
            "capture_bytes": sum(len(t.content.encode()) for t in inputs.turns),
            "extracted_bytes": sum(len(q.text.encode()) for q in quotes),
            "query_bytes": len(inputs.question.encode()), "context_bytes": context_bytes,
            "tokens": None, "token_estimate_chars_div_4": {
                "capture": sum(len(t.content) for t in inputs.turns) / 4,
                "extracted": sum(len(q.text) for q in quotes) / 4,
                "query": len(inputs.question) / 4, "context": sum(len(q.text) for q in context) / 4},
            "persistent_bytes": footprint(root),
            "rss_self_peak_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "rss_children_peak_bytes": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
            "end_to_end_ms": (time.perf_counter() - start) * 1000, **phase}


async def run(cfg: dict, system: str, out: Path, work: Path):
    allowed = set(cfg["selected_ids"]) if cfg.get("selection_filter") else None
    i = 0
    with out.open("x") as f:
        for inputs, gold in cases(Path(cfg["data"])):
            if allowed is not None and gold.id not in allowed:
                continue
            if i >= cfg["limit"]:
                break
            start = time.perf_counter()
            try:
                with tempfile.TemporaryDirectory(prefix=f"{system}-{i}-", dir=work) as d:
                    async with asyncio.timeout(cfg["case_seconds"]):
                        row = await evaluate(inputs, system, cfg, Path(d))
            except Exception as exc:
                # Every selected failed question remains in the denominator.
                row = {"status": "failed", "reason": type(exc).__name__ + ": " + str(exc),
                       "retrieved_turn_ids": [], "response": None,
                       "end_to_end_ms": (time.perf_counter() - start) * 1000}
            if row["status"] == "not_applicable":
                row.update(recall=None, mrr=None, ndcg=None, answer_em=None,
                           answer_f1=None, answer_contains=None, abstained=None)
            else:
                row.update(score(row["retrieved_turn_ids"], row["response"], gold, cfg["top_k"]))
            row.update(qid=gold.id, system=system, category=gold.category,
                       future_turns_excluded=gold.excluded_future_turns)
            # The full-context task is recall under a byte cap, not Recall@k;
            # score all observed turns only when the entire context fits.
            if system == "full-context" and row["status"] == "ok":
                coverage = score(row["retrieved_turn_ids"], row["response"], gold,
                                 max(cfg["top_k"], len(row["retrieved_turn_ids"])))
                row["full_context_evidence_coverage"] = coverage["recall"]
                row.update(recall=None, mrr=None, ndcg=None)
                row["metric_scope"] = "full-context coverage (not Recall@k)"
            f.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
            f.flush()
            print(f"{system} {i + 1}/{cfg['limit']} {row['status']}", flush=True)
            i += 1
            gc.collect()


def main():
    import os
    deny_ip_network()
    clean = offline_env()
    os.environ.clear()
    os.environ.update(clean)
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True, type=Path)
    p.add_argument("--system", required=True, choices=["memory", *BASELINES])
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--work", required=True, type=Path)
    args = p.parse_args()
    cfg = json.loads(args.config.read_text())
    asyncio.run(run(cfg, args.system, args.output, args.work))


if __name__ == "__main__":
    main()

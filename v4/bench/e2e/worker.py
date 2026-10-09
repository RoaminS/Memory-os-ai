"""One sequential system worker, supervised by the fail-closed resource guard."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
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


@asynccontextmanager
async def borrowed(client):
    yield client


def usage_delta(after, before):
    result = dict(after)
    for key in ('encoder_calls', 'encoder_texts', 'encoder_input_tokens', 'encoder_ms',
                'reranker_calls', 'reranker_pairs', 'reranker_input_tokens', 'reranker_ms',
                'llm_calls', 'api_usd'):
        result[key] = after.get(key, 0) - before.get(key, 0)
    return result


async def evaluate(inputs, system, cfg, root, shared_client=None):
    start = time.perf_counter()
    usage_root = shared_client.root if shared_client is not None else root
    usage_start = read(usage_root / "usage.json") if shared_client is not None else {}
    t = time.perf_counter()
    project = project_for(cfg["run_id"], inputs.id)
    quotes = extract(project, inputs.turns, cfg.get("chunk_chars", 1024))
    extraction_ms = (time.perf_counter() - t) * 1000
    quote_by_id = {q.id: q for q in quotes}
    if sum(len(q.text.encode()) for q in quotes) > cfg["ingest_bytes"]:
        raise ValueError("Shared ingestion budget exceeded")
    phase = {"extraction_ms": extraction_ms}
    calls = 0
    # Multi-query retrieval oversamples in proportion to requested top_k.
    # Diagnostics MUST NOT widen its primary ranking. Only E5 intentionally
    # widens the pool to build a context with distinct source turns.
    retrieval_k = max(cfg["top_k"], cfg.get("trace_k", 0)) if cfg.get("unique_turns") else cfg["top_k"]
    t = time.perf_counter()
    queries = extra_queries(inputs.question, cfg.get("query_mode", "none")) if system == "memory" else []
    phase["planning_ms"] = (time.perf_counter() - t) * 1000
    trace = []
    candidate_trace = []
    usage_phases = {}
    warm_times = []
    if system == "memory":
        t = time.perf_counter()
        context = borrowed(shared_client) if shared_client is not None else ToolClient(
            root, cfg["model"], cfg["transport"], cfg.get("engine_overrides"), cfg.get("reranker"))
        async with context as client:
            if shared_client is not None:
                opened = await client.call('eval_open_conversation', {'conversation_id': inputs.id})
                if not opened.get('encoder_reused') or not opened.get('store_empty'):
                    raise RuntimeError('Resident conversation isolation/model reuse not proven')
                phase.update(conversation_close_ms=opened['conversation_close_ms'],
                             conversation_init_ms=opened['conversation_init_ms'])
                calls += 1
            phase["init_ms"] = (time.perf_counter() - t) * 1000
            usage_phases["after_init"] = read(usage_root / "usage.json")
            adapter = AddSearchAdapter(client, cfg["run_id"])
            t = time.perf_counter()
            capture = await adapter._capture(inputs.id, inputs.turns, quotes)
            calls += capture["tool_calls"]
            phase["ingest_ms"] = (time.perf_counter() - t) * 1000
            usage_phases["after_ingest"] = read(usage_root / "usage.json")
            t = time.perf_counter()
            result = await adapter.search(inputs.id, inputs.question, retrieval_k,
                                          queries=queries or None, rerank=bool(cfg.get("reranker")))
            calls += result["tool_calls"]
            phase["retrieval_ms"] = (time.perf_counter() - t) * 1000
            usage_phases["after_retrieval"] = read(usage_root / "usage.json")
            # A returned id not produced by the client is an isolation/provenance
            # defect, not a silently ignored retrieval result.
            ranked = [quote_by_id[h["id"]] for h in result["results"]]
            trace = [{**h, "turn_id": quote_by_id[h["id"]].turn_id} for h in result["results"]]
            if cfg.get("reranker") and trace and not any("rerank" in h["signals"] for h in trace):
                raise RuntimeError("Configured reranker did not execute; silent fallback forbidden in evaluation")
            if cfg.get("trace_k", 0) > retrieval_k:
                t = time.perf_counter()
                diagnostic = await adapter.search(inputs.id, inputs.question, cfg["trace_k"],
                                                  queries=queries or None, rerank=bool(cfg.get("reranker")))
                phase["diagnostic_trace_ms"] = (time.perf_counter() - t) * 1000
                calls += 1
                candidate_trace = [{**h, "turn_id": quote_by_id[h["id"]].turn_id}
                                   for h in diagnostic["results"]]
            for _ in range(cfg.get("warm_searches", 0)):
                t = time.perf_counter()
                await adapter.search(inputs.id, inputs.question, retrieval_k,
                                     queries=queries or None, rerank=bool(cfg.get("reranker")))
                warm_times.append((time.perf_counter() - t) * 1000)
                calls += 1
            usage_phases["after_warm_searches"] = read(usage_root / "usage.json")
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
        if cfg.get("trace_k", 0) > retrieval_k:
            t = time.perf_counter()
            diagnostic = retriever.search(inputs.question, cfg["trace_k"])
            phase["diagnostic_trace_ms"] = (time.perf_counter() - t) * 1000
            candidate_trace = [{"id": q.id, "turn_id": q.turn_id} for q in diagnostic]
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
            "model_usage": usage_delta(read(usage_root / "usage.json"), usage_start),
            "model_usage_phase_snapshots": {k: usage_delta(v, usage_start) for k, v in usage_phases.items()},
            "warm_search_ms": warm_times,
            "queries": queries, "query_expansion_bytes": sum(len(q.encode()) for q in queries),
            "rank_trace": trace if cfg.get("trace_k", 0) else [],
            "candidate_trace": candidate_trace,
            "candidate_trace_request_k": cfg.get("trace_k", 0),
            "primary_request_k": retrieval_k,
            "ranked_quote_ids": [q.id for q in ranked] if cfg.get("trace_k", 0) else [],
            "selected_quote_ids": [q.id for q in context],
            "capture_bytes": sum(len(t.content.encode()) for t in inputs.turns),
            "extracted_bytes": sum(len(q.text.encode()) for q in quotes),
            "query_bytes": len(inputs.question.encode()), "context_bytes": context_bytes,
            "tokens": None, "token_estimate_chars_div_4": {
                "capture": sum(len(t.content) for t in inputs.turns) / 4,
                "extracted": sum(len(q.text) for q in quotes) / 4,
                "query": len(inputs.question) / 4, "context": sum(len(q.text) for q in context) / 4},
            "persistent_bytes": footprint(usage_root),
            "rss_self_peak_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "rss_children_peak_bytes": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
            "end_to_end_ms": (time.perf_counter() - start) * 1000, **phase}


async def run_cases(cfg: dict, system: str, out: Path, work: Path, shared_client=None):
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
                        row = await evaluate(inputs, system, cfg, Path(d), shared_client) if shared_client is not None else await evaluate(inputs, system, cfg, Path(d))
            except Exception as exc:
                # Every selected failed question remains in the denominator.
                row = {"status": "failed", "reason": type(exc).__name__ + ": " + str(exc),
                       "failure_class": "budget" if isinstance(exc, TimeoutError) else "correctness",
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
            if cfg.get("resident") and row["status"] == "failed":
                if row.get("failure_class") == "budget":
                    raise TimeoutError(row["reason"])
                raise RuntimeError(row["reason"])


async def run(cfg: dict, system: str, out: Path, work: Path):
    if not cfg.get("resident") or system != "memory":
        return await run_cases(cfg, system, out, work)
    with tempfile.TemporaryDirectory(prefix="resident-arm-", dir=work) as d:
        root = Path(d)
        lifecycle = {}
        t = time.perf_counter()
        try:
            async with ToolClient(root, cfg["model"], cfg["transport"],
                                  cfg.get("engine_overrides"), cfg.get("reranker"), True) as client:
                lifecycle.update(startup_ms=(time.perf_counter() - t) * 1000,
                                 startup_model_usage=read(root / "usage.json"),
                                 residency="one model/MCP process per arm; fresh conversation stores")
                try:
                    await run_cases(cfg, system, out, work, client)
                finally:
                    lifecycle["total_model_usage"] = read(root / "usage.json")
                    t = time.perf_counter()
        finally:
            lifecycle["shutdown_ms"] = (time.perf_counter() - t) * 1000
            out.with_name(system + "-lifecycle.json").write_text(json.dumps(lifecycle, allow_nan=False))


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
    try:
        asyncio.run(run(cfg, args.system, args.output, args.work))
    except TimeoutError as exc:
        print(f"BUDGET: {exc}", flush=True)
        raise SystemExit(3)


if __name__ == "__main__":
    main()

"""Scorer-only metrics and paired, observable-source-clustered bootstrap."""
from __future__ import annotations

from collections import defaultdict
import math
import random
import re
from statistics import mean

from .data import Gold


def normalize(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", "", text.casefold()).split())


def score(ids: list[str], response: str | None, gold: Gold, k: int) -> dict:
    unique = list(dict.fromkeys(ids))[:k]
    evidence = set(gold.evidence)
    found = len(evidence & set(unique))
    relevant = [i for i, tid in enumerate(unique, 1) if tid in evidence]
    ideal = sum(1 / math.log2(i + 1) for i in range(1, min(k, len(evidence)) + 1))
    return {"recall": found / len(evidence) if evidence else None,
            "mrr": 1 / relevant[0] if relevant else (0.0 if evidence else None),
            "ndcg": sum(1 / math.log2(i + 1) for i in relevant) / ideal if ideal else None,
            "answer_em": float(response is not None and any(
                normalize(response) == normalize(a) for a in gold.answers)) if gold.answers else None,
            "abstained": response is None}


def cluster_ids(golds: list[Gold]) -> dict[str, str]:
    """Connected components of shared haystack session ids (not gold sessions).

    Overlapping questions are NOT independent. No unknown user identity is
    invented; if all observations connect, CI is unavailable rather than narrow.
    """
    parent = {g.id: g.id for g in golds}

    def root(qid):
        while parent[qid] != qid:
            parent[qid] = parent[parent[qid]]
            qid = parent[qid]
        return qid

    owners = {}
    for g in golds:
        for sid in g.sessions:
            if sid in owners:
                parent[root(g.id)] = root(owners[sid])
            else:
                owners[sid] = g.id
    return {g.id: root(g.id) for g in golds}


def estimate(pairs: list[tuple[str, float]], seed: int, repetitions: int = 2000, check=None) -> dict:
    if not pairs:
        return {"n": 0, "clusters": 0, "mean": None, "ci95": None}
    groups = defaultdict(list)
    for cluster, value in pairs:
        groups[cluster].append(value)
    point = mean(v for _, v in pairs)
    if repetitions == 0:
        return {"n": len(pairs), "clusters": len(groups), "mean": point, "ci95": None,
                "reason": "Campaign stopped; bootstrap not executed"}
    if len(groups) < 2:
        return {"n": len(pairs), "clusters": len(groups), "mean": point, "ci95": None,
                "reason": "Fewer than two independent observable source clusters"}
    if repetitions < 100:
        raise ValueError("At least 100 bootstrap replicates required")
    summaries = [(sum(values), len(values)) for values in groups.values()]
    rng = random.Random(seed)
    samples = []
    for i in range(repetitions):
        if check and i % 50 == 0:
            check()
        chosen = [rng.choice(summaries) for _ in summaries]
        samples.append(sum(v for v, _ in chosen) / sum(n for _, n in chosen))
    samples.sort()
    return {"n": len(pairs), "clusters": len(groups), "mean": point,
            "ci95": [samples[int(.025 * repetitions)], samples[min(repetitions - 1, int(.975 * repetitions))]]}


def percentile(values: list[float], p: float) -> float | None:
    return sorted(values)[max(0, math.ceil(len(values) * p) - 1)] if values else None


def summarize(rows: list[dict], golds: list[Gold], systems: list[str], seed: int,
              bootstrap_replicates: int = 2000, check=None) -> dict:
    clusters = cluster_ids(golds)
    by_id = {g.id: g for g in golds}
    indexed = {(r["system"], r["qid"]): r for r in rows}
    output = {"systems": {}, "paired_delta_memory": {}, "paired_answer_em_delta_memory": {},
              "bootstrap": {"level": "shared-haystack-session connected components",
                            "seed": seed, "replicates": bootstrap_replicates, "confidence": .95,
                            "known_user_identity": False}}
    for system in systems:
        entries = [r for r in rows if r["system"] == system]
        metrics = {}
        for metric in ("recall", "mrr", "ndcg", "answer_em"):
            pairs = [(clusters[r["qid"]], r[metric]) for r in entries if r.get(metric) is not None]
            metrics[metric] = estimate(pairs, seed, bootstrap_replicates, check)
        categories = {}
        for category in sorted({g.category for g in golds}):
            selected = [r for r in entries if by_id[r["qid"]].category == category]
            categories[category] = {"n": len(selected), "recall": estimate(
                [(clusters[r["qid"]], r["recall"]) for r in selected if r.get("recall") is not None],
                seed, bootstrap_replicates, check)}
        times = [r["end_to_end_ms"] for r in entries if r.get("end_to_end_ms") is not None]
        output["systems"][system] = {
            "n": len(entries), "failures": sum(r["status"] == "failed" for r in entries),
            "not_applicable": sum(r["status"] == "not_applicable" for r in entries),
            "metrics": metrics, "by_category": categories,
            "macro_category_recall": mean(v["recall"]["mean"] for v in categories.values()
                                          if v["recall"]["mean"] is not None)
            if any(v["recall"]["mean"] is not None for v in categories.values()) else None,
            "end_to_end_ms": {"p50": percentile(times, .50), "p95": percentile(times, .95)},
            "api_usd": 0.0, "llm_calls": 0,
            "tool_calls": sum(r.get("tool_calls", 0) for r in entries),
            "context_bytes": sum(r.get("context_bytes", 0) for r in entries),
            "encoder_calls": sum(r.get("model_usage", {}).get("encoder_calls", 0) for r in entries),
            "encoder_texts": sum(r.get("model_usage", {}).get("encoder_texts", 0) for r in entries),
            "encoder_input_tokens": sum(r.get("model_usage", {}).get("encoder_input_tokens", 0) for r in entries),
            "partial_failed_usage_unknown": any(r["status"] == "failed" for r in entries),
        }
        if system == "full-context":
            output["systems"][system]["full_context_evidence_coverage"] = estimate(
                [(clusters[r["qid"]], r["full_context_evidence_coverage"]) for r in entries
                 if r.get("full_context_evidence_coverage") is not None], seed, bootstrap_replicates, check)
        if system == "memory":
            continue
        delta = []
        for g in golds:
            a, b = indexed.get(("memory", g.id)), indexed.get((system, g.id))
            if a and b and a.get("recall") is not None and b.get("recall") is not None:
                delta.append((clusters[g.id], a["recall"] - b["recall"]))
        output["paired_delta_memory"][system] = estimate(delta, seed, bootstrap_replicates, check)
        em_delta = []
        for g in golds:
            a, b = indexed.get(("memory", g.id)), indexed.get((system, g.id))
            if a and b and a.get("answer_em") is not None and b.get("answer_em") is not None:
                em_delta.append((clusters[g.id], a["answer_em"] - b["answer_em"]))
        output["paired_answer_em_delta_memory"][system] = estimate(em_delta, seed, bootstrap_replicates, check)
    return output

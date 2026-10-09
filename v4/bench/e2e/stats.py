"""Scorer-only metrics and paired, observable-source-clustered bootstrap."""
from __future__ import annotations

from collections import Counter, defaultdict
from functools import partial
import math
import random
from statistics import mean

from .data import Gold
from .answers import normalize as normalize, response_metrics


def score(ids: list[str], response: str | None, gold: Gold, k: int) -> dict:
    unique = list(dict.fromkeys(ids))[:k]
    aliases = dict(gold.turn_aliases)
    canonical = [aliases.get(tid, tid) for tid in unique]
    evidence = set(gold.evidence)
    found = len(evidence & set(canonical))
    seen_relevant = set()
    relevant = [i for i, tid in enumerate(canonical, 1)
                if tid in evidence and not (tid in seen_relevant or seen_relevant.add(tid))]
    ideal = sum(1 / math.log2(i + 1) for i in range(1, min(k, len(evidence)) + 1))
    return {"recall": found / len(evidence) if evidence else None,
            "mrr": 1 / relevant[0] if relevant else (0.0 if evidence else None),
            "ndcg": sum(1 / math.log2(i + 1) for i in relevant) / ideal if ideal else None,
            **response_metrics(response, gold.answers),
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


EVIDENCE_POLICY = 'evidence-sessions-abs-v3'
EVIDENCE_MIN_CLUSTERS = 30


def evidence_cluster_ids(golds: list[Gold], link_abs: bool = True) -> dict[str, str]:
    """Scorer-only source-proof components; derived abs tasks stay with source."""
    parent = {g.id: g.id for g in golds}
    if len(parent) != len(golds):
        raise ValueError('Duplicate question identities in evidence clusters')

    def root(qid):
        while parent[qid] != qid:
            parent[qid] = parent[parent[qid]]
            qid = parent[qid]
        return qid

    owners = {}
    for g in golds:
        for sid in g.answer_sessions:
            if sid in owners:
                parent[root(g.id)] = root(owners[sid])
            else:
                owners[sid] = g.id
        if link_abs and g.id.endswith('_abs') and g.id[:-4] in parent:
            parent[root(g.id)] = root(g.id[:-4])
    return {g.id: root(g.id) for g in golds}


def bootstrap_preflight(golds: list[Gold], policy: str, minimum_clusters: int) -> dict:
    if policy != EVIDENCE_POLICY or minimum_clusters != EVIDENCE_MIN_CLUSTERS:
        raise ValueError('Fixed S v3 bootstrap policy and minimum 30 required')
    clusters = evidence_cluster_ids(golds)
    usable = [g for g in golds if g.evidence]
    if any(not g.answer_sessions for g in usable):
        raise ValueError('S bootstrap unavailable: evidence-session labels missing for contributing task; no model worker allowed')
    count = len({clusters[g.id] for g in usable})
    if count < minimum_clusters:
        raise ValueError(f'S bootstrap unavailable: {count} contributing clusters < {minimum_clusters}; no model worker allowed')
    return {'policy': policy, 'minimum_clusters': minimum_clusters,
            'all_haystack_clusters': len(set(cluster_ids(golds).values())),
            'evidence_only_clusters': len(set(evidence_cluster_ids(golds, link_abs=False).values())),
            'evidence_abs_clusters': len(set(clusters.values())),
            'primary_contributing_clusters': count, 'primary_questions': len(usable),
            'component_sizes': sorted(Counter(clusters.values()).values(), reverse=True),
            'orphan_abs_questions': sorted(g.id for g in golds if g.id.endswith('_abs') and g.id[:-4] not in clusters),
            'question_clusters': clusters, 'conditional_on_fixed_distractor_corpus': True}


def estimate(pairs: list[tuple[str, float]], seed: int, repetitions: int = 2000, check=None,
             minimum_clusters: int = 2) -> dict:
    if not pairs:
        result = {"n": 0, "clusters": 0, "mean": None, "ci95": None}
        if minimum_clusters != 2:
            result['reason'] = 'No contributing evidence/derived-question pairs'
        return result
    groups = defaultdict(list)
    for cluster, value in pairs:
        groups[cluster].append(value)
    point = mean(v for _, v in pairs)
    if repetitions == 0:
        return {"n": len(pairs), "clusters": len(groups), "mean": point, "ci95": None,
                "reason": "Campaign stopped; bootstrap not executed"}
    if len(groups) < minimum_clusters:
        return {"n": len(pairs), "clusters": len(groups), "mean": point, "ci95": None,
                "reason": "Fewer than two independent observable source clusters" if minimum_clusters == 2
                else f"Fewer than {minimum_clusters} contributing evidence/derived-question clusters"}
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
              bootstrap_replicates: int = 2000, check=None,
              cluster_policy: str = 'haystack', minimum_clusters: int = 2) -> dict:
    if cluster_policy == 'haystack' and minimum_clusters == 2:
        clusters = cluster_ids(golds)
    elif cluster_policy == EVIDENCE_POLICY and minimum_clusters == EVIDENCE_MIN_CLUSTERS:
        clusters = evidence_cluster_ids(golds)
    else:
        raise ValueError('Unsupported bootstrap policy/minimum')
    calculate = partial(estimate, minimum_clusters=minimum_clusters)
    by_id = {g.id: g for g in golds}
    indexed = {(r["system"], r["qid"]): r for r in rows}
    output = {"systems": {}, "paired_delta_memory": {}, "paired_answer_em_delta_memory": {},
              "paired_other_delta_memory": {},
              "bootstrap": {"level": "shared-haystack-session connected components",
                            "seed": seed, "replicates": bootstrap_replicates, "confidence": .95,
                            "known_user_identity": False}}
    if cluster_policy == EVIDENCE_POLICY:
        output['bootstrap'].update(level='shared-evidence-session and source-abs connected components',
                                   policy=EVIDENCE_POLICY, minimum_clusters=minimum_clusters,
                                   conditional_on_fixed_distractor_corpus=True)
    for system in systems:
        entries = [r for r in rows if r["system"] == system]
        metrics = {}
        for metric in ("recall", "mrr", "ndcg", "answer_em", "answer_f1", "answer_contains"):
            pairs = [(clusters[r["qid"]], r[metric]) for r in entries if r.get(metric) is not None]
            metrics[metric] = calculate(pairs, seed, bootstrap_replicates, check)
        categories = {}
        for category in sorted({g.category for g in golds}):
            selected = [r for r in entries if by_id[r["qid"]].category == category]
            categories[category] = {"n": len(selected), "recall": calculate(
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
            "warm_search_ms": {"p50": percentile([v for r in entries for v in r.get("warm_search_ms", [])], .50),
                               "p95": percentile([v for r in entries for v in r.get("warm_search_ms", [])], .95)},
            "api_usd": 0.0, "llm_calls": 0,
            "tool_calls": sum(r.get("tool_calls", 0) for r in entries),
            "context_bytes": sum(r.get("context_bytes", 0) for r in entries),
            "encoder_calls": sum(r.get("model_usage", {}).get("encoder_calls", 0) for r in entries),
            "encoder_texts": sum(r.get("model_usage", {}).get("encoder_texts", 0) for r in entries),
            "encoder_input_tokens": sum(r.get("model_usage", {}).get("encoder_input_tokens", 0) for r in entries),
            "reranker_calls": sum(r.get("model_usage", {}).get("reranker_calls", 0) for r in entries),
            "reranker_pairs": sum(r.get("model_usage", {}).get("reranker_pairs", 0) for r in entries),
            "reranker_input_tokens": sum(r.get("model_usage", {}).get("reranker_input_tokens", 0) for r in entries),
            "partial_failed_usage_unknown": any(r["status"] == "failed" for r in entries),
        }
        if system == "full-context":
            output["systems"][system]["full_context_evidence_coverage"] = calculate(
                [(clusters[r["qid"]], r["full_context_evidence_coverage"]) for r in entries
                 if r.get("full_context_evidence_coverage") is not None], seed, bootstrap_replicates, check)
        if system == "memory":
            continue
        delta = []
        for g in golds:
            a, b = indexed.get(("memory", g.id)), indexed.get((system, g.id))
            if a and b and a.get("recall") is not None and b.get("recall") is not None:
                delta.append((clusters[g.id], a["recall"] - b["recall"]))
        output["paired_delta_memory"][system] = calculate(delta, seed, bootstrap_replicates, check)
        em_delta = []
        for g in golds:
            a, b = indexed.get(("memory", g.id)), indexed.get((system, g.id))
            if a and b and a.get("answer_em") is not None and b.get("answer_em") is not None:
                em_delta.append((clusters[g.id], a["answer_em"] - b["answer_em"]))
        output["paired_answer_em_delta_memory"][system] = calculate(em_delta, seed, bootstrap_replicates, check)
        output["paired_other_delta_memory"][system] = {}
        for metric in ("mrr", "answer_f1", "answer_contains"):
            pairs = []
            for g in golds:
                a, b = indexed.get(("memory", g.id)), indexed.get((system, g.id))
                if a and b and a.get(metric) is not None and b.get(metric) is not None:
                    pairs.append((clusters[g.id], a[metric] - b[metric]))
            output["paired_other_delta_memory"][system][metric] = calculate(
                pairs, seed, bootstrap_replicates, check)
    return output

"""Frozen non-vacuity gate for copy qualifications, independent of engine code."""
from __future__ import annotations

# Frozen before the new copy run: these are diagnostic probes, not relevance
# labels. One real hit per query is enough to make ordered equality meaningful;
# requiring ten would reject a legitimately small store. No query may be dropped.
SEARCH_CRITERION = {"minimum_hits_per_query": 1, "top_k": 10, "engine_project": None}


def search_hit_counts(search, expected_queries, *, worker, reported=None):
    """Recount IDs; refuse empty/missing probes and falsified reported counts."""
    expected = tuple(expected_queries)
    if (not expected or len(set(expected)) != len(expected)
            or not isinstance(search, dict) or set(search) != set(expected)):
        raise ValueError("Non-vacuity: frozen probe set missing/changed: " + worker)
    counts = {}
    for query in expected:
        row = search[query]
        ids = row.get("ids") if isinstance(row, dict) else row
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 10
                or any(not isinstance(i, str) or not i.strip() for i in ids)
                or len(set(ids)) != len(ids)):
            raise ValueError("Non-vacuity: missing/invalid hits: " + worker + ": " + query)
        counts[query] = len(ids)
    if reported is not None and (
            not isinstance(reported, dict) or set(reported) != set(counts)
            or any(type(reported[q]) is not int or reported[q] != counts[q] for q in counts)):
        raise ValueError("Non-vacuity: reported hit counts differ: " + worker)
    return counts

"""Non-vacuity of diagnostic equality; no model/download/production store."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from search_qualification import SEARCH_CRITERION, search_hit_counts
from sqlite_activation_worker import compare_probes, probe


def test_frozen_threshold_and_whole_probe_set():
    assert SEARCH_CRITERION == {"minimum_hits_per_query": 1, "top_k": 10, "engine_project": None}
    assert search_hit_counts({"q": ["a"]}, ["q"], worker="baseline") == {"q": 1}


@pytest.mark.parametrize("search", [{}, {"q": []}, {"q": {"ids": []}},
                                    {"q": [""]}, {"q": [None]}, {"q": ["a", "a"]},
                                    {"q": list(map(str, range(11)))},
                                    {"q": ["a"], "extra": ["a"]}, {"other": ["a"]}])
def test_empty_partial_or_invalid_comparisons_refused(search):
    with pytest.raises(ValueError, match="Non-vacuity"):
        search_hit_counts(search, ["q"], worker="baseline")


@pytest.mark.parametrize("reported", [{}, {"q": 0}, {"q": True}, {"q": 1.0}, {"q": 10}])
def test_reported_counts_are_recomputed_not_trusted(reported):
    with pytest.raises(ValueError, match="reported hit counts"):
        search_hit_counts({"q": {"ids": ["a"]}}, ["q"], worker="warm", reported=reported)


@pytest.mark.parametrize("baseline", [{}, {"q": {"ids": []}}])
def test_activation_empty_equals_empty_never_qualifies(baseline):
    with pytest.raises(ValueError, match="Non-vacuity"):
        compare_probes(baseline, {"cold": baseline, "warm": baseline})


def test_activation_requires_a_candidate():
    with pytest.raises(ValueError, match="no candidate"):
        compare_probes({"q": {"ids": ["a"]}}, {})


def test_activation_probes_all_projects_at_unchanged_frozen_time(tmp_path):
    from memory_os_v4 import Config, MemoryV4
    with MemoryV4(config=Config(data_dir=str(tmp_path), embed_model="hash", hash_dim=16)) as engine:
        engine.add_episode("Synthetic London preference", project="p", item_id="p-witness")
        engine.add_episode("Synthetic Paris preference", project="q", item_id="q-witness")
        when = 2_000_000_000
        assert engine.search("Synthetic", project="*", at_time=when) == []
        expected = engine.search("Synthetic", project=None, top_k=10, at_time=when, rerank=False)
        rows = probe(engine, ["Synthetic"], when, lambda: None)
        assert rows["Synthetic"]["ids"] == [h.item.id for h in expected]
        assert set(rows["Synthetic"]["ids"]) == {"p-witness", "q-witness"}
        assert rows["Synthetic"]["hit_count"] == 2

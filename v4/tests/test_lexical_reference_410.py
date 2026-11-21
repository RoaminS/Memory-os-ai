"""Frozen reference of policy ``lex-4.1.0-v1`` on the base engine -- GREEN NOW, must stay green.

Contract docs/LEXICAL_PROFILE_4_1_0.md §3 and §12. The reference is the 4.0.9 ranking of
the semantic store with ``use_dense=False`` (hash encoder: this file needs FAISS, i.e. the
``[semantic]`` closure; it is NOT part of the ML-free subset). It proves that the literal
data of ``lexical_frozen_83ddead.py`` is what the unmodified engine returns, in this
process and in fresh processes under several PYTHONHASHSEED values, and pins the
behaviours the policy inherits: C4 penalty, global half-life floor, global entity
statistics and top-N. The lexical arm of the same checks is in test_lexical_profile.py.
"""

import pytest

import lexical_fixture_410 as fx
from lexical_frozen_83ddead import FROZEN
from memory_os_v4 import Config, MemoryV4


def reference_config(path, **overrides):
    """Today's ranking without the dense channel: semantic hash store, use_dense=False."""
    return Config(data_dir=str(path), store_backend="sqlite_s1", embed_model="hash", hash_dim=16, use_dense=False,
                  **overrides)


def test_base_engine_reproduces_the_frozen_outputs(tmp_path, monkeypatch):
    fx.freeze_clock(monkeypatch)
    with MemoryV4(config=reference_config(tmp_path / "main")) as engine:
        fx.seed_main(engine)
        fx.same(fx.collect_main(engine, fx.context_without_vectors), FROZEN["main"])
    with MemoryV4(config=reference_config(tmp_path / "main")) as engine:  # reopened
        fx.same(fx.collect_main(engine, fx.context_without_vectors), FROZEN["main"])
    entity_ranks = [row[3] for scope in FROZEN["main"]["ranking"].values() for result in scope.values()
                    for row in result]
    assert any(rank is not None for rank in entity_ranks)  # the corpus really exercises the entity channel
    tied = [row[0] for row in FROZEN["main"]["ranking"]["alpha"]["Where does Pixel the cat sleep?"]]
    assert tied[:2] == ["tie-a", "tie-b"]  # inserted b then a: identical BM25, order by id


def test_semantic_context_uses_item_vectors_for_mmr_and_is_not_the_lexical_context(tmp_path, monkeypatch):
    """Distinct semantic assertion: get_context diversifies with the stored vectors (memory.py:1046).

    The lexical profile is compared to MMR over an EMPTY vector map instead (FROZEN main.context_empty_mmr).
    """
    fx.freeze_clock(monkeypatch)
    with MemoryV4(config=reference_config(tmp_path)) as engine:
        fx.seed_main(engine)
        semantic = {label: engine.get_context(fx.CONTEXT_QUERY, project=scope) for label, scope in fx.SCOPES.items()}
        assert engine.vector_index.get_one("tie-a") is not None
    assert semantic == FROZEN["context_semantic"]
    empty = FROZEN["main"]["context_empty_mmr"]
    assert semantic["alpha"] != empty["alpha"] and semantic["*"] != empty["*"]
    assert sorted(semantic["alpha"].splitlines()) == sorted(empty["alpha"].splitlines())  # same items, other order


@pytest.mark.parametrize("seed", [0, 1, 4242])
def test_base_ranking_is_identical_across_processes_and_hash_seeds(tmp_path, seed):
    got = fx.run_cross_process(tmp_path, "reference", seed)
    fx.same(got["main"], FROZEN["main"])
    fx.same(got["reopened"], FROZEN["main"])
    fx.same(got["c4"], FROZEN["c4"])
    fx.same(got["floor"], FROZEN["floor"])


def test_c4_anchors_are_sorted_after_the_invalidated_penalty(tmp_path, monkeypatch):
    fx.freeze_clock(monkeypatch)
    with MemoryV4(config=reference_config(tmp_path)) as engine:
        fx.same(fx.check_c4_penalty(engine), FROZEN["c4"])
    assert FROZEN["c4_state"] == {"c-old": "c-new", "c-new": None}


def test_adaptive_half_life_floor_uses_the_global_time_span(tmp_path, monkeypatch):
    fx.freeze_clock(monkeypatch)
    with MemoryV4(config=reference_config(tmp_path, recency_only_with_at_time=True)) as engine:
        fx.same(fx.check_half_life_floor(engine), FROZEN["floor"])


def test_entity_threshold_depends_on_other_projects(tmp_path):
    with MemoryV4(config=reference_config(tmp_path)) as engine:
        fx.check_entity_threshold_is_global(engine)


def test_entity_top_100_is_saturated_by_other_projects_before_filtering(tmp_path):
    with MemoryV4(config=reference_config(tmp_path)) as engine:
        fx.check_entity_top_n_is_saturated_across_projects(engine)

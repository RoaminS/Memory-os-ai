"""Lexical profile 4.1.0 (contract docs/LEXICAL_PROFILE_4_1_0.md) -- written first.

RED UNTIL STEPS 4-7: every test here needs ``Config.retrieval_profile`` /
``Config.lexical`` and the ML-free engine path, which do not exist at base
83ddead. No skip/xfail on purpose: they turn green with the implementation.

ML-FREE SUBSET (contract §11): this file never builds a semantic engine and never
needs torch, sentence-transformers, transformers or FAISS. The reference of
policy ``lex-4.1.0-v1`` is the literal data of ``lexical_frozen_83ddead.py``
(outputs of the unmodified base engine); the base engine itself is exercised in
test_lexical_reference_410.py, which needs the semantic closure.
tmp_path stores only.
"""

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

import lexical_fixture_410 as fx
from lexical_fixture_410 import ALPHA, BETA, MARKER, POLICY, QUERIES
from lexical_frozen_83ddead import FROZEN
import memory_os_v4
from memory_os_v4 import Config, MemoryV4
from memory_os_v4.idempotency import RequestConflictError


def lexical_config(path, **overrides):
    return Config(**{"data_dir": str(path), "retrieval_profile": "lexical", "store_backend": "sqlite_s1",
                     "use_dense": False, **overrides})


def ranking(engine, query, **kwargs):
    return [(hit.item.id, hit.score) for hit in engine.search(query, top_k=10, **kwargs)]


def seeded(config):
    engine = MemoryV4(config=config)
    engine.add_episodes_bulk([dict(spec) for spec in ALPHA + BETA])
    return engine


# --------------------------------------------------------------- no ML at all
NO_ML_SCRIPT = r"""
import importlib.abc, json, sys

BLOCKED = ("torch", "sentence_transformers", "faiss", "transformers")
attempts = []


class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BLOCKED:
            attempts.append(name)
            raise ImportError("ML import forbidden in the lexical profile: " + name)
        return None


sys.meta_path.insert(0, Block())


def loaded():
    return sorted(name for name in sys.modules if name.split(".")[0] in BLOCKED)


stages = {}
import memory_os_v4
from memory_os_v4 import Config, MemoryV4

stages["import"] = loaded()
config = Config.lexical(data_dir=sys.argv[1])
with MemoryV4(config=config) as engine:
    stages["open"] = loaded()
    engine.add_episode("Alice Martin adopted a grey cat named Pixel.", item_id="e1", project="alpha")
    engine.add_episodes_bulk([{"text": "The budget review moved to Thursday.", "item_id": "e2", "project": "alpha"}])
    facts = engine.add_facts([{"text": "Bruno Keller maintains the SQLite migration scripts."}], project="alpha")
    engine.add_document("notes", "Pixel the cat sleeps on the radiator every winter evening.", project="alpha")
    stages["write"] = loaded()
    first = [hit.item.id for hit in engine.search("cat Pixel", top_k=5, project="alpha")]
    multi = [hit.item.id for hit in engine.search("cat", top_k=5, project="alpha", queries=["budget Thursday"],
                                                  hypotheses=["Bruno Keller maintains the scripts."])]
    context = engine.get_context("budget Thursday", project="alpha")
    brief = engine.session_brief(project="alpha")
    status = engine.status()
    engine.forget("e2", project="alpha")
    engine.flush()
    stages["search"] = loaded()
    marker = engine.store.meta.get("retrieval_profile")
with MemoryV4(config=Config.lexical(data_dir=sys.argv[1])) as engine:
    again = [hit.item.id for hit in engine.search("cat Pixel", top_k=5, project="alpha")]
    gone = [hit.item.id for hit in engine.search("budget Thursday", top_k=5, project="alpha")]
    stages["reopen"] = loaded()
print(json.dumps({"stages": stages, "attempts": attempts, "first": first, "again": again, "multi": multi,
                  "gone": gone, "context": context, "brief_keys": sorted(brief), "marker": marker,
                  "facts": len(facts["added"]), "vector_count": status["vector_count"],
                  "version": memory_os_v4.__version__}))
"""


def test_import_write_search_reopen_never_import_ml_modules(tmp_path):
    """G1a: no torch/sentence_transformers/faiss/transformers, even attempted, in a fresh interpreter."""
    script = tmp_path / "no_ml.py"
    script.write_text(NO_ML_SCRIPT, encoding="utf-8")
    source_root = str(Path(memory_os_v4.__file__).resolve().parents[1])
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
           "PYTHONPATH": os.pathsep.join([source_root, os.environ.get("PYTHONPATH", "")]).rstrip(os.pathsep)}
    for name in [key for key in env if key.startswith("MEMORY_V4_")]:
        del env[name]
    done = subprocess.run([sys.executable, "-B", str(script), str(tmp_path / "store")], env=env, cwd=tmp_path,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]
    report = json.loads(done.stdout.strip().splitlines()[-1])
    assert report["stages"] == {"import": [], "open": [], "write": [], "search": [], "reopen": []}
    assert report["attempts"] == []
    assert report["first"] and "e1" in report["first"] and report["again"] == report["first"]
    assert "e2" in report["multi"] and "e2" not in report["gone"]
    assert "budget review" in report["context"] and "text" in report["brief_keys"]
    assert report["marker"] == MARKER and report["facts"] == 1 and report["vector_count"] == 0


# ------------------------------------------------------- selection, validation
def test_default_profile_is_semantic_and_lexical_selection_is_explicit(tmp_path, monkeypatch):
    assert Config().retrieval_profile == "semantic"
    assert Config(use_dense=False).retrieval_profile == "semantic"  # use_dense=False is NOT the lexical profile
    built = Config.lexical(data_dir=str(tmp_path))
    assert (built.retrieval_profile, built.store_backend, built.use_dense) == ("lexical", "sqlite_s1", False)
    assert built == lexical_config(tmp_path)
    for name in [key for key in os.environ if key.startswith("MEMORY_V4_")]:
        monkeypatch.delenv(name)
    monkeypatch.setenv("MEMORY_V4_RETRIEVAL_PROFILE", "lexical")
    monkeypatch.setenv("MEMORY_V4_STORE_BACKEND", "sqlite_s1")
    monkeypatch.setenv("MEMORY_V4_DIR", str(tmp_path))
    from_env = Config.from_env()
    assert (from_env.retrieval_profile, from_env.use_dense, from_env.lexical_guard_k) == ("lexical", False, 3)
    assert from_env == lexical_config(tmp_path)
    monkeypatch.delenv("MEMORY_V4_RETRIEVAL_PROFILE")
    assert Config.from_env().retrieval_profile == "semantic" and Config.from_env().use_dense is True


@pytest.mark.parametrize("overrides", [
    {"use_dense": True},
    {"store_backend": "jsonl"},
    {"embed_model": "hash"},
    {"embed_model": "sentence-transformers/all-MiniLM-L6-v2"},
    {"embed_dtype": "float32"},
    {"device": "cpu"},
    {"batch_facts": True},
    {"rerank_model": "tomaarsen/ms-marco-ettin-17m-reranker"},
    {"multi_vector": True},
    {"full_candidate_scoring": True},
    {"dense_norm": "fixed"},
    {"neighbor_expansion": True},
    {"entity_expansion": True},
    {"temporal_query_mode": "auto"},
    {"facet_channel": True},
    {"fusion": "rrf"},
    {"use_entities": False},
    {"use_stemming": False},
    {"use_bm25": False, "lexical_guard_k": 0},
    {"lexical_first": True, "lexical_guard_k": 0},
    {"lexical_guard_k": 0},
    {"w_bm25": 2.0},
    {"w_entity": 1.0},
    {"candidates": 50},
    {"multi_query_rrf_k": 60},
], ids=lambda overrides: ",".join(f"{key}={value}" for key, value in overrides.items()))
def test_options_incompatible_with_the_frozen_policy_are_refused_before_any_write(tmp_path, overrides):
    target = tmp_path / "store"
    config = lexical_config(target, **overrides)
    with pytest.raises(ValueError) as refusal:
        MemoryV4(config=config)
    assert getattr(refusal.value, "code", None) == "profile_option_conflict"
    assert not target.exists()


def test_unknown_profile_and_contradictory_environment_are_refused(tmp_path, monkeypatch):
    target = tmp_path / "store"
    with pytest.raises(ValueError) as refusal:
        MemoryV4(config=Config(data_dir=str(target), retrieval_profile="hybrid", store_backend="sqlite_s1"))
    assert getattr(refusal.value, "code", None) == "profile_unknown"
    for name in [key for key in os.environ if key.startswith("MEMORY_V4_")]:
        monkeypatch.delenv(name)
    monkeypatch.setenv("MEMORY_V4_RETRIEVAL_PROFILE", "lexical")
    monkeypatch.setenv("MEMORY_V4_STORE_BACKEND", "sqlite_s1")
    monkeypatch.setenv("MEMORY_V4_DIR", str(target))
    for name, value in (("MEMORY_V4_USE_DENSE", "1"), ("MEMORY_V4_EMBED_MODEL", "hash"),
                        ("MEMORY_V4_RERANK_MODEL", "some/reranker")):
        monkeypatch.setenv(name, value)
        with pytest.raises(ValueError) as refusal:
            MemoryV4(config=Config.from_env())
        assert getattr(refusal.value, "code", None) == "profile_option_conflict"
        monkeypatch.delenv(name)
    assert not target.exists()


# ------------------------------------------------------------ write and reopen
def test_fresh_lexical_store_carries_the_marker_and_no_semantic_state(tmp_path):
    with seeded(lexical_config(tmp_path)) as engine:
        assert engine.store.meta["retrieval_profile"] == MARKER
        assert not {"embed_model", "dim", "embed_compute_profile"} & set(engine.store.meta)
        assert engine.embedder is None
        connection = engine.store._connection
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 8  # no new migration
        assert connection.execute("SELECT count(*) FROM embedding_cache").fetchone()[0] == 0
        stored = json.loads(connection.execute(
            "SELECT value_json FROM store_metadata WHERE key='metadata'").fetchone()[0])
        assert stored["retrieval_profile"] == MARKER
        status = engine.status()
        assert status["retrieval_profile"] == MARKER and status["retrieval_ready"] is True
        assert (status["vector_count"], status["embed_model"], status["dim"], status["encoding_profile"]) == (
            0, None, None, None)
        assert status["total"] == len(ALPHA + BETA) == status["bm25_docs"]
        hits = engine.search("Where does Pixel the cat sleep?", top_k=5, project="alpha")
        assert hits and hits[0].signals["dense"] == {"rank": None, "score": None}
        assert hits[0].signals["ranking_policy"] == POLICY and hits[0].signals["bm25"]["rank"] == 1


def test_reopen_preserves_identity_marker_items_and_ranking(tmp_path):
    with seeded(lexical_config(tmp_path)) as engine:
        engine.add_facts([{"text": "Alice Martin lives in Lyon."}], project="alpha")
        assert engine.forget("a2", project="alpha") is True
        instance = engine.store.meta["store_instance_id"]
        before = {query: ranking(engine, query, project="alpha") for query in QUERIES}
        total = engine.status()["total"]
    assert sorted(path.name for path in tmp_path.iterdir()) == ["store.sqlite3", "writer.lock"]
    with MemoryV4(config=lexical_config(tmp_path)) as engine:
        assert engine.store.meta["store_instance_id"] == instance
        assert engine.store.meta["retrieval_profile"] == MARKER
        assert engine.store.meta["version"] == memory_os_v4.__version__
        assert engine.status()["total"] == total
        assert {query: ranking(engine, query, project="alpha") for query in QUERIES} == before
        assert "a2" not in {item_id for item_id, _score in ranking(engine, "budget Thursday", project="alpha")}
        assert engine.store._connection.execute("SELECT count(*) FROM embedding_cache").fetchone()[0] == 0


# ----------------------------------------------------------------- determinism
def test_ranking_is_deterministic_and_ties_break_on_item_id(tmp_path):
    with seeded(lexical_config(tmp_path / "one")) as engine:
        first = {query: ranking(engine, query, project="alpha") for query in QUERIES}
        for _ in range(3):  # access counters/recency touched by earlier searches never reorder results
            assert {query: ranking(engine, query, project="alpha") for query in QUERIES} == first
        tied = [item_id for item_id, _score in first["Where does Pixel the cat sleep?"]]
        assert tied[:2] == ["tie-a", "tie-b"]  # inserted b then a: identical BM25, order by id
        scores = [score for _id, score in first["Where does Pixel the cat sleep?"]]
        assert scores == [1.0 / rank for rank in range(1, len(scores) + 1)]  # C4 guard: public score = 1/rank
    # A second, independent store receiving the same writes in another order ranks identically.
    engine = MemoryV4(config=lexical_config(tmp_path / "two"))
    with engine:
        engine.add_episodes_bulk([dict(spec) for spec in reversed(BETA + ALPHA)])
        assert {query: ranking(engine, query, project="alpha") for query in QUERIES} == first


def test_policy_reproduces_the_frozen_outputs_of_base_83ddead(tmp_path, monkeypatch):
    """lex-4.1.0-v1 == pinned 83ddead outputs of the use_dense=False path, with no vector work at all.

    Rankings, raw BM25/entity signals, fusion, recency, C4 anchors, multi-query RRF, at_time, and
    get_context compared to MMR over an EMPTY vector map (never to the semantic, vector-diversified context).
    """
    fx.forbid_vectors(monkeypatch)
    fx.freeze_clock(monkeypatch)
    with seeded(lexical_config(tmp_path)) as engine:
        fx.same(fx.collect_main(engine, fx.public_context), FROZEN["main"])
        for hit in engine.search("budget Thursday", top_k=10, project="alpha"):
            assert hit.signals["ranking_policy"] == POLICY
            assert hit.signals["dense"] == {"rank": None, "score": None}
    with MemoryV4(config=lexical_config(tmp_path)) as engine:  # reopened store, same pinned answers
        fx.same(fx.collect_main(engine, fx.public_context), FROZEN["main"])
    assert FROZEN["context_semantic"]["alpha"] != FROZEN["main"]["context_empty_mmr"]["alpha"]


@pytest.mark.parametrize("seed", [0, 1, 4242])
def test_ranking_is_identical_across_processes_and_hash_seeds(tmp_path, seed):
    """Fresh interpreter per PYTHONHASHSEED, fixed clock and configuration: the pinned outputs, exactly."""
    got = fx.run_cross_process(tmp_path, "lexical", seed)
    fx.same(got["main"], FROZEN["main"])
    fx.same(got["reopened"], FROZEN["main"])
    fx.same(got["c4"], FROZEN["c4"])
    fx.same(got["floor"], FROZEN["floor"])


def test_c4_anchors_are_sorted_after_the_invalidated_penalty(tmp_path, monkeypatch):
    fx.forbid_vectors(monkeypatch)
    fx.freeze_clock(monkeypatch)
    with MemoryV4(config=lexical_config(tmp_path)) as engine:
        fx.same(fx.check_c4_penalty(engine), FROZEN["c4"])


def test_adaptive_half_life_floor_uses_the_global_time_span(tmp_path, monkeypatch):
    """recency_only_with_at_time stays an operator setting: its floor reads the GLOBAL event-time span."""
    fx.forbid_vectors(monkeypatch)
    fx.freeze_clock(monkeypatch)
    with MemoryV4(config=lexical_config(tmp_path, recency_only_with_at_time=True)) as engine:
        fx.same(fx.check_half_life_floor(engine), FROZEN["floor"])


def test_entity_threshold_depends_on_other_projects(tmp_path, monkeypatch):
    """Documented v1 limit: entity statistics are global (no content crosses projects)."""
    fx.forbid_vectors(monkeypatch)
    with MemoryV4(config=lexical_config(tmp_path)) as engine:
        fx.check_entity_threshold_is_global(engine)


def test_entity_top_100_is_saturated_by_other_projects_before_filtering(tmp_path, monkeypatch):
    """Documented v1 limit: entity candidates compete for the global top-N before the project filter."""
    fx.forbid_vectors(monkeypatch)
    with MemoryV4(config=lexical_config(tmp_path)) as engine:
        fx.check_entity_top_n_is_saturated_across_projects(engine)


# ------------------------------------------------------------ project isolation
def lexical_view(hits):
    """What scoped BM25 alone guarantees: per-item BM25 rank/score and the ordered C4 anchors."""
    matched = {hit.item.id: hit.signals["bm25"] for hit in hits if hit.signals["bm25"]["rank"] is not None}
    anchors = [hit.item.id for hit in hits if hit.signals["lexical_guard_anchor"]]
    assert anchors == [hit.item.id for hit in hits[:len(anchors)]]  # anchors lead the result
    return matched, anchors


def test_projects_are_isolated_in_results_and_in_scoped_bm25_statistics(tmp_path):
    engine = MemoryV4(config=lexical_config(tmp_path))
    with engine:
        engine.add_episodes_bulk([dict(spec) for spec in ALPHA])
        alone = {query: lexical_view(engine.search(query, top_k=10, project="alpha")) for query in QUERIES}
        engine.add_episodes_bulk([dict(spec) for spec in BETA])
        alpha_ids, beta_ids = {spec["item_id"] for spec in ALPHA}, {spec["item_id"] for spec in BETA}
        for query in QUERIES:
            hits = engine.search(query, top_k=10, project="alpha")
            assert {hit.item.id for hit in hits} <= alpha_ids
            # Scoped statistics: another project's documents change neither BM25 ranks, raw BM25 scores
            # nor the C4 anchors. The FINAL order of the tail is NOT asserted: global entity statistics
            # may legitimately move it (see the two entity tests above).
            assert lexical_view(hits) == alone[query]
            assert {hit.item.id for hit in engine.search(query, top_k=10, project="beta")} <= beta_ids
        multi = engine.search("Pixel", top_k=10, project="beta", queries=["budget Thursday", "Bruno Keller"],
                              hypotheses=["Alice Martin adopted a grey cat."])
        assert multi and {hit.item.id for hit in multi} <= beta_ids
        context = engine.get_context("budget Thursday Pixel", project="alpha")
        assert "confidential" not in context and "codename" not in context and "budget review" in context
        assert engine.status(project="alpha")["total"] == len(ALPHA)
        assert engine.status(project="beta")["total"] == len(BETA)
        assert engine.forget("b1", project="alpha") is False  # a foreign project cannot forget it
        assert "b1" in {hit.item.id for hit in engine.search("budget Thursday", top_k=10, project="beta")}
        brief = engine.session_brief(project="alpha")["text"]
        assert "confidential" not in brief and "codename" not in brief


# ------------------------------------- mutations without any vector access (contract §7)
def engine_state(engine, project="alpha"):
    return {"rankings": {query: ranking(engine, query, project=project) for query in QUERIES},
            "items": sorted(engine.store.items), "bm25_docs": engine.status()["bm25_docs"],
            "entity_docs": sorted(engine.entities.doc_entities), "facet_docs": sorted(engine.facet_index.ids()),
            "receipts": engine.store._connection.execute("SELECT count(*) FROM request_receipts").fetchone()[0],
            "versions": engine.store._connection.execute("SELECT count(*) FROM item_versions").fetchone()[0]}


def assert_no_vector_sql(statements, points=()):
    assert not [statement for statement in statements if "embedding_cache" in statement]
    assert not [point for point in points if "embedding_cache" in point]


def test_sql_fault_rolls_back_sources_and_lexical_indexes_without_vector_access(tmp_path, monkeypatch):
    """A faulted SQL mutation restores sources and BM25/entity/facet indexes; nothing is re-embedded."""
    fx.forbid_vectors(monkeypatch)
    with seeded(lexical_config(tmp_path)) as engine:
        before = engine_state(engine)
        statements = fx.trace_sql(engine)
        points = []
        for fault in ("after_item_head", "before_metadata", "before_receipt", "before_commit"):
            def fail(point, fault=fault):
                points.append(point)
                if point == fault:
                    raise sqlite3.OperationalError("injected fault at " + point)

            engine.store.fault_hook = fail
            with pytest.raises(sqlite3.OperationalError):
                engine.add_episodes_bulk([{"text": "Rollback victim about the radiator budget.",
                                           "item_id": "victim", "project": "alpha"}])
            engine.store.fault_hook = None
            assert engine_state(engine) == before, fault
            assert engine.store._connection.in_transaction is False
        # A faulted correction leaves the replaced fact valid.
        engine.add_facts([{"text": "The office is in Lyon.", "item_id": "o1"}], project="alpha")
        with_fact = engine_state(engine)
        engine.store.fault_hook = fail  # still "before_commit"
        with pytest.raises(sqlite3.OperationalError):
            engine.add_facts([{"text": "The office is in Paris.", "item_id": "o2", "replaces": ["o1"]}],
                             project="alpha")
        engine.store.fault_hook = None
        assert engine_state(engine) == with_fact and engine.store.get("o1").invalidated_by is None
        # The engine is still usable: the same write now commits and is searchable.
        engine.add_episodes_bulk([{"text": "Rollback victim about the radiator budget.", "item_id": "victim",
                                   "project": "alpha"}])
        assert "victim" in {item_id for item_id, _score in ranking(engine, "victim radiator", project="alpha")}
        assert_no_vector_sql(statements, points)
        assert engine.embedder is None and engine.status()["vector_count"] == 0
    with MemoryV4(config=lexical_config(tmp_path)) as engine:
        assert "victim" in engine.store.items and "o2" not in engine.store.items
        assert not {"embed_model", "dim", "embed_compute_profile"} & set(engine.store.meta)


def test_corrections_receipts_and_block_cas_work_without_vector_access(tmp_path, monkeypatch):
    fx.forbid_vectors(monkeypatch)
    fx.freeze_clock(monkeypatch)
    from mcp.types import Implementation

    from memory_os_v4 import server
    from memory_os_v4.ops.relay import task_owner_arguments

    def cas(arguments):
        return server.dispatch("memory_block_compare_and_set", task_owner_arguments(
            arguments, Implementation(name="codex", version="synthetic-v1"), creation=False))

    with MemoryV4(config=lexical_config(tmp_path, work_state=True)) as engine:
        monkeypatch.setattr(server, "_engine", engine)
        statements = fx.trace_sql(engine)
        points = []
        engine.store.fault_hook = points.append
        # Corrections: explicit replacement closes the old validity window; C4 follows the pinned outputs.
        fx.same(fx.check_c4_penalty(engine), FROZEN["c4"])
        assert engine.store.get("c-new").replaces == ["c-old"]
        # Receipts: replay is idempotent, a changed payload is refused, nothing is written twice.
        calls = []

        def action():
            calls.append(1)
            return engine.add_facts([{"text": "Bruno Keller prefers green tea."}], project="corr")

        first = engine.execute_request("memory_add_facts", "corr", {"facts": 1}, "request-1", action)
        items = len(engine.store.items)
        assert engine.execute_request("memory_add_facts", "corr", {"facts": 1}, "request-1", action) == first
        with pytest.raises(RequestConflictError):
            engine.execute_request("memory_add_facts", "corr", {"facts": 2}, "request-1", action)
        assert len(calls) == 1 and len(engine.store.items) == items
        # Block compare-and-set: create, stale ETag refused, fresh ETag accepted.
        block = {"project": "corr", "scope": "project", "name": "project_state"}
        created = cas({**block, "text": "v1", "expected_etag": None, "reason": "init", "request_id": "block-1"})
        assert created["ok"] is True and created["revision"] == 1
        stale = cas({**block, "text": "v2", "expected_etag": "stale", "reason": "race", "request_id": "block-2"})
        assert stale["ok"] is False and stale["code"] == "revision_conflict"
        assert stale["current_etag"] == created["etag"]
        updated = cas({**block, "text": "v2", "expected_etag": created["etag"], "reason": "update",
                       "request_id": "block-3"})
        assert updated["ok"] is True and updated["revision"] == 2
        current = server.dispatch("memory_block_get", {"project": "corr", "name": "project_state"})
        assert current["blocks"]["project_state"] == "v2" and current["etags"]["project_state"] == updated["etag"]
        engine.store.fault_hook = None
        assert points and assert_no_vector_sql(statements, points) is None
        assert engine.embedder is None
    with MemoryV4(config=lexical_config(tmp_path, work_state=True)) as engine:
        assert engine.store.get("c-old").invalidated_by == "c-new"
        assert engine.store.meta["retrieval_profile"] == MARKER
        assert not {"embed_model", "dim", "embed_compute_profile"} & set(engine.store.meta)
        assert engine.store._connection.execute("SELECT count(*) FROM embedding_cache").fetchone()[0] == 0


def test_consolidation_returns_an_explicit_partial_result_without_vector_access(tmp_path, monkeypatch):
    """Deduplication, replacement and decay are deterministic and kept; only cluster proposals need vectors.

    Write-time rules already apply the same text-equality and replacement logic, so the two sweeps find
    nothing on a normally written store: they must still RUN (keys present, exact empty values).
    """
    fx.forbid_vectors(monkeypatch)
    with MemoryV4(config=lexical_config(tmp_path, maintenance_mode="apply")) as engine:
        first = engine.add_facts([{"text": "the office kettle is broken.", "item_id": "d1", "event_time": fx.T0}],
                                 project="p")
        again = engine.add_facts([{"text": "the office  kettle is broken!", "item_id": "d2", "event_time": fx.T0}],
                                 project="p")
        assert first["added"] == ["d1"] and again == {"added": [], "deduped": ["d1"], "superseded": []}
        engine.add_facts([{"text": "the office is in lyon.", "item_id": "o1", "event_time": fx.T0}], project="p")
        moved = engine.add_facts([{"text": "the office is in paris.", "item_id": "o2", "event_time": fx.T0 + fx.DAY,
                                   "replaces": ["o1"]}], project="p")
        assert moved["superseded"] == [("o1", "o2")]
        engine.add_episodes_bulk([{"text": f"Standup notes: the release train leaves on Friday, part {index}.",
                                   "item_id": f"ep{index}", "project": "p", "event_time": fx.T0}
                                  for index in range(5)])  # a 5-member vector cluster in the semantic profile
        statements = fx.trace_sql(engine)
        importance = engine.store.get("d1").importance
        preview = engine.consolidate(dry_run=True, project="p")
        assert sorted(preview) == ["decayed", "deduped", "deduped_pairs", "summary_proposals",
                                   "summary_proposals_status", "superseded", "superseded_pairs"]
        assert (preview["deduped"], preview["deduped_pairs"]) == (0, [])
        assert (preview["superseded"], preview["superseded_pairs"]) == (0, [])
        assert isinstance(preview["decayed"], int) and preview["decayed"] > 0
        assert preview["summary_proposals"] == []
        assert preview["summary_proposals_status"] == "unavailable_in_lexical_profile"
        assert engine.store.get("d1").importance == importance  # preview mutates nothing
        assert engine.status(project="p")["last_consolidated"] is None
        applied = engine.consolidate(dry_run=False, project="p")
        assert {key: applied[key] for key in preview if key != "decayed"} == {
            key: preview[key] for key in preview if key != "decayed"}
        assert applied["decayed"] == preview["decayed"]
        assert engine.store.get("d1").importance != importance  # decay really applied
        assert engine.status(project="p")["last_consolidated"] is not None
        found = {item_id for item_id, _score in ranking(engine, "office kettle broken", project="p")}
        assert "d1" in found
        assert_no_vector_sql(statements)
        assert engine.embedder is None


# ------------------------------------ bare install: semantic refused, no automatic fallback
NO_SEMANTIC_SCRIPT = r"""
import json, sys
from pathlib import Path

# A module mapped to None is "not installed" for both `import` and importlib.util.find_spec.
for name in ("torch", "sentence_transformers", "faiss", "transformers"):
    sys.modules[name] = None

from memory_os_v4 import Config, MemoryV4

root = Path(sys.argv[1])
report = {"default_profile": Config().retrieval_profile, "env_profile": Config.from_env().retrieval_profile,
          "cases": {}}
cases = {
    "default_granite_sqlite": {"store_backend": "sqlite_s1"},
    "default_granite_jsonl": {},
    "hash_sqlite": {"store_backend": "sqlite_s1", "embed_model": "hash", "hash_dim": 16},
    "dense_disabled": {"store_backend": "sqlite_s1", "embed_model": "hash", "use_dense": False},
    "work_state_deferred": {"store_backend": "sqlite_s1", "work_state": True},
}
for name, options in cases.items():
    target = root / name
    try:
        MemoryV4(config=Config(data_dir=str(target), **options))
        outcome = {"error": None}
    except Exception as error:
        outcome = {"error": type(error).__name__, "is_value_error": isinstance(error, ValueError),
                   "code": getattr(error, "code", None), "message": str(error)}
    outcome["created"] = target.exists()
    report["cases"][name] = outcome
with MemoryV4(config=Config.lexical(data_dir=str(root / "lexical"))) as engine:
    engine.add_episode("Alice Martin adopted a grey cat named Pixel.", item_id="e1", project="alpha")
    report["lexical"] = [hit.item.id for hit in engine.search("cat Pixel", top_k=3, project="alpha")]
report["files"] = sorted(path.name for path in root.iterdir())
print(json.dumps(report))
"""


def test_semantic_profile_without_ml_is_refused_before_any_file_and_never_falls_back(tmp_path):
    """Bare install: the default stays semantic; it fails with an explicit [semantic] error, creating nothing."""
    script = tmp_path / "no_semantic.py"
    script.write_text(NO_SEMANTIC_SCRIPT, encoding="utf-8")
    root = tmp_path / "stores"
    root.mkdir()
    package_root = str(Path(memory_os_v4.__file__).resolve().parents[1])
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
           "PYTHONPATH": os.pathsep.join([package_root, os.environ.get("PYTHONPATH", "")]).rstrip(os.pathsep)}
    for name in [key for key in env if key.startswith("MEMORY_V4_")]:
        del env[name]
    done = subprocess.run([sys.executable, "-B", str(script), str(root)], env=env, cwd=tmp_path,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]
    report = json.loads(done.stdout.strip().splitlines()[-1])
    assert report["default_profile"] == "semantic" and report["env_profile"] == "semantic"  # no silent fallback
    assert sorted(report["cases"]) == ["default_granite_jsonl", "default_granite_sqlite", "dense_disabled",
                                       "hash_sqlite", "work_state_deferred"]
    for name, outcome in report["cases"].items():
        assert outcome["error"] is not None and outcome["is_value_error"], name
        assert outcome["code"] == "semantic_dependency_missing", name
        assert "memory-os-ai[semantic]" in outcome["message"], name
        assert outcome["created"] is False, name
    assert report["lexical"] == ["e1"]  # the lexical profile, selected explicitly, works in the same process
    assert report["files"] == ["lexical"]

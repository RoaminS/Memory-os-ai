"""Lexical profile 4.1.0 (contract docs/LEXICAL_PROFILE_4_1_0.md) -- written first.

RED UNTIL STEPS 4-7: every test here needs ``Config.retrieval_profile`` /
``Config.lexical`` and the ML-free engine path, which do not exist at base
83ddead. No skip/xfail on purpose: they turn green with the implementation.

tmp_path stores only. The hash embedder appears only on the semantic
comparison side (today's ``use_dense=False`` ranking is the frozen reference
of policy ``lex-4.1.0-v1``).
"""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import memory_os_v4
from memory_os_v4 import Config, MemoryV4

POLICY = "lex-4.1.0-v1"
MARKER = {"name": "lexical", "policy": POLICY}
T0 = 1.70e9

ALPHA = [
    {"text": "Pixel the cat sleeps on the radiator.", "item_id": "tie-b", "project": "alpha", "event_time": T0},
    {"text": "Pixel the cat sleeps on the radiator.", "item_id": "tie-a", "project": "alpha", "event_time": T0},
    {"text": "Alice Martin adopted a grey cat named Pixel.", "item_id": "a1", "project": "alpha", "event_time": T0},
    {"text": "The budget review moved to Thursday afternoon.", "item_id": "a2", "project": "alpha",
     "event_time": T0 + 86400},
    {"text": "Bruno Keller maintains the SQLite migration scripts.", "item_id": "a3", "project": "alpha",
     "event_time": T0 + 2 * 86400, "importance": 0.9},
    {"text": "Alice Martin asked Bruno Keller to review the budget.", "item_id": "a4", "project": "alpha",
     "event_time": T0 + 3 * 86400},
    {"text": "Le chat Pixel refuse les croquettes et préfère le thon.", "item_id": "a5", "project": "alpha",
     "event_time": T0 + 4 * 86400},
    {"text": "SQLite migration eight adds the change journal.", "item_id": "a6", "project": "alpha",
     "event_time": T0 + 5 * 86400, "importance": 0.2},
    # Unique entity (df/n <= 10 %): the only item reached through the entity channel as well.
    {"text": "Zoé Lambert joined the Grenoble team in March.", "item_id": "a7", "project": "alpha",
     "event_time": T0 + 6 * 86400},
]
BETA = [
    {"text": "The beta budget for Thursday is confidential.", "item_id": "b1", "project": "beta", "event_time": T0},
    {"text": "Pixel is also the codename of the beta budget cat.", "item_id": "b2", "project": "beta",
     "event_time": T0},
    {"text": "Bruno Keller never works on beta.", "item_id": "b3", "project": "beta", "event_time": T0},
]
QUERIES = [
    "Where does Pixel the cat sleep?",
    "Bruno Keller SQLite migration",
    "budget Thursday",
    "Alice Martin",
    "What did Zoé Lambert join?",
    "chat thon croquettes",
    "radiator budget migration cat",
]


def lexical_config(path, **overrides):
    return Config(**{"data_dir": str(path), "retrieval_profile": "lexical", "store_backend": "sqlite_s1",
                     "use_dense": False, **overrides})


def reference_config(path):
    """Today's ML-free-in-effect ranking: semantic hash store with the dense channel disabled."""
    return Config(data_dir=str(path), store_backend="sqlite_s1", embed_model="hash", hash_dim=16, use_dense=False)


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


def test_policy_equals_the_frozen_reference_without_the_dense_channel(tmp_path):
    """lex-4.1.0-v1 == 4.0.9 ranking with use_dense=False (bm25 scoped_literal + entities, cc, recency, C4)."""
    multi = {"queries": ["budget Thursday", "SQLite migration"], "hypotheses": ["Pixel sleeps on the radiator."]}
    with seeded(lexical_config(tmp_path / "lexical")) as lexical, seeded(reference_config(tmp_path / "ref")) as ref:
        entity_seen = False
        for scope in ("alpha", "beta", None):
            for query in QUERIES:
                got = lexical.search(query, top_k=10, project=scope)
                want = ref.search(query, top_k=10, project=scope)
                assert [(hit.item.id, hit.score) for hit in got] == [(hit.item.id, hit.score) for hit in want]
                for mine, theirs in zip(got, want):
                    assert mine.signals["bm25"] == theirs.signals["bm25"]
                    assert mine.signals["entity"] == theirs.signals["entity"]
                    assert mine.signals["fused"] == pytest.approx(theirs.signals["fused"])
                    assert mine.signals["lexical_guard_anchor"] == theirs.signals["lexical_guard_anchor"]
                    entity_seen = entity_seen or theirs.signals["entity"]["rank"] is not None
            assert ranking(lexical, "cat", project=scope, **multi) == ranking(ref, "cat", project=scope, **multi)
            assert lexical.get_context("budget Thursday", project=scope) == ref.get_context(
                "budget Thursday", project=scope)
        assert entity_seen  # the corpus really exercises the entity channel of the fusion
        assert ranking(lexical, "budget", at_time=T0 + 10 * 86400, kinds=["episode"]) == ranking(
            ref, "budget", at_time=T0 + 10 * 86400, kinds=["episode"])


# ------------------------------------------------------------ project isolation
def test_projects_are_isolated_in_results_and_in_corpus_statistics(tmp_path):
    engine = MemoryV4(config=lexical_config(tmp_path))
    with engine:
        engine.add_episodes_bulk([dict(spec) for spec in ALPHA])
        alone = {query: [(hit.item.id, hit.signals["bm25"]["score"]) for hit in
                         engine.search(query, top_k=10, project="alpha")] for query in QUERIES}
        engine.add_episodes_bulk([dict(spec) for spec in BETA])
        alpha_ids, beta_ids = {spec["item_id"] for spec in ALPHA}, {spec["item_id"] for spec in BETA}
        for query in QUERIES:
            hits = engine.search(query, top_k=10, project="alpha")
            assert {hit.item.id for hit in hits} <= alpha_ids
            # Scoped statistics: another project's documents change neither ranks nor raw BM25 scores.
            assert [(hit.item.id, hit.signals["bm25"]["score"]) for hit in hits] == alone[query]
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

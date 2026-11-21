"""Shared corpus, collectors and FROZEN outputs for policy ``lex-4.1.0-v1``.

Not a test module. ``FROZEN`` (see ``lexical_frozen_83ddead.py``) was produced by
running the UNMODIFIED base engine (commit 83ddead) on this corpus with the
semantic hash store and ``use_dense=False`` -- the normative reference of the
policy -- under a fixed clock, and is pinned as literal data. The lexical profile
must reproduce it; ``test_lexical_reference_410.py`` proves the base path still
does. Nothing here builds an engine or imports an ML module by itself.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

POLICY = "lex-4.1.0-v1"
MARKER = {"name": "lexical", "policy": POLICY}
T0 = 1.70e9
DAY = 86400.0
NOW = T0 + 400 * DAY  # fixed query clock for searches without at_time

ALPHA = [
    {"text": "Pixel the cat sleeps on the radiator.", "item_id": "tie-b", "project": "alpha", "event_time": T0},
    {"text": "Pixel the cat sleeps on the radiator.", "item_id": "tie-a", "project": "alpha", "event_time": T0},
    {"text": "Alice Martin adopted a grey cat named Pixel.", "item_id": "a1", "project": "alpha", "event_time": T0},
    {"text": "The budget review moved to Thursday afternoon.", "item_id": "a2", "project": "alpha",
     "event_time": T0 + DAY},
    {"text": "Bruno Keller maintains the SQLite migration scripts.", "item_id": "a3", "project": "alpha",
     "event_time": T0 + 2 * DAY, "importance": 0.9},
    {"text": "Alice Martin asked Bruno Keller to review the budget.", "item_id": "a4", "project": "alpha",
     "event_time": T0 + 3 * DAY},
    {"text": "Le chat Pixel refuse les croquettes et préfère le thon.", "item_id": "a5", "project": "alpha",
     "event_time": T0 + 4 * DAY},
    {"text": "SQLite migration eight adds the change journal.", "item_id": "a6", "project": "alpha",
     "event_time": T0 + 5 * DAY, "importance": 0.2},
    # Unique entity (df/n <= 10 %): reached through the entity channel as well.
    {"text": "Zoé Lambert joined the Grenoble team in March.", "item_id": "a7", "project": "alpha",
     "event_time": T0 + 6 * DAY},
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
SCOPES = {"alpha": "alpha", "beta": "beta", "*": None}
MULTI = {"queries": ["budget Thursday", "SQLite migration"], "hypotheses": ["Pixel sleeps on the radiator."]}
CONTEXT_QUERY = "radiator budget migration cat"
AT_TIME = T0 + 10 * DAY

# C4 guard with an invalidated best lexical match (correction by explicit replacement).
C4_FACTS = [
    {"text": "The deploy window for the billing service is Tuesday night.", "item_id": "c-old", "event_time": T0},
    {"text": "The billing service owner is Bruno Keller.", "item_id": "c-owner", "event_time": T0},
    {"text": "Deploy window notes are kept in the runbook.", "item_id": "c-notes", "event_time": T0},
    {"text": "Tuesday night is also the backup window.", "item_id": "c-backup", "event_time": T0},
]
C4_CORRECTION = {"text": "The deploy window for the billing service is Wednesday night.", "item_id": "c-new",
                 "replaces": ["c-old"], "event_time": T0 + 5 * DAY}
C4_QUERY = "deploy window billing service Tuesday night"
C4_BEFORE = T0 + DAY  # c-old still valid, c-new not yet

# Adaptive half-life floor: 0.25 x GLOBAL event-time span, other projects included.
FLOOR_FAR = {"text": "Archive note from long ago.", "item_id": "far", "project": "other",
             "event_time": T0 - 2000 * DAY}
FLOOR_QUERY = "budget Thursday"


def freeze_clock(monkeypatch):
    """Searches without at_time read the clock in retrieval.py only: pin it."""
    from memory_os_v4 import retrieval

    monkeypatch.setattr(retrieval, "time", SimpleNamespace(time=lambda: NOW))


def rows(hits):
    """One comparable row per hit; the public score is checked against 1/rank separately."""
    return [[hit.item.id, hit.signals["bm25"]["rank"], hit.signals["bm25"]["score"],
             hit.signals["entity"]["rank"], hit.signals["entity"]["score"], hit.signals["fused"],
             hit.signals["recency"], bool(hit.signals["lexical_guard_anchor"])] for hit in hits]


def seed_main(engine):
    engine.add_episodes_bulk([dict(spec) for spec in ALPHA + BETA])


def seed_c4(engine):
    engine.add_facts([dict(spec) for spec in C4_FACTS], project="corr")
    engine.add_facts([dict(C4_CORRECTION)], project="corr")


def collect_main(engine, context):
    """Everything FROZEN["main"] pins, from an engine seeded with ``seed_main``.

    ``context(engine, query, project)``: ``context_without_vectors`` on the base engine,
    the public ``get_context`` in the lexical profile.
    """
    result = {"ranking": {}, "multi": {}, "context_empty_mmr": {}}
    for label, scope in SCOPES.items():
        result["ranking"][label] = {}
        for query in QUERIES:
            hits = engine.search(query, top_k=10, project=scope)
            assert [hit.score for hit in hits] == [1.0 / rank for rank in range(1, len(hits) + 1)]
            result["ranking"][label][query] = rows(hits)
        result["multi"][label] = [[hit.item.id, hit.score] for hit in
                                  engine.search("cat", top_k=10, project=scope, **MULTI)]
        result["context_empty_mmr"][label] = context(engine, CONTEXT_QUERY, scope)
    result["at_time"] = rows(engine.search("budget", top_k=10, at_time=AT_TIME, kinds=["episode"]))
    return result


def collect_c4(engine):
    return {"now": rows(engine.search(C4_QUERY, top_k=10, project="corr")),
            "before": rows(engine.search(C4_QUERY, top_k=10, project="corr", at_time=C4_BEFORE))}


def collect_floor(engine):
    """Engine opened with recency_only_with_at_time=True; alpha alone, then a far item elsewhere."""
    engine.add_episodes_bulk([dict(spec) for spec in ALPHA])
    alone = rows(engine.search(FLOOR_QUERY, top_k=10, project="alpha", at_time=AT_TIME))
    engine.add_episodes_bulk([dict(FLOOR_FAR)])
    return {"alone": alone, "with_far_other_project": rows(
        engine.search(FLOOR_QUERY, top_k=10, project="alpha", at_time=AT_TIME))}


def context_without_vectors(engine, query, project):
    """get_context of policy v1: MMR over an EMPTY vector map (diversity 0, relevance order)."""
    from memory_os_v4.retrieval import mmr

    hits = engine.retriever.search(query, top_k=engine.config.candidates, at_time=None, project=project)
    return engine._render_hits(mmr(hits, {}, lambda_=0.7, k=len(hits)), 1500)


def same(got, want):
    """Structural equality with float tolerance (ids, ranks, flags exact)."""
    if isinstance(want, dict):
        assert isinstance(got, dict) and sorted(got) == sorted(want)
        for key in want:
            same(got[key], want[key])
    elif isinstance(want, (list, tuple)):
        assert isinstance(got, (list, tuple)) and len(got) == len(want), (got, want)
        for mine, theirs in zip(got, want):
            same(mine, theirs)
    elif isinstance(want, float) and not isinstance(want, bool):
        assert got == pytest.approx(want, rel=1e-9, abs=1e-12), (got, want)
    else:
        assert got == want, (got, want)


def public_context(engine, query, project):
    return engine.get_context(query, project=project)


# ---------------------------------------------------------------- shared scenarios
def check_c4_penalty(engine):
    """C4 anchors are sorted AFTER the x0.5 penalty of invalidated items (retrieval.py:831)."""
    seed_c4(engine)
    assert engine.store.get("c-old").invalidated_by == "c-new"
    got = collect_c4(engine)
    now = {row[0]: row for row in got["now"]}
    # Best raw BM25 match, but invalidated: halved, it falls behind two valid matches.
    assert now["c-old"][1] == 1 and now["c-old"][2] > now["c-new"][2] > now["c-old"][2] * 0.5
    assert [row[0] for row in got["now"]][:3] == ["c-new", "c-backup", "c-old"]
    assert [row[0] for row in got["now"] if row[7]] == ["c-new", "c-backup", "c-old"]
    # at_time inside the old validity window: no penalty, raw BM25 order, c-new not yet valid.
    assert [row[0] for row in got["before"]][:3] == ["c-old", "c-backup", "c-owner"]
    assert "c-new" not in {row[0] for row in got["before"]}
    return got


def check_half_life_floor(engine):
    """With recency_only_with_at_time, the floor is 0.25 x the GLOBAL event-time span (retrieval.py:761)."""
    got = collect_floor(engine)
    alone = {row[0]: row for row in got["alone"]}
    far = {row[0]: row for row in got["with_far_other_project"]}
    assert set(alone) == set(far) == {"a2", "a4"}  # the foreign item itself never appears
    for item_id in alone:
        assert far[item_id][6] > alone[item_id][6]  # recency changed by another project's time span
        assert far[item_id][1:3] == alone[item_id][1:3]  # scoped BM25 rank and score unchanged
    return got


def _entity(engine, query, project, item_id):
    for hit in engine.search(query, top_k=10, project=project):
        if hit.item.id == item_id:
            return hit.signals["entity"], hit.signals["bm25"]
    raise AssertionError(item_id + " not returned")


def check_entity_threshold_is_global(engine):
    """Entity df/n and its 10 % cut-off use the whole store: another project can switch a weight off."""
    fillers = [{"text": f"plain filler words number {index} about nothing special", "item_id": f"t-fill-{index}",
                "project": "alpha", "event_time": T0, "entities": []} for index in range(9)]
    engine.add_episodes_bulk(fillers + [{"text": "Zoé Lambert joined the team.", "item_id": "t-zoe",
                                         "project": "alpha", "event_time": T0}])
    entity, bm25 = _entity(engine, "Zoé Lambert", "alpha", "t-zoe")
    assert entity["rank"] == 1 and entity["score"] > 0  # df/n = 1/10: not above 10 %, kept
    engine.add_episodes_bulk([{"text": "Zoé Lambert is on leave.", "item_id": "t-other", "project": "beta",
                               "event_time": T0}])
    after, bm25_after = _entity(engine, "Zoé Lambert", "alpha", "t-zoe")
    assert after == {"rank": None, "score": None}  # df/n = 2/11 > 10 %: dropped because of project beta
    assert bm25_after == bm25  # scoped BM25 untouched
    assert {hit.item.id for hit in engine.search("Zoé Lambert", top_k=10, project="alpha")} <= {
        "t-zoe", *(spec["item_id"] for spec in fillers)}


def check_entity_top_n_is_saturated_across_projects(engine):
    """Entity candidates compete for the global top-100 BEFORE the project filter (retrieval.py:695,701)."""
    fillers = [{"text": f"plain filler words number {index} about nothing special", "item_id": f"s-fill-{index:04d}",
                "project": "gamma", "event_time": T0, "entities": []} for index in range(1000)]
    foreign = [{"text": f"memo {index} mentions Zoé Lambert again", "item_id": f"m-{index:03d}", "project": "beta",
                "event_time": T0} for index in range(99)]
    mine = [{"text": "first note about Zoé Lambert", "item_id": "a-first", "project": "alpha", "event_time": T0},
            {"text": "last note about Zoé Lambert", "item_id": "z-last", "project": "alpha", "event_time": T0}]
    engine.add_episodes_bulk(fillers + foreign + mine)  # df/n = 101/1101 < 10 %: the entity is weighted
    first, _ = _entity(engine, "Zoé Lambert", "alpha", "a-first")
    last, last_bm25 = _entity(engine, "Zoé Lambert", "alpha", "z-last")
    assert first["rank"] == 1 and first["score"] > 0
    # Equal entity scores tie on id: a-first + 99 foreign items fill the top-100, z-last is cut off
    # although it is the second of only two entity matches in its own project.
    assert last == {"rank": None, "score": None}
    assert last_bm25["rank"] is not None  # still a scoped lexical match
    assert {hit.item.id for hit in engine.search("Zoé Lambert", top_k=10, project="alpha")} == {"a-first", "z-last"}


# ------------------------------------------------------------- cross-process replay
CROSS_PROCESS_SCRIPT = r"""
import json, sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, sys.argv[1])
import lexical_fixture_410 as fx
from memory_os_v4 import Config, MemoryV4, retrieval

retrieval.time = SimpleNamespace(time=lambda: fx.NOW)
profile, root = sys.argv[2], Path(sys.argv[3])


def config(name, **options):
    if profile == "lexical":
        return Config.lexical(data_dir=str(root / name), **options)
    return Config(data_dir=str(root / name), store_backend="sqlite_s1", embed_model="hash", hash_dim=16,
                  use_dense=False, **options)


context = fx.public_context if profile == "lexical" else fx.context_without_vectors
out = {}
with MemoryV4(config=config("main")) as engine:
    fx.seed_main(engine)
    out["main"] = fx.collect_main(engine, context)
with MemoryV4(config=config("main")) as engine:  # reopened store, same answers
    out["reopened"] = fx.collect_main(engine, context)
with MemoryV4(config=config("c4")) as engine:
    fx.seed_c4(engine)
    out["c4"] = fx.collect_c4(engine)
with MemoryV4(config=config("floor", recency_only_with_at_time=True)) as engine:
    out["floor"] = fx.collect_floor(engine)
print(json.dumps(out, ensure_ascii=False, sort_keys=True))
"""


def run_cross_process(tmp_path, profile, seed):
    """Fresh interpreter, given PYTHONHASHSEED, fixed clock and configuration. Uses the imported package."""
    import memory_os_v4

    script = tmp_path / "cross_process.py"
    script.write_text(CROSS_PROCESS_SCRIPT, encoding="utf-8")
    package_root = str(Path(memory_os_v4.__file__).resolve().parents[1])
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": str(seed),
           "PYTHONPATH": os.pathsep.join([package_root, os.environ.get("PYTHONPATH", "")]).rstrip(os.pathsep)}
    for name in [key for key in env if key.startswith("MEMORY_V4_")]:
        del env[name]
    done = subprocess.run([sys.executable, "-B", str(script), str(Path(__file__).resolve().parent), profile,
                           str(tmp_path / f"stores-{seed}")], env=env, cwd=tmp_path, capture_output=True,
                          text=True, timeout=180)
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


# ------------------------------------------------------------------ vector tripwire
def forbid_vectors(monkeypatch):
    """Any encoder, vector-index or corpus-vector-cache use raises. Imports no ML module."""
    from memory_os_v4 import embed, sqlite_embeddings, vector_index

    def forbidden(*_args, **_kwargs):
        raise AssertionError("vector access in the lexical profile")

    for owner, names in ((vector_index.VectorIndex, ("__init__", "add", "remove", "search", "get_one", "get",
                                                     "scores_for", "export", "ids")),
                         (embed.HashEmbedder, ("__init__", "encode")),
                         (embed.Embedder, ("__init__", "encode", "encoding_profile")),
                         (sqlite_embeddings.SQLiteEmbeddings, ("_read_vectors", "encode"))):
        for name in names:
            monkeypatch.setattr(owner, name, forbidden)


def trace_sql(engine):
    """Collect every SQL statement of the engine connection from now on."""
    statements = []
    engine.store._connection.set_trace_callback(statements.append)
    return statements

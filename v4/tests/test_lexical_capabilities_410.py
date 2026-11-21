"""Capabilities, readiness and qualified-runtime states per profile (contract §9) -- written first.

RED UNTIL STEPS 4-6, no skip/xfail. ML-FREE SUBSET: nothing here builds a semantic engine or
needs torch/sentence-transformers/transformers/FAISS; installed versions are injected.

Three states that are never confused:
- absent_by_profile : lexical profile, encoder absent by choice             -> normal
- missing           : semantic catalog, package not installed               -> failure, install [semantic]
- drift             : package installed at another version than the lock    -> failure, qualified runtime
"""

from importlib import metadata

import pytest

import lexical_fixture_410 as fx
from lexical_fixture_410 import MARKER, POLICY
from memory_os_v4 import Config, MemoryV4, capabilities
from memory_os_v4.ops import qualified_runtime as runtime
from memory_os_v4.readiness import engine_readiness

ML_PACKAGES = ("torch", "sentence-transformers", "transformers", "faiss-cpu")
EXTRAS = ("ops", "brief")


def lexical_config(path, **overrides):
    return Config(**{"data_dir": str(path), "retrieval_profile": "lexical", "store_backend": "sqlite_s1",
                     "use_dense": False, **overrides})


def installed(versions, *, missing=(), drifted=()):
    """version_getter over a synthetic environment: no real package is inspected."""
    def get(name):
        if name in missing or name not in versions:
            raise metadata.PackageNotFoundError(name)
        return versions[name] + (".1" if name in drifted else "")
    return get


# ------------------------------------------------------------------ catalogs
def test_each_profile_has_its_own_catalog_and_semantic_stays_the_default():
    semantic = runtime.catalog(profile="semantic")
    assert semantic == runtime.catalog() and semantic["profile_id"] == "granite-macos-arm64-py311-v1"
    assert semantic["encoding_runtime_keys"] == ["torch", "sentence-transformers", "transformers", "numpy"]
    lexical = runtime.catalog(profile="lexical")
    assert lexical["profile_id"] == "lexical-macos-arm64-py311-v1" and lexical["encoding_runtime_keys"] == []
    assert lexical["schema_version"] == 1
    wanted = runtime.expected_versions(profile="lexical", extras=EXTRAS)
    reference = runtime.expected_versions(profile="semantic", extras=EXTRAS)
    assert reference == runtime.expected_versions(extras=EXTRAS)  # existing callers unchanged
    assert not set(ML_PACKAGES) & set(wanted)
    assert wanted and set(wanted) < set(reference)  # strict subset of the qualified semantic closure
    assert all(reference[name] == version for name, version in wanted.items())  # same exact locks
    assert {"numpy", "mcp", "tiktoken"} <= set(wanted)
    with pytest.raises(ValueError):
        runtime.catalog(profile="hybrid")


# ------------------------------------------- missing dependency versus drift
def test_missing_dependency_and_drift_are_distinct_states_of_the_semantic_runtime():
    versions = runtime.expected_versions(profile="semantic", extras=EXTRAS)
    clean = runtime.inspect_current(profile="semantic", extras=EXTRAS, version_getter=installed(versions))
    assert clean["ok"] is True and clean["mismatches"] == {} and clean["state"] == "qualified"
    bare = runtime.inspect_current(profile="semantic", extras=EXTRAS,
                                   version_getter=installed(versions, missing=ML_PACKAGES))
    assert bare["ok"] is False and bare["state"] == "missing_dependency"
    assert set(bare["mismatches"]) == set(ML_PACKAGES)
    for name in ML_PACKAGES:
        assert bare["mismatches"][name] == {"expected": versions[name], "actual": None, "state": "missing"}
    drifted = runtime.inspect_current(profile="semantic", extras=EXTRAS,
                                      version_getter=installed(versions, drifted=("torch",)))
    assert drifted["ok"] is False and drifted["state"] == "drift"
    assert drifted["mismatches"] == {"torch": {"expected": versions["torch"], "actual": versions["torch"] + ".1",
                                               "state": "drift"}}
    both = runtime.inspect_current(profile="semantic", extras=EXTRAS, version_getter=installed(
        versions, missing=("faiss-cpu",), drifted=("torch",)))
    assert both["state"] == "missing_dependency"  # a missing package is reported first; both stay listed
    assert {name: entry["state"] for name, entry in both["mismatches"].items()} == {
        "faiss-cpu": "missing", "torch": "drift"}


def test_ml_absence_is_normal_and_core_drift_is_a_failure_in_the_lexical_runtime():
    semantic = runtime.expected_versions(profile="semantic", extras=EXTRAS)
    bare = runtime.inspect_current(profile="lexical", extras=EXTRAS,
                                   version_getter=installed(semantic, missing=ML_PACKAGES))
    assert bare["ok"] is True and bare["state"] == "qualified" and bare["mismatches"] == {}
    assert bare["profile_id"] == "lexical-macos-arm64-py311-v1"
    # ML packages installed at any version are outside the lexical catalog: neither required nor inspected.
    extra_ml = runtime.inspect_current(profile="lexical", extras=EXTRAS,
                                       version_getter=installed(semantic, drifted=ML_PACKAGES))
    assert extra_ml["ok"] is True and not set(ML_PACKAGES) & set(extra_ml["actual"])
    drifted = runtime.inspect_current(profile="lexical", extras=EXTRAS,
                                      version_getter=installed(semantic, drifted=("numpy",)))
    assert drifted["ok"] is False and drifted["state"] == "drift"
    assert drifted["mismatches"]["numpy"]["state"] == "drift"
    missing = runtime.inspect_current(profile="lexical", extras=EXTRAS,
                                      version_getter=installed(semantic, missing=("tiktoken",)))
    assert missing["state"] == "missing_dependency" and missing["mismatches"]["tiktoken"]["state"] == "missing"


# ----------------------------------------------- absent by choice, readiness
def test_lexical_capabilities_declare_the_encoder_absent_by_profile(tmp_path, monkeypatch):
    fx.forbid_vectors(monkeypatch)
    with MemoryV4(config=lexical_config(tmp_path)) as engine:
        fx.seed_main(engine)
        facts = capabilities.deployed(engine, {})
        assert facts["retrieval_profile"] == MARKER
        assert facts["encoding"] == {"state": "absent_by_profile", "model": None, "profile": None, "loaded": False}
        assert facts["policies"]["ranking_policy"] == POLICY
        assert (facts["policies"]["bm25_profile"], facts["policies"]["lexical_guard_k"],
                facts["policies"]["fusion"]) == ("scoped_literal", 3, "cc")
        assert facts["runtime"]["profile_id"] == "lexical-macos-arm64-py311-v1"
        assert not set(ML_PACKAGES) & set(facts["runtime"]["expected"])
        assert facts["retrieval_ready"] is True
        assert facts["store"] == {"backend": "sqlite_s1", "atomic_mutations": True,
                                  "instance_id": engine.store.meta["store_instance_id"]}
        # "No model" never waives the brief tokenizer: cl100k and its attested table stay reported.
        assert facts["brief"]["tokenizer"]["encoding"] == "cl100k_base"
        assert len(facts["brief"]["tokenizer"]["table_sha256"]) == 64
        ok, reason, snapshot = capabilities.readiness_snapshot(engine, {})
        assert (ok, reason) == (True, "ready") and snapshot["encoding"]["state"] == "absent_by_profile"


def test_lexical_readiness_needs_no_vector_but_refuses_an_incomplete_lexical_index(tmp_path, monkeypatch):
    fx.forbid_vectors(monkeypatch)
    with MemoryV4(config=lexical_config(tmp_path)) as engine:
        assert engine_readiness(engine) == (True, "ready")  # empty store
        fx.seed_main(engine)
        assert engine_readiness(engine) == (True, "ready")  # no model_not_loaded, no vector coverage check
        length = engine.bm25.doc_len.pop("a1")
        assert engine_readiness(engine) == (False, "indexes_not_ready")
        engine.bm25.doc_len["a1"] = length
        assert engine_readiness(engine) == (True, "ready")
        entities = engine.entities.doc_entities.pop("a3")
        assert engine_readiness(engine) == (False, "indexes_not_ready")
        engine.entities.doc_entities["a3"] = entities
        assert engine_readiness(engine) == (True, "ready")
        assert engine.forget("a2", project="alpha") is True
        assert engine_readiness(engine) == (True, "ready")
    with MemoryV4(config=lexical_config(tmp_path, work_state=True)) as engine:
        # Retrieval is ready at open in the lexical profile: never the cold "ready_work_only" state.
        assert engine_readiness(engine) == (True, "ready") and engine.status()["retrieval_ready"] is True

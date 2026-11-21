"""Retrospective mode of bench/evaluation_manifest.py (contract §10, H2) -- written first.

RED until the mode exists (no skip/xfail), except the two guards on today's behaviour.
H2 reuses BEAM 100K, already observed for C4/BM25: there is no honest dev/test split to
declare. The manifest must say so explicitly instead of inventing an independent split.
Needs no ML package and no engine.
"""

import copy
import hashlib
import json

import pytest

from bench.evaluation_manifest import ManifestError, validate

ROWS = [{"id": f"q{index}", "conversation_id": f"c{index % 2}"} for index in range(4)]


def artifact(tmp_path, name, content):
    path = tmp_path / name
    path.write_text(content)
    return {"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def manifest(tmp_path, split, **dataset):
    systems = [{"name": name, "revision": "a" * 40, "command": "python -m fixture",
                "config": {"retrieval_profile": profile, "store_backend": "sqlite_s1", "use_recency": False},
                "budget_policy": "shared"} for name, profile in (("lexical", "lexical"), ("semantic", "semantic"))]
    return {
        "protocol_version": "1.0", "stage": "final", "run_id": "h2-lexical-parity-v1",
        "source": {"commit": "a" * 40, "artifact": artifact(tmp_path, "candidate.whl", "candidate")},
        "environment_lock": artifact(tmp_path, "requirements.lock", "numpy==2.4.2"), "seed": 20261010,
        "hardware": {"os": "macOS", "cpu": "arm64", "accelerator": "mps", "device": "mps", "python": "3.11",
                     "ram_gib": 16},
        "timing": {"mode": "live", "cache": "both", "repetitions": 1, "warmup_queries": 0,
                   "scope": "ingest+retrieval+generation"},
        "models": {role: {"enabled": False} for role in ("embedding", "reranker", "planner", "response", "judge")},
        "tasks": [{"kind": "retrieval", "metrics": ["recall_at_k"], "scoring_rule": "annotated evidence turns"}],
        "top_k": 10,
        "budgets": {"memory_bytes": 1024, "ingest_tokens": 100, "context_tokens": 100, "query_tokens": 100,
                    "model_calls": 1, "wall_seconds": 60, "api_usd": 0},
        "systems": systems,
        "datasets": [{"name": "beam-100k", "revision": "3205395e897e7318c7b094ef4e6047b9b82dbb03", "subset": "100K",
                      "exclusions": "questions without annotated evidence are executed but not scored",
                      "data": artifact(tmp_path, "data.json", "{}"),
                      "splits": artifact(tmp_path, "splits.json", json.dumps(split)),
                      "group_keys": ["conversation_id"], **dataset}],
        "reporting": "separate_task_families",
    }


def retrospective(tmp_path):
    document = manifest(tmp_path, {"evaluation": ROWS}, evaluation_count=len(ROWS))
    document["selection_rule"] = "retrospective_no_selection"
    document["retrospective"] = {
        "independence_claim": False, "tuning_allowed": False,
        "prior_exposure": ["BEAM 100K results of C4 and BM25 were observed before the threshold was set"]}
    return document


def test_retrospective_manifest_without_any_split_is_accepted(tmp_path):
    validate(retrospective(tmp_path), tmp_path)


@pytest.mark.parametrize("change", [
    lambda document: document["retrospective"].__setitem__("independence_claim", True),
    lambda document: document["retrospective"].pop("independence_claim"),
    lambda document: document["retrospective"].__setitem__("tuning_allowed", True),
    lambda document: document["retrospective"].__setitem__("prior_exposure", []),
    lambda document: document["retrospective"].__setitem__("prior_exposure", ["todo"]),
    lambda document: document.pop("retrospective"),
    lambda document: document["datasets"][0].__setitem__("evaluation_count", 3),
    lambda document: document["datasets"][0].__setitem__("test_count", 4),
], ids=["claims_independence", "claim_missing", "tuning_allowed", "no_prior_exposure", "placeholder_exposure",
        "block_missing", "count_mismatch", "test_count_present"])
def test_retrospective_manifest_must_be_explicit_about_what_it_is_not(tmp_path, change):
    document = retrospective(tmp_path)
    validate(copy.deepcopy(document), tmp_path)  # precondition: the unmodified manifest is valid
    change(document)
    with pytest.raises(ManifestError):
        validate(document, tmp_path)


@pytest.mark.parametrize("split", [
    {"dev": ROWS[:2], "test": ROWS[2:]},
    {"evaluation": ROWS[:2], "test": ROWS[2:]},
    {"evaluation": ROWS, "dev": []},
    {"evaluation": []},
    {"evaluation": [ROWS[0], ROWS[0]]},
], ids=["fake_dev_test", "evaluation_plus_test", "empty_dev_key", "empty", "duplicate_rows"])
def test_retrospective_mode_refuses_a_fake_or_empty_split(tmp_path, split):
    document = retrospective(tmp_path)
    validate(copy.deepcopy(document), tmp_path)  # precondition: the unmodified manifest is valid
    path = tmp_path / "splits.json"
    path.write_text(json.dumps(split))
    document["datasets"][0]["splits"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    document["datasets"][0]["evaluation_count"] = len(split.get("evaluation", []))
    with pytest.raises(ManifestError):
        validate(document, tmp_path)


def test_selection_mode_refuses_retrospective_fields(tmp_path):
    """dev_only_then_freeze keeps its exact meaning: no retrospective block may ride along."""
    document = manifest(tmp_path, {"dev": ROWS[:2], "test": [dict(row, conversation_id="t" + row["id"])
                                                              for row in ROWS[2:]]}, test_count=2)
    document["selection_rule"] = "dev_only_then_freeze"
    validate(copy.deepcopy(document), tmp_path)  # today's mode, unchanged
    document["retrospective"] = {"independence_claim": False, "tuning_allowed": False, "prior_exposure": ["seen"]}
    with pytest.raises(ManifestError):
        validate(document, tmp_path)


def test_selection_mode_still_requires_dev_and_test(tmp_path):
    """GREEN NOW: an evaluation-only split is not accepted under the selection rule."""
    document = manifest(tmp_path, {"evaluation": ROWS}, test_count=4)
    document["selection_rule"] = "dev_only_then_freeze"
    with pytest.raises(ManifestError):
        validate(document, tmp_path)


def test_unknown_selection_rule_is_refused(tmp_path):
    """GREEN NOW."""
    document = retrospective(tmp_path)
    document["selection_rule"] = "whatever"
    with pytest.raises(ManifestError):
        validate(document, tmp_path)

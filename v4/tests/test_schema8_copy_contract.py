"""Source-copy acceptance gates: drift is a refusal, not a warning."""

import copy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from qualify_schema8_copy import compare  # noqa: E402


def rows():
    base = {
        "ok": True,
        "store_instance_id": "id",
        "total": 10,
        "encoding_profile": {"device": "mps"},
        "search": {"fixed-query": ["one", "two"]},
        "corpus_encoded_texts": 0,
        "cache_misses": 0,
        "startup_seconds": 2,
    }
    candidate = {
        **base,
        "migration": {"ok": True, "from_schema": 7, "to_schema": 8},
        "migration_seconds": 1,
        "journal": {"generation": "generation", "coverage_start": 10, "events": 0},
        "authority_sha256": "identical",
    }
    return {"baseline": base, "candidate": candidate, "candidate-warm": copy.deepcopy(candidate)}


def test_source_copy_positive_is_never_release_or_activation_qualification():
    verdict = compare(rows(), "id")
    assert verdict["ok"] and not verdict["release_qualified"] and not verdict["production_activation"]


@pytest.mark.parametrize(
    "damage",
    [
        "identity",
        "ranks",
        "profile",
        "cold_encode",
        "warm_encode",
        "cache_miss",
        "startup",
        "generation",
        "coverage",
        "events",
        "authority",
        "migration",
    ],
)
def test_source_copy_refuses_each_drift(damage):
    result = rows()
    cold, warm = result["candidate"], result["candidate-warm"]
    if damage == "identity":
        cold["store_instance_id"] = "another"
    elif damage == "ranks":
        cold["search"] = {"fixed-query": ["two", "one"]}
    elif damage == "profile":
        cold["encoding_profile"] = {"device": "cpu"}
    elif damage == "cold_encode":
        cold["corpus_encoded_texts"] = 1
    elif damage == "warm_encode":
        warm["corpus_encoded_texts"] = 1
    elif damage == "cache_miss":
        cold["cache_misses"] = 1
    elif damage == "startup":
        cold["startup_seconds"] = 121
    elif damage in ("generation", "coverage", "events"):
        warm["journal"][{"coverage": "coverage_start", "events": "events"}.get(damage, damage)] = 1
    elif damage == "authority":
        warm["authority_sha256"] = "changed"
    elif damage == "migration":
        cold["migration_seconds"] = 121
    with pytest.raises(ValueError):
        compare(result, "id")


@pytest.mark.parametrize("variant", ["default", "parent", "plist"])
def test_private_home_preserves_real_offline_cache_and_drops_hash_override(tmp_path, variant):
    from qualify_schema8_copy import offline_environment

    real, private = tmp_path / "operator", tmp_path / "private"
    parent = {
        "HOME": str(real),
        "MEMORY_V4_EMBED_MODEL": "hash",
        "MEMORY_V4_DIR": "production",
        "MEMORY_V4_TOKEN_FILE": "production-token",
        "PYTHONPATH": "checkout",
        "HF_HUB_OFFLINE": "0",
    }
    profile = {"MEMORY_V4_EMBED_MODEL": "qualified-granite", "MEMORY_V4_DEVICE": "auto"}
    if variant == "parent":
        parent["HF_HOME"] = str(tmp_path / "parent-cache")
    if variant == "plist":
        profile.update(HF_HOME=str(tmp_path / "plist-cache"), HF_HUB_CACHE=str(tmp_path / "hub"))
    env = offline_environment(parent, profile, private, real, 3_000_000_000)
    expected = profile.get("HF_HOME", parent.get("HF_HOME", str(real / ".cache/huggingface")))
    assert env["HF_HOME"] == expected and env["HOME"] == str(private)
    assert env["MEMORY_V4_EMBED_MODEL"] == "qualified-granite"
    assert env["MEMORY_V4_MIN_FREE_BYTES"] == "3000000000"
    assert env["HF_HUB_OFFLINE"] == env["TRANSFORMERS_OFFLINE"] == "1"
    assert env["PYTHONPATH"] == ""
    assert "MEMORY_V4_DIR" not in env and "MEMORY_V4_TOKEN_FILE" not in env
    assert env["MEMORY_V4_WRITE_MODE"] == "read_only"

"""Per-profile package metadata and exact constraints (contract §8) -- written first.

SOURCE CHECKOUT ONLY (reads pyproject.toml and the shipped resources); needs no ML package.
Not part of the wheel subset. The semantic resources are byte-pinned and green now; everything
about the lexical resources, ``check_constraints`` and the ``[semantic]`` extra is red until
steps 6-7 (no skip/xfail).
"""

import hashlib
import json
from pathlib import Path
import tomllib

import pytest

from memory_os_v4.ops import qualified_runtime as runtime

ROOT = Path(__file__).resolve().parents[2]
RESOURCES = runtime.PROFILE.parent
ML_PACKAGES = ("torch", "sentence-transformers", "transformers", "faiss-cpu")
EXTRAS = ("ops", "brief")
SEMANTIC_RESOURCES_AT_83DDEAD = {
    "qualified_runtime_v1.json": "20a9f65087a81b1d2af839b0baa01d4b0194029cfe4c5512947b1e211cc52444",
    "qualified_runtime_constraints_v1.txt": "eeffd1c60060a2c4a2279f48e07818095914f6de34f5638c4bbeaa524055220d",
    "qualified_runtime_closure_v1.json": "d2ec37faa017e37d750e651e4b62f4ac1be839992b04890045d3df012d51bb51",
}


def constraint_lines(name):
    return [line for line in (RESOURCES / name).read_text().splitlines() if line and not line.startswith("#")]


def project():
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]


def names(requirements):
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    return {canonicalize_name(Requirement(text).name) for text in requirements}


def test_semantic_runtime_resources_are_byte_identical_to_base_83ddead():
    """GREEN NOW: the historical semantic catalog, constraints and closure never change."""
    for name, digest in SEMANTIC_RESOURCES_AT_83DDEAD.items():
        assert hashlib.sha256((RESOURCES / name).read_bytes()).hexdigest() == digest, name
    lines = constraint_lines("qualified_runtime_constraints_v1.txt")
    assert dict(line.split("==") for line in lines) == runtime.expected_versions(extras=EXTRAS)


def test_exact_locks_live_in_the_constraints_of_each_profile():
    semantic = constraint_lines("qualified_runtime_constraints_v1.txt")
    assert runtime.check_constraints(semantic, profile="semantic", extras=EXTRAS) == {"ok": True, "mismatches": {}}
    loosened = [line for line in semantic if not line.startswith("torch==")] + ["torch>=2.10,<2.11"]
    proof = runtime.check_constraints(loosened, profile="semantic", extras=EXTRAS)
    assert proof["ok"] is False and set(proof["mismatches"]) == {"torch"}  # exact == stays mandatory here
    dropped = [line for line in semantic if not line.startswith("numpy==")]
    assert set(runtime.check_constraints(dropped, profile="semantic", extras=EXTRAS)["mismatches"]) == {"numpy"}

    lexical = constraint_lines("qualified_runtime_constraints_lexical_v1.txt")
    assert runtime.check_constraints(lexical, profile="lexical", extras=EXTRAS) == {"ok": True, "mismatches": {}}
    assert dict(line.split("==") for line in lexical) == runtime.expected_versions(profile="lexical", extras=EXTRAS)
    assert not set(ML_PACKAGES) & names(lexical)
    assert set(lexical) < set(semantic)  # same exact pins, minus the ML closure
    record = json.loads((RESOURCES / "qualified_runtime_lexical_v1.json").read_text())
    assert record["profile_id"] == "lexical-macos-arm64-py311-v1" and record["encoding_runtime_keys"] == []
    # The lexical constraints are not a valid semantic lock and conversely.
    assert runtime.check_constraints(lexical, profile="semantic", extras=EXTRAS)["ok"] is False
    assert runtime.check_constraints(semantic, profile="lexical", extras=EXTRAS)["ok"] is False


def test_package_metadata_uses_compatible_bounds_and_puts_ml_in_the_semantic_extra():
    meta = project()
    core, optional = meta["dependencies"], meta["optional-dependencies"]
    assert not set(ML_PACKAGES) & names(core)  # a bare install carries no ML package
    assert set(ML_PACKAGES) <= names(optional["semantic"])
    assert "numpy" in names(core)
    active_lexical = core + optional["ops"] + optional["brief"]
    assert runtime.check_requirements(active_lexical, profile="lexical", extras=EXTRAS)["ok"] is True
    active_semantic = active_lexical + optional["semantic"]
    assert runtime.check_requirements(active_semantic, profile="semantic", extras=EXTRAS)["ok"] is True
    # Metadata are bounds that CONTAIN the qualified version, not exact pins ...
    assert not any("==" in text for text in core + optional["semantic"])
    # ... a bound excluding the qualified version is refused ...
    excluded = [text for text in active_semantic if names([text]) != {"torch"}] + ["torch>=99"]
    proof = runtime.check_requirements(excluded, profile="semantic", extras=EXTRAS)
    assert proof["ok"] is False and set(proof["mismatches"]) == {"torch"}
    # ... a semantic install without the extra misses the closure ...
    proof = runtime.check_requirements(active_lexical, profile="semantic", extras=EXTRAS)
    assert proof["ok"] is False and set(ML_PACKAGES) <= set(proof["mismatches"])
    # ... and the lexical metadata may not require an ML package outside the extra.
    proof = runtime.check_requirements(active_lexical + ["torch>=2.4"], profile="lexical", extras=EXTRAS)
    assert proof["ok"] is False and set(proof["mismatches"]) == {"torch"}


@pytest.mark.parametrize("name", ["pyproject.toml", "v4/pyproject.toml"])
def test_release_identity_is_4_1_3_everywhere(name):
    """Version identities move together (test_distribution_d1a.py:39 is kept as strict as today)."""
    import memory_os_v4

    assert tomllib.loads((ROOT / name).read_text())["project"]["version"] == memory_os_v4.__version__ == "4.1.3"

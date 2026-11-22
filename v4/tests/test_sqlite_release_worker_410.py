"""G11 (QUALIFY_4_1_0.md rev. 8): the opposite (semantic) open of a brand-new lexical store, through the
qualification worker's own ``refusal``. Real engine on a tmp store, hash encoder, no network, no install.

The engine checks the semantic ML closure BEFORE the profile of the store: an interpreter without FAISS
(the lexical venv) is refused by ``semantic_dependency_missing`` and proves nothing about the store.
Needs FAISS: not part of the ML-free subset.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import qualify_sqlite_release_410 as sqlite_gate  # noqa: E402
import sqlite_release_worker_410 as worker  # noqa: E402

pytest.importorskip("faiss")


@pytest.fixture
def lexical_store(tmp_path, monkeypatch):
    from memory_os_v4 import Config, MemoryV4

    for name in ("MEMORY_V4_RETRIEVAL_PROFILE", "MEMORY_V4_DIR", "MEMORY_V4_STORE_BACKEND", "MEMORY_V4_WRITE_MODE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MEMORY_V4_EMBED_MODEL", "hash")
    store = tmp_path / "lexical-store"
    with MemoryV4(config=Config.lexical(data_dir=str(store))) as engine:
        engine.add_facts([{"text": text} for text in worker.LEXICAL_FACTS], project=worker.LEXICAL_PROJECT,
                         event_time=worker.LEXICAL_EVENT_TIME)
    return store


def opposite_open(tmp_path, store, **attested):
    before_rows, before = sqlite_gate.hashed_inventory(store)
    worker.refusal(tmp_path, "semantic-refusal", store, "semantic", **attested)
    after_rows, after = sqlite_gate.hashed_inventory(store)
    assert before_rows and before == after and after_rows == before_rows   # refused without any mutation
    return json.loads((tmp_path / "semantic-refusal.json").read_text())


def test_semantic_open_with_the_ml_closure_reaches_the_store_profile(tmp_path, lexical_store):
    report = opposite_open(tmp_path, lexical_store, package_path="/e/wheel/site-packages/memory_os_v4/__init__.py",
                           ml_find_spec={"faiss": True})
    assert report == {"requested": "semantic", "refused": True, "code": "store_profile_mismatch",
                      "package_path": "/e/wheel/site-packages/memory_os_v4/__init__.py",
                      "ml_find_spec": {"faiss": True}}


def test_semantic_open_without_faiss_stops_before_the_store_profile(tmp_path, lexical_store, monkeypatch):
    """The latent defect: in the lexical venv (no FAISS) the refusal is NOT ``store_profile_mismatch``."""
    monkeypatch.setitem(sys.modules, "faiss", None)
    report = opposite_open(tmp_path, lexical_store)
    assert report == {"requested": "semantic", "refused": True, "code": "semantic_dependency_missing"}

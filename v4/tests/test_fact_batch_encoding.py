"""Batching is a throughput change, never a dedup/update/transaction policy."""
import copy

import numpy as np
import pytest

from memory_os_v4 import Config, MemoryV4
from memory_os_v4.embed import HashEmbedder


def engine(path, backend, size):
    return MemoryV4(config=Config(data_dir=str(path), embed_model="hash", hash_dim=32,
                                 store_backend=backend, embed_batch_size=size, use_recency=False))


@pytest.mark.parametrize("backend", ["jsonl", "sqlite_s1"])
def test_batch_vectors_rankings_dedup_and_temporal_updates_match_scalar(tmp_path, monkeypatch, backend):
    monkeypatch.setattr("time.time", lambda: 1000.0)
    ident = {"subject_id": "user", "predicate": "city", "cardinality": "single"}
    facts = [{"text": f"Project uses unique component {i}", "item_id": f"f{i}"} for i in range(9)]
    facts.insert(2, {"text": "Project uses unique component 0", "item_id": "dup"})
    facts += [
        {"text": "User lives in Paris", "item_id": "old", "identity": ident, "event_time": 100},
        {"text": "Independent fact after old", "item_id": "middle"},
        {"text": "User lives in Lyon", "item_id": "new", "identity": ident, "event_time": 200},
        {"text": "Explicit correction", "item_id": "correct", "replaces": ["middle"], "event_time": 300},
    ]
    snapshots = []
    for size in (1, 4):
        with engine(tmp_path / str(size), backend, size) as m:
            result = m.add_facts(copy.deepcopy(facts), project="p", event_time=50)
            assert result["deduped"] == ["f0"]
            assert ("old", "new") in result["superseded"]
            assert ("middle", "correct") in result["superseded"]
            ids, vecs = m.vector_index.export()
            ranks = [[h.item.id for h in m.search(query, project="p", top_k=10)]
                     for query in ("component 4", "User lives in Lyon", "Independent fact")]
            snapshots.append((result, ids, vecs, ranks,
                              [copy.deepcopy(vars(it)) for it in m.store.all()]))
    a, b = snapshots
    assert a[:2] == b[:2] and a[3:] == b[3:]
    np.testing.assert_array_equal(a[2], b[2])


@pytest.mark.parametrize("backend", ["jsonl", "sqlite_s1"])
def test_duplicates_are_not_encoded_and_full_batches_are_real(tmp_path, monkeypatch, backend):
    calls = []
    original = HashEmbedder.encode

    def counted(self, texts):
        calls.append(len(texts))
        return original(self, texts)

    monkeypatch.setattr(HashEmbedder, "encode", counted)
    with engine(tmp_path, backend, 4) as m:
        facts = [{"text": f"Unique value {i}", "item_id": str(i)} for i in range(10)]
        facts.insert(2, {"text": "Unique value 0", "item_id": "duplicate"})
        result = m.add_facts(facts, event_time=100)
        assert calls == [4, 4, 2]
        assert result["added"] == [str(i) for i in range(10)]
        assert result["deduped"] == ["0"]


@pytest.mark.parametrize("backend", ["jsonl", "sqlite_s1"])
def test_duplicate_id_keeps_existing_failure_contract(tmp_path, backend):
    with engine(tmp_path, backend, 4) as m:
        with pytest.raises(ValueError, match="duplicate item id"):
            m.add_facts([{"item_id": "a", "text": "first"},
                         {"item_id": "a", "text": "second"}], event_time=100)
        assert bool(m.store.get("a")) == (backend == "jsonl")


def test_batched_sqlite_write_failure_leaves_no_receipt_or_partial_vectors(tmp_path):
    with engine(tmp_path, "sqlite_s1", 4) as m:
        m.store._connection.execute(
            "CREATE TRIGGER injected BEFORE INSERT ON item_versions "
            "WHEN NEW.item_id='bad' BEGIN SELECT RAISE(ABORT,'batch failure'); END"
        )
        with pytest.raises(Exception, match="batch failure"):
            m.add_facts([{"item_id": "good", "text": "good"},
                         {"item_id": "bad", "text": "bad"}], event_time=100)
        assert not m.store.items and m.vector_index.ntotal == 0
        assert not m.requests.records
        assert m.store._connection.execute("SELECT count(*) FROM request_receipts").fetchone()[0] == 0


def test_fault_at_each_batched_write_boundary_never_acknowledges_partial_batch(tmp_path):
    facts = [{"item_id": f"batch{i}", "text": f"Batched unique fact {i}"} for i in range(5)]
    points = []
    with engine(tmp_path / "trace", "sqlite_s1", 4) as m:
        m.store.fault_hook = points.append
        m.add_facts(facts, event_time=100)
        m.store.fault_hook = None
    assert "before_receipt" in points and "after_commit" in points
    expected = {f"batch{i}" for i in range(5)}
    for ordinal in range(len(points)):
        path = tmp_path / f"fault{ordinal}"
        m = engine(path, "sqlite_s1", 4)
        m.add_episode("Already acknowledged", item_id="seed", event_time=10)
        counter = 0

        def fail(point):
            nonlocal counter
            counter += 1
            if counter == ordinal + 1:
                raise OSError("injected boundary")

        m.store.fault_hook = fail
        try:
            with pytest.raises((OSError, RuntimeError)):
                m.add_facts(facts, event_time=100)
        finally:
            m.store.fault_hook = None
            try:
                m.close()
            except RuntimeError:
                assert m._closed
        with engine(path, "sqlite_s1", 4) as reopened:
            assert reopened.store.get("seed") is not None
            present = expected & set(reopened.store.items)
            assert present in (set(), expected)
            complete = [r for r in reopened.requests.records.values()
                        if r["operation"] == "add_facts" and r["state"] == "complete"]
            if complete:
                assert present == expected

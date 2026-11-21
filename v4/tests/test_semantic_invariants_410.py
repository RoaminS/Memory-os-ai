"""4.1.0 non-regression guards for the semantic profile (contract §6, G1a).

These tests pass on base 83ddead and must keep passing unchanged while the
lexical profile is implemented: embed.py stays byte-identical, schema 8 and
its migration checksums are untouched, the corpus-vector cache keeps its key
formula, float32 codec, adoption rules and warm reuse, and replay stays
idempotent. Hash embedder and tmp_path stores only; no model is loaded.
"""

import hashlib
import importlib.metadata
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import numpy as np
import pytest

from memory_os_v4 import Config, MemoryV4
from memory_os_v4 import embed, sqlite_store
from memory_os_v4.embed import Embedder, HashEmbedder
from memory_os_v4.idempotency import RequestConflictError
from memory_os_v4.sqlite_embeddings import SQLiteEmbeddings

# `git show 83ddead:v4/src/memory_os_v4/embed.py | shasum -a 256`
EMBED_SHA256_AT_83DDEAD = "c2c335c468d7dedf7cff0ca2ce281c83d7c12fc9c65fd2c5b93adbbfcc5d60a7"

MIGRATION_CHECKSUMS_SCHEMA_8 = [
    (1, "54e6fec963090c02f40f6e10d3b5fa35872cfe20dcdf795be8c90b04dae0d751"),
    (2, "4f49cbdfb4f02a0b66e60a0eb55202e1f681b00bc8d532e06303a26fc95fcda8"),
    (3, "0163af0cb61a33d4a48270e215a5d7edf9e0045779caedb67336a101fd7da2f3"),
    (4, "01da599ca7d40b1b81de34c3b416f97df3ac7b5d2207e3fb5f6ca4db41278dfc"),
    (5, "015ee4f350ff4d98ea939f1df54d36c92cec243b42d7bc89258726f3bfe051be"),
    (6, "3e1c85a2f4ae0e92e6280a372df03eed6c606bdfa0888bfb8ea95385ed433823"),
    (7, "6eb4c999718d2e660c9cc88ff340dbfef113bc5d46c8ee2528720d739fedeab1"),
    (8, "c318fdc44abef5abd88dc61fba5d937b92ac96ec540a5a9db01a1db7bc99d86a"),
]

HASH_ATTESTATION = {
    "device": "hash", "dtype": "native", "batch_size": 32, "max_seq_length": 512,
    "parameter_dtypes": ["hash-float32"], "parameter_devices": ["hash"], "model_revision": "hash-v1",
}

CORPUS = [
    {"text": "Alice Martin adopted a grey cat named Pixel.", "item_id": "s1", "event_time": 1.70e9},
    {"text": "The budget review moved to Thursday afternoon.", "item_id": "s2", "event_time": 1.70e9},
    {"text": "Pixel the cat refuses dry food and prefers tuna.", "item_id": "s3", "event_time": 1.70e9},
    {"text": "Bruno Keller maintains the SQLite migration scripts.", "item_id": "s4", "event_time": 1.70e9},
]


def _semantic_config(path, **overrides):
    return Config(data_dir=str(path), store_backend="sqlite_s1", embed_model="hash", hash_dim=16, **overrides)


def _cache_rows(engine):
    return engine.store._connection.execute(
        "SELECT cache_key,dim,vector,vector_sha256 FROM embedding_cache ORDER BY cache_key").fetchall()


def _vectors(engine):
    ids, vectors = engine.vector_index.export()
    return {item_id: vector.tobytes() for item_id, vector in zip(ids, vectors)}


def test_embed_source_is_byte_identical_to_base_83ddead():
    source = Path(embed.__file__).read_bytes()
    assert hashlib.sha256(source).hexdigest() == EMBED_SHA256_AT_83DDEAD


def test_encoding_profile_content_is_stable_on_a_fixture_model():
    class Parameter:
        def __init__(self, dtype, device):
            self.dtype, self.device = dtype, SimpleNamespace(type=device)

    class FixtureModel:
        def parameters(self):
            return [Parameter("torch.bfloat16", "mps"), Parameter("torch.float32", "mps"),
                    Parameter("torch.bfloat16", "mps")]

        def __getitem__(self, index):
            return SimpleNamespace(auto_model=SimpleNamespace(config=SimpleNamespace(_commit_hash="fixture-revision")))

    encoder = Embedder("fixture/model", device="cpu")  # explicit device: no torch probe, no model load
    encoder._model, encoder._dim = FixtureModel(), 8
    runtime = {}
    for name in ("torch", "sentence-transformers", "transformers", "numpy"):
        try:
            runtime[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            runtime[name] = None
    assert encoder.encoding_profile() == {
        "parameter_dtypes": ["torch.bfloat16", "torch.float32"],
        "parameter_devices": ["mps"],
        "model_revision": "fixture-revision",
        "runtime": runtime,
        "encoder_source_sha256": EMBED_SHA256_AT_83DDEAD,
        "normalized": True,
        "output_dtype": "float32",
    }
    assert list(encoder.encoding_profile()["runtime"]) == ["torch", "sentence-transformers", "transformers", "numpy"]


def test_schema_8_and_migration_checksums_are_unchanged(tmp_path):
    assert sqlite_store.SCHEMA_VERSION == 8
    assert sqlite_store.APPLICATION_ID == 0x4D4F5334
    assert len(sqlite_store.MIGRATIONS) == 8
    assert sqlite_store.schema_checksums() == MIGRATION_CHECKSUMS_SCHEMA_8
    with MemoryV4(config=_semantic_config(tmp_path)):
        pass
    connection = sqlite3.connect(tmp_path / "store.sqlite3")
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 8
        assert connection.execute(
            "SELECT version,checksum FROM schema_migrations ORDER BY version").fetchall() == MIGRATION_CHECKSUMS_SCHEMA_8
    finally:
        connection.close()


def test_semantic_store_metadata_keys_and_no_profile_marker(tmp_path):
    """A semantic store never receives a profile marker: absence means semantic."""
    for _ in range(2):  # creation, then reopening: neither writes a marker
        with MemoryV4(config=_semantic_config(tmp_path)) as engine:
            if not engine.store.items:
                engine.add_episodes_bulk([dict(spec) for spec in CORPUS])
            assert set(engine.store.meta) == {"store_instance_id", "embed_compute_profile", "version",
                                              "embed_model", "dim"}
            assert engine.store.meta["embed_model"] == "hash" and engine.store.meta["dim"] == 16
            assert engine.store.meta["embed_compute_profile"] == HASH_ATTESTATION
            keys = {row[0] for row in engine.store._connection.execute("SELECT key FROM store_metadata")}
            assert "retrieval_profile" not in keys and keys <= {"metadata", "chunk_map", "change_journal"}


def test_cache_key_formula_and_float32_codec_are_unchanged(tmp_path):
    config = _semantic_config(tmp_path)
    with MemoryV4(config=config) as engine:
        engine.add_episodes_bulk([dict(spec) for spec in CORPUS])
        profile = {"model": "hash", "dim": 16, "max_seq_length": 512, "batch_size": 32, "device": "hash",
                   "cache_schema": 2, "namespace": "v1", "attestation": HASH_ATTESTATION}
        prefix = json.dumps(profile, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode() + b"\0"
        from memory_os_v4.memory import index_text
        rows = {key: (dim, blob, checksum) for key, dim, blob, checksum in _cache_rows(engine)}
        assert len(rows) == len(CORPUS)
        for spec in CORPUS:
            item = engine.store.get(spec["item_id"])
            text = index_text(item, config)
            key = hashlib.sha256(prefix + text.encode()).hexdigest()
            dim, blob, checksum = rows[key]
            assert dim == 16 and len(blob) == 16 * 4 and hashlib.sha256(blob).hexdigest() == checksum
            expected = HashEmbedder(dim=16).encode([text])[0]
            assert np.array_equal(np.frombuffer(blob, dtype="<f4"), expected)
            assert np.array_equal(engine.vector_index.get_one(item.id), expected)


def test_warm_semantic_cache_is_reused_without_reencoding_the_corpus(tmp_path, monkeypatch):
    config = _semantic_config(tmp_path)
    with MemoryV4(config=config) as engine:
        engine.add_episodes_bulk([dict(spec) for spec in CORPUS])
        assert (engine._embedding_cache.hits, engine._embedding_cache.misses) == (0, len(CORPUS))
        rows = _cache_rows(engine)
        exported = _vectors(engine)
        ranking = [hit.item.id for hit in engine.search("cat Pixel tuna", top_k=4)]
        metadata = {key: engine.store.meta[key] for key in ("embed_model", "dim", "embed_compute_profile",
                                                             "store_instance_id")}
    corpus_texts = []
    original = HashEmbedder.encode

    def counted(self, texts):
        corpus_texts.extend(text for text in texts if text != "cat Pixel tuna")
        return original(self, texts)

    monkeypatch.setattr(HashEmbedder, "encode", counted)
    with MemoryV4(config=config) as engine:
        assert (engine._embedding_cache.hits, engine._embedding_cache.misses,
                engine._embedding_cache.legacy_hits) == (len(CORPUS), 0, 0)
        assert _cache_rows(engine) == rows
        assert _vectors(engine) == exported
        assert [hit.item.id for hit in engine.search("cat Pixel tuna", top_k=4)] == ranking
        assert {key: engine.store.meta[key] for key in metadata} == metadata
        assert _cache_rows(engine) == rows  # queries stay uncached
    assert corpus_texts == []


def test_corrupt_cache_row_is_reencoded_alone_and_old_rows_are_kept(tmp_path, monkeypatch):
    config = _semantic_config(tmp_path)
    with MemoryV4(config=config) as engine:
        engine.add_episodes_bulk([dict(spec) for spec in CORPUS])
        rows = _cache_rows(engine)
        engine.store._connection.execute("UPDATE embedding_cache SET vector=x'00' WHERE cache_key=?", (rows[0][0],))
    encoded = []
    original = HashEmbedder.encode

    def counted(self, texts):
        encoded.append(len(texts))
        return original(self, texts)

    monkeypatch.setattr(HashEmbedder, "encode", counted)
    with MemoryV4(config=config) as engine:
        assert encoded == [1]
        assert _cache_rows(engine) == rows  # repaired to the identical float32 bytes
    encoded.clear()
    with MemoryV4(config=_semantic_config(tmp_path, sqlite_embedding_namespace="v2")) as engine:
        assert encoded == [len(CORPUS)]
        after = _cache_rows(engine)
        assert set(rows) <= set(after) and len(after) == 2 * len(rows)  # previous namespace rows are preserved


@pytest.mark.parametrize("release,attestation,expected", [
    ("4.0.5", {"encoder_source_sha256": "x", "model_revision": "rev"}, True),
    ("4.0.6", {"model_revision": "hash-v1"}, True),  # with model == "hash"
    ("4.0.6", {"encoder_source_sha256": "x", "model_revision": None}, False),  # unknown revision
    ("4.0.6", {"encoder_source_sha256": "x", "model_revision": "rev", "unattested_revision_nonce": "n"}, False),
    ("4.0.6", {"model_revision": "rev"}, False),  # no encoder attestation
    ("4.0.7", {"encoder_source_sha256": "x", "model_revision": "rev"}, False),  # release outside the adoption set
    ("4.0.9", {"encoder_source_sha256": "x", "model_revision": "rev"}, False),
    (None, {"encoder_source_sha256": "x", "model_revision": "rev"}, False),
])
def test_historical_adoption_rules_never_adopt_an_unknown_revision(release, attestation, expected):
    cache = SQLiteEmbeddings(SimpleNamespace(meta={"version": release} if release else {}))
    model = "hash" if attestation == {"model_revision": "hash-v1"} else "fixture/model"
    profile = {"model": model, "dim": 16, "cache_schema": 2, "namespace": "v1", "attestation": attestation}
    legacy = cache._legacy_profile(profile)
    if expected:
        assert legacy == {"model": model, "dim": 16, "namespace": "v1", "attestation": attestation,
                          "implementation": release}
    else:
        assert legacy is None
    assert cache._legacy_profile({**profile, "cache_schema": 3}) is None


def test_explicit_request_replay_is_idempotent_and_conflicts_are_refused(tmp_path):
    with MemoryV4(config=_semantic_config(tmp_path)) as engine:
        calls = []

        def action():
            calls.append(1)
            return engine.add_facts([{"text": "Bruno Keller prefers green tea."}], project="p")

        first = engine.execute_request("memory_add_facts", "p", {"facts": 1}, "request-1", action)
        items = len(engine.store.items)
        cache = _cache_rows(engine)
        replay = engine.execute_request("memory_add_facts", "p", {"facts": 1}, "request-1", action)
        assert replay == first and len(calls) == 1
        assert len(engine.store.items) == items and _cache_rows(engine) == cache
        assert engine.store._connection.execute("SELECT count(*) FROM request_receipts").fetchone()[0] == 1
        with pytest.raises(RequestConflictError):
            engine.execute_request("memory_add_facts", "p", {"facts": 2}, "request-1", action)
        assert len(calls) == 1 and len(engine.store.items) == items


# ---- Complements requested by the contract review (scope of the guards above is limited:
# the adoption cases only exercise `_legacy_profile`, re-encoding uses the hash encoder, and
# test_sqlite_cache_release.py remains the reference for adoption/atomicity). Green on 83ddead.
def _nan_row(connection, key):
    blob = np.full(16, np.nan, dtype="<f4").tobytes()
    connection.execute("UPDATE embedding_cache SET vector=?, vector_sha256=? WHERE cache_key=?",
                       (blob, hashlib.sha256(blob).hexdigest(), key))


@pytest.mark.parametrize("corrupt", [
    lambda connection, key: connection.execute(
        "UPDATE embedding_cache SET vector_sha256=? WHERE cache_key=?", ("0" * 64, key)),
    _nan_row,
    lambda connection, key: connection.execute("UPDATE embedding_cache SET dim=8 WHERE cache_key=?", (key,)),
], ids=["checksum_mismatch", "non_finite_values", "wrong_dimension"])
def test_each_cache_corruption_kind_reencodes_only_the_faulty_row(tmp_path, monkeypatch, corrupt):
    config = _semantic_config(tmp_path)
    with MemoryV4(config=config) as engine:
        engine.add_episodes_bulk([dict(spec) for spec in CORPUS])
        rows = _cache_rows(engine)
        exported = _vectors(engine)
        corrupt(engine.store._connection, rows[1][0])
    encoded = []
    original = HashEmbedder.encode

    def counted(self, texts):
        encoded.append(len(texts))
        return original(self, texts)

    monkeypatch.setattr(HashEmbedder, "encode", counted)
    with MemoryV4(config=config) as engine:
        assert encoded == [1]
        assert _cache_rows(engine) == rows and _vectors(engine) == exported


def test_request_replay_is_idempotent_across_a_reopen(tmp_path):
    config = _semantic_config(tmp_path)
    calls = []

    def run(engine):
        def action():
            calls.append(1)
            return engine.add_facts([{"text": "Bruno Keller prefers green tea."}], project="p")
        return engine.execute_request("memory_add_facts", "p", {"facts": 1}, "request-1", action)

    with MemoryV4(config=config) as engine:
        first = run(engine)
        items, cache = len(engine.store.items), _cache_rows(engine)
    with MemoryV4(config=config) as engine:  # another engine instance, same durable receipt
        count = "SELECT count(*) FROM request_receipts"
        receipts = engine.store._connection.execute(count).fetchone()[0]
        assert run(engine) == first and len(calls) == 1
        assert len(engine.store.items) == items and _cache_rows(engine) == cache
        assert engine.store._connection.execute(count).fetchone()[0] == receipts  # the replay wrote nothing
        with pytest.raises(RequestConflictError):
            engine.execute_request("memory_add_facts", "p", {"facts": 2}, "request-1", lambda: {})

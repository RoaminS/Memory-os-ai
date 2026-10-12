#!/usr/bin/env python3
"""Read preserved 406 copy evidence; optional cache-only replay on a NEW copy.

No neural forward pass, downloads, queries or production service access. Never
accept the live store as input: require a completed owned qualification folder.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

import numpy as np

from memory_os_v4 import Config, MemoryV4
from memory_os_v4.memory import index_text
from memory_os_v4.ops.sqlite_backup import inventory
from memory_os_v4.sqlite_store import _json
from memory_os_v4.store import MemoryItem


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sealed(path):
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 64 * 1024**2:
        raise ValueError("Sealed owned evidence database required, <=64 MiB")
    if any(Path(str(path) + s).exists() and Path(str(path) + s).stat().st_size for s in ("-wal", "-journal")):
        raise ValueError("Uncheckpointed evidence refused; never ignore WAL")
    return sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)


def cache_key(profile, text):
    return hashlib.sha256(_json(profile).encode() + b"\0" + text.encode()).hexdigest()


def authority(con):
    return {k: v for k, v in inventory(con)["tables"].items() if k != "embedding_cache"}


def diagnose(evidence, output, reproduce=False):
    evidence, output = evidence.resolve(), output.resolve()
    if not (evidence / "copy-owner.json").is_file() or not (evidence / "report.json").is_file():
        raise ValueError("Completed private copy qualification evidence required")
    if output == evidence or evidence in output.parents:
        raise ValueError("Output must be outside preserved evidence")
    output.mkdir(parents=True, exist_ok=False)
    snapshot, after = evidence / "snapshot.sqlite3", evidence / "wheel-store/store.sqlite3"
    paths = [snapshot, after, evidence / "baseline.json", evidence / "wheel.json"]
    fingerprints = {p.name if p.parent == evidence else "wheel-store/" + p.name: digest(p) for p in paths}
    baseline = json.loads((evidence / "baseline.json").read_text())
    wheel = json.loads((evidence / "wheel.json").read_text())
    if baseline["version"] != "4.0.5" or wheel["version"] != "4.0.6":
        raise ValueError("Wrong preserved release comparison")
    con = sealed(snapshot)
    try:
        meta = json.loads(con.execute("SELECT value_json FROM store_metadata WHERE key='metadata'").fetchone()[0])
        items = [MemoryItem.from_dict(json.loads(r[0])) for r in con.execute(
            "SELECT v.record_json FROM items i JOIN item_versions v ON v.item_id=i.id AND v.revision=i.current_revision")]
        items = [i for i in items if not i.deleted]
        old = {key: blob for key, blob in con.execute("SELECT cache_key,vector FROM embedding_cache")}
        before_authority = authority(con)
    finally:
        con.close()
    cfg = Config(data_dir=str(output / "replay-store"), store_backend="sqlite_s1", work_state=True,
                 write_mode="read_only", embed_model=meta["embed_model"], device="mps")
    att = baseline["encoding_profile"]
    cfg.embed_dtype, cfg.embed_batch_size, cfg.embed_max_seq_length = att["dtype"], att["batch_size"], att["max_seq_length"]
    texts = [index_text(i, cfg) for i in items]
    if len(texts) != baseline["total"]:
        raise ValueError("This diagnostic requires the attested non-multivector corpus")
    common = dict(model=cfg.embed_model, dim=meta["dim"], max_seq_length=cfg.embed_max_seq_length,
                  batch_size=cfg.embed_batch_size, device=att["device"], namespace=cfg.sqlite_embedding_namespace,
                  attestation=att)
    if cfg.embed_dtype != "native":
        common["dtype"] = cfg.embed_dtype
    profiles = {v: dict(common, implementation=v) for v in ("4.0.5", "4.0.6")}
    keys = {v: [cache_key(p, t) for t in texts] for v, p in profiles.items()}
    if any(k not in old for k in keys["4.0.5"]):
        raise ValueError("Text/profile mismatch: cannot attribute misses to release alone")
    con = sealed(after)
    try:
        new = {key: blob for key, blob in con.execute("SELECT cache_key,vector FROM embedding_cache")}
    finally:
        con.close()
    a = np.stack([np.frombuffer(old[k], dtype="<f4") for k in keys["4.0.5"]]).astype("float64")
    b = np.stack([np.frombuffer(new[k], dtype="<f4") for k in keys["4.0.6"]]).astype("float64")
    cosine = np.sum(a*b, axis=1) / (np.linalg.norm(a, axis=1)*np.linalg.norm(b, axis=1))
    report = {"ok": True, "inference_calls": 0, "production_access": False,
              "snapshot_sha256": fingerprints["snapshot.sqlite3"],
              "encoding_profiles_equal": att == wheel["encoding_profile"],
              "requested_texts": len(texts), "unique_texts": len(set(texts)),
              "real_405_hits": sum(k in old for k in keys["4.0.5"]),
              "real_406_hits": sum(k in old for k in keys["4.0.6"]),
              "old_406_counter_artifact": len(texts) - len(set(keys["4.0.6"]) - old.keys()),
              "old_cache_rows_preserved": all(new.get(k) == v for k, v in old.items()),
              "added_cache_rows": len(new) - len(old), "added_vector_bytes": sum(len(v) for k, v in new.items() if k not in old),
              "database_growth_bytes": after.stat().st_size - snapshot.stat().st_size,
              "changed_unique_vectors": len({k for k, x, y in zip(keys["4.0.5"], a, b) if not np.array_equal(x, y)}),
              "max_abs_vector_delta": float(np.max(np.abs(a-b))), "min_cosine": float(np.min(cosine)),
              "all_top10_identical": all(baseline["search"][q]["ids"] == wheel["search"][q]["ids"] for q in baseline["search"]),
              "zero_encoding_406_criterion_met": False}
    if reproduce:
        # Stub ATTESTATION ONLY. Calling encode fails before any model loads.
        class NoInference:
            dim, device = meta["dim"], att["device"]
            def encoding_profile(self):
                return dict(att)
            def encode(self, texts):
                raise AssertionError("Unexpected corpus encoding; replay refused")
        import memory_os_v4.memory as module
        original = module._make_embedder
        module._make_embedder = lambda config: NoInference()
        store = Path(cfg.data_dir)
        store.mkdir(mode=0o700)
        shutil.copyfile(snapshot, store / "store.sqlite3")
        expected = {i.id: old[k] for i, k in zip(items, keys["4.0.5"])}
        try:
            with MemoryV4(config=cfg) as engine:
                engine.ensure_retrieval()
                ids, vecs = engine.vector_index.export()
                if any(vec.astype("<f4").tobytes() != expected[i] for i, vec in zip(ids, vecs)) or set(ids) != set(expected):
                    raise ValueError("Historical vectors changed")
                if authority(engine.store._connection) != before_authority:
                    raise ValueError("Authoritative table changed")
                report["copy_replay"] = {"corpus_encoding_calls": 0, "historical_vectors_byte_identical": True,
                                        "authority_identical": True, "hits": engine._embedding_cache.hits,
                                        "legacy_hits": engine._embedding_cache.legacy_hits,
                                        "misses": engine._embedding_cache.misses}
            with MemoryV4(config=cfg) as engine:
                engine.ensure_retrieval()
                if engine._embedding_cache.misses or authority(engine.store._connection) != before_authority:
                    raise ValueError("Warm reopen failed")
                report["copy_replay"]["warm_reopen_zero_encoding"] = True
        finally:
            module._make_embedder = original
    report["evidence_unchanged"] = fingerprints == {p.name if p.parent == evidence else "wheel-store/" + p.name: digest(p) for p in paths}
    if not report["evidence_unchanged"]:
        raise ValueError("Evidence changed concurrently; invalidate diagnosis")
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reproduce-on-copy", action="store_true")
    args = parser.parse_args()
    print(json.dumps(diagnose(args.evidence, args.output, args.reproduce_on_copy), indent=2))


if __name__ == "__main__":
    main()

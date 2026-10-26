"""Aggregate-only, immutable private snapshot analysis; never emits memory text."""

from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3


def analyze(path):
    con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        if con.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("Private snapshot integrity failed")
        sizes = {
            name: size
            for name, size in con.execute(
                "SELECT name,sum(pgsize) FROM dbstat GROUP BY name"
            )
        }
        counts = {
            name: con.execute('SELECT count(*) FROM "' + name + '"').fetchone()[0]
            for (name,) in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        changed = Counter()
        previous = {}
        access_only = semantic = first = access_bytes = 0
        excluded = {"hash", "access_count", "last_accessed"}
        by_day = Counter()
        for ident, raw, recorded in con.execute(
            "SELECT item_id,record_json,recorded_at FROM item_versions ORDER BY item_id,revision"
        ):
            obj = json.loads(raw)
            import datetime as dt

            by_day[
                dt.datetime.fromtimestamp(recorded, dt.timezone.utc).date().isoformat()
            ] += 1
            if ident not in previous:
                first += 1
            else:
                old = previous[ident]
                fields = {
                    k for k in old.keys() | obj.keys() if old.get(k) != obj.get(k)
                }
                changed.update(fields)
                if not fields - excluded:
                    access_only += 1
                    access_bytes += len(raw.encode())
                else:
                    semantic += 1
            previous[ident] = obj
        vectors = con.execute(
            "SELECT count(*), count(DISTINCT vector_sha256),sum(length(vector)) FROM embedding_cache"
        ).fetchone()
        meta = json.loads(
            con.execute(
                "SELECT value_json FROM store_metadata WHERE key='metadata'"
            ).fetchone()[0]
        )
        # Key reconstruction only: no model/engine, no encoding, no write.
        from memory_os_v4.config import Config
        from memory_os_v4.memory import index_text
        from memory_os_v4.store import MemoryItem
        from memory_os_v4.sqlite_store import _json

        cfg = Config(embed_model=meta["embed_model"])
        att = meta["embed_compute_profile"]
        profile = {
            "model": meta["embed_model"],
            "dim": meta["dim"],
            "max_seq_length": att["max_seq_length"],
            "batch_size": att["batch_size"],
            "device": att["device"],
            "cache_schema": 2,
            "namespace": cfg.sqlite_embedding_namespace,
            "attestation": att,
        }
        if att["dtype"] != "native":
            profile["dtype"] = att["dtype"]
        keys = {k for (k,) in con.execute("SELECT cache_key FROM embedding_cache")}
        matches = {}
        for label, p in [
            ("current", profile),
            (
                "legacy405",
                {
                    **{k: v for k, v in profile.items() if k != "cache_schema"},
                    "implementation": "4.0.5",
                },
            ),
            (
                "legacy406",
                {
                    **{k: v for k, v in profile.items() if k != "cache_schema"},
                    "implementation": "4.0.6",
                },
            ),
        ]:
            prefix = _json(p).encode() + b"\0"
            candidates = {
                hashlib.sha256(
                    prefix + index_text(MemoryItem.from_dict(obj), cfg).encode()
                ).hexdigest()
                for obj in previous.values()
            }
            matches[label] = len(keys & candidates)
        return {
            "bytes": path.stat().st_size,
            "counts": counts,
            "page_bytes_by_object": sizes,
            "versions": {
                "first": first,
                "semantic_updates": semantic,
                "access_only_updates": access_only,
                "access_only_json_bytes": access_bytes,
                "changed_field_counts": dict(changed),
                "recorded_versions_by_utc_day": dict(sorted(by_day.items())),
            },
            "cache": {
                "rows": vectors[0],
                "unique_vector_checksums": vectors[1],
                "vector_bytes": vectors[2],
                "full_item_key_matches": matches,
                "chunk_key_analysis": "full-item keys only; no model loaded",
            },
            "neural_calls": 0,
            "source_sql_writes": False,
        }
    finally:
        con.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--private-copy", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    root = Path(__file__).resolve().parents[1] / ".local-qualification"
    if not a.private_copy.resolve().is_relative_to(root.resolve()) or a.output.exists():
        p.error(
            "Only a private copy below this clone .local-qualification and fresh output"
        )
    import shutil

    if shutil.disk_usage(root).free < 3_000_000_000:
        raise RuntimeError("3 GB disk guard")
    before = hashlib.sha256(a.private_copy.read_bytes()).hexdigest()
    result = analyze(a.private_copy)
    assert hashlib.sha256(a.private_copy.read_bytes()).hexdigest() == before
    result["private_copy_sha256"] = before
    with a.output.open("x") as f:
        json.dump(result, f, indent=2)
    a.output.chmod(0o600)
    print(json.dumps({k: result[k] for k in ("bytes", "versions", "cache")}))


if __name__ == "__main__":
    main()

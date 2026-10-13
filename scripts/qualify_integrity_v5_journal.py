#!/usr/bin/env python3
"""Private hash-only B5 page50/10k gate; guarded outside-sandbox measurement."""

from pathlib import Path
import argparse
import hashlib
import json
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "v4/src"))
sys.path.insert(0, str(ROOT / "scripts"))
from qualify_405_test_isolation import resource_sample  # noqa: E402


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / ".local-qualification"):
        p.error("Private qualification output required")
    output.mkdir(parents=True, exist_ok=False)
    report = {
        "ok": False,
        "events": 10000,
        "page_size": 50,
        "samples": 100,
        "limit_p95_ms": 30,
        "neural_calls": 0,
        "production_modified": False,
    }
    try:
        report["resources"] = resource_sample(True)
        from memory_os_v4 import Config, MemoryV4
        from memory_os_v4.changes import listing, state
        from memory_os_v4.sqlite_store import _json

        with tempfile.TemporaryDirectory(prefix="journal-", dir=output) as temp:
            with MemoryV4(
                config=Config(
                    data_dir=temp,
                    store_backend="sqlite_s1",
                    embed_model="hash",
                    hash_dim=16,
                )
            ) as m:
                with m.store.transaction():
                    for i in range(1, 10001):
                        obj = {
                            "sequence": i,
                            "event_id": str(i),
                            "operation": "synthetic",
                            "project": "P",
                            "committed_at": time.time(),
                            "changed_by": {
                                "client_id": "synthetic",
                                "authenticated": False,
                            },
                            "reason": None,
                            "impacts": [],
                            "refs": [],
                            "audit_ids": [],
                            "no_op": True,
                        }
                        raw = _json(obj)
                        m.store.write(
                            "fixture_event",
                            "INSERT INTO change_events VALUES(?,?,?,?,?,?,?)",
                            (
                                i,
                                str(i),
                                "synthetic",
                                "project:P",
                                raw,
                                hashlib.sha256(raw.encode()).hexdigest(),
                                obj["committed_at"],
                            ),
                        )
                    st = state(m.store)
                    st["high"] = 10000
                    m.store.write(
                        "fixture_state",
                        "UPDATE store_metadata SET value_json=? WHERE key='change_journal'",
                        (_json(st),),
                    )
                cursor = None
                times = []
                seen = []
                for _ in range(100):
                    resource_sample(True)
                    start = time.perf_counter()
                    page = listing(m, "P", cursor, 50)
                    times.append((time.perf_counter() - start) * 1000)
                    assert len(page["events"]) == 50 and page["has_more"]
                    seen.extend(e["sequence"] for e in page["events"])
                    cursor = page["next_cursor"]
                assert seen == list(range(1, 5001))
                times.sort()
                report.update(
                    p50_ms=times[49],
                    p95_ms=times[94],
                    max_ms=times[-1],
                    integrity=m.store.check_integrity(),
                )
                report["ok"] = report["integrity"] and times[94] <= 30
    except Exception as exc:
        report["error"] = str(exc)
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

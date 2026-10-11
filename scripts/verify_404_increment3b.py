#!/usr/bin/env python3
"""Combine structural/TCP and exact token proofs; no release/production authorization."""

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source-report", required=True, type=Path)
    p.add_argument("--brief-report", required=True, type=Path)
    a = p.parse_args()
    source, brief = (
        json.loads(path.read_text()) for path in (a.source_report, a.brief_report)
    )
    assert (
        source["ok"] and source["tcp_qualified"] and source["l1a_transport_qualified"]
    )
    assert (
        brief["ok"] and brief["exact_tokens_qualified"] and len(brief["cases"]) == 400
    )
    for report in (source, brief):
        assert report["production_modified"] is False
        for path, digest in report["source_sha256"].items():
            assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == digest, (
                path
            )
    manifest = json.loads(
        (ROOT / "v4/tests/fixtures/brief_l1a/manifest_v1.json").read_text()
    )
    assert brief["scenarios_sha256"] == manifest["scenarios_sha256"]
    assert (
        brief["tokenizer"]["table_sha256"]
        == "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"
    )
    assert brief["tokenizer"]["implementation_version"] == "0.12.0"
    cases = {(r["id"], r["backend"], r["path"]) for r in brief["cases"]}
    assert cases == {
        (f"L1A-{i:03}", backend, path)
        for i in range(1, 101)
        for backend in ("jsonl", "sqlite_s1")
        for path in ("server", "relay")
    }
    assert all(
        r["ok"]
        and (
            not r["compact"] or (r["bytes"] <= 6000 and r["tokens_cl100k_base"] <= 1500)
        )
        for r in brief["cases"]
    )
    print(
        json.dumps(
            {
                "increment_qualified": True,
                "release_qualified": False,
                "production_modified": False,
                "scenarios_sha256": brief["scenarios_sha256"],
            }
        )
    )


if __name__ == "__main__":
    main()

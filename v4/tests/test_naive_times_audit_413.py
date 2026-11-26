"""4.1.3 decision D1-c: read-only audit of instants a pre-4.1.3 server may have stored from a naive date."""
import json
import os
from datetime import datetime, timezone

import pytest

from memory_os_v4 import Config, MemoryV4
from memory_os_v4.ops import cli
from memory_os_v4.ops.naive_times_audit import audit

PARIS_MIDNIGHT = datetime(2026, 11, 25, 23, tzinfo=timezone.utc).timestamp()   # 2026-11-26 00:00 Paris (old rule)
UTC_MIDNIGHT = datetime(2026, 11, 26, tzinfo=timezone.utc).timestamp()        # 2026-11-26 00:00 UTC (4.1.3 rule)


@pytest.fixture
def store(tmp_path):
    data = tmp_path / "store"
    with MemoryV4(config=Config(data_dir=str(data), store_backend="sqlite_s1", embed_model="hash", hash_dim=16)) as m:
        old = m.add_facts([{"text": "Secret synthetic decision A.", "event_time": PARIS_MIDNIGHT}], project="P")["added"][0]
        new = m.add_facts([{"text": "Secret synthetic decision B.", "event_time": UTC_MIDNIGHT}], project="P")["added"][0]
        text = m.add_facts([{"text": "Secret synthetic decision C on 2026-11-26."}], project="P")["added"][0]
        until = m.add_facts([{"text": "Secret synthetic decision D.", "event_time": UTC_MIDNIGHT + 3600,
                              "valid_until": PARIS_MIDNIGHT + 86400}], project="P")["added"][0]
        noon = m.add_facts([{"text": "Secret synthetic decision E.",
                             "event_time": UTC_MIDNIGHT + 12 * 3600}], project="P")["added"][0]
    return data, {"old": old, "new": new, "text": text, "until": until, "noon": noon}


def listing(directory):
    return sorted((p.name, p.stat().st_size) for p in directory.iterdir())


def test_paris_lists_exactly_the_local_midnights(store):
    data, ids = store
    before = listing(data)
    report = audit(data, "Europe/Paris")
    assert report["ok"] and report["rewritten"] == 0 and report["scanned"] >= 5
    found = {(c["id"], c["field"]) for c in report["candidates"]}
    assert found == {(ids["old"], "event_time"), (ids["old"], "valid_from"), (ids["until"], "valid_until")}
    first = next(c for c in report["candidates"] if c["id"] == ids["old"] and c["field"] == "event_time")
    assert first["naive_date"] == "2026-11-26" and first["difference_hours"] == 1.0
    assert first["meaning_since_4_1_3_utc"] == "2026-11-26T00:00:00+00:00"
    assert "Secret synthetic" not in json.dumps(report)            # identifiers only, never the memory text
    assert listing(data) == before                                 # read-only: nothing created or changed


def test_utc_server_has_nothing_to_review(store):
    data, _ = store
    report = audit(data, "UTC")
    assert report["ok"] and report["candidates"] == []


def test_read_only_volume_and_refusals(store, tmp_path):
    data, _ = store
    os.chmod(data, 0o555)
    try:
        assert audit(data, "Europe/Paris")["ok"]
    finally:
        os.chmod(data, 0o755)
    assert audit(data, "Mars/Olympus")["code"] == "zone_invalid"
    assert audit(tmp_path / "nowhere", "UTC")["code"] == "store_missing"


def test_cli_prints_the_report(store, monkeypatch, capsys):
    data, ids = store
    monkeypatch.setenv("MEMORY_V4_DIR", str(data))
    assert cli.main(["audit-naive-times", "--tz", "Europe/Paris"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["ok"] and {c["id"] for c in printed["candidates"]} == {ids["old"], ids["until"]}

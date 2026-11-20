"""4.0.9 health autonomy: explicit client corrections need no review; /ready probe tolerates the integrity audit."""
import hashlib
import json
import time

import pytest

from memory_os_v4 import Config, MemoryV4
from memory_os_v4.fact_updates import mutation_policy
from memory_os_v4.ops import health
from memory_os_v4.ops.settings import OpsSettings


def settings(root):
    return OpsSettings("http://127.0.0.1:8999/mcp/", root / "token", root / "store", root / "backups",
                       root / "key", root / "ops", "private.test")


def ok_http(calls):
    def fake(url, settings, **kw):
        calls.append(kw)
        return 200, json.dumps({"capabilities": {"policies": {"mutations": mutation_policy()}}}).encode()
    return fake


def fact(i, **kw):
    d = {"id": i, "kind": "fact", "project": "P", "source": "client", "text": i, "replaces": [],
         "valid_until": None, "invalidated_by": None, "deleted": False}
    d.update(kw)
    return d


TARGET = fact("old", valid_until=2.0, invalidated_by="new")


@pytest.mark.parametrize("successor,expected", [
    (fact("new", replaces=["old"]), True),
    (fact("new"), False),                                         # implicit invalidation
    (fact("new", replaces=["old"], project="Q"), False),          # cross-project
    (fact("new", replaces=["old"], source="consolidation"), False),
    (fact("new", replaces=["old"], deleted=True), False),
    (fact("new", replaces=["old"], valid_until=1.0), False),       # successor already expired
    (fact("new", replaces="old"), False),                         # replaces must be a list
    (fact("new", replaces=["old"], correction={"impacts": [{"target_id": "old", "successor_id": "new",
                                                             "mechanism": "single_identity"}]}), False),
    (fact("new", replaces=["old"], correction={"impacts": [{"target_id": "old", "successor_id": "new",
                                                             "mechanism": "explicit_replaces"}]}), True),
    (fact("new", replaces=["old", "a", "b", "c"]), False),        # bulk replacement
    (None, False),                                                # successor missing
])
def test_is_explicit_supersession(successor, expected):
    assert health.is_explicit_supersession(TARGET, successor) is expected
    assert health.is_explicit_supersession({**TARGET, "valid_until": None}, successor) is False
    assert health.is_explicit_supersession({**TARGET, "deleted": True}, successor) is False


def test_chain_of_explicit_corrections_needs_live_end():
    a = fact("a", valid_until=2.0, invalidated_by="b")
    b = fact("b", replaces=["a"], valid_until=3.0, invalidated_by="c")
    c = fact("c", replaces=["b"])
    records = {"a": a, "b": b, "c": c}
    assert health.explicit_chain_end("a", records)["id"] == "c"
    assert health.explicit_chain_end("a", {**records, "c": {**c, "deleted": True}}) is None
    assert health.explicit_chain_end("a", {"a": a, "b": {**b, "invalidated_by": "a"}}) is None  # cycle
    assert health.explicit_chain_end("a", {"a": a, "b": {**b, "invalidated_by": "zz"}}) is None  # implicit end


def test_jsonl_explicit_correction_is_info_not_anomaly_and_never_acknowledged(tmp_path, monkeypatch):
    s = settings(tmp_path)
    s.store_dir.mkdir()
    rows = [TARGET, fact("new", replaces=["old"]), fact("lone", valid_until=2.0, invalidated_by="missing")]
    (s.store_dir / "items.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    monkeypatch.setattr(health, "http_get", ok_http([]))
    report = health.report(s)
    assert report["superseded_explicit_new"] == ["old"]
    assert report["superseded_new"] == ["lone"]  # unexplained invalidation still requires review
    assert any("1 unacknowledged" in a for a in report["anomalies"])
    assert any("explicit client correction" in i for i in report["info"])
    # explicit corrections are reported, never silently written into the acknowledgement baseline
    assert json.loads((s.ops_dir / "known_supersessions.json").read_text())["ids"] == []


def test_sqlite_engine_replace_needs_no_review(tmp_path, monkeypatch):
    s = settings(tmp_path)
    with MemoryV4(config=Config(data_dir=str(s.store_dir), embed_model="hash", hash_dim=16,
                                store_backend="sqlite_s1")) as engine:
        a = engine.add_facts([{"text": "The relay port is 8765.", "entities": ["relay"]}], project="P")
        old = next(iter(a.get("ids") or [])) if a.get("ids") else engine.store.all(False)[0].id
        engine.add_facts([{"text": "The relay port is 8766.", "entities": ["relay"], "replaces": [old]}],
                         project="P", mode=None)
        assert engine.store.get(old).invalidated_by is not None
    monkeypatch.setattr(health, "http_get", ok_http([]))
    report = health.report(s)
    assert report["superseded_explicit_new"] == [old]
    assert report["superseded_new"] == []
    assert not any("unacknowledged" in a for a in report["anomalies"])


def test_probe_timeout_tolerates_slow_ready(tmp_path, monkeypatch):
    s = settings(tmp_path)
    s.store_dir.mkdir()
    (s.store_dir / "items.jsonl").write_text("")
    calls = []
    monkeypatch.setattr(health, "http_get", ok_http(calls))
    health.report(s)
    assert calls and all(kw.get("timeout") == health.PROBE_TIMEOUT_S >= 10 for kw in calls)
    monkeypatch.setattr(health, "http_get", lambda url, settings, **kw: (-1, b""))
    report = health.report(s)
    assert any(f"within {health.PROBE_TIMEOUT_S:.0f} s" in a for a in report["anomalies"])


def _write(s, rows):
    s.store_dir.mkdir(exist_ok=True)
    (s.store_dir / "items.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_bulk_explicit_corrections_within_24h_alert(tmp_path, monkeypatch):
    s = settings(tmp_path)
    now, rows = time.time(), []
    for k in range(health.EXPLICIT_DAILY_MAX + 1):
        rows += [fact(f"o{k}", valid_until=now, invalidated_by=f"n{k}"),
                 fact(f"n{k}", replaces=[f"o{k}"], created_at=now - 60)]
    _write(s, rows)
    monkeypatch.setattr(health, "http_get", ok_http([]))
    report = health.report(s)
    assert report["superseded_explicit_24h"] == health.EXPLICIT_DAILY_MAX + 1
    assert report["superseded_explicit_new"] == []
    assert len(report["superseded_new"]) == health.EXPLICIT_DAILY_MAX + 1
    assert any("bulk pattern" in a for a in report["anomalies"])


def test_old_explicit_corrections_do_not_count_as_bulk(tmp_path, monkeypatch):
    s = settings(tmp_path)
    old, rows = time.time() - 3 * 86400, []
    for k in range(health.EXPLICIT_DAILY_MAX + 5):
        rows += [fact(f"o{k}", valid_until=old, invalidated_by=f"n{k}"),
                 fact(f"n{k}", replaces=[f"o{k}"], created_at=old)]
    _write(s, rows)
    monkeypatch.setattr(health, "http_get", ok_http([]))
    report = health.report(s)
    assert report["superseded_explicit_24h"] == 0 and report["superseded_new"] == []
    assert not any("supersession" in a or "bulk" in a for a in report["anomalies"])


def test_poisoned_record_only_affects_itself(tmp_path, monkeypatch):
    s = settings(tmp_path)
    _write(s, [TARGET, fact("new", replaces=["old"], created_at=0),
               fact("bad", valid_until=2.0, invalidated_by=["unhashable"])])
    monkeypatch.setattr(health, "http_get", ok_http([]))
    report = health.report(s)
    assert report["superseded_explicit_new"] == ["old"]


def test_sqlite_store_untouched_by_report(tmp_path, monkeypatch):
    s = settings(tmp_path)
    with MemoryV4(config=Config(data_dir=str(s.store_dir), embed_model="hash", hash_dim=16,
                                store_backend="sqlite_s1")) as engine:
        engine.add_facts([{"text": "The relay port is 8765.", "entities": ["relay"]}], project="P")
        old = engine.store.all(False)[0].id
        engine.add_facts([{"text": "The relay port is 8766.", "entities": ["relay"], "replaces": [old]}],
                         project="P", mode=None)
    db = s.store_dir / "store.sqlite3"
    before = (hashlib.sha256(db.read_bytes()).hexdigest(), db.stat().st_mtime_ns)
    monkeypatch.setattr(health, "http_get", ok_http([]))
    health.report(s)
    assert (hashlib.sha256(db.read_bytes()).hexdigest(), db.stat().st_mtime_ns) == before
    wal = s.store_dir / "store.sqlite3-wal"  # SQLite opens a WAL database read-only with an empty -wal/-shm
    assert not wal.exists() or wal.stat().st_size == 0

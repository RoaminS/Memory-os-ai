"""4.1.3 single time policy: a naive date or date-time means UTC everywhere, whatever the server's time zone.

Before 4.1.3 explicit naive values (event_time, valid_from, valid_until, at_time) were read in the server's
LOCAL time zone while dates found in a fact's text and v3 timestamps were read in UTC (demo412, anomaly 6:
a same-day correction was refused or accepted depending on the host time zone).
"""
import time
from datetime import datetime, timezone

import pytest

from memory_os_v4 import Config, MemoryV4, server
from memory_os_v4.extract import _coerce_time
from memory_os_v4.migrate import _parse_v3_timestamp
from memory_os_v4.temporal import iso_to_timestamp, naive_time_notices, parse_absolute_dates

ZONES = ("UTC", "Europe/Paris", "America/New_York", "America/Los_Angeles", "Asia/Tokyo")
MIDNIGHT_UTC = datetime(2026, 11, 26, tzinfo=timezone.utc).timestamp()
TEN_UTC = datetime(2026, 11, 26, 10, tzinfo=timezone.utc).timestamp()


@pytest.fixture(params=ZONES)
def zone(request, monkeypatch):
    monkeypatch.setenv("TZ", request.param)
    time.tzset()
    yield request.param
    monkeypatch.undo()
    time.tzset()


@pytest.fixture
def memory(tmp_path, monkeypatch):
    with MemoryV4(config=Config(data_dir=str(tmp_path), store_backend="sqlite_s1", work_state=True,
                                embed_model="hash", hash_dim=16)) as m:
        monkeypatch.setattr(server, "_engine", m)
        yield m


def test_every_entry_point_reads_a_naive_value_in_utc(zone):
    for value, expected in (("2026-11-26", MIDNIGHT_UTC), ("2026-11-26T10:00", TEN_UTC),
                            ("2026-11-26T10:00:00", TEN_UTC), ("2026-11-26 10:00", TEN_UTC)):
        assert iso_to_timestamp(value) == expected, (zone, value)
        assert _coerce_time(value) == expected, (zone, value)
        assert server.parse_iso(value) == expected, (zone, value)
        assert _parse_v3_timestamp(value) == expected, (zone, value)
    assert parse_absolute_dates("Deployed on 2026-11-26.") == [MIDNIGHT_UTC]


def test_explicit_offsets_are_honoured_in_every_zone(zone):
    assert iso_to_timestamp("2026-11-26T10:00Z") == TEN_UTC
    assert iso_to_timestamp("2026-11-26T11:00+01:00") == TEN_UTC
    assert iso_to_timestamp("2026-11-26T02:00-08:00") == TEN_UTC
    assert server.parse_iso(TEN_UTC) == TEN_UTC and _coerce_time(TEN_UTC) == TEN_UTC
    assert server.parse_iso("") is None and _coerce_time("not a date") is None
    with pytest.raises(ValueError, match="invalid ISO timestamp"):
        server.parse_iso("not a date")


def test_only_a_naive_date_time_produces_a_notice():
    args = {"project": "P", "at_time": "2026-11-26T10:00", "facts": [
        {"text": "A.", "event_time": "2026-11-26"},                 # a date alone: midnight UTC, no notice
        {"text": "B.", "valid_from": "2026-11-26T10:00Z"},          # explicit offset: no notice
        {"text": "C.", "valid_until": "2026-11-26T10:00"},
        {"text": "D.", "event_time": "not a date"},                 # left to the handler
        {"text": "E.", "keywords": ["event_time"], "note": "2026-11-26T10:00"}]}
    notices = naive_time_notices(args)
    assert [(n["field"], n["value"]) for n in notices] == [("at_time", "2026-11-26T10:00"),
                                                           ("valid_until", "2026-11-26T10:00")]
    assert {n["code"] for n in notices} == {"naive_time_assumed_utc"}
    assert naive_time_notices({"facts": [{"event_time": 1764151200.0}]}) == []


def test_dispatch_reports_naive_date_times_without_altering_the_receipt(memory, zone):
    args = {"project": "P", "request_id": f"naive-{zone}",
            "facts": [{"text": "Synthetic decision.", "event_time": "2026-11-26T10:00"}]}
    first = server.dispatch("memory_add_facts", args)
    assert first["added"] and first["time_notices"][0]["code"] == "naive_time_assumed_utc"
    replay = server.dispatch("memory_add_facts", args)
    assert replay == first                       # same arguments, same receipt, same notice
    item = memory.store.get(first["added"][0])
    assert item.event_time == TEN_UTC
    dated = server.dispatch("memory_add_facts", {"project": "P", "facts": [
        {"text": "Another synthetic decision.", "event_time": "2026-11-26"}]})
    assert dated["added"] and "time_notices" not in dated
    assert memory.store.get(dated["added"][0]).event_time == MIDNIGHT_UTC


def _anomaly6(memory):
    """The demo412 anomaly 6 sequence: a fact dated 2026-11-26, then same-day replacements."""
    old = server.dispatch("memory_add_facts", {"project": "pg-demo", "facts": [
        {"text": "For pg-demo staging the team chose PostgreSQL 17.", "event_time": "2026-11-26"}]})["added"][0]
    explicit = server.dispatch("memory_add_facts", {"project": "pg-demo", "mode": "preview", "reason": "Plan change.",
                                                    "facts": [{"text": "Staging stays on PostgreSQL 16.",
                                                               "event_time": "2026-11-26", "replaces": [old]}]})
    from_text = server.dispatch("memory_add_facts", {"project": "pg-demo", "mode": "preview", "reason": "Plan change.",
                                                     "facts": [{"text": "Staging stays on PostgreSQL 16 after the "
                                                                "change of plan on 2026-11-26.", "replaces": [old]}]})
    next_day = server.dispatch("memory_add_facts", {"project": "pg-demo", "mode": "preview", "reason": "Plan change.",
                                                    "facts": [{"text": "Staging stays on PostgreSQL 16.",
                                                               "event_time": "2026-11-27", "replaces": [old]}]})
    return [(r.get("ok"), r.get("code")) for r in (explicit, from_text, next_day)]


def test_anomaly6_same_day_correction_has_the_same_outcome_in_every_zone(memory, zone):
    # Both the explicit date and the date found in the text now mean midnight UTC: a replacement dated the
    # same day as its target is refused (not strictly after) in EVERY zone; the next day is accepted.
    assert _anomaly6(memory) == [(False, "invalid_correction_time"), (False, "invalid_correction_time"), (True, None)]


def test_at_time_reads_a_naive_value_in_utc(memory, zone):
    server.dispatch("memory_add_facts", {"project": "P", "facts": [
        {"text": "Synthetic fact valid from ten.", "event_time": "2026-11-26T10:00Z"}]})
    found = server.dispatch("memory_search", {"project": "P", "query": "synthetic fact", "at_time": "2026-11-26T10:30"})
    assert found["time_notices"][0]["value"] == "2026-11-26T10:30"

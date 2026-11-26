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
from memory_os_v4.temporal import (NAIVE_TIME_NOTICE, begin_time_notices, end_time_notices, iso_to_timestamp,
                                   parse_absolute_dates, utc_date)

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


def test_only_an_accepted_naive_date_time_produces_a_notice():
    token = begin_time_notices()
    iso_to_timestamp("2026-11-26")              # a date alone: documented midnight UTC, no notice
    iso_to_timestamp("2026-11-26T10:00Z")       # explicit offset: no notice
    with pytest.raises(ValueError):
        iso_to_timestamp("2026-11-26T25:00")    # refused: never announced as read in UTC
    assert _coerce_time("tomorrow-ish") is None
    assert end_time_notices(token) == []
    token = begin_time_notices()
    assert _coerce_time(" 2026-11-26T10:00 ") == TEN_UTC   # blanks ignored on every entry point (as parse_iso)
    iso_to_timestamp("2026-11-26T11:00")
    notices = end_time_notices(token)
    assert notices == [NAIVE_TIME_NOTICE]       # one notice, the input value is never echoed
    assert "2026" not in str(notices)
    assert iso_to_timestamp("2026-11-26T11:00") == TEN_UTC + 3600   # outside a handler run: silent
    assert utc_date(2026, 11, 26) == MIDNIGHT_UTC


def _status(args, first):
    pre = server.dispatch("memory_request_status", {"operation": "memory_add_facts", "arguments": args})
    query = {"request_id": args["request_id"], **{k: pre[k] for k in
             ("scope", "operation", "raw_args_sha256", "store_instance_id")}}
    return server.dispatch("memory_request_status", query)


def test_notice_lives_in_the_receipt_replay_status_reorder_restart(tmp_path, monkeypatch, zone):
    config = Config(data_dir=str(tmp_path), store_backend="sqlite_s1", work_state=True, embed_model="hash", hash_dim=16)
    args = {"project": "P", "request_id": "naive-receipt",
            "facts": [{"text": "Synthetic decision.", "event_time": "2026-11-26T10:00"}]}
    with MemoryV4(config=config) as m:
        monkeypatch.setattr(server, "_engine", m)
        first = server.dispatch("memory_add_facts", args)
        assert first["added"] and first["time_notices"] == [NAIVE_TIME_NOTICE]
        assert m.store.get(first["added"][0]).event_time == TEN_UTC
        assert server.dispatch("memory_add_facts", args) == first                       # direct replay
        reordered = {"facts": args["facts"], "request_id": args["request_id"], "project": "P"}
        assert server.dispatch("memory_add_facts", reordered) == first                  # key order
        status = _status(args, first)
        assert status["status"] == "completed" and status["result"] == first            # what the relay replays
    with MemoryV4(config=config) as m:                                                  # restart
        monkeypatch.setattr(server, "_engine", m)
        assert server.dispatch("memory_add_facts", args) == first
        assert _status(args, first)["result"] == first
    dated = {"project": "P", "facts": [{"text": "Another synthetic decision.", "event_time": "2026-11-26"}]}
    with MemoryV4(config=config) as m:
        monkeypatch.setattr(server, "_engine", m)
        result = server.dispatch("memory_add_facts", dated)
        assert result["added"] and "time_notices" not in result
        assert m.store.get(result["added"][0]).event_time == MIDNIGHT_UTC


def test_refused_naive_value_gets_no_notice(memory):
    result = server.dispatch("memory_search", {"project": "P", "query": "x", "at_time": "2026-11-26T25:00"})
    assert result.get("ok") is False and "time_notices" not in result


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
        {"text": "Synthetic orchid fact valid from ten.", "event_time": "2026-11-26T10:00Z"}]})
    def ids(at):
        found = server.dispatch("memory_search", {"project": "P", "query": "synthetic orchid fact", "at_time": at})
        return found, [hit.get("id") or hit.get("item_id") for hit in found.get("results", found.get("hits", []))]
    before, before_ids = ids("2026-11-26T09:30")   # naive = 09:30 UTC in every zone: not yet valid
    after, after_ids = ids("2026-11-26T10:30")     # naive = 10:30 UTC in every zone: valid
    assert before_ids == [] and len(after_ids) == 1, (zone, before, after)
    assert before["time_notices"] == after["time_notices"] == [NAIVE_TIME_NOTICE]
    assert ids("2026-11-26T10:30+01:00")[1] == []  # explicit offset honoured: 09:30 UTC

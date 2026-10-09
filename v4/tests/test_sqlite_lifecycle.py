"""Smoke the measurement workflow on four elements and test fail-closed preflight."""

import json
import sqlite3

import pytest

from bench import sqlite_lifecycle
from bench.e2e.guard import ResourceStop
from memory_os_v4.sqlite_store import MIN_SQLITE

pytestmark = pytest.mark.skipif(sqlite3.sqlite_version_info < MIN_SQLITE, reason="SQLite runtime too old")


def test_small_hash_workflow_measures_zero_warm_and_rollback_encoding(tmp_path):
    # Four synthetic elements are a unit smoke, not the guarded 1k/10k campaign.
    result = sqlite_lifecycle.measure(tmp_path / "four", 4, 1, lambda: None)
    assert result["cold"]["encoder_texts"] == 4
    assert result["warm"][0]["encoder_texts"] == 0
    assert result["rollback"][0]["encoder_texts"] == 0


def test_unknown_resource_state_stops_before_any_workload(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sqlite_lifecycle, "ensure_priority", lambda _: None)

    def refused(*_):
        raise ResourceStop("Swap denied")

    monkeypatch.setattr(sqlite_lifecycle, "sample", refused)
    monkeypatch.setattr(sqlite_lifecycle, "measure", lambda *_: pytest.fail("workload must not run"))
    out = tmp_path / "blocked"
    assert sqlite_lifecycle.main(["--output", str(out)]) == 2
    assert json.loads(capsys.readouterr().out)["complete"] is False
    report = json.loads((out / "report.json").read_text())
    assert report["results"] == [] and report["blocked"] == "Swap denied"
    assert not (out / "1000").exists() and not (out / "10000").exists()

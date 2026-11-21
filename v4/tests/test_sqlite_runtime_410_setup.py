"""SETUP_4_1_0.md §3: the SQLite library loaded by THIS interpreter is verified before any apply.

Red by construction until ``ops/sqlite_runtime.py`` exists; the last two tests pin today's engine
floor, which the probe must reuse rather than redefine.
"""
from __future__ import annotations

import sqlite3
import sys
from types import SimpleNamespace

import pytest

from setup_fixture_410 import prepare, setup_context, tree


def runtime():
    from memory_os_v4.ops import sqlite_runtime
    return sqlite_runtime


class Connection:
    """Real in-memory connection whose answers can be degraded per statement."""

    def __init__(self, *, fail=(), options=None):
        self._real = sqlite3.connect(":memory:")
        self._fail, self._options = fail, options

    def execute(self, sql, *args):
        if any(marker in sql for marker in self._fail):
            raise sqlite3.OperationalError("no such function: json_extract")
        if self._options is not None and "compile_options" in sql.lower():
            return self._real.execute("SELECT value FROM (SELECT ? AS value" + " UNION ALL SELECT ?" * (
                len(self._options) - 1) + ")", self._options)
        return self._real.execute(sql, *args)

    def close(self):
        self._real.close()


def module(*, version=(3, 53, 4), fail=(), options=None, connect_error=None):
    def connect(target, *args, **kwargs):
        assert target == ":memory:", "the preview probe may only open an in-memory database"
        if connect_error:
            raise connect_error
        return Connection(fail=fail, options=options)
    return SimpleNamespace(sqlite_version_info=version, sqlite_version=".".join(map(str, version)),
                           connect=connect, Error=sqlite3.Error, OperationalError=sqlite3.OperationalError)


def test_current_interpreter_is_qualified_and_the_probe_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    before = tree(tmp_path)
    report = runtime().inspect()
    assert report["ok"] is True and report["code"] is None
    assert report["version"] == sqlite3.sqlite_version and report["minimum"] == "3.51.3"
    assert report["checks"] == {"loaded": True, "version": True, "json1": True, "wal_compiled": True}
    assert report["writes"] == "none"
    assert isinstance(report["fts5"], bool)  # informational only: the store uses no FTS table
    assert tree(tmp_path) == before


def test_minimum_is_the_engine_floor_not_a_second_constant():
    from memory_os_v4.sqlite_store import MIN_SQLITE
    assert runtime().MIN_VERSION == MIN_SQLITE == (3, 51, 3)


@pytest.mark.parametrize("version", [(3, 51, 2), (3, 43, 2), (3, 9, 0)])
def test_too_old_library_is_refused_with_its_code(version):
    report = runtime().inspect(module=module(version=version))
    assert report["ok"] is False and report["code"] == "sqlite_runtime_too_old"
    assert report["version"] == ".".join(map(str, version))
    assert report["checks"]["loaded"] is True and report["checks"]["version"] is False


@pytest.mark.parametrize("version", [(3, 51, 3), (3, 52, 0), (4, 0, 0)])
def test_floor_and_newer_versions_are_accepted_by_integer_triplet(version):
    assert runtime().inspect(module=module(version=version))["ok"] is True


def test_missing_json_functions_are_refused():
    report = runtime().inspect(module=module(fail=("json_extract",)))
    assert (report["ok"], report["code"]) == (False, "sqlite_json_missing")
    assert report["checks"]["json1"] is False


def test_library_built_without_wal_is_refused():
    report = runtime().inspect(module=module(options=("ENABLE_FTS5", "OMIT_WAL", "THREADSAFE=1")))
    assert (report["ok"], report["code"]) == (False, "sqlite_wal_unavailable")
    assert report["checks"]["wal_compiled"] is False


def test_unloadable_library_is_refused_not_raised(monkeypatch):
    monkeypatch.setitem(sys.modules, "sqlite3", None)  # ``import sqlite3`` now raises ImportError
    report = runtime().inspect()
    assert (report["ok"], report["code"]) == (False, "sqlite_runtime_unavailable")
    assert report["checks"]["loaded"] is False and report["version"] is None


def test_connection_failure_is_unavailable():
    report = runtime().inspect(module=module(connect_error=sqlite3.OperationalError("unable to open")))
    assert (report["ok"], report["code"]) == (False, "sqlite_runtime_unavailable")


def test_refusal_priority_is_version_then_json_then_wal():
    report = runtime().inspect(module=module(version=(3, 40, 0), fail=("json_extract",), options=("OMIT_WAL",)))
    assert report["code"] == "sqlite_runtime_too_old"
    report = runtime().inspect(module=module(fail=("json_extract",), options=("OMIT_WAL",)))
    assert report["code"] == "sqlite_json_missing"


def test_functional_wal_probe_runs_only_inside_the_given_private_directory(tmp_path):
    private = tmp_path / "journal"
    private.mkdir(mode=0o700)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    before = tree(outside)
    report = runtime().wal_probe(private)
    assert report["ok"] is True and report["journal_mode"] == "wal" and report["code"] is None
    assert list(private.iterdir()) == []  # the probe database and its -wal/-shm are removed
    assert tree(outside) == before


def test_functional_wal_probe_refuses_when_wal_is_not_obtained(tmp_path):
    class Refusing:
        def execute(self, sql, *args):
            return SimpleNamespace(fetchone=lambda: ("delete",))

        def close(self):
            pass

    fake = SimpleNamespace(connect=lambda *a, **k: Refusing(), Error=sqlite3.Error)
    report = runtime().wal_probe(tmp_path, module=fake)
    assert (report["ok"], report["code"]) == (False, "sqlite_wal_unavailable")


@pytest.mark.parametrize("profile", ["lexical", "semantic"])
@pytest.mark.parametrize("code", ["sqlite_runtime_unavailable", "sqlite_runtime_too_old",
                                  "sqlite_json_missing", "sqlite_wal_unavailable"])
def test_quickstart_refuses_an_unfit_sqlite_before_any_write(tmp_path, monkeypatch, profile, code):
    from memory_os_v4.ops import quickstart as qs
    c = setup_context(tmp_path, monkeypatch)
    bad = {"ok": False, "code": code, "version": "3.40.0", "minimum": "3.51.3",
           "checks": {"loaded": True, "version": False, "json1": True, "wal_compiled": True},
           "fts5": True, "writes": "none"}
    monkeypatch.setattr(runtime(), "inspect", lambda **kw: dict(bad))
    before = tree(c.root)
    plan = prepare(c, profile=profile)
    assert plan["ok"] is False and code in plan["blocker_codes"]
    assert plan["sqlite_runtime"]["code"] == code
    with pytest.raises(qs.QuickstartError, match="Preflight refused"):
        qs.apply_install(plan, c.settings, home=c.home, run=c.system)
    assert tree(c.root) == before
    assert set(c.system.verbs()) <= {"print"}


def test_jsonl_backend_reports_the_sqlite_runtime_without_blocking(tmp_path, monkeypatch):
    c = setup_context(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime(), "inspect", lambda **kw: {
        "ok": False, "code": "sqlite_runtime_too_old", "version": "3.40.0", "minimum": "3.51.3",
        "checks": {"loaded": True, "version": False, "json1": True, "wal_compiled": True},
        "fts5": True, "writes": "none"})
    plan = prepare(c, profile="semantic", backend="jsonl")
    assert plan["ok"] is True and plan["sqlite_runtime"]["code"] == "sqlite_runtime_too_old"
    assert "sqlite_runtime_too_old" not in plan["blocker_codes"]


def test_apply_runs_the_functional_wal_probe_before_token_and_service(tmp_path, monkeypatch):
    from memory_os_v4.ops import quickstart as qs
    c = setup_context(tmp_path, monkeypatch)
    plan = prepare(c, profile="lexical")
    monkeypatch.setattr(runtime(), "wal_probe", lambda directory, **kw: {
        "ok": False, "code": "sqlite_wal_unavailable", "journal_mode": "delete"})
    result = qs.apply_install(plan, c.settings, home=c.home, run=c.system)
    assert result["ok"] is False and result["code"] == "sqlite_wal_unavailable"
    assert not c.settings.token_file.exists() and not c.settings.store_dir.exists()
    assert "bootstrap" not in c.system.verbs()


def test_doctor_reports_the_sqlite_runtime(monkeypatch):
    from memory_os_v4.ops import doctor
    check = doctor._sqlite_runtime()
    assert (check.area, check.status) == ("sqlite-runtime", "PASS") and sqlite3.sqlite_version in check.detail
    monkeypatch.setattr(runtime(), "inspect", lambda **kw: {
        "ok": False, "code": "sqlite_runtime_too_old", "version": "3.40.0", "minimum": "3.51.3",
        "checks": {}, "fts5": False, "writes": "none"})
    check = doctor._sqlite_runtime()
    assert check.status == "FAIL" and "sqlite_runtime_too_old" in check.detail and "3.51.3" in check.detail


# ---- green today: the engine floor the probe must reuse ----

def test_engine_floor_is_3_51_3():
    from memory_os_v4.sqlite_store import MIN_SQLITE
    assert MIN_SQLITE == (3, 51, 3)


def test_qualification_interpreter_meets_what_the_store_uses():
    con = sqlite3.connect(":memory:")
    try:
        assert sqlite3.sqlite_version_info >= (3, 51, 3)
        assert con.execute("SELECT json_extract('{\"a\": 7}', '$.a')").fetchone()[0] == 7
        assert "OMIT_WAL" not in {row[0] for row in con.execute("PRAGMA compile_options")}
    finally:
        con.close()

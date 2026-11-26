"""4.1.1 lot L2 (contract §3): SQLite routes (a) and (i), both read-back values, actionable refusals.

No network and no ``uv`` execution: the standalone route is a pure description of what the user
would launch. The floor itself (3.51.3, WAL only) is unchanged and is not re-tested here beyond
its boundaries.
"""
from __future__ import annotations

import socket
import subprocess

import pytest

from memory_os_v4.ops import quickstart as qs
from memory_os_v4.ops import sqlite_backup, sqlite_runtime
from memory_os_v4.sqlite_store import MIN_SQLITE
from setup_fixture_410 import install, prepare, setup_context, tree
from test_cell_411 import RETAINED, as_cell, lock_everything

DURABLE = "/Users/someone/.memory-os/venv/bin/python"
CHECKS = {"loaded": True, "version": True, "json1": True, "wal_compiled": True}


def inspected(version, *, code=None) -> dict:
    """An ``inspect`` report for a simulated loaded library."""
    ok = code is None and tuple(int(p) for p in version.split(".")) >= MIN_SQLITE
    if not ok and code is None:
        code = "sqlite_runtime_too_old"
    return {"ok": ok, "code": code, "version": version, "minimum": "3.51.3",
            "checks": {**CHECKS, "version": code != "sqlite_runtime_too_old"}, "fts5": True, "writes": "none"}


class Library:
    """A fake sqlite3 module with a chosen version: the real ``inspect`` runs on it."""

    def __init__(self, version):
        import sqlite3
        self.sqlite_version_info, self._real = version, sqlite3

    def connect(self, target):
        return self._real.connect(target)


@pytest.fixture(autouse=True)
def nothing_is_executed(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("L2 starts no process (uv included) and opens no socket")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


@pytest.fixture
def c(tmp_path, monkeypatch):
    return setup_context(tmp_path, monkeypatch)


# ---- the floor: below, equal, above ----

def test_floor_is_unchanged_and_shared():
    assert MIN_SQLITE == (3, 51, 3) and sqlite_runtime.MIN_VERSION is MIN_SQLITE
    assert sqlite_backup.MIN_SQLITE is MIN_SQLITE


@pytest.mark.parametrize("version,ok", [((3, 51, 2), False), ((3, 50, 4), False), ((3, 45, 1), False),
                                        ((3, 51, 3), True), ((3, 51, 4), True), ((3, 53, 1), True), ((4, 0, 0), True)])
def test_route_follows_the_value_read_back(version, ok):
    report = sqlite_runtime.inspect(module=Library(version))
    result = sqlite_runtime.attestation(report, python=DURABLE, cell_id="macos-arm64-py312", built_by="other")
    assert report["ok"] is ok and result["ok"] is ok
    assert result["route"] == ("a" if ok else "none")
    assert result["interpreter"]["version"] == ".".join(str(part) for part in version)
    assert result["interpreter"]["ok"] is ok and result["minimum"] == "3.51.3"
    assert result["code"] == (None if ok else "sqlite_runtime_too_old")
    assert (result["message"] is None) is ok and (result["remedy"] is None) is ok
    assert result["journal_modes"] == "WAL only (no DELETE fallback)"


# ---- route (a): any durable interpreter, whatever its provider ----

@pytest.mark.parametrize("python", [
    "/opt/homebrew/opt/python@3.11/bin/python3.11", "/usr/local/bin/python3.12", DURABLE,
    "/home/u/.local/share/uv/python/cpython-3.11.18-linux-aarch64-gnu/bin/python3.11", "/opt/conda/bin/python"])
@pytest.mark.parametrize("built_by,route", [("other", "a"), ("python-build-standalone", "i")])
def test_a_durable_conformant_interpreter_is_accepted_whatever_its_provider(python, built_by, route):
    result = sqlite_runtime.attestation(inspected("3.53.1"), python=python, built_by=built_by)
    assert result["ok"] is True and result["route"] == route and result["durable"] is True
    assert result["provider"] == built_by and result["code"] is None


@pytest.mark.parametrize("built_by", ["other", "python-build-standalone"])
def test_the_provider_never_rescues_an_old_library(built_by):
    result = sqlite_runtime.attestation(inspected("3.50.4"), python=DURABLE, cell_id="macos-arm64-py312",
                                        built_by=built_by)
    assert result["ok"] is False and result["route"] == "none" and result["code"] == "sqlite_runtime_too_old"


def test_provider_of_the_running_interpreter_is_one_of_the_two_labels():
    assert sqlite_runtime.provider() in {"other", "python-build-standalone"}


# ---- ephemeral interpreters ----

@pytest.mark.parametrize("python", ["/Users/u/.cache/uv/archive-v0/abc/bin/python", "/tmp/pip-build-env-x/bin/python",
                                    "/Users/u/.local/share/uvx/env/bin/python", "/var/cache/uv/x/bin/python",
                                    "/Users/u/Library/Caches/uv/environments-v2/x/bin/python"])
def test_an_ephemeral_interpreter_is_refused_even_with_a_conformant_library(python):
    assert sqlite_runtime.is_ephemeral(python) is True
    result = sqlite_runtime.attestation(inspected("3.53.4"), python=python, built_by="python-build-standalone")
    assert result["ok"] is False and result["route"] == "none" and result["durable"] is False
    assert result["code"] == "runtime_unqualified" and result["interpreter"]["ok"] is True


@pytest.mark.parametrize("python", [DURABLE, "/Users/u/.local/share/uv/python/cpython-3.12.16-macos-aarch64-none/bin/python3.12"])
def test_a_uv_managed_python_or_a_venv_is_durable(python):
    assert sqlite_runtime.is_ephemeral(python) is False


def test_quickstart_refuses_an_ephemeral_interpreter(c):
    ephemeral = c.home / ".cache/uv/archive-v0/abc/bin/python"
    ephemeral.parent.mkdir(parents=True)
    ephemeral.write_text("# synthetic executable, never executed")
    ephemeral.chmod(0o700)
    before = tree(c.root)
    plan = qs.prepare(c.settings, home=c.home, python=str(ephemeral), clients=("cursor",), run=c.system,
                      profile="lexical")
    assert plan["ok"] is False and plan["blocker_codes"] == ["runtime_unqualified"]
    assert "no uvx ephemeral runtime" in plan["blockers"][0]
    assert plan["sqlite_attestation"]["route"] == "none" and plan["sqlite_attestation"]["durable"] is False
    assert tree(c.root) == before


# ---- route (i): described, never launched ----

def test_uv_minimum_is_0_13_9():
    assert sqlite_runtime.UV_MINIMUM == (0, 13, 9)


@pytest.mark.parametrize("text,version,ok,code", [
    ("uv 0.13.9 (abcdef 2026-11-17)", "0.13.9", True, None), ("uv 0.13.9\n", "0.13.9", True, None),
    ("uv 0.13.10", "0.13.10", True, None), ("uv 0.14.0", "0.14.0", True, None), ("uv 1.0.0", "1.0.0", True, None),
    ("uv 0.13.8", "0.13.8", False, "uv_too_old"), ("uv 0.10.8 (Homebrew 2026-03-03)", "0.10.8", False, "uv_too_old"),
    ("uv 0.9.30", "0.9.30", False, "uv_too_old"),
    ("uv 0.13.9rc1", None, False, "uv_unreadable"), ("uv 0.13", None, False, "uv_unreadable"),
    ("0.13.9", None, False, "uv_unreadable"), ("uvx 0.13.9", None, False, "uv_unreadable"),
    ("", None, False, "uv_unreadable"), (None, None, False, "uv_unreadable")])
def test_uv_version_is_parsed_from_text_given_by_the_caller(text, version, ok, code):
    assert sqlite_runtime.uv_version(text) == {"version": version, "minimum": "0.13.9", "ok": ok, "code": code}


@pytest.mark.parametrize("cell_id,version,megabytes", [("macos-arm64-py312", "3.12.16", 25.4),
                                                       ("macos-arm64-py313", "3.13.18", 25.6),
                                                       ("ubuntu2404-arm64-py311", "3.11.18", 49.5),
                                                       ("ubuntu2404-x86_64-py311", "3.11.18", 49.2)])
def test_standalone_plan_names_the_acquisition_and_the_exact_commands(cell_id, version, megabytes, monkeypatch):
    monkeypatch.setenv("HOME", "/Users/someone")
    request = sqlite_runtime.STANDALONE_CPYTHON[cell_id]["request"]
    plan = sqlite_runtime.standalone_plan(cell_id)
    assert plan == sqlite_runtime.standalone_plan(cell_id)  # pure
    assert plan["route"] == "i" and plan["download"] is True and plan["implicit_download"] is False
    assert plan["uv_minimum"] == "0.13.9" and plan["venv"] == "~/.memory-os/venv"
    assert plan["acquisitions"] == [{
        "object": "CPython " + version, "version": version, "megabytes": megabytes,
        "source": "python-build-standalone 20261110 (github.com/astral-sh/python-build-standalone), via uv",
        "destination": "uv managed Python directory"}]
    # L3 (Codex reservations on L2): the request is the complete uv key, the build is named, and no
    # argument list keeps a "~" that only a shell would expand.
    assert request.startswith(f"cpython-{version}-") and plan["build"] == "20261110"
    assert plan["commands"] == [
        ["uv", "python", "install", request],
        ["uv", "venv", "--python", request, "--managed-python", "--no-python-downloads",
         "/Users/someone/.memory-os/venv"],
        ["/Users/someone/.memory-os/venv/bin/python", "-c", "import sqlite3; print(sqlite3.sqlite_version)"]]
    assert plan["command_lines"][0] == f"uv python install {request}"
    assert plan["sqlite_declared"] == "3.53.1" and plan["sqlite_attested"] is None  # declared is not attested
    assert not any("uvx" in part for command in plan["commands"] for part in command)
    assert sum("install" in command for command in plan["commands"]) == 1  # one download step, the first


def test_standalone_plan_takes_the_venv_and_the_uv_binary_of_the_caller():
    plan = sqlite_runtime.standalone_plan("macos-arm64-py312", venv="/srv/mos/venv", uv="/opt/uv-0.13.9/uv")
    assert plan["commands"][0][0] == plan["commands"][1][0] == "/opt/uv-0.13.9/uv"
    assert plan["commands"][1][-1] == "/srv/mos/venv" and plan["python"] == "/srv/mos/venv/bin/python"


@pytest.mark.parametrize("cell_id", ["macos-arm64-py311", "ubuntu2404-arm64-py312", "macos-x86_64-py312",
                                     "macos-x86_64-py311", "ubuntu2404-x86_64-py312", None])
def test_no_standalone_cpython_is_named_outside_the_four_cells(cell_id):
    assert sqlite_runtime.standalone_plan(cell_id) is None
    assert set(sqlite_runtime.STANDALONE_CPYTHON) == {"macos-arm64-py312", "macos-arm64-py313",
                                                      "ubuntu2404-arm64-py311", "ubuntu2404-x86_64-py311"}


def test_each_standalone_request_names_the_architecture_and_libc_of_its_cell():
    """The complete uv key of each cell: never the CPython of the other architecture, never musl."""
    assert {cell_id: record["request"] for cell_id, record in sqlite_runtime.STANDALONE_CPYTHON.items()} == {
        "macos-arm64-py312": "cpython-3.12.16-macos-aarch64-none",
        "macos-arm64-py313": "cpython-3.13.18-macos-aarch64-none",
        "ubuntu2404-arm64-py311": "cpython-3.11.18-linux-aarch64-gnu",
        "ubuntu2404-x86_64-py311": "cpython-3.11.18-linux-x86_64-gnu"}
    # megabytes: the install_only.tar.gz asset of release 20261110 (49,244,720 bytes for linux x86_64).
    assert round(49_244_720 / 1e6, 1) == sqlite_runtime.STANDALONE_CPYTHON["ubuntu2404-x86_64-py311"]["megabytes"]
    assert round(49_469_868 / 1e6, 1) == sqlite_runtime.STANDALONE_CPYTHON["ubuntu2404-arm64-py311"]["megabytes"]


# ---- actionable refusal ----

@pytest.mark.parametrize("cell_id,version", [("macos-arm64-py312", "3.12.16"), ("macos-arm64-py313", "3.13.18"),
                                             ("ubuntu2404-arm64-py311", "3.11.18"), ("ubuntu2404-x86_64-py311", "3.11.18")])
def test_refusal_names_the_uv_commands_and_says_it_is_a_download(cell_id, version, monkeypatch):
    monkeypatch.setenv("HOME", "/Users/someone")
    request = sqlite_runtime.STANDALONE_CPYTHON[cell_id]["request"]
    result = sqlite_runtime.attestation(inspected("3.50.4"), python=DURABLE, cell_id=cell_id, built_by="other")
    message = result["message"]
    assert message.startswith("sqlite_runtime_too_old: SQLite loaded by this interpreter is unfit "
                              "(version 3.50.4, minimum 3.51.3); nothing written")
    assert f"uv python install {request} && uv venv --python {request} --managed-python --no-python-downloads " \
           "/Users/someone/.memory-os/venv" in message
    assert "a DOWNLOAD you launch yourself" in message and f"CPython {version}" in message
    assert "python-build-standalone 20261110" in message and "uv >= 0.13.9" in message
    assert "no implicit download" in message and "no other journal mode" in message
    assert "Route (a)" in message and "Route (i)" in message and "re-preview" in message
    assert "declared 3.53.1, not yet attested" in message and "uvx" not in message
    assert result["remedy"] == sqlite_runtime.standalone_plan(cell_id)


@pytest.mark.parametrize("cell_id", ["macos-arm64-py311", None])
def test_refusal_without_a_named_standalone_offers_route_a_only(cell_id):
    result = sqlite_runtime.attestation(inspected("3.45.1"), python=DURABLE, cell_id=cell_id, built_by="other")
    assert result["remedy"] is None and "Route (a)" in result["message"] and "Route (i)" not in result["message"]
    assert "uv python install" not in result["message"] and "re-preview" in result["message"]
    assert ("no standalone CPython is named for this cell" in result["message"]) is (cell_id is not None)


@pytest.mark.parametrize("code", ["sqlite_runtime_unavailable", "sqlite_json_missing", "sqlite_wal_unavailable"])
def test_other_unfit_codes_keep_their_code_in_the_message(code):
    result = sqlite_runtime.attestation(inspected("3.53.1", code=code), python=DURABLE,
                                        cell_id="macos-arm64-py312", built_by="other")
    assert result["ok"] is False and result["code"] == code and result["message"].startswith(code + ": ")


# ---- the server value ----

@pytest.mark.parametrize("value,expected", [
    ("3.53.4", {"version": "3.53.4", "ok": True, "state": "attested", "code": None}),
    ("3.51.3", {"version": "3.51.3", "ok": True, "state": "attested", "code": None}),
    ("3.51.2", {"version": "3.51.2", "ok": False, "state": "too_old", "code": "sqlite_runtime_too_old"}),
    ("3.50.4", {"version": "3.50.4", "ok": False, "state": "too_old", "code": "sqlite_runtime_too_old"}),
    (None, {"version": None, "ok": False, "state": "unattested", "code": "sqlite_runtime_unattested"}),
    ("3.53", {"version": None, "ok": False, "state": "unattested", "code": "sqlite_runtime_unattested"}),
    ((3, 53, 4), {"version": None, "ok": False, "state": "unattested", "code": "sqlite_runtime_unattested"})])
def test_server_attestation(value, expected):
    assert sqlite_runtime.server_attestation(value) == expected


def test_before_the_service_runs_the_server_value_is_absent_not_assumed():
    result = sqlite_runtime.attestation(inspected("3.53.4"), python=DURABLE, built_by="other")
    assert result["server"] == {"version": None, "ok": None, "state": "not_running", "code": None}


# ---- quickstart: plan and apply carry the attestation ----

def test_plan_carries_the_value_read_back_by_this_interpreter(c):
    import sqlite3
    plan = prepare(c, profile="lexical")
    attested = plan["sqlite_attestation"]
    assert plan["ok"] is True and attested["ok"] is True and attested["route"] in {"a", "i"}
    assert attested["interpreter"]["version"] == sqlite3.sqlite_version == plan["sqlite_runtime"]["version"]
    assert attested["interpreter"]["target_python"] == c.python
    assert attested["server"]["state"] == "not_running" and attested["message"] is None


@pytest.mark.parametrize("cell_id", ["macos-arm64-py312", "macos-arm64-py313", "ubuntu2404-arm64-py311",
                                     "ubuntu2404-x86_64-py311"])
def test_quickstart_refusal_is_actionable_on_a_cell_with_a_standalone_route(c, monkeypatch, cell_id):
    as_cell(monkeypatch, RETAINED[cell_id]())
    lock_everything(monkeypatch)
    monkeypatch.setattr(sqlite_runtime, "inspect", lambda **kw: inspected("3.50.4"))
    before = tree(c.root)
    plan = prepare(c, profile="lexical")
    assert plan["ok"] is False and plan["blocker_codes"] == ["sqlite_runtime_too_old"]
    version = sqlite_runtime.STANDALONE_CPYTHON[cell_id]["python"]
    assert f"uv python install cpython-{version}-" in plan["blockers"][0] and "DOWNLOAD" in plan["blockers"][0]
    assert plan["sqlite_attestation"]["route"] == "none"
    assert plan["sqlite_attestation"]["remedy"]["acquisitions"][0]["version"] == version
    with pytest.raises(qs.QuickstartError, match="Preflight refused"):
        qs.apply_install(plan, c.settings, home=c.home, run=c.system)
    assert tree(c.root) == before and set(c.system.verbs()) <= {"print"}
    assert not any("uv" in str(call) for call in c.system.verbs())


def test_quickstart_refusal_on_the_reference_cell_names_no_download(c, monkeypatch):
    monkeypatch.setattr(sqlite_runtime, "inspect", lambda **kw: inspected("3.50.4"))
    plan = prepare(c, profile="semantic")
    assert plan["blocker_codes"] == ["sqlite_runtime_too_old"]
    assert "uv python install" not in plan["blockers"][0] and "Route (a)" in plan["blockers"][0]


def test_jsonl_backend_reports_the_attestation_without_blocking(c, monkeypatch):
    monkeypatch.setattr(sqlite_runtime, "inspect", lambda **kw: inspected("3.40.0"))
    plan = prepare(c, profile="semantic", backend="jsonl")
    assert plan["ok"] is True and plan["sqlite_attestation"]["route"] == "none"


def test_apply_records_both_values_side_by_side(c):
    import sqlite3
    c.ready["capabilities"]["store"]["sqlite_version"] = "3.53.4"
    result = install(c, profile="lexical")
    assert result["ok"] is True, result
    attested = result["sqlite_attestation"]
    assert attested["interpreter"]["version"] == sqlite3.sqlite_version
    assert attested["server"] == {"version": "3.53.4", "ok": True, "state": "attested", "code": None}


@pytest.mark.parametrize("value,code", [("3.50.4", "sqlite_runtime_too_old"), (None, "sqlite_runtime_unattested"),
                                        ("3.51.2", "sqlite_runtime_too_old")])
def test_the_real_readiness_check_refuses_a_server_below_the_floor(c, monkeypatch, value, code):
    """The server value is read back by the real ``_ready`` (the fixture replaces it elsewhere)."""
    import json
    from memory_os_v4.config import LEXICAL_MARKER, LEXICAL_POLICY
    from memory_os_v4.ops import auth
    store = {"backend": "sqlite_s1", "instance_id": "synthetic", "schema_version": 8}
    if value is not None:
        store["sqlite_version"] = value
    body = {"commit": c.identity["commit"], "version": c.identity["version"], "ready": True, "reason": "ready",
            "capabilities": {"store": store, "work_state": True, "retrieval_profile": LEXICAL_MARKER,
                             "policies": {"ranking_policy": LEXICAL_POLICY, "bm25_profile": "scoped_literal",
                                          "lexical_guard_k": 3},
                             "encoding": {"state": "absent_by_profile", "profile": None},
                             "brief": {"tokenizer": {"ready": True}}}}
    monkeypatch.setattr(auth, "http_get", lambda *a, **k: (200, json.dumps(body)))
    with pytest.raises(qs.QuickstartError) as refusal:
        REAL_READY(c.settings, c.identity, backend="sqlite", profile="lexical")
    outcome = refusal.value
    assert isinstance(outcome, qs.QuickstartError) and outcome.code == code
    if value is not None:
        assert value in str(outcome) and "3.51.3" in str(outcome)
    # L3: the value read back travels with the refusal, so that the failure report keeps it.
    assert outcome.sqlite_server == sqlite_runtime.server_attestation(value)


REAL_READY = qs._ready  # captured at import, before any fixture replaces it

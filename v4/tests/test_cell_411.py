"""4.1.1 lot L1: cell detection, guards lifted for the retained cells only, Linux resource guard.

Every cell is simulated by injection (``cell.detect`` arguments); nothing here forces the whole
process to another platform, starts a service or touches the network.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from memory_os_v4.ops import cell, provision, qualified_runtime, storage
from memory_os_v4.ops import quickstart as qs
from memory_os_v4.ops.settings import OpsSettings
from setup_fixture_410 import prepare, setup_context, tree

UBUNTU_2404 = ('PRETTY_NAME="Ubuntu 24.04.3 LTS"\nNAME="Ubuntu"\nVERSION_ID="24.04"\n'
               'VERSION="24.04.3 LTS (Noble Numbat)"\nID=ubuntu\nID_LIKE=debian\n')
UBUNTU_2204 = 'NAME="Ubuntu"\nVERSION_ID="22.04"\nID=ubuntu\n'
DEBIAN_12 = "ID=debian\nVERSION_ID='12'\n"
KERNEL = "6.8.0-88-generic\n"
WSL_KERNEL = "5.15.167.4-microsoft-standard-WSL2\n"
GB = 1024 ** 3


def simulate(**overrides) -> dict:
    """A cell report from injected facts only: no file of the host, no process, no host SQLite."""
    facts = {"sys_platform": "darwin", "machine": "arm64", "python_version": (3, 11, 14),
             "implementation": "CPython", "sqlite_version": "3.53.4", "os_release": None, "kernel_release": None,
             "environ": {}, "which": lambda name: None, "exists": lambda path: False}
    facts.update(overrides)
    return cell.detect(**facts)


def linux(python=(3, 11, 18), **overrides) -> dict:
    facts = {"sys_platform": "linux", "machine": "aarch64", "python_version": python, "sqlite_version": "3.53.1",
             "os_release": UBUNTU_2404, "kernel_release": KERNEL, "environ": {"XDG_RUNTIME_DIR": "/run/user/1000"},
             "which": lambda name: "/usr/bin/" + name,
             "exists": lambda path: str(path) in {"/run/systemd/system", "/run/user/1000/systemd"}}
    facts.update(overrides)
    return simulate(**facts)


RETAINED = {
    "macos-arm64-py311": lambda: simulate(),
    "macos-arm64-py312": lambda: simulate(python_version=(3, 12, 16), sqlite_version="3.53.1"),
    "macos-arm64-py313": lambda: simulate(python_version=(3, 13, 18), sqlite_version="3.53.1"),
    "ubuntu2404-arm64-py311": lambda: linux(),
}
NOT_RETAINED = {
    "ubuntu-24.04-arm64-py312": lambda: linux(python=(3, 12, 16)),        # the fallback: named, not retained
    "ubuntu-24.04-arm64-py313": lambda: linux(python=(3, 13, 18)),
    "ubuntu-22.04-arm64-py311": lambda: linux(os_release=UBUNTU_2204),
    "debian-12-arm64-py311": lambda: linux(os_release=DEBIAN_12),
    "ubuntu-24.04-x86_64-py311": lambda: linux(machine="x86_64"),
    "ubuntu-2404-arm64-py311": lambda: linux(os_release="ID=ubuntu\nVERSION_ID=2404\n"),
    "ubuntu-2.4.04-arm64-py311": lambda: linux(os_release="ID=ubuntu\nVERSION_ID=2.4.04\n"),
    "ubuntu-24.04.1-arm64-py311": lambda: linux(os_release='ID=ubuntu\nVERSION_ID="24.04.1"\n'),
    "ubuntu-24.10-arm64-py311": lambda: linux(os_release='ID=ubuntu\nVERSION_ID="24.10"\n'),
    "ubuntu-arm64-py311": lambda: linux(os_release="ID=ubuntu\n"),                      # no VERSION_ID
    "Ubuntu-24.04-arm64-py311": lambda: linux(os_release="ID=Ubuntu\nVERSION_ID=24.04\n"),  # verbatim, not folded
    "linux-24.04-arm64-py311": lambda: linux(os_release='ID_LIKE=ubuntu\nVERSION_ID="24.04"\n'),  # ID_LIKE alone
    "linuxmint-24.04-arm64-py311": lambda: linux(os_release="ID=linuxmint\nID_LIKE=ubuntu\nVERSION_ID=24.04\n"),
    "linux-arm64-py311": lambda: linux(os_release=None),                  # no os-release: never guessed
    "macos-x86_64-py311": lambda: simulate(machine="x86_64"),
    "macos-arm64-py310": lambda: simulate(python_version=(3, 10, 14)),
    "macos-arm64-py314": lambda: simulate(python_version=(3, 14, 0)),
    "win32-amd64-py311": lambda: simulate(sys_platform="win32", machine="AMD64"),
}


# ---- detection ----

@pytest.mark.parametrize("cell_id", sorted(RETAINED))
def test_the_four_retained_cells_are_identified(cell_id):
    report = RETAINED[cell_id]()
    assert report["cell_id"] == report["label"] == cell_id
    assert report["retained"] is True and report["refusal"] is None
    assert report["arch"] == "arm64" and report["sqlite_ok"] is True
    assert report["writes"] == "none" and report["network"] == "none"


def test_retained_cells_are_exactly_the_contract_ones():
    assert cell.RETAINED_CELLS == ("macos-arm64-py311", "macos-arm64-py312", "macos-arm64-py313",
                                   "ubuntu2404-arm64-py311")
    assert cell.SEMANTIC_CELLS == ("macos-arm64-py311",)
    assert set(RETAINED) == set(cell.RETAINED_CELLS)


@pytest.mark.parametrize("label", sorted(NOT_RETAINED))
def test_every_other_combination_is_named_but_not_retained(label):
    report = NOT_RETAINED[label]()
    assert report["label"] == label
    assert report["cell_id"] is None and report["retained"] is False
    assert label in report["refusal"]


@pytest.mark.parametrize("machine", ["arm64", "aarch64", "ARM64", "AArch64"])
def test_arm_architecture_names_are_normalized(machine):
    assert simulate(machine=machine)["arch"] == "arm64"
    assert linux(machine=machine)["cell_id"] == "ubuntu2404-arm64-py311"


def test_report_fields_of_a_linux_cell():
    report = linux()
    assert report["os"] == "linux" and report["sys_platform"] == "linux" and report["machine"] == "aarch64"
    assert report["python"] == "3.11" and report["python_full"] == "3.11.18"
    assert report["distribution"] == {"id": "ubuntu", "version_id": "24.04", "conflicts": []}
    assert report["wsl"] is False
    assert report["service_manager"] == {
        "kind": "systemd-user", "available": True, "attestation": "presence only",
        "evidence": {"systemctl": "/usr/bin/systemctl", "system_booted": True, "user_manager_dir": True}}
    assert report["sqlite_version"] == "3.53.1" and report["sqlite_minimum"] == "3.51.3"


def test_report_fields_of_a_macos_cell():
    report = simulate(exists=lambda path: str(path) == "/bin/launchctl")
    assert report["os"] == "darwin" and report["distribution"] is None and report["wsl"] is False
    assert report["service_manager"] == {"kind": "launchd", "available": True, "attestation": "presence only",
                                         "evidence": {"launchctl": "/bin/launchctl"}}
    assert simulate()["service_manager"]["available"] is False  # launchctl absent: said, not assumed


@pytest.mark.parametrize("missing", ["systemctl", "system_booted", "user_manager_dir"])
def test_systemd_user_needs_every_presence_evidence(missing):
    present = {"/run/systemd/system", "/run/user/1000/systemd"}
    if missing == "system_booted":
        present.discard("/run/systemd/system")
    if missing == "user_manager_dir":
        present.discard("/run/user/1000/systemd")
    report = linux(which=(lambda name: None) if missing == "systemctl" else (lambda name: "/usr/bin/" + name),
                   exists=lambda path: str(path) in present)
    assert report["service_manager"]["kind"] == "systemd-user"
    assert report["service_manager"]["available"] is False
    assert report["cell_id"] == "ubuntu2404-arm64-py311"  # the manager is reported, the cell is not renamed


@pytest.mark.parametrize("facts", [
    {"kernel_release": WSL_KERNEL},
    {"kernel_release": "4.4.0-19041-Microsoft"},
    {"environ": {"WSL_DISTRO_NAME": "Ubuntu-24.04"}},
    {"environ": {"WSL_INTEROP": "/run/WSL/8_interop"}},
])
def test_wsl_is_detected_and_refused_even_on_an_otherwise_retained_combination(facts):
    report = linux(**facts)
    assert report["wsl"] is True and report["label"] == "ubuntu2404-arm64-py311"
    assert report["cell_id"] is None and report["retained"] is False and "WSL" in report["refusal"]
    refusal = cell.acceptance(report, profile="lexical")
    assert refusal["ok"] is False and refusal["reason"] == "cell_not_retained" and "WSL" in refusal["message"]


def test_wsl_markers_are_ignored_outside_linux():
    assert simulate(kernel_release=WSL_KERNEL, environ={"WSL_DISTRO_NAME": "x"})["wsl"] is False


def test_another_python_implementation_is_refused():
    report = simulate(implementation="PyPy")
    assert report["cell_id"] is None and "CPython" in report["refusal"]


@pytest.mark.parametrize("text,expected", [
    (UBUNTU_2404, {"id": "ubuntu", "version_id": "24.04", "conflicts": []}),
    (DEBIAN_12, {"id": "debian", "version_id": "12", "conflicts": []}),
    ("# comment\n\nID=ubuntu\nVERSION_ID=24.04\nID=ubuntu\n", {"id": "ubuntu", "version_id": "24.04", "conflicts": []}),
    ("ID=Ubuntu\nVERSION_ID=24.04\n", {"id": "Ubuntu", "version_id": "24.04", "conflicts": []}),
    ("ID=ubuntu\nVERSION_ID=24.04\nID=other\n", {"id": None, "version_id": "24.04", "conflicts": ["ID"]}),
    ('ID=ubuntu\nVERSION_ID="24.04"\nVERSION_ID="22.04"\n', {"id": "ubuntu", "version_id": None,
                                                              "conflicts": ["VERSION_ID"]}),
    ("ID=a\nID=b\nVERSION_ID=1\nVERSION_ID=2\nNAME=x\nNAME=y\n", {"id": None, "version_id": None,
                                                                 "conflicts": ["ID", "VERSION_ID"]}),
    ('VERSION_ID="24.04\n', {"id": None, "version_id": '"24.04', "conflicts": []}),
    ("NAME=x\n", {"id": None, "version_id": None, "conflicts": []}),
    ("", {"id": None, "version_id": None, "conflicts": []}),
    (None, {"id": None, "version_id": None, "conflicts": []}),
])
def test_os_release_parsing(text, expected):
    assert cell.parse_os_release(text) == expected


@pytest.mark.parametrize("text", ['ID=ubuntu\nVERSION_ID="24.04"\n', "ID=ubuntu\nVERSION_ID=24.04\n",
                                  "ID='ubuntu'\nVERSION_ID='24.04'\nID=ubuntu\n", UBUNTU_2404])
def test_the_linux_cell_needs_exactly_ubuntu_and_24_04(text):
    report = linux(os_release=text)
    assert report["cell_id"] == report["label"] == "ubuntu2404-arm64-py311" and report["retained"] is True


@pytest.mark.parametrize("text,keys", [
    ("ID=ubuntu\nVERSION_ID=24.04\nID=debian\n", "ID"),
    ("ID=ubuntu\nVERSION_ID=24.04\nVERSION_ID=24.10\n", "VERSION_ID"),
    ("ID=debian\nID=ubuntu\nVERSION_ID=22.04\nVERSION_ID=24.04\n", "ID, VERSION_ID"),
])
def test_contradictory_os_release_keys_refuse_the_cell(text, keys):
    report = linux(os_release=text)
    assert report["cell_id"] is None and report["retained"] is False
    assert report["label"] not in cell.RETAINED_CELLS
    assert f"repeats {keys} with different values" in report["refusal"] and "cannot be attested" in report["refusal"]
    verdict = cell.acceptance(report, profile="lexical", lock=lambda *a, **k: {"catalog": "x"})
    assert verdict["ok"] is False and verdict["reason"] == "cell_not_retained"


def test_an_unmatched_label_never_spells_a_retained_identifier():
    for label, build in NOT_RETAINED.items():
        assert build()["label"] == label and label not in cell.RETAINED_CELLS


def test_the_decision_is_an_explicit_fact_table_for_every_retained_cell():
    assert tuple(cell._CELL_FACTS) == cell.RETAINED_CELLS
    assert cell._CELL_FACTS["ubuntu2404-arm64-py311"] == {
        "os": "linux", "distribution_id": "ubuntu", "version_id": "24.04", "python": (3, 11)}
    for minor in (11, 12, 13):
        assert cell._CELL_FACTS[f"macos-arm64-py3{minor}"] == {"os": "darwin", "python": (3, minor)}
    assert simulate(python_version=(4, 11, 0))["cell_id"] is None  # py411 is not 3.11
    assert simulate(python_version=(31, 1, 0))["cell_id"] is None  # nor would a glued "311" be


@pytest.mark.parametrize("version,ok", [("3.53.4", True), ("3.51.3", True), ("3.51.2", False), ("3.50.4", False),
                                        ("3.45.1", False), (None, False), ("garbage", False)])
def test_sqlite_floor_is_reported_from_the_loaded_value(version, ok):
    report = simulate(sqlite_version=version)
    assert report["sqlite_version"] == version and report["sqlite_ok"] is ok
    assert report["sqlite_minimum"] == "3.51.3"
    assert report["cell_id"] == "macos-arm64-py311"  # the floor is enforced by sqlite_runtime, not by the name


def test_sqlite_minimum_is_the_engine_floor_not_a_second_constant():
    from memory_os_v4.sqlite_store import MIN_SQLITE
    assert simulate()["sqlite_minimum"] == ".".join(str(part) for part in MIN_SQLITE)


def test_detection_is_deterministic_and_serializable():
    for build in (*RETAINED.values(), *NOT_RETAINED.values()):
        first, second = build(), build()
        assert first == second
        assert json.loads(json.dumps(first, sort_keys=True)) == first


def test_os_release_and_kernel_are_read_from_files_when_not_injected(tmp_path, monkeypatch):
    release, kernel = tmp_path / "os-release", tmp_path / "osrelease"
    release.write_text(UBUNTU_2404)
    kernel.write_text(KERNEL)
    monkeypatch.setattr(cell, "OS_RELEASE_PATHS", (tmp_path / "absent", release))
    monkeypatch.setattr(cell, "KERNEL_RELEASE_PATH", kernel)
    facts = {"sys_platform": "linux", "machine": "aarch64", "python_version": (3, 11, 18), "environ": {},
             "which": lambda name: None, "exists": lambda path: False}
    assert cell.detect(**facts)["cell_id"] == "ubuntu2404-arm64-py311"
    kernel.write_text(WSL_KERNEL)
    assert cell.detect(**facts)["wsl"] is True
    monkeypatch.setattr(cell, "OS_RELEASE_PATHS", (tmp_path / "absent",))
    assert cell.detect(**facts)["label"] == "linux-arm64-py311"
    release.write_text("ID=ubuntu\nVERSION_ID=2404\n")
    monkeypatch.setattr(cell, "OS_RELEASE_PATHS", (release,))
    assert cell.detect(**facts)["cell_id"] is None


def test_real_detection_starts_no_process_opens_no_socket_and_writes_nothing(tmp_path, monkeypatch):
    import socket
    import subprocess

    def forbidden(*args, **kwargs):
        raise AssertionError("cell detection must not start a process or open a socket")
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.chdir(tmp_path)
    report = cell.detect()
    assert list(tmp_path.iterdir()) == []
    assert report["python"] == f"{sys.version_info[0]}.{sys.version_info[1]}"
    assert report["sqlite_version"] == sqlite3.sqlite_version
    assert report["sys_platform"] == sys.platform
    assert report == cell.detect()


# ---- locks and acceptance ----

def test_only_the_reference_cell_embeds_locks_today():
    assert set(qualified_runtime.CELL_LOCKS) == {("semantic", "macos-arm64-py311"), ("lexical", "macos-arm64-py311")}
    for profile in ("semantic", "lexical"):
        lock = qualified_runtime.cell_lock("macos-arm64-py311", profile=profile)
        assert lock["catalog"] == str(qualified_runtime.CATALOGS[profile])
        assert lock["constraints"] == str(qualified_runtime.CONSTRAINTS[profile])
        assert "macos-arm64-py311" in lock["profile_id"]
    for cell_id in ("macos-arm64-py312", "macos-arm64-py313", "ubuntu2404-arm64-py311", "ubuntu2404-arm64-py312"):
        assert qualified_runtime.cell_lock(cell_id, profile="lexical") is None
        assert qualified_runtime.cell_lock(cell_id, profile="semantic") is None


def test_a_lock_counts_only_if_both_files_exist_and_name_the_cell(tmp_path, monkeypatch):
    catalog, constraints = tmp_path / "catalog.json", tmp_path / "constraints.txt"
    key = ("lexical", "ubuntu2404-arm64-py311")
    monkeypatch.setattr(qualified_runtime, "CELL_LOCKS", {**qualified_runtime.CELL_LOCKS, key: (catalog, constraints)})
    assert qualified_runtime.cell_lock(key[1], profile="lexical") is None             # declared, files absent
    document = {"schema_version": 1, "profile_id": "lexical-ubuntu2404-arm64-py311-v1", "versions": {"a": "1"}}
    catalog.write_text(json.dumps(document))
    assert qualified_runtime.cell_lock(key[1], profile="lexical") is None             # constraints absent
    constraints.write_text("a==1\n")
    assert qualified_runtime.cell_lock(key[1], profile="lexical")["profile_id"] == document["profile_id"]
    catalog.write_text(json.dumps({**document, "profile_id": "lexical-macos-arm64-py311-v1"}))
    assert qualified_runtime.cell_lock(key[1], profile="lexical") is None             # the lock of another cell
    catalog.write_text(json.dumps({**document, "versions": {}}))
    assert qualified_runtime.cell_lock(key[1], profile="lexical") is None
    catalog.write_text("not json")
    assert qualified_runtime.cell_lock(key[1], profile="lexical") is None


@pytest.mark.parametrize("profile", ["lexical", "semantic"])
def test_the_reference_cell_is_accepted_for_both_profiles(profile):
    verdict = cell.acceptance(RETAINED["macos-arm64-py311"](), profile=profile)
    assert verdict["ok"] is True and verdict["code"] is None and verdict["message"] is None
    assert verdict["cell_id"] == "macos-arm64-py311" and Path(verdict["lock"]["catalog"]).is_file()


@pytest.mark.parametrize("cell_id", ["macos-arm64-py312", "macos-arm64-py313", "ubuntu2404-arm64-py311"])
def test_a_retained_cell_without_embedded_lock_is_refused_without_false_promise(cell_id):
    verdict = cell.acceptance(RETAINED[cell_id](), profile="lexical")
    assert verdict["ok"] is False and verdict["code"] == "runtime_unqualified"
    assert verdict["reason"] == "cell_lock_missing" and verdict["lock"] is None
    assert f"no qualified lock embedded for cell {cell_id}" in verdict["message"]
    assert "supported" not in verdict["message"].lower()


@pytest.mark.parametrize("cell_id", ["macos-arm64-py312", "macos-arm64-py313", "ubuntu2404-arm64-py311"])
def test_the_semantic_profile_stays_on_the_reference_cell_even_with_a_lock(cell_id):
    def every_lock(found, *, profile):
        return {"catalog": "x", "constraints": "y", "profile_id": f"{profile}-{found}-v1"}
    report = RETAINED[cell_id]()
    verdict = cell.acceptance(report, profile="semantic", lock=every_lock)
    assert verdict["ok"] is False and verdict["reason"] == "profile_not_qualified_on_cell"
    assert "macos-arm64-py311" in verdict["message"] and "--profile lexical" in verdict["message"]
    assert cell.acceptance(report, profile="lexical", lock=every_lock)["ok"] is True


@pytest.mark.parametrize("label", sorted(NOT_RETAINED))
def test_a_non_retained_cell_is_refused_whatever_the_lock(label):
    def every_lock(found, *, profile):
        raise AssertionError("no lock lookup for a cell that is not retained")
    for profile in ("lexical", "semantic"):
        verdict = cell.acceptance(NOT_RETAINED[label](), profile=profile, lock=every_lock)
        assert verdict["ok"] is False and verdict["code"] == "runtime_unqualified"
        assert verdict["reason"] == "cell_not_retained" and label in verdict["message"]
        assert "remain manual" in verdict["message"]


# ---- quickstart: the guard follows the cell ----

@pytest.fixture
def c(tmp_path, monkeypatch):
    return setup_context(tmp_path, monkeypatch)


def as_cell(monkeypatch, report):
    """Quickstart and provision see this cell; the rest of the process is left alone."""
    monkeypatch.setattr(cell, "detect", lambda **kwargs: dict(report))


def lock_everything(monkeypatch):
    monkeypatch.setattr(qualified_runtime, "cell_lock", lambda cell_id, *, profile: {
        "catalog": "synthetic", "constraints": "synthetic", "profile_id": f"{profile}-{cell_id}-v1"})


@pytest.mark.parametrize("profile", ["lexical", "semantic"])
def test_quickstart_accepts_the_reference_cell_and_reports_it(c, monkeypatch, profile):
    as_cell(monkeypatch, RETAINED["macos-arm64-py311"]())
    plan = prepare(c, profile=profile)
    assert plan["ok"] is True, plan["blockers"]
    assert plan["cell"]["cell_id"] == "macos-arm64-py311"
    assert plan["cell_acceptance"]["ok"] is True


@pytest.mark.parametrize("cell_id", ["macos-arm64-py312", "macos-arm64-py313", "ubuntu2404-arm64-py311"])
def test_quickstart_refuses_a_retained_cell_whose_lock_is_not_embedded(c, monkeypatch, cell_id):
    as_cell(monkeypatch, RETAINED[cell_id]())
    before = tree(c.root)
    plan = prepare(c, profile="lexical")
    assert plan["ok"] is False and plan["blocker_codes"] == ["runtime_unqualified"]
    assert f"no qualified lock embedded for cell {cell_id}" in plan["blockers"][0]
    assert plan["cell"]["cell_id"] == cell_id and plan["cell_acceptance"]["reason"] == "cell_lock_missing"
    with pytest.raises(qs.QuickstartError):
        qs.apply_install(plan, c.settings, home=c.home, run=c.system, download_approved=True)
    assert tree(c.root) == before


@pytest.mark.parametrize("cell_id", ["macos-arm64-py312", "macos-arm64-py313"])
def test_the_lock_is_the_only_gate_of_a_retained_macos_cell(c, monkeypatch, cell_id):
    """What L5 will unlock: with a lock, the lexical profile passes and the semantic one is still refused."""
    as_cell(monkeypatch, RETAINED[cell_id]())
    lock_everything(monkeypatch)
    plan = prepare(c, profile="lexical")
    assert plan["ok"] is True, plan["blockers"]
    plan = prepare(c, profile="semantic")
    assert plan["ok"] is False and plan["blocker_codes"] == ["runtime_unqualified"]
    assert plan["cell_acceptance"]["reason"] == "profile_not_qualified_on_cell"


@pytest.mark.parametrize("label", sorted(NOT_RETAINED))
def test_quickstart_refuses_every_non_retained_cell(c, monkeypatch, label):
    as_cell(monkeypatch, NOT_RETAINED[label]())
    lock_everything(monkeypatch)
    before = tree(c.root)
    plan = prepare(c, profile="lexical")
    assert plan["ok"] is False and "runtime_unqualified" in plan["blocker_codes"]
    assert any(label in blocker and "remain manual" in blocker for blocker in plan["blockers"])
    assert tree(c.root) == before


def test_quickstart_refuses_wsl(c, monkeypatch):
    as_cell(monkeypatch, linux(kernel_release=WSL_KERNEL))
    lock_everything(monkeypatch)
    plan = prepare(c, profile="lexical")
    assert plan["ok"] is False and any("WSL" in blocker for blocker in plan["blockers"])


def test_quickstart_keeps_the_sqlite_floor_on_a_retained_cell(c, monkeypatch):
    as_cell(monkeypatch, RETAINED["macos-arm64-py312"]())
    lock_everything(monkeypatch)
    monkeypatch.setattr(qs.sqlite_runtime, "inspect", lambda **kw: {
        "ok": False, "code": "sqlite_runtime_too_old", "version": "3.50.4", "minimum": "3.51.3"})
    plan = prepare(c, profile="lexical")
    assert plan["ok"] is False and plan["blocker_codes"] == ["sqlite_runtime_too_old"]


# ---- Linux resource guard ----

def meminfo(tmp_path, monkeypatch, *, total_kb=4 * 1024 ** 2, free_kb=4 * 1024 ** 2, text=None):
    path = tmp_path / "meminfo"
    path.write_text(text if text is not None else
                    f"MemTotal:       16000000 kB\nSwapCached:            0 kB\nSwapTotal:      {total_kb} kB\n"
                    f"SwapFree:       {free_kb} kB\nDirty:                 0 kB\n")
    monkeypatch.setattr(storage, "PROC_MEMINFO", path)
    return path


def linux_guard(monkeypatch, *, report=None, free=20_000_000_000, locked=True):
    """The real provision guard on a simulated Linux cell; any process start is a failure."""
    as_cell(monkeypatch, report or linux())
    if locked:
        lock_everything(monkeypatch)
    seen = SimpleNamespace(statvfs=[])

    def free_bytes(path):
        seen.statvfs.append(Path(path))
        return free

    def forbidden(*args, **kwargs):
        raise AssertionError("the Linux guard starts no process and does not use shutil.disk_usage")
    monkeypatch.setattr(provision, "sys_platform", lambda: "linux")
    monkeypatch.setattr(storage, "statvfs_free_bytes", free_bytes)
    monkeypatch.setattr(provision.subprocess, "run", forbidden)
    monkeypatch.setattr(provision.shutil, "disk_usage", forbidden)
    return seen


def test_linux_swap_used_bytes_reads_proc_meminfo(tmp_path, monkeypatch):
    meminfo(tmp_path, monkeypatch, total_kb=4_194_304, free_kb=3_145_728)
    assert storage.linux_swap_used_bytes() == 1_048_576 * 1024
    meminfo(tmp_path, monkeypatch, total_kb=0, free_kb=0)  # no swap configured: zero used, attested
    assert storage.linux_swap_used_bytes() == 0


@pytest.mark.parametrize("text", ["", "SwapTotal:  100 kB\n", "SwapFree:  100 kB\n", "SwapTotal: x kB\nSwapFree: 1 kB\n",
                                  "SwapTotal:        100 kB\nSwapFree:        200 kB\n"])
def test_linux_swap_that_cannot_be_attested_is_refused(tmp_path, monkeypatch, text):
    meminfo(tmp_path, monkeypatch, text=text)
    with pytest.raises(RuntimeError, match="Swap cannot be attested"):
        storage.linux_swap_used_bytes()


def test_statvfs_free_bytes_counts_blocks_available_to_the_user(tmp_path, monkeypatch):
    fake = SimpleNamespace(f_bavail=10, f_frsize=4096, f_bfree=99)
    assert storage.statvfs_free_bytes(tmp_path, statvfs=lambda path: fake) == 40_960
    real = os.statvfs(tmp_path)  # the real call, on this host: same unit as shutil.disk_usage
    assert abs(storage.statvfs_free_bytes(tmp_path) - real.f_bavail * real.f_frsize) < 1024 ** 3


def test_linux_guard_passes_on_the_retained_cell_with_its_lock(tmp_path, monkeypatch):
    meminfo(tmp_path, monkeypatch)
    seen = linux_guard(monkeypatch)
    provision.guard(tmp_path / "not" / "yet" / "created")
    assert seen.statvfs == [tmp_path]  # nearest existing parent, measured through os.statvfs


@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_linux_disk_guard_uses_the_same_minimum(tmp_path, monkeypatch, offset):
    monkeypatch.setenv("MEMORY_V4_MIN_FREE_BYTES", "4500000000")
    minimum = provision.min_free_bytes()
    assert minimum == 4_500_000_000
    meminfo(tmp_path, monkeypatch)
    linux_guard(monkeypatch, free=minimum + offset)
    if offset < 0:
        with pytest.raises(provision.ProvisionError, match=f"Disk guard: .* effective minimum {minimum} bytes"):
            provision.guard(tmp_path)
    else:
        provision.guard(tmp_path)


def test_linux_default_disk_minimum_is_five_gigabytes(tmp_path, monkeypatch):
    monkeypatch.delenv("MEMORY_V4_MIN_FREE_BYTES", raising=False)
    meminfo(tmp_path, monkeypatch)
    linux_guard(monkeypatch, free=4_999_999_999)
    with pytest.raises(provision.ProvisionError, match="effective minimum 5000000000 bytes"):
        provision.guard(tmp_path)


@pytest.mark.parametrize("used_kb,refused", [(8_300_781, False), (8_300_782, True), (12_000_000, True)])
def test_linux_swap_guard_uses_the_same_threshold(tmp_path, monkeypatch, used_kb, refused):
    total = 16_000_000
    meminfo(tmp_path, monkeypatch, total_kb=total, free_kb=total - used_kb)
    linux_guard(monkeypatch)
    assert (used_kb * 1024 > 8_500_000_000) is refused
    if refused:
        with pytest.raises(provision.ProvisionError, match="Swap guard: more than 8.5 GB"):
            provision.guard(tmp_path)
    else:
        provision.guard(tmp_path)


def test_linux_guard_refuses_when_swap_cannot_be_measured(tmp_path, monkeypatch):
    linux_guard(monkeypatch)
    monkeypatch.setattr(storage, "PROC_MEMINFO", tmp_path / "absent")
    with pytest.raises(provision.ProvisionError, match="Swap measurement unavailable"):
        provision.guard(tmp_path)
    meminfo(tmp_path, monkeypatch, text="MemTotal: 1 kB\n")
    with pytest.raises(provision.ProvisionError, match="Swap measurement unavailable"):
        provision.guard(tmp_path)


def test_linux_guard_refuses_the_retained_cell_until_its_lock_is_embedded(tmp_path, monkeypatch):
    meminfo(tmp_path, monkeypatch)
    linux_guard(monkeypatch, locked=False)
    with pytest.raises(provision.ProvisionError, match="no qualified lock embedded for cell ubuntu2404-arm64-py311"):
        provision.guard(tmp_path)


@pytest.mark.parametrize("report", [
    lambda: linux(python=(3, 12, 16)), lambda: linux(machine="x86_64"), lambda: linux(os_release=DEBIAN_12),
    lambda: linux(kernel_release=WSL_KERNEL)])
def test_linux_guard_refuses_every_non_retained_linux(tmp_path, monkeypatch, report):
    meminfo(tmp_path, monkeypatch)
    linux_guard(monkeypatch, report=report())
    with pytest.raises(provision.ProvisionError, match="runtime_unqualified"):
        provision.guard(tmp_path)


def test_guard_still_refuses_other_platforms(tmp_path, monkeypatch):
    monkeypatch.setattr(provision, "sys_platform", lambda: "win32")
    monkeypatch.setattr(provision.shutil, "disk_usage", lambda *a: SimpleNamespace(free=20_000_000_000))
    with pytest.raises(provision.ProvisionError, match="qualified only for macOS and the retained Linux cell"):
        provision.guard(tmp_path)


def test_darwin_guard_is_unchanged(tmp_path, monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout="vm.swapusage: total = 2048.00M  used = 1024.00M  free = 1024.00M",
                               stderr="")

    def forbidden(*args, **kwargs):
        raise AssertionError("the macOS guard reads neither the cell nor /proc/meminfo nor os.statvfs directly")
    monkeypatch.setattr(provision, "sys_platform", lambda: "darwin")
    monkeypatch.setattr(provision.shutil, "disk_usage", lambda *a: SimpleNamespace(free=20_000_000_000))
    monkeypatch.setattr(provision.subprocess, "run", run)
    monkeypatch.setattr(cell, "detect", forbidden)
    monkeypatch.setattr(storage, "linux_swap_used_bytes", forbidden)
    monkeypatch.setattr(storage, "statvfs_free_bytes", forbidden)
    provision.guard(tmp_path)
    assert calls == [(["/usr/sbin/sysctl", "vm.swapusage"], {"text": True, "capture_output": True, "timeout": 3})]
    monkeypatch.setattr(provision.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout="used = 8.50G", stderr=""))
    with pytest.raises(provision.ProvisionError, match="Swap guard: more than 8.5 GB"):
        provision.guard(tmp_path)
    monkeypatch.setattr(provision.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=1, stdout="", stderr=""))
    with pytest.raises(provision.ProvisionError, match="Swap measurement unavailable"):
        provision.guard(tmp_path)


def test_granite_is_refused_outside_the_semantic_cell(monkeypatch):
    monkeypatch.setattr(provision, "sys_platform", lambda: "linux")
    as_cell(monkeypatch, linux())
    lock_everything(monkeypatch)
    provision.resource_gate(("cl100k",))
    with pytest.raises(provision.ProvisionError, match="semantic profile is qualified only on macos-arm64-py311"):
        provision.resource_gate(("granite", "cl100k"))
    monkeypatch.setattr(provision, "sys_platform", lambda: "darwin")  # macOS: unchanged, no cell lookup
    monkeypatch.setattr(cell, "detect", lambda **kw: (_ for _ in ()).throw(AssertionError("no cell lookup")))
    provision.resource_gate(("granite", "cl100k"))


# ---- storage.machine_guard: the shared Linux branch ----

def ops_settings(tmp_path) -> OpsSettings:
    return OpsSettings("http://127.0.0.1:43219/mcp/", tmp_path / "token", tmp_path / "store", tmp_path / "backups",
                       tmp_path / "key", tmp_path / "ops", "test.memory-os")


def test_machine_guard_linux_branch_shares_the_measurement_and_threshold(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "sys_platform", lambda: "linux")
    monkeypatch.setattr(storage.shutil, "disk_usage", lambda *a: SimpleNamespace(free=50_000_000_000))
    monkeypatch.setattr(storage.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("no process on Linux")))
    meminfo(tmp_path, monkeypatch, total_kb=4_194_304, free_kb=3_145_728)
    assert storage.machine_guard(ops_settings(tmp_path), require_nice=False)["swap_bytes"] == 1_048_576 * 1024
    meminfo(tmp_path, monkeypatch, total_kb=16_000_000, free_kb=16_000_000 - 8_300_782)
    with pytest.raises(RuntimeError, match="Swap exceeds 8.5 GB"):
        storage.machine_guard(ops_settings(tmp_path), require_nice=False)
    meminfo(tmp_path, monkeypatch, text="")
    with pytest.raises(RuntimeError, match="Swap cannot be attested"):
        storage.machine_guard(ops_settings(tmp_path), require_nice=False)


def test_machine_guard_darwin_branch_is_unchanged(tmp_path, monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout="total = 2048.00M  used = 512.00M  free = 1536.00M", stderr="")
    monkeypatch.setattr(storage, "sys_platform", lambda: "darwin")
    monkeypatch.setattr(storage.shutil, "disk_usage", lambda *a: SimpleNamespace(free=50_000_000_000))
    monkeypatch.setattr(storage.subprocess, "run", run)
    monkeypatch.setattr(storage, "linux_swap_used_bytes", lambda: (_ for _ in ()).throw(AssertionError("darwin")))
    assert storage.machine_guard(ops_settings(tmp_path), require_nice=False)["swap_bytes"] == 512 * 1024 ** 2
    assert calls == [(["/usr/sbin/sysctl", "-n", "vm.swapusage"], {"capture_output": True, "text": True, "timeout": 3})]


def test_machine_guard_refuses_other_platforms(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "sys_platform", lambda: "win32")
    monkeypatch.setattr(storage.shutil, "disk_usage", lambda *a: SimpleNamespace(free=50_000_000_000))
    with pytest.raises(RuntimeError, match="Unqualified swap measurement platform"):
        storage.machine_guard(ops_settings(tmp_path), require_nice=False)

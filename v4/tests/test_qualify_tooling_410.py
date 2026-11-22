"""QUALIFY_4_1_0.md: closed rules of the 4.1.0 qualification tooling. Fakes and tmp dirs only.

No network, no launchctl, no pip, no install, no store copy: every process boundary is replaced
by a fake. The only real access is read-only git on this checkout (pinned commits).
"""
from __future__ import annotations

import copy
import errno
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import check_brief_perimeter_410 as perimeter  # noqa: E402
import make_release_410 as maker  # noqa: E402
import qualify_install_410_helper as helper  # noqa: E402
import qualify_lexical_wheel_410 as lexical_wheel  # noqa: E402
import qualify_sqlite_release_410 as sqlite_gate  # noqa: E402
import release_410_checks as gate  # noqa: E402
import sqlite_release_worker_410 as worker  # noqa: E402
import verify_release_manifest as verifier  # noqa: E402

COMMIT = "a" * 40
CONTRACT_COMMIT = "97e126ad64075a44d43fd3379707a2d99be2b9fb"  # carries QUALIFY rev. 4; pinned for real-git checks
QUAL_BASE = "453a893a60f26bf4ed42fff97d3a03564a7f67e7"
SHA = "b" * 64
WHEEL_SHA = "c" * 64
SDIST_SHA = "d" * 64
CHAIN_SHA = "e" * 64
TAG = "v7"
LEX_SHA, SEM_SHA = "1" * 64, "2" * 64
PINS_SHA = hashlib.sha256((SCRIPTS / "qualify_410_pytest_pins.txt").read_bytes()).hexdigest()
PINS = gate.exact_pins((SCRIPTS / "qualify_410_pytest_pins.txt").read_text())
CONTEXT = {"pins_sha256": PINS_SHA, "pins": PINS, "constraints": {"lexical": LEX_SHA, "semantic": SEM_SHA}}


def manifest():
    return {
        "version": "4.1.0", "source_commit": COMMIT, "run_tag": TAG, "release_ready": False, "open_blockers": [],
        "artifacts": [{"name": "memory_os_ai-4.1.0-py3-none-any.whl", "bytes": 1, "sha256": WHEEL_SHA},
                      {"name": "memory_os_ai-4.1.0.tar.gz", "bytes": 1, "sha256": SDIST_SHA}],
        "profiles": {"lexical": {"extras": ["ops", "brief"], "constraints_sha256": LEX_SHA, "locks": 36},
                     "semantic": {"extras": ["ops", "brief", "semantic"], "constraints_sha256": SEM_SHA, "locks": 65}},
        "gates": gate.new_gates(), "decisions": {}, "evidence": {},
        "measures": {"M1": {}, "M2": {"state": "non mesuré"}, "M3": None, "M4": {"state": "historical"}},
        "qualification_tooling_sha256": {"check_brief_perimeter_410.py": SHA},
        "external_tooling_sha256": {"chain": {"path": "/x/" + gate.CHAIN_NAME, "sha256": CHAIN_SHA},
                                    "supervisor": {"path": "/x/" + gate.SUPERVISOR_NAME,
                                                   "sha256": gate.SUPERVISOR_SHA256}},
        "pytest_pins_sha256": PINS_SHA, "qualification_source_sha256": {
            "v4/tests/" + name: SHA for name in gate.LEXICAL_WHEEL_FILES},
        "copy_scripts_sha256": {}, "lexical_store_scripts_sha256": {"runner": SHA},
        "lexical_store_fixed_data_sha256": SHA, "installed_relay_scripts_sha256": {"runner": SHA, "case": SHA},
    }


def refused(call, *args, **kw):
    with pytest.raises((ValueError, KeyError, TypeError)):
        call(*args, **kw)


# ---------- states, admissibility, release_ready ----------

def test_gate_states_and_admissible_exceptions_are_the_closed_list():
    assert gate.GATES == tuple(f"G{i}" for i in range(1, 16))
    assert set(gate.STATES) == {"pending", "pass", "fail", "transfer_accepted", "waived_by_romain"}
    for name in gate.GATES:
        if name not in ("G12", "G13"):
            assert gate.ADMISSIBLE[name] == ("pass",)
    # transfer_accepted does not exist for G12; G13 is never an automatic pass.
    assert set(gate.ADMISSIBLE["G12"]) == {"pass", "waived_by_romain"}
    assert set(gate.ADMISSIBLE["G13"]) == {"transfer_accepted", "waived_by_romain"}


def test_a_failure_or_a_missing_measure_never_becomes_a_success():
    m = manifest()
    gate.set_gate(m, "G6", "fail")
    refused(gate.set_gate, m, "G6", "pass")
    refused(gate.set_gate, m, "G6", "waived_by_romain", decision="g12_waiver")
    refused(gate.set_gate, m, "G13", "waived_by_romain")  # a waiver needs a recorded decision
    gate.set_gate(m, "G13", "waived_by_romain", decision="g13_p_a")
    refused(gate.set_gate, m, "G13", "pass")
    refused(gate.set_gate, m, "G99", "pass")


def test_sealed_validation_folders_are_refused_before_any_read(tmp_path):
    for name in gate.FORBIDDEN_NAMES:
        refused(gate.refuse_forbidden, tmp_path / name / "pb" / "proof.json")
        with pytest.raises(SystemExit):
            helper.refuse_forbidden(tmp_path / name / "x")
    target = tmp_path / "brief_v5_val"
    target.mkdir()
    (tmp_path / "link").symlink_to(target)
    refused(gate.refuse_forbidden, tmp_path / "link" / "proof.json")  # a link cannot lead back inside
    assert gate.refuse_forbidden(tmp_path / "elsewhere.json") == tmp_path / "elsewhere.json"


def test_no_tool_references_the_sealed_folders_except_to_refuse_them():
    for name in maker.TOOLS:
        if not name.endswith("_410.py") and not name.endswith("_410.sh") and name != "qualify_install_410_helper.py":
            continue
        for line in (SCRIPTS / name).read_text().splitlines():
            if "brief_v5_val" in line or "brient_v5_val" in line:
                assert any(word in line for word in ("FORBIDDEN", "REFUS", "never read", "jamais", "refus")), (name, line)
    assert "run_integrity_v5_validation" not in "".join((SCRIPTS / n).read_text() for n in maker.TOOLS
                                                        if n.endswith(("_410.py", "_410.sh")))


# ---------- §1 invariants ----------

def fake_git(overrides=None, ancestor=True):
    """Git answers built from real literals; ``overrides`` replaces the blob of a path at the candidate."""
    overrides = overrides or {}
    constraints = {name: ("\n".join(f"pkg{i}=={i}.0" for i in range(spec["locks"])) + "\n").encode()
                   for name, spec in gate.PROFILES.items()}
    version = b'__version__ = "4.1.0"\n'
    blobs = {
        gate.EMBED_FILE: (ROOT / gate.EMBED_FILE).read_bytes(),
        gate.SEMANTIC_FROZEN[0]: constraints["semantic"], gate.SEMANTIC_FROZEN[1]: b"{}",
        gate.PROFILES["lexical"]["constraints"]: constraints["lexical"],
        "pyproject.toml": b'[project]\nversion = "4.1.0"\n', "v4/pyproject.toml": b'[project]\nversion = "4.1.0"\n',
        "server.json": json.dumps({"version": "4.1.0", "packages": [{"version": "4.1.0"}]}).encode(),
        "v4/src/memory_os_v4/__init__.py": version, "src/memory_os_ai/__init__.py": version,
    }

    def git(root, *args, text=True):
        if args[0] == "merge-base":
            if not ancestor:
                raise subprocess.CalledProcessError(1, "git")
            return ""
        if args[0] == "show":
            revision, path = args[1].split(":", 1)
            data = overrides.get(path, blobs[path]) if revision == COMMIT else blobs[path]
            return data
        if args[0] in ("rev-parse", "hash-object"):
            return "blob\n"
        raise AssertionError(args)

    return git


def schema_ok(root):
    return 8, list(gate.MIGRATION_CHECKSUMS)


def test_invariants_pass_and_record_profiles_and_versions():
    record = gate.verify_invariants(ROOT, COMMIT, git=fake_git(), schema=schema_ok)
    assert record["schema_version"] == 8 and len(record["migration_checksums"]) == 8
    assert record["profiles"]["lexical"]["locks"] == 36 and record["profiles"]["semantic"]["locks"] == 65
    assert set(record["versions"].values()) == {"4.1.0"} and record["embed_sha256"] == gate.EMBED_SHA256


@pytest.mark.parametrize("path,data", [
    (gate.EMBED_FILE, b"changed"),
    (gate.SEMANTIC_FROZEN[0], b"numpy==1.0\n"),
    (gate.SEMANTIC_FROZEN[1], b'{"x": 1}'),
    (gate.PROFILES["lexical"]["constraints"], b"numpy==1.0\n"),
    ("pyproject.toml", b'[project]\nversion = "4.0.9"\n'),
    ("server.json", json.dumps({"version": "4.1.0", "packages": [{"version": "4.0.9"}]}).encode()),
    ("src/memory_os_ai/__init__.py", b'__version__ = "4.0.9"\n'),
])
def test_invariants_refuse_any_drift(path, data):
    refused(gate.verify_invariants, ROOT, COMMIT, git=fake_git({path: data}), schema=schema_ok)


def test_invariants_refuse_missing_ancestor_schema_change_and_short_sha():
    refused(gate.verify_invariants, ROOT, COMMIT, git=fake_git(ancestor=False), schema=schema_ok)
    refused(gate.verify_invariants, ROOT, COMMIT, git=fake_git(), schema=lambda root: (9, gate.MIGRATION_CHECKSUMS))
    refused(gate.verify_invariants, ROOT, COMMIT, git=fake_git(),
            schema=lambda root: (8, gate.MIGRATION_CHECKSUMS[:7] + [(8, "0" * 64)]))
    refused(gate.verify_invariants, ROOT, "97e126a", git=fake_git(), schema=schema_ok)


def test_invariants_hold_on_the_real_blobs_of_the_contract_commit():
    def git(root, *args, text=True):
        if args[0] in ("rev-parse", "hash-object"):
            return "same\n"  # tree/commit binding is exercised by the fakes; here: the real blobs
        return gate._git(root, *args, text=text)

    record = gate.verify_invariants(ROOT, CONTRACT_COMMIT, git=git)
    assert record["schema_version"] == 8 and [tuple(r) for r in record["migration_checksums"]] == gate.MIGRATION_CHECKSUMS
    assert [record["profiles"][p]["locks"] for p in ("lexical", "semantic")] == [36, 65]


# ---------- decisions (§10) ----------

def dossier_text(acquisition, context=None):
    """The exact dossier of one acquisition for this candidate and run, as the tool writes it."""
    return json.dumps(gate.network_dossier(acquisition, commit=COMMIT, run_tag=TAG, context=context or CONTEXT),
                      indent=2, sort_keys=True) + "\n"


def write_dossier(folder, acquisition, text=None):
    path = Path(folder) / (acquisition + ".dossier.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dossier_text(acquisition) if text is None else text)
    return path


def decision(name, **changes):
    d = {"format": gate.DECISION_FORMAT, "decision": name, "answer": "oui", "decided_by": "Romain",
         "decided_at": "2026-11-23T10:00:00+01:00", "source_commit": COMMIT,
         "statement": "Pour le candidat aaaa, j'accepte explicitement cette proposition telle qu'écrite."}
    if gate.DECISIONS[name][0]:
        d["run_tag"] = TAG
    if name in ("g13_p_a", "g12_waiver"):
        d.update(status="waived_by_romain", scope="G13" if name == "g13_p_a" else "G12",
                 reason="Motif détaillé : limites documentées du lien historique et des appelants modifiés.",
                 evidence=[{"reference": "perimeter", "sha256": SHA}], claims_result_on_candidate=False,
                 release_ready_effect="may_satisfy_" + ("G13" if name == "g13_p_a" else "G12"))
    if name == "g13_p_a":
        d.update(replay=False, result_attributed_to=gate.P_A_PROOF_COMMIT)
    if name == "g12_external_proof":
        d["proof"] = {"reference": "pb.json", "sha256": SHA}
    if name == "d1_resign":
        d["measure"] = {"reference": "report.json", "sha256": SHA}
    if name.startswith("network_"):
        n = name[-2:]
        expected = gate.network_dossier(n, commit=COMMIT, run_tag=TAG, context=CONTEXT)
        d.update(acquisition=n, acquisitions=[n], operations=[expected["operation"]], sources=list(expected["sources"]),
                 dossier={"reference": n + ".dossier.json",
                          "sha256": hashlib.sha256(dossier_text(n).encode()).hexdigest()})
    if name == "launch":
        d.update(chain_sha256=CHAIN_SHA, authorises_publication=False, authorises_push=False,
                 authorises_production_switch=False)
    d.update(changes)
    return d


@pytest.fixture
def dossiers(tmp_path):
    for n in gate.NETWORK:
        write_dossier(tmp_path, n)
    return tmp_path


def check(d, folder=None, **kw):
    name = d["decision"]
    if name.startswith("network_") and folder is not None:
        kw.setdefault("dossier_path", Path(folder) / str((d.get("dossier") or {}).get("reference")))
        kw.setdefault("context", CONTEXT)
    return gate.check_decision(d, decision=name, commit=COMMIT, run_tag=TAG, chain_sha256=CHAIN_SHA, **kw)


@pytest.mark.parametrize("name", sorted(gate.DECISIONS))
def test_every_decision_of_section_10_has_an_accepted_explicit_form(name, dossiers):
    assert check(decision(name), dossiers)["answer"] == "oui"
    assert check(decision(name, answer="non"), dossiers)["answer"] == "non"  # a no is recorded; it authorises nothing


def test_the_nine_propositions_are_covered():
    assert sorted({section for _, section in gate.DECISIONS.values()}) == list(range(1, 10))
    assert {"network_" + n for n in gate.NETWORK} <= set(gate.DECISIONS)


@pytest.mark.parametrize("name,changes", [
    ("launch", {"answer": "ok"}), ("launch", {"decided_by": "Claude"}), ("launch", {"statement": "oui"}),
    ("launch", {"source_commit": "f" * 40}), ("launch", {"run_tag": "v8"}), ("launch", {"chain_sha256": SHA}),
    ("launch", {"authorises_publication": True}), ("launch", {"authorises_push": True}),
    ("launch", {"authorises_production_switch": True}), ("launch", {"format": "other"}),
    ("launchd_nice", {"run_tag": None}), ("semantic_pth_variant", {"run_tag": "v1"}),
    ("network_N1", {"sources": ["example.org"]}), ("network_N6", {"sources": ["pypi.org"]}),
    ("network_N3", {"dossier": {"reference": "x"}}), ("network_N3", {"acquisition": "N5"}),
    ("network_N2", {"operations": []}), ("network_N2", {"operations": ["pip install"]}),
    ("network_N4", {"sources": ["pypi.org"]}),  # narrower than what the step really fetches from
    ("network_N4", {"sources": ["pypi.org", "files.pythonhosted.org", "example.org"]}),
    ("network_N3", {"acquisitions": ["N3", "N5"]}),
    ("network_N3", {"dossier": {"reference": "../N3.dossier.json", "sha256": SHA}}),
    ("network_N5", {"dossier": {"reference": "N3.dossier.json", "sha256": SHA}}),
    ("g13_p_a", {"status": "pass"}), ("g13_p_a", {"reason": "court"}), ("g13_p_a", {"evidence": []}),
    ("g13_p_a", {"replay": True}), ("g13_p_a", {"result_attributed_to": COMMIT}),
    ("g13_p_a", {"claims_result_on_candidate": True}), ("g13_p_a", {"release_ready_effect": "may_satisfy_G12"}),
    ("g12_waiver", {"status": "transfer_accepted"}), ("g12_waiver", {"claims_result_on_candidate": True}),
    ("g12_waiver", {"scope": ""}), ("g12_external_proof", {"proof": {"reference": "pb"}}),
    ("d1_resign", {"measure": None}),
])
def test_decisions_refuse_anything_implicit_unbound_or_widened(name, changes, dossiers):
    refused(check, decision(name, **changes), dossiers)


def test_unknown_decision_and_wrong_id_are_refused():
    refused(gate.check_decision, decision("launch"), decision="go", commit=COMMIT, run_tag=TAG)
    refused(gate.check_decision, decision("launch"), decision="launchd_nice", commit=COMMIT, run_tag=TAG)
    refused(gate.check_decision, decision("launch"), decision="launch", commit=COMMIT, run_tag=TAG)  # no chain sha


# Blocker 1 (Codex review of 2dfec6f): a network GO must bound what is really acquired.

def test_blocker1_n4_go_with_a_missing_dossier_no_pins_and_only_pypi_org_is_refused(tmp_path):
    """Counterexample of the review: nonexistent dossier, no pin hash, sources = pypi.org only."""
    go = decision("network_N4", sources=["pypi.org"], dossier={"reference": "absent.json", "sha256": SHA})
    refused(check, go, tmp_path)                      # the dossier file is read and hashed: absent = refusal
    refused(check, decision("network_N4"))            # no dossier/context given: cannot be checked, so refused
    write_dossier(tmp_path, "N4")
    assert check(decision("network_N4"), tmp_path)["answer"] == "oui"
    refused(check, decision("network_N4", sources=["pypi.org"]), tmp_path)  # wheels come from files.pythonhosted.org


def test_blocker1_dossier_is_the_exact_description_of_the_effective_acquisition(tmp_path):
    n4 = gate.network_dossier("N4", commit=COMMIT, run_tag=TAG, context=CONTEXT)
    assert n4["pins_sha256"] == PINS_SHA and n4["pins"] == PINS and n4["constraints_sha256"] == LEX_SHA
    assert n4["sources"] == ["files.pythonhosted.org", "pypi.org"] and n4["only_binary"] is True
    n6 = gate.network_dossier("N6", commit=COMMIT, run_tag=TAG, context=CONTEXT)
    assert (n6["url"], n6["bytes"], n6["sha256"], n6["times"]) == (gate.CL100K_URL, 1681126, gate.CL100K_SHA256, 1)
    for change in ({"pins_sha256": SHA}, {"constraints_sha256": SEM_SHA}, {"run_tag": "v8"}, {"source_commit": "f" * 40},
                   {"attempts": 3}, {"index_url": "https://example.org/simple"}, {"only_binary": False},
                   {"retries_or_widening_covered": True}, {"extra": "widened"}):
        text = json.dumps({**n4, **change}, indent=2, sort_keys=True) + "\n"
        write_dossier(tmp_path, "N4", text)
        go = decision("network_N4", dossier={"reference": "N4.dossier.json",
                                             "sha256": hashlib.sha256(text.encode()).hexdigest()})
        refused(check, go, tmp_path)  # approved hash matches, but the content is not what N4 does
    # Pins changed after the GO: the sealed context no longer matches the approved dossier.
    write_dossier(tmp_path, "N4")
    refused(check, decision("network_N4"), tmp_path, context={**CONTEXT, "pins_sha256": SHA})
    # The dossier changed on disk after approval.
    write_dossier(tmp_path, "N4", dossier_text("N4") + " ")
    refused(check, decision("network_N4"), tmp_path)


def test_blocker1_effective_parameters_are_those_the_scripts_use():
    install = (SCRIPTS / "qualify_install_410.sh").read_text()
    wheel = (SCRIPTS / "qualify_lexical_wheel_410.py").read_text()
    resolve = (SCRIPTS / "qualify_runtime_resolution.py").read_text()
    for text in (install, wheel, resolve):
        assert gate.PYPI_INDEX in text and "--only-binary=:all:" in text
    assert "qualify_410_pytest_pins.txt" in wheel and '"-r", str(PINS)' in wheel
    assert gate.CL100K_URL in (ROOT / "v4/src/memory_os_v4/resources/provision_v1.json").read_text()
    assert {s["tool"] for s in gate.NETWORK_SPEC.values()} <= {"scripts/" + n for n in maker.TOOLS}
    real = gate.network_context(ROOT)
    assert real["pins_sha256"] == PINS_SHA and set(real["constraints"]) == {"lexical", "semantic"}


# ---------- G5 ----------

def pip_report(m, profile, *, url="https://files.pythonhosted.org/packages/x/pkg%d-1-py3-none-any.whl"):
    rows = [{"metadata": {"name": "memory-os-ai", "version": "4.1.0"},
             "download_info": {"url": "file:///w.whl", "archive_info": {"hashes": {"sha256": WHEEL_SHA}}}}]
    for i in range(m["profiles"][profile]["locks"]):
        rows.append({"metadata": {"name": f"pkg{i}", "version": "1"},
                     "download_info": {"url": url % i, "archive_info": {"hashes": {"sha256": SHA}}}})
    return {"install": rows}


def resolution(m, profile, tmp_path, report=None):
    path = tmp_path / f"pip-{profile}.json"
    path.write_text(json.dumps(report or pip_report(m, profile)))
    n = m["profiles"][profile]["locks"] + 1
    return {"ok": True, "profile": profile, "installed": False, "model_calls": 0, "wheel_sha256": WHEEL_SHA,
            "constraints_sha256": m["profiles"][profile]["constraints_sha256"], "mismatches": {},
            "unexpected_packages": [], "duplicates": [], "ml_packages": [], "scope_ok": True, "wheel_ok": True,
            "expected_packages": n, "resolved_packages": n,
            "pip_report_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}, path


@pytest.mark.parametrize("profile", ["lexical", "semantic"])
def test_resolution_records_url_and_sha256_of_every_distribution(tmp_path, profile):
    m = manifest()
    report, path = resolution(m, profile, tmp_path)
    rows = gate.validate_resolution(report, path, m, profile)
    assert len(rows) == m["profiles"][profile]["locks"] + 1
    assert all(gate.is_sha(r["sha256"]) and r["url"] for r in rows)


@pytest.mark.parametrize("change", [
    {"ok": False}, {"installed": True}, {"ml_packages": ["torch"]}, {"unexpected_packages": ["x"]},
    {"mismatches": {"a": 1}}, {"wheel_sha256": SHA}, {"constraints_sha256": SHA}, {"profile": "semantic"},
    {"pip_report_sha256": SHA}, {"resolved_packages": 3},
])
def test_resolution_refuses_drift(tmp_path, change):
    m = manifest()
    report, path = resolution(m, "lexical", tmp_path)
    refused(gate.validate_resolution, {**report, **change}, path, m, "lexical")


def test_resolution_refuses_a_distribution_outside_the_authorised_wheel_host(tmp_path):
    m = manifest()
    for url in ("https://example.org/pkg%d.whl", "https://files.pythonhosted.org/packages/pkg%d.tar.gz"):
        report, path = resolution(m, "lexical", tmp_path, pip_report(m, "lexical", url=url))
        refused(gate.validate_resolution, report, path, m, "lexical")


# ---------- G7, G8, M1 ----------

def service(profile):
    row = {"present": True, "belongs_to_run": True, "plist_under_home": True, "program_is_run_venv": True,
           "listener_is_service_pid": True, "ready": True, "label": gate.TEST_LABEL, "port": gate.TEST_PORT,
           "version": "4.1.0", "commit": COMMIT,
           "retrieval_profile": gate.LEXICAL_MARKER if profile == "lexical" else gate.SEMANTIC_MARKER}
    if profile == "lexical":
        row["ml_libraries_open"] = 0
    return row


def doctor_ok():
    areas = sorted(gate.REQUIRED_DOCTOR_AREAS)
    statuses = {area: "PASS" for area in areas}
    statuses["storage"] = "WARN"  # a warning is not a failure
    return {"returncode": 0, "fail": 0, "checks": len(areas) + 3, "areas": sorted(areas + ["backup", "backup-key"]),
            "missing_areas": [], "statuses": statuses}


def zero_write():
    return {"ok": True, "returncode": 0, "inventory_identical": True, "inventory_hashes_content": True,
            "inventory_before_sha256": SHA, "inventory_after_sha256": SHA, "inventory_entries": 9}


def install_block(profile, home, download):
    clean = {"label_absent": True, "port_free": True}
    return {"home": home, "pre": dict(clean), "post": dict(clean),
            "apply": {"ok": True, "returncode": 0, "demo": True, "profile": profile, "download_bytes": download},
            "service": service(profile), "doctor": doctor_ok(),
            "client_entries_created": [".codex/config.toml", ".cursor/mcp.json"]}


def install_proof(profile="lexical", variant="autonomous"):
    m = manifest()
    locks = m["profiles"][profile]["locks"]
    env = {"constraints_sha256": m["profiles"][profile]["constraints_sha256"], "locator_under_site_packages": True,
           "package_under_site_packages": True, "pip_check_ok": True, "mismatch": [], "unpinned": [],
           "candidate_origin_sha256": WHEEL_SHA, "package_version": "4.1.0",
           "bootstrap": ["pip", "setuptools"], "pinned_and_equal": locks, "ml_packages": []}
    if variant == "autonomous":
        env.update(wheels_only=True, distributions=[
            {"name": f"p{i}", "sha256": SHA, "url": f"https://files.pythonhosted.org/p{i}.whl"} for i in range(locks)])
    else:
        env.update(reused_paths=[{"path": "/site", "distributions": ["torch==2.10.0"]}])
    one = install_block(profile, "/p/home1", gate.CL100K_BYTES if profile == "lexical" else 0)
    one["preview"] = zero_write()
    one["undo"] = {"ok": True, "returncode": 0, "with_service": True, "label_absent": True, "port_free": True,
                   "plist_removed": True, "client_entries_restored": True}
    two = install_block(profile, "/p/home2", 0)
    two.update(client_entries_created=[".codex/config.toml", ".cursor/mcp.json"], uninstall_preview=zero_write(),
               uninstall={"ok": True, "partial": False, "complete": False, "returncode": 0, "data_deleted": False,
                          "entries_removed": [".cursor/mcp.json", ".codex/config.toml"], "label_absent": True,
                          "port_free": True, "plist_removed_from_active": True, "plist_journal_copy_kept": True},
               data_conservation={"immutable_unchanged": True, "immutable_kinds": ["backups", "key", "resources", "token"],
                                  "compared_documents": ["install2/state_after/quickstart/S1/journal.json",
                                                         "install2/state_before/quickstart/S1/journal.json"],
                                  "immutable_witnessed": True,
                                  "immutable_files": {"token": 1, "resources": 1, "backups": 1, "key": 1},
                                  "store_identity_unchanged": True, "store_logical_unchanged": True,
                                  "checked_after_stop": True, "journals_present": True, "unexpected_changes": []})
    timing = {"clock": "monotonic", "download_bytes": one["apply"]["download_bytes"], "profile": profile}
    deviations = ["non_blank_machine", "local_wheel"] + (["model_pre_placed"] if profile == "semantic" else [])
    proof = {
        "format": "qualify-410-install-v1", "ok": True, "profile": profile, "variant": variant,
        "source_commit": COMMIT, "run_tag": TAG, "version": "4.1.0", "wheel": {"sha256": WHEEL_SHA},
        "label": gate.TEST_LABEL, "port": gate.TEST_PORT, "environment": env, "install_1": one, "install_2": two,
        "m1": {"t0_epoch": 100.0, "t1_epoch": 160.0, "total_s": 60.0, "pip_s": 20.0, "controls_s": 5.0, "net_s": 55.0,
               "outside_model_s": 58.0, "install_json_timing": timing, "pip_downloaded_bytes": None,
               "pip_downloaded_bytes_measured": False, "pip_cache_bytes": 1000,
               "pip_cache_bytes_nature": "size of the run's own pip cache; not transferred bytes",
               "quickstart_download_bytes": timing["download_bytes"], "protocol_deviations": deviations,
               "label": "reused-dependencies variant" if variant == "pth" else "first installation"},
        "production": {"unchanged": True, "pid_before": 77, "pid_after": 77, "snapshot_before_sha256": SHA,
                       "snapshot_after_sha256": SHA, **{flag: True for flag in gate.PRODUCTION_FLAGS}},
        "safety_net_fired": False,
    }
    return proof, m


@pytest.mark.parametrize("profile,variant", [("lexical", "autonomous"), ("semantic", "autonomous"), ("semantic", "pth")])
def test_install_proof_passes_and_reports_m1_without_any_threshold(profile, variant):
    proof, m = install_proof(profile, variant)
    measure = gate.validate_install(proof, m, profile)
    assert measure["total_s"] == 60.0 and measure["label"].startswith("reused" if variant == "pth" else "first")
    proof["m1"].update(t1_epoch=100000.0, total_s=99900.0, net_s=99895.0)  # a slow install is measured, not refused
    assert gate.validate_install(proof, m, profile)["total_s"] == 99900.0


def mutate(proof, path, value):
    target = proof
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return proof


@pytest.mark.parametrize("path,value", [
    (("ok",), False), (("safety_net_fired",), True), (("source_commit",), "f" * 40), (("run_tag",), "v8"),
    (("wheel", "sha256"), SHA), (("label",), "com.romain.memory-os-v4.test"), (("port",), 8765),
    (("variant",), "pth"), (("environment", "ml_packages"), ["torch"]), (("environment", "unpinned"), ["x"]),
    (("environment", "mismatch"), ["a 1 != 2"]), (("environment", "pinned_and_equal"), 35),
    (("environment", "bootstrap"), ["pip", "pytest"]), (("environment", "pip_check_ok"), False),
    (("environment", "locator_under_site_packages"), False), (("environment", "wheels_only"), False),
    (("install_1", "pre", "label_absent"), False), (("install_2", "post", "port_free"), False),
    (("install_1", "preview", "inventory_identical"), False), (("install_1", "preview", "inventory_hashes_content"), False),
    (("install_1", "preview", "inventory_after_sha256"), WHEEL_SHA), (("install_1", "apply", "ok"), False),
    (("install_1", "apply", "demo"), False), (("install_1", "apply", "download_bytes"), 0),
    (("install_2", "apply", "download_bytes"), gate.CL100K_BYTES),
    (("install_1", "service", "belongs_to_run"), False), (("install_1", "service", "program_is_run_venv"), False),
    (("install_1", "service", "plist_under_home"), False), (("install_1", "service", "retrieval_profile"), gate.SEMANTIC_MARKER),
    (("install_1", "service", "ml_libraries_open"), 2), (("install_2", "service", "present"), False),
    (("install_1", "doctor", "fail"), 1), (("install_1", "doctor", "returncode"), 9),
    (("install_2", "doctor", "missing_areas"), ["server"]), (("install_1", "doctor", "areas"), ["store"]),
    (("install_1", "doctor", "statuses"), {"server": "PASS"}), (("install_1", "service", "commit"), "f" * 40),
    (("install_2", "service", "commit"), None), (("install_1", "client_entries_created"), [".cursor/mcp.json"]),
    (("environment", "candidate_origin_sha256"), SHA), (("environment", "package_version"), "4.0.9"),
    (("install_2", "data_conservation", "immutable_witnessed"), False),
    (("install_2", "data_conservation", "immutable_files"), {"token": 1, "resources": 1, "backups": 0, "key": 1}),
    (("install_2", "data_conservation", "immutable_files"), {"token": 1, "resources": 1}),
    (("production", "real_launch_agents_unchanged"), False), (("production", "real_client_configs_unchanged"), False),
    (("production", "real_memory_os_config_unchanged"), None),
    (("m1", "pip_downloaded_bytes"), 1000), (("m1", "pip_downloaded_bytes_measured"), True),
    (("m1", "pip_download_bytes"), 1000), (("m1", "pip_cache_bytes"), None),
    (("install_1", "undo", "plist_removed"), False),
    (("install_1", "undo", "label_absent"), False), (("install_1", "undo", "client_entries_restored"), False),
    (("install_2", "home"), "/p/home1"), (("install_2", "uninstall_preview", "inventory_identical"), False),
    (("install_2", "uninstall", "partial"), True), (("install_2", "uninstall", "ok"), False),
    (("install_2", "uninstall", "entries_removed"), [".cursor/mcp.json"]), (("install_2", "client_entries_created"), []),
    (("install_2", "client_entries_created"), [".cursor/mcp.json"]),
    (("install_2", "uninstall", "label_absent"), False), (("install_2", "uninstall", "port_free"), False),
    (("install_2", "uninstall", "plist_removed_from_active"), False),
    (("install_2", "uninstall", "plist_journal_copy_kept"), False), (("install_2", "uninstall", "data_deleted"), True),
    (("install_2", "data_conservation", "immutable_unchanged"), False),
    (("install_2", "data_conservation", "store_logical_unchanged"), False),
    (("install_2", "data_conservation", "store_identity_unchanged"), False),
    (("install_2", "data_conservation", "checked_after_stop"), False),
    (("install_2", "data_conservation", "journals_present"), False),
    (("install_2", "data_conservation", "unexpected_changes"), [{"path": "x"}]),
    (("install_2", "data_conservation", "immutable_kinds"), ["token"]),
    (("production", "pid_after"), 78), (("production", "unchanged"), False),
    (("production", "snapshot_after_sha256"), WHEEL_SHA),
    (("m1", "total_s"), 0), (("m1", "net_s"), 60.0), (("m1", "protocol_deviations"), []),
    (("m1", "label"), "reused-dependencies variant"), (("m1", "quickstart_download_bytes"), 5),
])
def test_install_proof_refuses_each_gap(path, value):
    proof, m = install_proof("lexical")
    refused(gate.validate_install, mutate(proof, path, value), m, "lexical")


def test_uninstall_complete_false_alone_is_not_a_failure_but_a_kept_service_is():
    proof, m = install_proof("lexical")
    assert proof["install_2"]["uninstall"]["complete"] is False
    gate.validate_install(proof, m, "lexical")


def test_blocker3_service_of_another_commit_and_doctor_exit_9_are_refused():
    """Counterexample of the review: every other field positive, another commit attested, doctor returning 9."""
    proof, m = install_proof("lexical")
    proof["install_1"]["service"]["commit"] = "f" * 40
    refused(gate.validate_install, proof, m, "lexical")
    proof, m = install_proof("lexical")
    proof["install_1"]["doctor"]["returncode"] = 9
    refused(gate.validate_install, proof, m, "lexical")
    # Both at once, as in the review; and a doctor that did not run its expected checks.
    proof, m = install_proof("semantic")
    proof["install_2"]["service"]["commit"] = "f" * 40
    proof["install_2"]["doctor"]["returncode"] = 9
    refused(gate.validate_install, proof, m, "semantic")
    proof, m = install_proof("lexical")
    proof["install_1"]["doctor"] = {"returncode": 0, "fail": 0, "checks": 1, "areas": ["token"], "missing_areas": [],
                                    "statuses": {"token": "PASS"}}
    refused(gate.validate_install, proof, m, "lexical")
    proof, m = install_proof("lexical")
    proof["install_1"]["doctor"]["statuses"]["client:cursor"] = "WARN"  # the requested client entry is not a relay
    refused(gate.validate_install, proof, m, "lexical")


def test_pth_variant_must_attest_paths_and_candidate_origin_and_is_semantic_only():
    proof, m = install_proof("semantic", "pth")
    refused(gate.validate_install, mutate(copy.deepcopy(proof), ("environment", "reused_paths"), []), m, "semantic")
    refused(gate.validate_install, mutate(copy.deepcopy(proof), ("environment", "reused_paths"), [{"path": "/site"}]), m, "semantic")
    # Reduced coverage: other packages of the reused environment are listed, a drifting lock still refuses.
    gate.validate_install(mutate(copy.deepcopy(proof), ("environment", "unpinned"), ["pytest", "ruff"]), m, "semantic")
    refused(gate.validate_install, mutate(copy.deepcopy(proof), ("environment", "mismatch"), ["torch 2.9 != 2.10"]), m, "semantic")
    refused(gate.validate_install, mutate(copy.deepcopy(proof), ("environment", "missing"), ["torch"]), m, "semantic")
    refused(gate.validate_install, mutate(copy.deepcopy(proof), ("environment", "unpinned"), None), m, "semantic")
    refused(gate.validate_install, mutate(copy.deepcopy(proof), ("environment", "candidate_origin_sha256"), SHA), m, "semantic")
    refused(gate.validate_install, mutate(copy.deepcopy(proof), ("m1", "label"), "first installation"), m, "semantic")
    refused(gate.validate_install, mutate(copy.deepcopy(proof), ("m1", "protocol_deviations"),
                                          ["non_blank_machine", "local_wheel"]), m, "semantic")


# ---------- G6, G11 ----------

def lexical_wheel_proof():
    return {"format": "qualify-410-lexical-wheel-v1", "ok": True, "source_commit": COMMIT, "run_tag": TAG,
            "wheel_sha256": WHEEL_SHA, "outside_checkout": True, "pythonpath_set": False,
            "package_under_site_packages": True, "find_spec": {n: None for n in gate.ML_MODULES},
            "pytest_pins_sha256": PINS_SHA, "pip_check_ok": True, "runtime_recheck_ok": True, "returncode": 0,
            "pytest_distributions": [{"name": name, "version": version, "sha256": SHA,
                                      "url": f"https://files.pythonhosted.org/x/{name}-{version}-py3-none-any.whl"}
                                     for name, version in sorted(PINS.items())],
            "counts": {"tests": 80, "failures": 0, "errors": 0, "skipped": 0},
            "test_files": {n: SHA for n in gate.LEXICAL_WHEEL_FILES}}


def test_blocker4_g6_without_the_recorded_pytest_closure_is_refused():
    """Counterexample of the review: G6 accepted although the URL/SHA-256 required by N4 are missing."""
    m = manifest()
    for change in (lambda p: p.pop("pytest_distributions"), lambda p: p.update(pytest_distributions=[]),
                   lambda p: p["pytest_distributions"].pop(), lambda p: p["pytest_distributions"][0].update(sha256=None),
                   lambda p: p["pytest_distributions"][0].update(version="0.0.1"),
                   lambda p: p["pytest_distributions"][0].update(url="https://example.org/x.whl"),
                   lambda p: p["pytest_distributions"].append({"name": "colorama", "version": "1", "sha256": SHA,
                                                               "url": "https://files.pythonhosted.org/c.whl"})):
        proof = lexical_wheel_proof()
        change(proof)
        refused(gate.validate_lexical_wheel, proof, m)


@pytest.mark.parametrize("path,value", [
    (("outside_checkout",), False), (("pythonpath_set",), True), (("package_under_site_packages",), False),
    (("find_spec", "torch"), "present"), (("find_spec",), {"torch": None}), (("pytest_pins_sha256",), WHEEL_SHA),
    (("runtime_recheck_ok",), False), (("pytest_distributions",), None), (("counts", "skipped"), 1), (("counts", "failures"), 1), (("counts", "tests"), 0),
    (("returncode",), 1), (("wheel_sha256",), SHA), (("test_files", "lexical_fixture_410.py"), WHEEL_SHA),
    (("test_files",), {"test_lexical_profile.py": SHA}),
])
def test_lexical_wheel_proof(path, value):
    m = manifest()
    gate.validate_lexical_wheel(lexical_wheel_proof(), m)
    refused(gate.validate_lexical_wheel, mutate(lexical_wheel_proof(), path, value), m)


def lexical_store_proof():
    facts = worker.LEXICAL_FACTS
    ranks = {q: [facts[i], facts[(i + 1) % len(facts)]] for i, q in enumerate(worker.LEXICAL_QUERIES)}
    return {"format": "qualify-410-lexical-store-v1", "ok": True, "source_commit": COMMIT, "run_tag": TAG,
            "wheel_sha256": WHEEL_SHA, "scripts_sha256": {"runner": SHA}, "package_under_site_packages": True,
            "fixed_data_sha256": SHA, "marker": dict(gate.LEXICAL_MARKER), "restored_marker": dict(gate.LEXICAL_MARKER),
            "added": len(facts), "backup": {"ok": True, "archive": "lexical.mosb", "archive_sha256": SHA},
            "restore": {"ok": True, "archive_sha256": SHA},
            "rankings": {k: copy.deepcopy(ranks) for k in ("after_write", "after_reopen", "after_restore")},
            "opposite_profile_open": {"refused": True, "code": "store_profile_mismatch", "requested": "semantic",
                                      "inventory_before_sha256": SHA, "inventory_after_sha256": SHA}}


def test_blocker4_g11_with_one_arbitrary_query_or_no_restored_marker_is_refused():
    """Counterexample of the review: one arbitrary query identical at the three moments; restored_marker None."""
    m = manifest()
    proof = lexical_store_proof()
    proof["rankings"] = {k: {"anything": ["x"]} for k in ("after_write", "after_reopen", "after_restore")}
    refused(gate.validate_lexical_store, proof, m)
    proof = lexical_store_proof()
    proof["restored_marker"] = None
    refused(gate.validate_lexical_store, proof, m)
    for change in (lambda p: [r.pop(worker.LEXICAL_QUERIES[3]) for r in p["rankings"].values()],   # a query missing
                   lambda p: [r.update(extra=[worker.LEXICAL_FACTS[0]]) for r in p["rankings"].values()],
                   lambda p: [r.update({worker.LEXICAL_QUERIES[0]: ["not a fixed fact"]}) for r in p["rankings"].values()],
                   lambda p: p.update(added=11), lambda p: p["restore"].update(archive_sha256=WHEEL_SHA),
                   lambda p: p.update(restored_marker={"name": "semantic", "policy": None})):
        proof = lexical_store_proof()
        change(proof)
        refused(gate.validate_lexical_store, proof, m)


@pytest.mark.parametrize("path,value", [
    (("rankings", "after_restore", worker.LEXICAL_QUERIES[0]), [worker.LEXICAL_FACTS[1], worker.LEXICAL_FACTS[0]]),
    (("rankings", "after_reopen", worker.LEXICAL_QUERIES[1]), []), (("rankings",), {"after_write": {}}),
    (("restored_marker",), None), (("added",), 0),
    (("marker",), {"name": "semantic", "policy": None}), (("marker",), None), (("backup", "ok"), False),
    (("restore", "ok"), False), (("fixed_data_sha256",), WHEEL_SHA), (("scripts_sha256",), {}),
    (("opposite_profile_open", "refused"), False), (("opposite_profile_open", "code"), "store_profile_unverifiable"),
    (("opposite_profile_open", "inventory_after_sha256"), WHEEL_SHA), (("opposite_profile_open", "requested"), "lexical"),
])
def test_lexical_store_proof(path, value):
    m = manifest()
    gate.validate_lexical_store(lexical_store_proof(), m)
    refused(gate.validate_lexical_store, mutate(lexical_store_proof(), path, value), m)


def test_lexical_store_data_and_queries_are_fixed_literals():
    assert len(worker.LEXICAL_FACTS) == 12 and len(worker.LEXICAL_QUERIES) == 10
    assert len(set(worker.LEXICAL_FACTS)) == 12 and worker.fixed_data_sha256() == worker.fixed_data_sha256()
    source = (SCRIPTS / "sqlite_release_worker_410.py").read_text()
    assert "random" not in source and "uuid" not in source


# ---------- G9, G10 ----------

def copy_proof():
    spec = importlib.util.spec_from_file_location("release_409_fixture", ROOT / "tests/test_release_409.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    p, _ = module.proof(COMMIT)
    m = manifest()
    p.update(version="4.1.0", run_tag=TAG, artifacts=m["artifacts"],
             disk_budget={"source_footprint_bytes": 158_000_000, "dynamic_budget_bytes": 5_000_000_000,
                          "required_start_free_bytes": 9_200_000_000, "observed_start_free_bytes": 15_000_000_000},
             lexical_open_refusal={"refused": True, "code": "store_profile_mismatch", "requested": "lexical",
                                   "inventory_before_sha256": SHA, "inventory_after_sha256": SHA, "files_created": []})
    for name, row in p["workers"].items():
        row["encoding_profile"]["encoder_source_sha256"] = gate.EMBED_SHA256
        if name != "baseline":
            row.update(version="4.1.0", retrieval_profile="semantic")
    p["doctor"]["ready"].update(version="4.1.0")
    p["doctor"]["ready"]["encoding_profile"]["encoder_source_sha256"] = gate.EMBED_SHA256
    p["doctor"]["ready"]["capabilities"]["retrieval_profile"] = dict(gate.SEMANTIC_MARKER)
    for s in p["s4"].values():
        s["drill"]["server_check"]["version"] = "4.1.0"
        s["backup"]["archive_sha256"] = s["drill"]["archive_sha256"] = SHA
    p["s4"]["sdist"]["backup"]["archive"] = p["s4"]["sdist"]["drill"]["archive"] = "sdist.mosb"
    p["s4"]["sdist"]["backup"]["archive_sha256"] = p["s4"]["sdist"]["drill"]["archive_sha256"] = WHEEL_SHA
    # One sealed archive (the wheel exercise's) restored by both artifacts.
    drill = copy.deepcopy(p["s4"]["wheel"]["drill"])
    p["s4_common"] = {"archive": "private.mosb", "archive_sha256": SHA, "archive_sha256_after": SHA,
                      "drills": {"wheel": {**copy.deepcopy(drill), "artifact_sha256": WHEEL_SHA},
                                 "sdist": {**copy.deepcopy(drill), "artifact_sha256": SDIST_SHA}}}
    return p, m


def test_blocker7_g10_needs_one_common_sealed_archive_restored_by_both_artifacts():
    p, m = copy_proof()
    gate.validate_copy(p, m)
    assert p["s4"]["wheel"]["backup"]["archive_sha256"] != p["s4"]["sdist"]["backup"]["archive_sha256"]  # two exercises kept
    for change in (lambda p: p.pop("s4_common"), lambda p: p["s4_common"]["drills"].pop("sdist"),
                   lambda p: p["s4_common"]["drills"]["sdist"].update(archive_sha256=WHEEL_SHA, archive="sdist.mosb"),
                   lambda p: p["s4_common"].update(archive_sha256=WHEEL_SHA),
                   lambda p: p["s4_common"].update(archive_sha256_after=WHEEL_SHA),
                   lambda p: p["s4_common"]["drills"]["sdist"].update(artifact_sha256=WHEEL_SHA),
                   lambda p: p["s4_common"]["drills"]["sdist"].update(level="files"),
                   lambda p: p["s4_common"]["drills"]["sdist"]["server_check"].update(commit="f" * 40),
                   lambda p: p["s4_common"]["drills"]["wheel"].update(ok=False)):
        p, m = copy_proof()
        change(p)
        refused(gate.validate_copy, p, m)


def test_copy_proof_passes_with_the_lexical_refusal_addition():
    p, m = copy_proof()
    gate.validate_copy(p, m)


@pytest.mark.parametrize("path,value", [
    (("lexical_open_refusal", "refused"), False), (("lexical_open_refusal", "code"), "other"),
    (("lexical_open_refusal", "inventory_after_sha256"), WHEEL_SHA), (("lexical_open_refusal", "files_created"), ["x-wal"]),
    (("lexical_open_refusal", "requested"), "semantic"),
    (("disk_budget", "required_start_free_bytes"), 5_000_000_000), (("disk_budget", "observed_start_free_bytes"), 9_000_000_000),
    (("disk_budget", "dynamic_budget_bytes"), 12_000_000_000), (("disk_budget", "source_footprint_bytes"), 0),
    (("workers", "wheel", "retrieval_profile"), "lexical"), (("workers", "sdist", "migration"), {"to": 9}),
    (("workers", "wheel-warm", "corpus_encoded_texts"), 3), (("run_tag",), "v8"), (("production_modified",), True),
    (("source_sql_writes",), True), (("baseline_version",), "4.0.9"), (("seconds",), 901),
    (("doctor", "ready", "capabilities", "retrieval_profile"), dict(gate.LEXICAL_MARKER)),
    (("s4", "sdist", "drill", "archive_sha256"), "other"), (("s4", "wheel", "drill", "level"), "files"),
])
def test_copy_proof_refuses_each_gap(path, value):
    p, m = copy_proof()
    refused(gate.validate_copy, mutate(p, path, value), m)


def test_copy_start_threshold_is_the_higher_of_9_2_gb_and_the_dynamic_budget(tmp_path):
    (tmp_path / "store.sqlite3").write_bytes(b"x" * 1000)
    (tmp_path / "store.sqlite3-wal").write_bytes(b"x" * 500)
    (tmp_path / "store.sqlite3-shm").write_bytes(b"x" * 24)
    (tmp_path / "other").write_bytes(b"x" * 99)
    small = sqlite_gate.start_disk_budget(tmp_path, 10**10, policy=lambda size: ("adaptive_v1", 3 * 10**9, 6 * size))
    assert small["source_footprint_bytes"] == 1524  # DB + WAL + SHM, measured now
    assert small["required_start_free_bytes"] == 9_200_000_000 > small["dynamic_budget_bytes"]
    big = sqlite_gate.start_disk_budget(tmp_path, 10**10, policy=lambda size: ("adaptive_v1", 10 * 10**9, 0))
    assert big["required_start_free_bytes"] == big["dynamic_budget_bytes"] > 9_200_000_000
    assert small["dynamic_budget_bytes"] == 3 * 10**9 + 6 * 1524 + 4 * 1524 + 256 * 1024**2 + 3 * 1524
    legacy = sqlite_gate.start_disk_budget(tmp_path, 1, policy=lambda size: ("legacy5gb", 0, 0))
    assert legacy["required_start_free_bytes"] == 9_200_000_000
    refused(sqlite_gate.start_disk_budget, tmp_path / "other", 1, policy=lambda size: ("adaptive_v1", 0, 0))


def test_baseline_interpreter_comes_from_the_plist_without_substitution(tmp_path):
    python = tmp_path / "venv-4.0.7/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("")
    plist = tmp_path / "prod.plist"
    plist.write_bytes(plistlib.dumps({"ProgramArguments": [str(python), "-m", "memory_os_v4.server"],
                                      "EnvironmentVariables": {"MEMORY_V4_DIR": "/s"}}))
    before = plist.read_bytes()
    found, env = sqlite_gate.baseline_from_plist(plist, python)
    assert found == python and env == {"MEMORY_V4_DIR": "/s"} and plist.read_bytes() == before
    refused(sqlite_gate.baseline_from_plist, plist, tmp_path / "other/bin/python")


def test_hashed_inventory_sees_a_content_change_that_keeps_name_size_and_date(tmp_path):
    target = tmp_path / "store.sqlite3"
    target.write_bytes(b"aaaa")
    stamp = target.stat()
    _, before = sqlite_gate.hashed_inventory(tmp_path)
    target.write_bytes(b"bbbb")
    os.utime(target, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    _, after = sqlite_gate.hashed_inventory(tmp_path)
    assert before != after
    rows_a, a = helper.inventory(tmp_path), None
    target.write_bytes(b"cccc")
    os.utime(target, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert helper.inventory_digest(rows_a) != helper.inventory_digest(helper.inventory(tmp_path)) and a is None


# ---------- G13 ----------

def test_brief_perimeter_matches_the_contract_counts_from_git_only():
    report = perimeter.report(ROOT, QUAL_BASE)
    assert {role: row["count"] for role, row in report["closures"].items()} == {
        "p_a_proof": 14, "intermediate": 22, "candidate": 23}
    first, second = report["p_a_proof_to_intermediate"], report["intermediate_to_candidate"]
    assert len(first["joined"]) == 8 and first["left"] == []
    assert [Path(p).name for p in first["changed_among_common"]] == ["models.py", "sqlite_store.py", "store.py"]
    assert [Path(p).name for p in second["joined"]] == ["config.py"]
    assert [Path(p).name for p in second["changed_among_common"]] == ["models.py", "sqlite_store.py"]
    assert all(p in second["identical_among_common"] for p in report["closures"]["candidate"]["roots"])
    assert len(report["closures"]["candidate"]["roots"]) == 5
    assert set(report["callers"]) == set(perimeter.CALLERS)
    assert all(row["changed_intermediate_to_candidate"] for row in report["callers"].values())
    assert report["commits"] == {"p_a_proof": gate.P_A_PROOF_COMMIT, "intermediate": gate.P_A_INTERMEDIATE,
                                 "candidate": QUAL_BASE}
    # Never a transfer, never a replay, deterministic (its SHA-256 can be quoted in a decision).
    assert report["automatic_transfer"] is False and report["conclusion"] == "decision_of_romain_required"
    assert report["git_only"] and not report["sealed_inputs_read"] and not report["brief_executed"]
    assert report == perimeter.report(ROOT, QUAL_BASE)
    assert len(report["union"]) >= 23 and all(set(v) == set(perimeter.ROLES) for v in report["union"].values())


def test_brief_perimeter_only_runs_read_only_git_commands():
    calls = []

    def run(argv, **kw):
        calls.append(argv[:3])
        return subprocess.check_output(argv, **kw)

    perimeter.report(ROOT, QUAL_BASE, run=run)
    assert {tuple(c[:2]) for c in calls} <= {("git", "ls-tree"), ("git", "cat-file")}
    with pytest.raises(ValueError):
        perimeter.report(ROOT, "453a893")
    source = (SCRIPTS / "check_brief_perimeter_410.py").read_text()
    assert "import memory_os_v4" not in source and "from memory_os_v4" not in source


_REAL_PERIMETER = []


def same_perimeter(root, commit):
    """Recomputation stub for a synthetic candidate: the untampered inventory."""
    return perimeter_report()


def test_round2_blocker3_g13_refuses_invalid_cells_and_a_fictitious_resource():
    """Round 2 counterexample: sha256 "INVALID" for callers, dependencies and resources, None in the union
    cells, and a fictitious resource fake/provision_v1.json were all accepted."""
    m = manifest()
    echo = lambda tampered: (lambda root, commit: tampered)  # noqa: E731  even if "Git" agreed, the cells are checked
    report = perimeter_report()
    for section in ("callers", "dependencies", "resources"):
        for row in report[section].values():
            row["sha256"] = {role: "INVALID" for role in perimeter.ROLES}
    for cells in report["union"].values():
        for role in cells:
            cells[role] = None
    report["resources"]["fake/provision_v1.json"] = {"sha256": {role: "INVALID" for role in perimeter.ROLES},
                                                     "changed_p_a_proof_to_intermediate": False,
                                                     "changed_intermediate_to_candidate": False}
    refused(gate.validate_perimeter, report, m, echo(report))
    refused(gate.validate_perimeter, report, m, same_perimeter)
    # Each defect alone, with a recomputation that would agree: the validator itself must refuse.
    defects = (
        lambda p: next(iter(p["callers"].values()))["sha256"].update(candidate="INVALID"),
        lambda p: next(iter(p["dependencies"].values()))["sha256"].update(intermediate="INVALID"),
        lambda p: next(iter(p["resources"].values()))["sha256"].update(p_a_proof="zz"),
        lambda p: p["union"].update({next(iter(p["union"])): {role: None for role in perimeter.ROLES}}),
        lambda p: next(iter(p["union"].values()))["candidate"].update(sha256="INVALID"),
        lambda p: next(iter(p["union"].values()))["candidate"].update(in_closure=False),
        lambda p: p["resources"].update({"fake/provision_v1.json": copy.deepcopy(next(iter(p["resources"].values())))}),
        lambda p: p["closures"]["candidate"]["files"].update({next(iter(p["closures"]["candidate"]["files"])): "INVALID"}),
        lambda p: next(iter(p["callers"].values())).update(changed_intermediate_to_candidate=False),  # flag not derived
        lambda p: next(iter(p["callers"].values()))["sha256"].update(candidate=None),               # caller absent
        lambda p: p["intermediate_to_candidate"].update(joined=[]),                                  # comparison not derived
        lambda p: p["union"].pop(next(iter(p["union"]))),
    )
    for defect in defects:
        report = perimeter_report()
        defect(report)
        refused(gate.validate_perimeter, report, m, echo(report))
    # A historical absence is admissible only where Git has none: here it is invented.
    report = perimeter_report()
    path = next(p for p, cells in report["union"].items() if not cells["p_a_proof"]["in_closure"]
                and cells["p_a_proof"]["sha256"] is not None)
    report["union"][path]["p_a_proof"]["sha256"] = None
    gate.validate_perimeter(report, m, echo(report))          # structurally admissible ...
    refused(gate.validate_perimeter, report, m, same_perimeter)  # ... but not what the Git objects say


def test_round2_blocker3_g13_is_recomputed_from_git_objects_at_the_three_commits():
    m = manifest()
    m["source_commit"] = QUAL_BASE
    m["qualification_tooling_sha256"]["check_brief_perimeter_410.py"] = gate.digest(SCRIPTS / "check_brief_perimeter_410.py")
    real = perimeter.report(ROOT, QUAL_BASE)
    gate.validate_perimeter(copy.deepcopy(real), m)           # default: real read-only git, no stub
    resource = next(iter(real["resources"]))
    for change in (lambda p: p["resources"].pop(resource),     # an inexact resource set
                   lambda p: p["resources"][resource]["sha256"].update(candidate="0" * 64) or
                   p["resources"][resource].update(changed_intermediate_to_candidate=True),
                   lambda p: p["callers"][gate.PERIMETER_CALLERS[0]]["sha256"].update(p_a_proof="1" * 64) or
                   p["callers"][gate.PERIMETER_CALLERS[0]].update(changed_p_a_proof_to_intermediate=True)):
        report = copy.deepcopy(real)
        change(report)
        refused(gate.validate_perimeter, report, m)
    assert "sealed_inputs_read" in real and real["sealed_inputs_read"] is False


def perimeter_report():
    """The real report of the pinned base (read-only git), rebound to the synthetic candidate."""
    if not _REAL_PERIMETER:
        _REAL_PERIMETER.append(perimeter.report(ROOT, QUAL_BASE))
    report = copy.deepcopy(_REAL_PERIMETER[0])
    report["commits"]["candidate"] = COMMIT
    report["tool_sha256"] = SHA
    return report


def test_blocker4_g13_without_dependencies_or_resources_inventory_is_refused():
    """Counterexample of the review: a perimeter report accepted without dependencies nor resources."""
    m = manifest()
    gate.validate_perimeter(perimeter_report(), m, same_perimeter)
    for change in (lambda p: p.pop("dependencies"), lambda p: p.pop("resources"), lambda p: p.update(dependencies={}),
                   lambda p: p.update(resources={}), lambda p: p["dependencies"].pop("v4/pyproject.toml"),
                   lambda p: next(iter(p["resources"].values())).pop("sha256"),
                   lambda p: p["closures"]["candidate"].pop("roots"), lambda p: p.pop("intermediate_to_candidate"),
                   lambda p: p["closures"]["candidate"]["files"].update({"v4/src/memory_os_v4/zz.py": SHA}),
                   lambda p: p.update(limits=[])):
        report = perimeter_report()
        change(report)
        refused(gate.validate_perimeter, report, m, same_perimeter)


@pytest.mark.parametrize("path,value", [
    (("automatic_transfer",), True), (("conclusion",), "transfer"), (("git_only",), False),
    (("sealed_inputs_read",), True), (("brief_executed",), True), (("commits", "candidate"), "f" * 40),
    (("commits", "p_a_proof"), COMMIT), (("callers",), {}), (("union",), {}), (("tool_sha256",), WHEEL_SHA),
])
def test_perimeter_report_never_concludes_a_transfer(path, value):
    m = manifest()
    gate.validate_perimeter(perimeter_report(), m, same_perimeter)
    refused(gate.validate_perimeter, mutate(perimeter_report(), path, value), m, same_perimeter)


# ---------- G12 ----------

def external_proof():
    return {"candidate_commit": COMMIT, "complete": True, "production_modified": False, "inputs_sha256": SHA,
            "scorer_sha256": SHA, "replay_sha256": SHA,
            "scorer_result": {"gates": {g: True for g in gate.P_B_GATES}, "passed": 40, "total": 40, "verdict": True,
                              "replay": True, "inputs_sha256_recomputed": SHA}}


@pytest.mark.parametrize("path,value", [
    (("candidate_commit",), "f" * 40), (("scorer_result", "passed"), 39), (("scorer_result", "gates", "hard_safety_0"), False),
    (("scorer_result", "gates"), {"all_40_pass": True}), (("scorer_sha256",), None), (("replay_sha256",), "x"),
    (("scorer_result", "replay"), False), (("scorer_result", "inputs_sha256_recomputed"), WHEEL_SHA),
    (("scorer_result", "failure_categories"), ["x"]), (("complete",), False),
])
def test_external_p_b_proof_is_bound_to_the_candidate_and_40_of_40(path, value):
    m = manifest()
    gate.validate_external_p_b(external_proof(), m)
    refused(gate.validate_external_p_b, mutate(external_proof(), path, value), m)


# ---------- G14 ----------

def relay_proof():
    cases = []
    for kind, profile in gate.RELAY_CASES:
        case = {"kind": kind, "profile": profile, "ok": True, "exit_code": 0, "facts": 2, "version": "4.1.0",
                "source_commit": COMMIT, "artifact_sha256": WHEEL_SHA if kind == "wheel" else SDIST_SHA,
                "handshake_version": "4.1.0", "brief": {"ok": True, "bytes": 900, "tokens": 300},
                "profile_attested": dict(gate.LEXICAL_MARKER if profile == "lexical" else gate.SEMANTIC_MARKER),
                "store_backend": "sqlite_s1" if profile == "lexical" else "jsonl",
                **{c: True for c in gate.RELAY_CHECKS}}
        if profile == "lexical":
            case.update(embed_model_option_removed=True, find_spec={n: None for n in gate.ML_MODULES})
        cases.append(case)
    return {"ok": True, "source_commit": COMMIT, "run_tag": TAG, "scripts_sha256": {"runner": SHA, "case": SHA},
            "cases": cases}


def test_relay_proof_needs_exactly_the_three_cases_with_attested_profiles():
    m = manifest()
    gate.validate_relay(relay_proof(), m)
    verifier.validate_relay(relay_proof(), m)  # the shared verifier dispatches 4.1.0 to the same rule
    for change in (lambda p: p["cases"].pop(), lambda p: p["cases"].reverse(), lambda p: p["cases"].append(p["cases"][0])):
        proof = relay_proof()
        change(proof)
        refused(gate.validate_relay, proof, m)


@pytest.mark.parametrize("index,key,value", [
    (2, "store_backend", "jsonl"), (2, "embed_model_option_removed", False), (2, "find_spec", {"torch": "present"}),
    (2, "profile_attested", {"name": "semantic", "policy": None}), (0, "profile_attested", None),
    (1, "artifact_sha256", WHEEL_SHA), (0, "no_replay", False), (1, "local_status_during_outage", False),
    (0, "handshake_version", "4.0.9"), (2, "reconnected", False), (0, "failure_reported", False), (1, "facts", 3),
    (2, "brief", {"ok": True, "bytes": 7000, "tokens": 300}),
])
def test_relay_case_gaps_are_refused(index, key, value):
    proof = relay_proof()
    proof["cases"][index][key] = value
    refused(gate.validate_relay, proof, manifest())


def test_relay_matrix_and_lexical_environment():
    import installed_relay_e2e as e2e

    assert [(k, p) for k, _, p, _ in e2e.matrix({"version": "4.1.0"})] == list(gate.RELAY_CASES)
    assert e2e.matrix({"version": "4.1.0"})[2][3] == "lexical_environment"
    assert [(k, p) for k, _, p, _ in e2e.matrix({"version": "4.0.9"})] == [("wheel", None), ("sdist", None)]
    case = pytest.importorskip("installed_relay_case") if False else __import__("installed_relay_case")
    base = {"MEMORY_V4_EMBED_MODEL": "hash", "X": "1"}
    env, removed = case.profile_environment(dict(base), "lexical")
    assert "MEMORY_V4_EMBED_MODEL" not in env and removed is True  # never 'hash' in the lexical case
    assert env["MEMORY_V4_RETRIEVAL_PROFILE"] == "lexical" and env["MEMORY_V4_STORE_BACKEND"] == "sqlite_s1"
    env, removed = case.profile_environment(dict(base), "semantic")
    assert env["MEMORY_V4_EMBED_MODEL"] == "hash" and env["MEMORY_V4_RETRIEVAL_PROFILE"] == "semantic" and removed is False
    assert case.profile_environment(dict(base), None) == (base, None)
    assert "4.1.0" in case.COMPACT_BRIEF_AND_LOCAL_STATUS_VERSIONS


# ---------- M3, exit checks ----------

IDENTITY = [1795344108, 525280]  # kernel start time of the command's process: [seconds, microseconds]


def m3_rows(name, start, *, observation="during", command_seconds=3.0, probe_rc=0, pid=4242):
    """Rows as the chain writes them for one step: bounds of the command and of each probe, monotonic clock."""
    base = {"step": name, "free_bytes": int(15e9 - start), "swap_bytes": int(start)}
    cmd_start = start + 0.4          # the supervisor's own checks come first
    end = cmd_start + command_seconds + 0.5
    rows = [{**base, "phase": "before", "t": 1e9 + start, "mono": start}]
    probe = {**base, "command_pid": pid, "command_start_mono": cmd_start, "detection": "ok",
             "command_identity": IDENTITY, "identity_verified": True,
             "post_exec_at_probe_start": observation != "none_short_command"}
    if observation == "during":
        rows.append({**probe, "phase": "during", "t": 1e9 + cmd_start + 1, "mono": cmd_start + 1.1,
                     "probe_start_mono": cmd_start + 1.0, "probe_end_mono": cmd_start + 1.1,
                     "alive_at_probe_start": True, "alive_at_probe_end": True})
    elif observation == "overlap_only":
        rows.append({**probe, "phase": "during", "t": 1e9 + cmd_start, "mono": cmd_start + 0.2,
                     "probe_start_mono": cmd_start + 0.05, "probe_end_mono": cmd_start + 0.2,
                     "alive_at_probe_start": True, "alive_at_probe_end": False})
    elif observation == "none_short_command":
        rows.append({**probe, "phase": "missed", "t": 1e9 + cmd_start, "mono": cmd_start + 0.2,
                     "probe_start_mono": cmd_start + 0.1, "probe_end_mono": cmd_start + 0.2,
                     "alive_at_probe_start": False, "alive_at_probe_end": False,
                     "reason": "command_ended_before_first_probe"})
    rows.append({**base, "phase": "after", "t": 1e9 + end, "mono": end, "probe_rc": probe_rc, "supervisor_rc": 0,
                 "command": {"started": True, "pid": pid, "identity": IDENTITY, "start_mono": cmd_start,
                             "start_t": 1e9 + cmd_start}})
    return rows


def samples(steps=gate.CHAIN_STEPS, **kw):
    rows, start = [], 1000.0
    for name in steps:
        rows += m3_rows(name, start, **kw)
        start += 10
    return [json.dumps(row) for row in rows]


def test_m3_summary_reports_sampled_extrema_and_requires_usable_samples():
    summary = gate.m3_summary(samples())
    assert summary["min_free_bytes_observed"] < 15e9 and summary["max_swap_bytes_observed"] > 1000
    assert set(summary["step_seconds"]) == set(gate.CHAIN_STEPS) and "not guaranteed" in summary["nature"]
    assert summary["clock"] == "monotonic" and set(summary["observation"].values()) == {"during"}
    assert summary["short_command_exceptions"] == {} and all(c == 1 for c in summary["during_samples"].values())
    assert summary["command_seconds_upper_bound"]["full"] == pytest.approx(3.5)  # bounds of the command, kept apart
    assert summary["step_seconds"]["full"] == pytest.approx(3.9)
    refused(gate.m3_summary, [])
    refused(gate.m3_summary, [json.dumps({"t": 1, "mono": 1, "phase": "before", "step": "full", "free_bytes": None, "swap_bytes": 1})])
    refused(gate.m3_summary, [json.dumps({"t": 1, "phase": "before", "step": "full", "free_bytes": 1, "swap_bytes": 1})])
    # Run-bracket samples carry no step: they count for the extrema only.
    bracket = [json.dumps({"t": 1.0, "mono": 1.0, "phase": "before", "step": "", "free_bytes": 5, "swap_bytes": 9 * 10**9})]
    assert gate.m3_summary(bracket + samples(["full"]))["max_swap_bytes_observed"] == 9 * 10**9


def m3(rows):
    return gate.m3_summary([json.dumps(row) for row in rows])


def test_blocker8_m3_without_any_sample_during_the_step_is_refused():
    """Round 1 counterexample: two samples (before, after), no ``during``: the summary was accepted."""
    rows = m3_rows("full", 100.0)
    refused(m3, [rows[0], rows[-1]])                       # nothing observed, nothing explained
    assert m3(rows)["observation"] == {"full": "during"}


def test_round2_blocker5_a_probe_finished_before_the_command_started_never_counts():
    """Round 2: the probe started before the supervisor launched the command and could end before it."""
    rows = m3_rows("full", 100.0)
    start = rows[-1]["command"]["start_mono"]
    early = {**rows[1], "probe_start_mono": start - 0.3, "probe_end_mono": start - 0.1, "mono": start - 0.1}
    refused(m3, [rows[0], early, rows[-1]])                # between "before" and "after", yet before the launch
    straddling = {**rows[1], "probe_start_mono": start - 0.1, "probe_end_mono": start + 0.2}
    refused(m3, [rows[0], straddling, rows[-1]])           # started before the command: never a "during"
    blind = {k: v for k, v in rows[1].items() if k not in ("probe_start_mono", "probe_end_mono")}
    refused(m3, [rows[0], blind, rows[-1]])                # no probe bounds: no demonstrated overlap
    refused(m3, [rows[0], {**rows[1], "alive_at_probe_start": False}, rows[-1]])
    refused(m3, [rows[0], {**rows[1], "command_pid": 1}, rows[-1]])              # a probe of another process
    refused(m3, [rows[0], {**rows[1], "probe_end_mono": rows[-1]["mono"] + 5}, rows[-1]])  # ended after the step closed
    no_bounds = {**rows[-1], "command": {"started": False}}
    refused(m3, [rows[0], rows[1], no_bounds])             # the bounds of the command are required
    refused(m3, [rows[0], rows[1], {k: v for k, v in rows[-1].items() if k != "command"}])


def test_round2_blocker5_short_commands_are_flagged_never_given_an_invented_sample():
    overlap = m3(m3_rows("rec_source", 100.0, observation="overlap_only", command_seconds=0.1))
    assert overlap["observation"] == {"rec_source": "overlap_only"} and overlap["during_samples"] == {"rec_source": 0}
    assert overlap["short_command_exceptions"] == {"rec_source": "overlap_only"}  # flagged in the summary
    missed = m3(m3_rows("lint1", 100.0, observation="none_short_command", command_seconds=0.05))
    assert missed["short_command_exceptions"] == {"lint1": "none_short_command"} and missed["during_samples"] == {"lint1": 0}
    # The exception is only credible for a short command, and only when the probe itself reported it.
    refused(m3, m3_rows("copy", 100.0, observation="none_short_command", command_seconds=300.0))
    rows = m3_rows("lint1", 100.0, observation="none_short_command", command_seconds=0.05)
    refused(m3, [rows[0], {**rows[1], "reason": "other"}, rows[-1]])
    refused(m3, [rows[0], {**rows[1], "alive_at_probe_start": True}, rows[-1]])
    mixed = gate.m3_summary(samples(["full"]) + [json.dumps(r) for r in m3_rows("lint1", 500.0, observation="overlap_only")])
    assert mixed["observation"] == {"full": "during", "lint1": "overlap_only"}
    exit_like = {**exit_proof(["full", "lint1"]), "m3": mixed}
    exit_like["m3"] = {**mixed, "short_command_exceptions": {}}  # an exception hidden from the summary
    refused(gate.validate_exit_checks, {**exit_like, "steps_ok": ["full", "lint1"]}, manifest())


def test_round2_blocker5_a_dead_probe_without_any_error_file_is_blocking():
    """The sampler status is carried by its exit code in the ``after`` row: no marker file is needed."""
    refused(m3, m3_rows("full", 100.0, probe_rc=1))
    refused(m3, m3_rows("full", 100.0, probe_rc=137))
    rows = m3_rows("full", 100.0)
    refused(m3, [rows[0], rows[1], {k: v for k, v in rows[-1].items() if k != "probe_rc"}])  # status unknown
    chain = (Path.home() / "memory-os-local/ops/qualify_410_v1_chain.sh")
    if chain.is_file():  # outside the repository; its own tests exercise the behaviour
        text = chain.read_text()
        assert 'wait $probe_pid; probe_rc=$?' in text and "stop_sampler" not in text and "sampler.failed" not in text


def exit_proof(steps=None, samples_path="/nonexistent/m3_samples.jsonl", samples_sha256=SHA):
    steps = list(steps or [s for s in gate.CHAIN_STEPS if s not in gate.OPTIONAL_STEPS])
    return {"format": "qualify-410-exit-checks-v1", "source_commit": COMMIT, "run_tag": TAG, "chain_sha256": CHAIN_SHA,
            "supervisor_sha256": gate.SUPERVISOR_SHA256, "steps_ok": steps, "test_label_absent": True,
            "test_port_free": True, "no_suspect_process": True, **{flag: True for flag in gate.PRODUCTION_FLAGS},
            "production_before_sha256": SHA, "production_after_sha256": SHA, "lock_409_absent": True,
            "m3": gate.m3_summary(samples(steps)), "m3_samples_sha256": samples_sha256, "m3_samples_path": samples_path}


def test_exit_checks_require_every_step_in_order_and_every_control():
    m = manifest()
    gate.validate_exit_checks(exit_proof(), m)
    gate.validate_exit_checks(exit_proof(gate.CHAIN_STEPS), m)  # optional steps present, in order
    required = [s for s in gate.CHAIN_STEPS if s not in gate.OPTIONAL_STEPS]
    refused(gate.validate_exit_checks, exit_proof(required[:-1]), m)            # a step missing
    refused(gate.validate_exit_checks, exit_proof(required[1:] + required[:1]), m)  # out of order
    refused(gate.validate_exit_checks, exit_proof(required + ["rec_external"]), m)  # optional step out of place
    for key in ("test_label_absent", "test_port_free", "no_suspect_process", *gate.PRODUCTION_FLAGS,
                "lock_409_absent"):
        refused(gate.validate_exit_checks, {**exit_proof(), key: False}, m)
    assert "real_memory_os_config_unchanged" in gate.PRODUCTION_FLAGS
    refused(gate.validate_exit_checks, {**exit_proof(), "production_after_sha256": WHEEL_SHA}, m)
    broken = exit_proof()
    broken["m3"]["observation"].pop("full")  # a step without any observation
    refused(gate.validate_exit_checks, broken, m)
    refused(gate.validate_exit_checks, {**exit_proof(), "chain_sha256": SHA}, m)
    refused(gate.validate_exit_checks, {**exit_proof(), "supervisor_sha256": SHA}, m)
    refused(gate.validate_exit_checks, {**exit_proof(), "run_tag": "v8"}, m)
    refused(gate.validate_exit_checks, {**exit_proof(), "m3": {}}, m)


# ---------- recording commands and the final verification (G15) ----------

class Args:
    def __init__(self, manifest_path, **options):
        self.manifest = manifest_path
        for name in (*gate.RECORD_OPTIONS, *gate.UNSUPPORTED):
            setattr(self, name, options.get(name))


@pytest.fixture
def release(tmp_path, monkeypatch):
    """A manifest directory where artifact/source binding is assumed (tested on its own above)."""
    directory = tmp_path / "release"
    directory.mkdir()
    m = manifest()
    for g in ("G1", "G2", "G3"):
        gate.set_gate(m, g, "pass")
    path = directory / "manifest.json"
    gate.write(path, m)
    monkeypatch.setattr(gate, "validate_local", lambda *a, **k: None)
    # G13 recomputes its inventory from Git; the synthetic candidate has no Git objects.
    monkeypatch.setattr(gate, "perimeter_inventory", lambda root, commit: perimeter_report())
    inputs = tmp_path / "inputs"
    inputs.mkdir()

    def put(name, value):
        target = inputs / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value))
        return target

    def sealed(folder, report, name):
        """A proof in its own folder, with the supporting pieces it seals (as each harness leaves them)."""
        origin = inputs / folder
        origin.mkdir(parents=True)
        report, inventory = copy.deepcopy(report), {}
        for rel in gate.required_pieces(name, report):  # sub-folders, archives and compared documents included
            piece = origin / rel
            piece.parent.mkdir(parents=True, exist_ok=True)
            piece.write_text("piece " + rel)
            inventory[rel] = gate.digest(piece)
        report["evidence_sha256"] = inventory
        if name == "copy":  # the sealed common archive carries the SHA-256 the report states
            sha = inventory["s4-wheel/backups/" + report["s4_common"]["archive"]]
            wheel = report["s4"]["wheel"]
            wheel["backup"]["archive_sha256"] = wheel["drill"]["archive_sha256"] = sha
            report["s4_common"].update(archive_sha256=sha, archive_sha256_after=sha)
            for drill in report["s4_common"]["drills"].values():
                drill["archive_sha256"] = sha
        if name == "lexical_store":
            sha = inventory["backups/" + report["backup"]["archive"]]
            report["backup"]["archive_sha256"] = report["restore"]["archive_sha256"] = sha
        (origin / "report.json").write_text(json.dumps(report))
        return origin / "report.json"

    def go(name, **changes):
        """A decision file of Romain, with its dossier beside it for a network GO."""
        if name.startswith("network_"):
            write_dossier(inputs / "d", name[-2:])
        return put(f"d/{name}.json", decision(name, **changes))

    def exit_file(**changes):
        folder = inputs / ("exit-%d" % len(list(inputs.iterdir())))
        folder.mkdir()
        rows = folder / "m3_samples.jsonl"
        rows.write_text("\n".join(samples([s for s in gate.CHAIN_STEPS if s not in gate.OPTIONAL_STEPS])) + "\n")
        proof = {**exit_proof(samples_path=str(rows), samples_sha256=gate.digest(rows)), **changes}
        (folder / "exit.json").write_text(json.dumps(proof))
        return folder / "exit.json"

    def run(**options):
        return gate.main(Args(path, **options))

    def load():
        return json.loads(path.read_text())

    return type("Release", (), {"dir": directory, "path": path, "put": staticmethod(put), "run": staticmethod(run),
                                "load": staticmethod(load), "inputs": inputs, "sealed": staticmethod(sealed),
                                "go": staticmethod(go), "exit_file": staticmethod(exit_file)})


def record_everything(release, *, variant="autonomous", g12="waiver"):
    """Every proof and decision of a full run, in chain order. Returns the sha of the recorded proofs."""
    m = manifest()
    pre = ["launch", "launchd_nice", "network_N1", "network_N2", "network_N3", "network_N4", "network_N6",
           "network_N5" if variant == "autonomous" else "semantic_pth_variant"]
    for name in pre:
        assert release.run(record_decision=release.go(name)) == 0
    assert release.run(record_source_proof=release.put("source.json", {"ok": True})) == 0
    for profile in ("lexical", "semantic"):
        folder = release.inputs / ("res-" + profile)
        folder.mkdir()
        report, pip = resolution(m, profile, folder)
        pip.rename(folder / "pip-report.json")
        assert release.run(record_resolution_proof=release.put(f"res-{profile}/report.json", report)) == 0
    proof, _ = install_proof("lexical")
    lexical_install = release.sealed("install-lexical", proof, "install_lexical")
    assert release.run(record_install_proof=lexical_install) == 0
    assert release.run(record_lexical_proof=release.sealed("lexical-wheel", lexical_wheel_proof(), "lexical_wheel")) == 0
    assert release.run(record_lexical_proof=release.sealed("lexical-store", lexical_store_proof(), "lexical_store")) == 0
    proof, _ = install_proof("semantic", variant)
    assert release.run(record_install_proof=release.sealed("install-semantic", proof, "install_semantic")) == 0
    copy_report, _ = copy_proof()
    assert release.run(record_copy_proof=release.sealed("copy", copy_report, "copy")) == 0
    report = release.put("perimeter.json", perimeter_report())
    assert release.run(record_perimeter_report=report) == 0
    sha = {"perimeter": gate.digest(report), "install_lexical": gate.digest(lexical_install)}
    assert release.load()["gates"]["G13"]["state"] == "pending"  # recorded, never concluded
    assert release.run(record_decision=release.go(
        "g13_p_a", evidence=[{"reference": "perimeter", "sha256": sha["perimeter"]}])) == 0
    assert release.run(record_decision=release.go(
        "d1_resign", measure={"reference": "install", "sha256": sha["install_lexical"]})) == 0
    if g12 == "waiver":
        assert release.run(record_decision=release.go("g12_waiver")) == 0
    elif g12 == "proof":
        external = release.put("pb.json", external_proof())
        assert release.run(record_decision=release.go(
            "g12_external_proof", proof={"reference": "pb", "sha256": gate.digest(external)})) == 0
        assert release.run(record_external_proof=external) == 0
    assert release.run(record_relay_proof=release.put("relays.json", relay_proof())) == 0
    return sha


@pytest.mark.parametrize("variant,g12", [("autonomous", "waiver"), ("pth", "proof")])
def test_only_the_final_verification_raises_release_ready(release, variant, g12):
    record_everything(release, variant=variant, g12=g12)
    m = release.load()
    assert m["release_ready"] is False  # no recording command raises the guard, the relay one included
    assert m["gates"]["G15"]["state"] == "pending" and m["gates"]["G14"]["state"] == "pass"
    assert m["gates"]["G12"]["state"] == ("waived_by_romain" if g12 == "waiver" else "pass")
    assert m["gates"]["G13"]["state"] == "waived_by_romain" and m["gates"]["G13"]["decision"] == "g13_p_a"
    assert set(m["measures"]["M1"]) == {"lexical", "semantic"} and m["measures"]["M2"]["state"] == "non mesuré"
    assert m["measures"]["M1"]["lexical"]["pip_downloaded_bytes_measured"] is False
    assert len(m["resolved_distributions"]["lexical"]) == 37
    assert m["decisions"]["network_N3"]["dossier"]["sha256"] == hashlib.sha256(dossier_text("N3").encode()).hexdigest()
    assert release.run() == 0 and release.load()["release_ready"] is False  # plain check: read-only
    assert release.run(finalize=release.exit_file(no_suspect_process=False)) == 1
    assert release.run(finalize=release.exit_file(test_port_free=False)) == 1
    assert release.load()["release_ready"] is False
    assert release.run(finalize=release.exit_file()) == 0
    m = release.load()
    assert m["release_ready"] is True and m["gates"]["G15"]["state"] == "pass" and m["measures"]["M3"]["samples"] > 0
    assert (release.dir / m["evidence"]["m3_samples"]["path"]).is_file()  # the samples themselves are kept, sealed
    # A manifest alone is not a success: CHAIN_RESULT=PASS must exist and match.
    result = release.dir / "CHAIN_RESULT"
    assert release.run(check_final=result) == 1
    result.write_text(f"PASS {COMMIT} {SHA} {TAG} 2026-11-23 10:00:00\n")
    assert release.run(check_final=result) == 1
    (release.dir / "CHAIN_RESULT.invalid").write_text(m["chain_result_expected"] + " 2026-11-23 10:00:00\n")
    assert release.run(check_final=release.dir / "CHAIN_RESULT.invalid") == 1
    result.write_text(m["chain_result_expected"] + " 2026-11-23 10:00:00\n")
    assert release.run(check_final=result) == 0
    # Nothing can be recorded or finalised again afterwards.
    assert release.run(finalize=release.exit_file()) == 1
    assert release.run(record_decision=release.go("m2_lexical")) == 1


def finalised(release):
    record_everything(release)
    assert release.run(finalize=release.exit_file()) == 0
    m = release.load()
    result = release.dir / "CHAIN_RESULT"
    result.write_text(m["chain_result_expected"] + " 2026-11-23 10:00:00\n")
    assert release.run(check_final=result) == 0
    return m, result


def test_blocker9_a_truncated_or_extended_chain_result_is_refused(release):
    """Counterexample of the review: a file stopping after ``PASS <commit> <chain_sha> <tag>`` was accepted."""
    m, result = finalised(release)
    good = m["chain_result_expected"] + " 2026-11-23 10:00:00\n"
    for text in (m["chain_result_expected"],                       # truncated after the tag
                 m["chain_result_expected"] + "\n",
                 m["chain_result_expected"] + " 2026-11-23\n",      # truncated inside the date
                 good[:-1],                                         # write cut before the final newline
                 good + "PASS again\n", good + "\n", " " + good,   # anything more
                 good.replace("PASS", "FAIL"), "", good.replace(TAG, "v8"),
                 good.replace(COMMIT, "f" * 40), good.replace(CHAIN_SHA, SHA)):
        result.write_text(text)
        assert release.run(check_final=result) == 1, repr(text)
        refused(gate.check_chain_result, text, m)
    result.write_text(good)
    assert release.run(check_final=result) == 0
    gate.check_chain_result(good, m)
    result.unlink()
    result.symlink_to(release.dir / "manifest.json")
    assert release.run(check_final=result) == 1


SEALED_SAMPLES = (
    ("install-lexical", "install1/doctor.json"), ("install-semantic", "install2/uninstall.json"),
    ("lexical-wheel", "lexical-wheel.xml"), ("lexical-store", "semantic-refusal.json"), ("copy", "wheel-warm.json"),
    # Round 2, blocker 4: pieces living in sub-folders.
    ("copy", "s4-wheel/s4.log"), ("copy", "s4-sdist/s4.log"), ("copy", "s4-common-sdist/s4.log"),
    ("copy", "s4-wheel/backups/private.mosb"),                              # the common sealed archive itself
    ("lexical-store", "backups/lexical.mosb"),
    ("install-lexical", "install2/state_before/quickstart/S1/journal.json"),  # documents of the G8 comparison
    ("install-semantic", "install2/state_after/quickstart/S1/journal.json"),
)


def test_blocker4_sealed_pieces_are_replayed_at_the_final_verification(release):
    """Deleted or corrupted AFTER recording, BEFORE G15: the final verification refuses."""
    record_everything(release)
    for folder, piece in SEALED_SAMPLES:
        target = release.inputs / folder / piece
        kept = target.read_text()
        target.write_text(kept + " tampered")
        assert release.run(finalize=release.exit_file()) == 1, piece
        target.unlink()
        assert release.run(finalize=release.exit_file()) == 1, piece  # an absent piece is a failure too
        target.write_text(kept)
    assert release.load()["release_ready"] is False
    assert release.run(finalize=release.exit_file()) == 0


def test_round2_blocker4_sealed_pieces_are_replayed_after_g15_at_check_final(release):
    """Deleted or corrupted AFTER G15: --check-final refuses, sub-folder pieces included."""
    m, result = finalised(release)
    for folder, piece in SEALED_SAMPLES:
        target = release.inputs / folder / piece
        kept = target.read_text()
        target.write_text(kept + " tampered")
        assert release.run(check_final=result) == 1, piece
        target.unlink()
        assert release.run(check_final=result) == 1, piece
        target.write_text(kept)
        assert release.run(check_final=result) == 0, piece


def test_round2_blocker4_reports_must_seal_their_subfolder_pieces_and_archives(release, tmp_path):
    copy_report, m = copy_proof()
    names = gate.required_pieces("copy", copy_report)
    assert set(gate.S4_LOGS) <= set(names) and "s4-wheel/backups/private.mosb" in names
    assert "backups/lexical.mosb" in gate.required_pieces("lexical_store", lexical_store_proof())
    proof, _ = install_proof("lexical")
    assert "install2/state_before/quickstart/S1/journal.json" in gate.required_pieces("install_lexical", proof)
    # A copy report sealing only its top-level files (the previous behaviour) is not recorded.
    path = release.sealed("copy-top", copy_report, "copy")
    report = json.loads(path.read_text())
    for rel in list(report["evidence_sha256"]):
        if "/" in rel:
            report["evidence_sha256"].pop(rel)
    path.write_text(json.dumps(report))
    assert release.run(record_copy_proof=path) == 1
    # The sealed archive must be the one the report states.
    path = release.sealed("copy-other-archive", copy_report, "copy")
    report = json.loads(path.read_text())
    (path.parent / "s4-wheel/backups/private.mosb").write_text("another archive")
    report["evidence_sha256"]["s4-wheel/backups/private.mosb"] = gate.digest(path.parent / "s4-wheel/backups/private.mosb")
    path.write_text(json.dumps(report))
    assert release.run(record_copy_proof=path) == 1
    # An install report whose compared state documents are not kept is not recorded.
    bare, _ = install_proof("lexical")
    bare["install_2"]["data_conservation"]["compared_documents"] = []
    assert release.run(record_install_proof=release.sealed("install-nostate", bare, "install_lexical")) == 1
    # The harness itself seals the named sub-folder pieces and fails when one is missing.
    root = tmp_path / "harness"
    (root / "s4-wheel").mkdir(parents=True)
    (root / "plan.json").write_text("{}")
    (root / "report.json").write_text("{}")
    (root / "s4-wheel" / "s4.log").write_text("log")
    sealed = sqlite_gate.sealed_pieces(root, ["s4-wheel/s4.log"])
    assert set(sealed) == {"plan.json", "s4-wheel/s4.log"}
    refused(sqlite_gate.sealed_pieces, root, ["s4-sdist/s4.log"])
    source = (SCRIPTS / "qualify_sqlite_release_410.py").read_text()
    for name in ("s4-wheel/s4.log", "s4-sdist/s4.log", "s4-common-sdist/s4.log", '"s4-wheel/backups/" + common["archive"]'):
        assert name in source


def test_blocker4_a_report_without_its_required_pieces_is_not_recorded(release):
    proof, _ = install_proof("lexical")
    assert release.run(record_install_proof=release.put("bare/report.json", proof)) == 1          # no inventory at all
    path = release.sealed("partial", proof, "install_lexical")
    report = json.loads(path.read_text())
    report["evidence_sha256"].pop("install2/data_after.json")
    path.write_text(json.dumps(report))
    assert release.run(record_install_proof=path) == 1                                             # a required piece unsealed
    path = release.sealed("escape", proof, "install_lexical")
    report = json.loads(path.read_text())
    report["evidence_sha256"]["../outside.json"] = SHA
    path.write_text(json.dumps(report))
    assert release.run(record_install_proof=path) == 1
    assert release.run(record_lexical_proof=release.put("bare2/report.json", lexical_wheel_proof())) == 1
    copy_report, _ = copy_proof()
    assert release.run(record_copy_proof=release.put("bare3/report.json", copy_report)) == 1


def test_blocker8_sealed_samples_are_reverified_at_check_final(release):
    m, result = finalised(release)
    stored = release.dir / m["evidence"]["m3_samples"]["path"]
    kept = stored.read_text()
    stored.write_text(kept.replace('"alive_at_probe_end": true', '"alive_at_probe_end": false', 1))
    assert release.run(check_final=result) == 1
    stored.write_text(kept)
    assert release.run(check_final=result) == 0


def test_blocker8_exit_checks_whose_samples_do_not_match_are_refused(release):
    record_everything(release)
    path = release.exit_file()
    proof = json.loads(path.read_text())
    Path(proof["m3_samples_path"]).write_text(Path(proof["m3_samples_path"]).read_text() + "\n")
    assert release.run(finalize=path) == 1                       # samples changed after their hash was taken
    assert release.run(finalize=release.exit_file(m3_samples_path="/nonexistent/samples.jsonl")) == 1
    assert release.run(finalize=release.exit_file()) == 0


def test_blocker1_the_sealed_dossier_is_rechecked_at_the_final_verification(release):
    record_everything(release)
    m = release.load()
    sealed_dossier = release.dir / m["decisions"]["network_N4"]["dossier"]["path"]
    kept = sealed_dossier.read_text()
    sealed_dossier.write_text(kept.replace("pypi.org", "example.org"))
    assert release.run(finalize=release.exit_file()) == 1
    sealed_dossier.write_text(kept)
    m["decisions"]["network_N4"].pop("dossier")
    gate.write(release.path, m)
    assert release.run(finalize=release.exit_file()) == 1          # a network GO without its sealed dossier
    # Recording a network GO whose dossier is absent, or not the effective acquisition, is refused.
    assert release.run(record_decision=release.put("lone/network_N5.json", decision("network_N5"))) == 1


def finalize(release):
    return release.run(finalize=release.exit_file())


def test_final_verification_refuses_when_a_gate_or_a_required_decision_is_missing(release):
    record_everything(release)
    for name in ("d1_resign", "launch", "launchd_nice", "network_N3", "network_N6", "g13_p_a"):
        m = release.load()
        kept = m["decisions"].pop(name)
        gate.write(release.path, m)
        assert finalize(release) == 1 and release.load()["release_ready"] is False
        m["decisions"][name] = kept
        gate.write(release.path, m)
    for g in ("G4", "G5", "G6", "G7", "G8", "G9", "G10", "G11", "G14"):
        m = release.load()
        m["gates"][g]["state"] = "pending"
        gate.write(release.path, m)
        assert finalize(release) == 1
        m["gates"][g]["state"] = "fail"
        gate.write(release.path, m)
        assert finalize(release) == 1
        m["gates"][g]["state"] = "waived_by_romain"  # no waiver exists outside G12/G13
        gate.write(release.path, m)
        assert finalize(release) == 1
        m["gates"][g]["state"] = "pass"
        gate.write(release.path, m)
    assert finalize(release) == 0


def test_g12_pending_blocks_and_transfer_accepted_never_exists_for_g12(release):
    record_everything(release, g12="none")
    assert release.load()["gates"]["G12"]["state"] == "pending"
    assert finalize(release) == 1
    m = release.load()
    m["gates"]["G12"].update(state="transfer_accepted", decision="g12_waiver")
    gate.write(release.path, m)
    assert finalize(release) == 1
    assert release.run(record_decision=release.put("w.json", decision("g12_waiver", status="transfer_accepted"))) == 1


def test_g13_needs_a_reasoned_decision_referencing_this_perimeter_report(release):
    report = release.put("perimeter.json", perimeter_report())
    # A decision before the report, or quoting another report, is refused.
    assert release.run(record_decision=release.put("early.json", decision("g13_p_a"))) == 1
    assert release.run(record_perimeter_report=report) == 0
    assert release.load()["gates"]["G13"]["state"] == "pending"
    assert release.run(record_decision=release.put("other.json", decision("g13_p_a"))) == 1
    refusal = decision("g13_p_a", answer="non")
    assert release.run(record_decision=release.put("no.json", refusal)) == 0
    assert release.load()["gates"]["G13"]["state"] == "pending"  # a refusal keeps release_ready false
    assert release.run(record_decision=release.put("late.json", decision(
        "g13_p_a", evidence=[{"reference": "p", "sha256": gate.digest(report)}]))) == 1  # one answer per decision


def test_d1_resignature_must_be_on_this_runs_lexical_measure(release):
    record_everything(release)
    m = release.load()
    target = release.dir / m["decisions"]["d1_resign"]["path"]
    target.write_text(json.dumps(decision("d1_resign", measure={"reference": "old", "sha256": SHA})))
    m["decisions"]["d1_resign"]["sha256"] = gate.digest(target)
    gate.write(release.path, m)
    assert finalize(release) == 1


def test_semantic_variant_decision_must_match_what_the_run_did(release):
    record_everything(release, variant="pth")
    m = release.load()
    m["decisions"]["network_N5"] = m["decisions"].pop("semantic_pth_variant")  # N5 never selects .pth
    gate.write(release.path, m)
    assert finalize(release) == 1


def test_recordings_are_single_shot_unmixed_and_evidence_is_tamper_evident(release):
    proof, _ = install_proof("lexical")
    path = release.sealed("install", proof, "install_lexical")
    assert release.run(record_install_proof=path) == 0
    assert release.run(record_install_proof=path) == 1  # earlier evidence is kept, never replaced
    assert release.run(record_install_proof=path, record_decision=path) == 1
    assert release.run(record_gate_proof=path) == 1 and release.run(record_full_proof=path) == 1
    m = release.load()
    assert m["gates"]["G7"]["state"] == "pending"  # both profiles are required
    (release.dir / m["evidence"]["install_lexical"]["path"]).write_text("{}")
    refused(gate.evidence, m, release.dir, "install_lexical")


def test_external_proof_and_decisions_under_a_sealed_folder_are_refused_unread(release, tmp_path):
    sealed = tmp_path / "brief_v5_val" / "pb"
    assert release.run(record_external_proof=sealed / "proof.json") == 1
    assert release.run(record_decision=sealed / "launch.json") == 1
    assert not sealed.exists()


def test_other_release_branches_refuse_the_4_1_0_options(tmp_path, capsys):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"version": "4.0.5"}))
    for option in ("--record-decision", "--finalize", "--record-install-proof", "--check-final"):
        assert verifier.main([str(path), option, str(path)]) == 1
    assert "4.1.0" in capsys.readouterr().out


def test_verifier_dispatches_4_1_0_and_plain_check_never_raises_the_guard(release):
    assert verifier.main([str(release.path)]) == 0
    assert release.load()["release_ready"] is False
    proof, _ = install_proof("semantic", "pth")
    assert verifier.main([str(release.path), "--record-install-proof", str(release.sealed("s", proof, "install_semantic"))]) == 0
    assert release.load()["release_ready"] is False and "install_semantic" in release.load()["evidence"]


# ---------- tooling inventory ----------

@pytest.fixture
def outside_repo():
    """A directory outside this checkout: pytest's ``--basetemp`` (hence ``tmp_path``) may live inside it."""
    with tempfile.TemporaryDirectory() as name:
        directory, root = Path(os.path.realpath(name)), Path(os.path.realpath(ROOT))
        assert directory != root and root not in directory.parents, (
            f"the temporary directory {directory} is inside the repository {root}: point TMPDIR elsewhere")
        yield directory


def test_tool_inventory_exists_and_seals_the_external_chain_and_supervisor(outside_repo):
    assert all((SCRIPTS / name).is_file() for name in maker.TOOLS) and len(set(maker.TOOLS)) == len(maker.TOOLS)
    for name in ("release_410_checks.py", "make_release_410.py", "source_release_checks_410.py",
                 "check_brief_perimeter_410.py", "prepare_release_410_envs.py", "qualify_install_410.sh",
                 "qualify_lexical_wheel_410.py", "qualify_sqlite_release_410.py", "sqlite_release_worker_410.py",
                 "verify_release_manifest.py", "installed_relay_e2e.py", "installed_relay_case.py",
                 "qualify_405_test_isolation.py", "qualify_distribution_d1a.py", "qualify_runtime_resolution.py",
                 "check_runtime_resolution.py", gate.PYTEST_PINS):
        assert name in maker.TOOLS
    # Excluded from this chain: never sealed, never called.
    assert not {"score_release_410_gate.py", "record_release_410_gate.py", "run_integrity_v5_validation.py"} & set(maker.TOOLS)
    chain = outside_repo / gate.CHAIN_NAME  # external tooling lives outside the repository
    chain.write_text("#!/bin/zsh\n")
    assert maker.external_tool(chain, gate.CHAIN_NAME)["sha256"] == gate.digest(chain)
    refused(maker.external_tool, chain, gate.SUPERVISOR_NAME)
    refused(maker.external_tool, Path(gate.CHAIN_NAME), gate.CHAIN_NAME)
    refused(maker.external_tool, SCRIPTS / "make_release_410.py", "make_release_410.py")  # must live outside the repo
    link = outside_repo / "link" / gate.CHAIN_NAME
    link.parent.mkdir()
    link.symlink_to(chain)
    refused(maker.external_tool, link, gate.CHAIN_NAME)
    record = {"chain": maker.external_tool(chain, gate.CHAIN_NAME),
              "supervisor": {"path": str(chain), "sha256": gate.SUPERVISOR_SHA256}}
    refused(gate.verify_external_tooling, record)  # wrong file name / bytes for the supervisor
    assert gate.SUPERVISOR_SHA256 == "483c0e8d5d951adbb64c2607c782ee36e2a6af547d05df8bb07b95754e8bded2"


def test_entry_points_are_read_from_the_built_wheel(tmp_path):
    def wheel(scripts):
        path = tmp_path / ("w%d.whl" % len(list(tmp_path.iterdir())))
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("memory_os_ai-4.1.0.dist-info/entry_points.txt",
                             "[console_scripts]\n" + "".join(f"{k} = {v}\n" for k, v in scripts.items()))
        return path

    good = {name: "m:main" for name in maker.ENTRY_POINTS}
    good["memory-os-constraints"] = "memory_os_constraints:main"
    assert maker.check_entry_points(wheel(good)) is True
    refused(maker.check_entry_points, wheel({k: v for k, v in good.items() if k != "memory-os-constraints"}))
    refused(maker.check_entry_points, wheel({**good, "memory-os-constraints": "other:main"}))


def test_chain_steps_are_unique_and_optional_steps_are_listed():
    assert len(set(gate.CHAIN_STEPS)) == len(gate.CHAIN_STEPS)
    assert set(gate.OPTIONAL_STEPS) <= set(gate.CHAIN_STEPS) and gate.CHAIN_STEPS[0] == "preconditions"
    assert gate.CHAIN_STEPS.index("copy") > max(gate.CHAIN_STEPS.index(s) for s in (
        "resolve_semantic", "install_lexical", "install_semantic", "lexical_wheel"))  # network and installs BEFORE the copy
    assert gate.TEST_LABEL == helper.TEST_LABEL == "local.memory-os-v4.qual410"
    assert gate.TEST_PORT == helper.TEST_PORT == 18765 and not gate.TEST_LABEL.startswith(gate.PRODUCTION_LABEL)


# ---------- N4: frozen pytest closure ----------

def test_pytest_pins_are_exact_frozen_and_disjoint_from_the_lexical_locks():
    wanted = lexical_wheel.pins(lexical_wheel.PINS.read_text())
    assert wanted == {"pytest": "9.0.2", "iniconfig": "2.3.0", "pluggy": "1.6.0", "pygments": "2.19.2"}
    locked = lexical_wheel.locks((ROOT / gate.PROFILES["lexical"]["constraints"]).read_text())
    assert len(locked) == 36 and not set(wanted) & set(locked) and locked["packaging"] == "26.3"
    refused(lexical_wheel.pins, "pytest>=9\n")
    refused(lexical_wheel.pins, "# only a comment\n")


def test_pytest_install_report_must_be_exactly_the_frozen_closure():
    wanted, locked = {"pytest": "9.0.2", "pluggy": "1.6.0"}, {"packaging": "26.3"}

    def item(name, version, url="https://files.pythonhosted.org/x/%s.whl"):
        return {"metadata": {"name": name, "version": version},
                "download_info": {"url": url % name, "archive_info": {"hashes": {"sha256": SHA}}}}

    rows = lexical_wheel.check_pytest_install({"install": [item("pytest", "9.0.2"), item("Pluggy", "1.6.0")]}, wanted, locked)
    assert [r["name"] for r in rows] == ["pluggy", "pytest"] and all(r["sha256"] == SHA for r in rows)
    for report in (
        {"install": [item("pytest", "9.0.2")]},                                               # closure incomplete
        {"install": [item("pytest", "9.0.3"), item("pluggy", "1.6.0")]},                        # version chosen at run time
        {"install": [item("pytest", "9.0.2"), item("pluggy", "1.6.0"), item("packaging", "26.3")]},  # a lock moved
        {"install": [item("pytest", "9.0.2"), item("pluggy", "1.6.0"), item("colorama", "1")]},
        {"install": [item("pytest", "9.0.2", "https://example.org/%s.whl"), item("pluggy", "1.6.0")]},
        {"install": [item("pytest", "9.0.2", "https://files.pythonhosted.org/%s.tar.gz"), item("pluggy", "1.6.0")]},
    ):
        refused(lexical_wheel.check_pytest_install, report, wanted, locked)


# ---------- §3: launchd and port observation (fakes only; nothing is ever loaded or signalled) ----------

class Done:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def launchctl(answer):
    calls = []

    def run(argv, **kw):
        calls.append(list(argv))
        if argv[0] == "/bin/launchctl":
            assert argv[1] == "print", "observation only"
            return answer
        raise AssertionError(argv)

    run.calls = calls
    return run


PRINTED = ("gui/501/local.memory-os-v4.qual410 = {\n\tactive count = 1\n\tpath = %s\n\tstate = running\n\n"
           "\tprogram = %s\n\tpid = 4242\n}\n")
ABSENT = Done(113, "", 'Could not find service "local.memory-os-v4.qual410" in domain for user gui: 501\n')


def test_label_is_absent_only_when_launchctl_says_so_explicitly():
    assert helper.label_state(helper.TEST_LABEL, uid=501, run=launchctl(ABSENT))[0] == "absent"
    assert helper.label_state(helper.TEST_LABEL, uid=501, run=launchctl(Done(0, PRINTED % ("/p", "/v/bin/python"))))[0] == "present"
    for answer in (Done(1, "", "Operation not permitted"), Done(113, "", "something else"), Done(0, "", ""),
                   Done(0, "gui/501/another.label = {\n}\n"), Done(5)):
        with pytest.raises(helper.Unobservable):  # an impossible observation is a refusal, never "absent"
            helper.label_state(helper.TEST_LABEL, uid=501, run=launchctl(answer))

    def broken(argv, **kw):
        raise OSError("no launchctl")

    with pytest.raises(helper.Unobservable):
        helper.label_state(helper.TEST_LABEL, uid=501, run=broken)


def test_port_is_free_busy_or_unobservable():
    def bind(error=None):
        def probe(port):
            assert port == helper.TEST_PORT
            if error:
                raise OSError(error, "x")
        return probe

    assert helper.port_state(helper.TEST_PORT, bind()) == "free"
    assert helper.port_state(helper.TEST_PORT, bind(errno.EADDRINUSE)) == "busy"
    with pytest.raises(helper.Unobservable):
        helper.port_state(helper.TEST_PORT, bind(errno.EACCES))
    row = helper.service_state(helper.TEST_LABEL, helper.TEST_PORT, uid=501, run=launchctl(ABSENT), bind=bind())
    assert row["label_state"] == "absent" and row["port_state"] == "free" and row["label_absent"] is True


def test_test_identity_is_never_the_production_label_or_port():
    helper.check_test_identity(helper.TEST_LABEL, helper.TEST_PORT)
    for label, port in ((helper.PRODUCTION_LABEL, 18765), (helper.PRODUCTION_LABEL + ".qual", 18765),
                        (helper.TEST_LABEL, 8765), ("local.other", 18765)):
        with pytest.raises(SystemExit):
            helper.check_test_identity(label, port)


def service_fixture(tmp_path, *, program=None, plist_home=None, listener=4242, profile="lexical", ml_lines=0):
    home, venv = tmp_path / "home1", tmp_path / "venv"
    plist = (plist_home or home) / "Library/LaunchAgents" / (helper.TEST_LABEL + ".plist")
    plist.parent.mkdir(parents=True, exist_ok=True)
    program = program or str(venv / "bin/python")
    token = home / ".config/memory-os/http.token"
    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_text("disposable-test-token")
    plist.write_bytes(plistlib.dumps({"Label": helper.TEST_LABEL, "ProgramArguments": [program, "-m", "memory_os_v4.server"],
                                      "EnvironmentVariables": {"MEMORY_V4_TOKEN_FILE": str(token),
                                                               "MEMORY_V4_RETRIEVAL_PROFILE": profile}}))

    def run(argv, **kw):
        if argv[0] == "/bin/launchctl":
            assert argv[1] == "print"
            return Done(0, PRINTED % (plist, program))
        if argv[:2] == ["/usr/sbin/lsof", "-nP"]:
            return Done(0, "p%d\nf12\n" % listener)
        if argv[:2] == ["/usr/sbin/lsof", "-p"]:
            return Done(0, "python 4242 /usr/lib/libz.dylib\n" + "python 4242 /x/site-packages/torch/lib.dylib\n" * ml_lines)
        raise AssertionError(argv)

    def fetch(port, token_file):
        assert port == helper.TEST_PORT and Path(token_file) == token
        marker = {"name": "lexical", "policy": "lex-4.1.0-v1"} if profile == "lexical" else {"name": "semantic", "policy": None}
        return {"ready": True, "version": "4.1.0", "commit": COMMIT,
                "capabilities": {"retrieval_profile": marker, "store": {"backend": "sqlite_s1"}}}

    return home, venv, run, fetch


def test_service_is_attested_as_belonging_to_the_run(tmp_path):
    home, venv, run, fetch = service_fixture(tmp_path)
    row = helper.attest_service(helper.TEST_LABEL, helper.TEST_PORT, home, venv, "lexical", uid=501, run=run, fetch=fetch)
    assert row["present"] and row["belongs_to_run"] and row["plist_under_home"] and row["program_is_run_venv"]
    assert row["listener_is_service_pid"] and row["ready"] and row["ml_libraries_open"] == 0
    assert row["retrieval_profile"] == gate.LEXICAL_MARKER and row["nice_in_plist"] is None
    m = manifest()
    gate._service({**row}, "lexical", m, "test")
    assert "disposable-test-token" not in json.dumps(row)  # the token is never reported


def test_a_foreign_or_preexisting_service_is_never_attested_as_ours(tmp_path):
    # program outside the run venv
    home, venv, run, fetch = service_fixture(tmp_path / "a", program="/opt/other/bin/python")
    row = helper.attest_service(helper.TEST_LABEL, helper.TEST_PORT, home, venv, "lexical", uid=501, run=run, fetch=fetch)
    assert row["present"] and not row["belongs_to_run"] and not row["program_is_run_venv"]
    # plist loaded from another HOME (e.g. the real LaunchAgents)
    home, venv, run, fetch = service_fixture(tmp_path / "b", plist_home=tmp_path / "b" / "real-home")
    row = helper.attest_service(helper.TEST_LABEL, helper.TEST_PORT, home, venv, "lexical", uid=501, run=run, fetch=fetch)
    assert not row["belongs_to_run"] and not row["plist_under_home"]
    # another process listens on the port
    home, venv, run, fetch = service_fixture(tmp_path / "c", listener=999)
    row = helper.attest_service(helper.TEST_LABEL, helper.TEST_PORT, home, venv, "lexical", uid=501, run=run, fetch=fetch)
    assert not row["belongs_to_run"] and not row["listener_is_service_pid"] and row["ready"] is False
    # ML libraries opened by a lexical server are counted
    home, venv, run, fetch = service_fixture(tmp_path / "d", ml_lines=3)
    row = helper.attest_service(helper.TEST_LABEL, helper.TEST_PORT, home, venv, "lexical", uid=501, run=run, fetch=fetch)
    assert row["ml_libraries_open"] == 3
    refused(gate._service, {**row, "label": gate.TEST_LABEL, "port": gate.TEST_PORT}, "lexical", manifest(), "test")
    # absent label: nothing attested
    row = helper.attest_service(helper.TEST_LABEL, helper.TEST_PORT, home, venv, "lexical", uid=501, run=launchctl(ABSENT))
    assert row["present"] is False and row["belongs_to_run"] is False


# ---------- installed runtime against the locks ----------

def test_constraints_check_finds_drift_unpinned_ml_and_missing_packages():
    constraints = "numpy==2.0  # comment\nmcp==1.2\npackaging==26.3\n"
    good = "numpy==2.0\nmcp==1.2\npackaging==26.3\nmemory-os-ai @ file:///w.whl\npip==24.0\nsetuptools==65\n"
    row = helper.constraints_check(constraints, good, "lexical")
    assert row["ok"] and row["pinned_and_equal"] == row["locks"] == 3 and row["bootstrap"] == ["pip", "setuptools"]
    assert not helper.constraints_check(constraints, good.replace("numpy==2.0", "numpy==2.1"), "lexical")["ok"]
    assert helper.constraints_check(constraints, good + "requests==2\n", "lexical")["unpinned"] == ["requests"]
    assert helper.constraints_check(constraints, good + "torch==2.10.0\n", "lexical")["ml_packages"] == ["torch"]
    assert helper.constraints_check(constraints, good.replace("mcp==1.2\n", ""), "lexical")["missing"] == ["mcp"]
    assert not helper.constraints_check(constraints, good + "pytest==9.0.2\n", "lexical")["ok"]  # only pip/setuptools/wheel
    reused = helper.constraints_check(constraints, good + "pytest==9.0.2\n", "semantic", "pth")
    assert reused["ok"] and reused["unpinned"] == ["pytest"] and reused["variant"] == "pth"  # listed, not refused
    assert not helper.constraints_check(constraints, good.replace("mcp==1.2", "mcp==1.3"), "semantic", "pth")["ok"]


def test_pip_distributions_tie_installed_wheels_to_url_and_sha256():
    report = {"install": [
        {"metadata": {"name": "memory-os-ai", "version": "4.1.0"}, "download_info": {"url": "file:///w.whl"}},
        {"metadata": {"name": "numpy", "version": "2.0"},
         "download_info": {"url": "https://files.pythonhosted.org/n.whl", "archive_info": {"hashes": {"sha256": SHA}}}}]}
    rows, wheels_only = helper.pip_distributions(report)
    assert rows == [{"name": "numpy", "version": "2.0", "url": "https://files.pythonhosted.org/n.whl", "sha256": SHA}]
    assert wheels_only is True
    report["install"][1]["download_info"]["url"] = "https://files.pythonhosted.org/n.tar.gz"
    assert helper.pip_distributions(report)[1] is False


# ---------- G8: user data preserved by uninstall ----------

JOURNAL = ".local/state/memory-os/quickstart/S1/journal.json"
INSTALL_JSON = ".local/state/memory-os/quickstart/S1/install.json"
PLIST = "Library/LaunchAgents/%s.plist" % helper.TEST_LABEL
PLIST_COPY = ".local/state/memory-os/uninstall/U1/%s.plist" % helper.TEST_LABEL
RECORDS = {"version": 1, "stamp": "S1", "records": [
    {"client": "codex", "path": ".codex/config.toml", "digest": "d1"},
    {"client": "cursor", "path": ".cursor/mcp.json", "digest": "d2"},
    {"client": "service", "label": helper.TEST_LABEL}]}


def revoked(records=RECORDS, by="U1", only=("codex", "cursor")):
    after = copy.deepcopy(records)
    for record in after["records"]:
        if record["client"] in only:
            record.update(revoked=True, revoked_by=by)
    return after


def home_inventory(extra=None):
    f = lambda sha: {"type": "file", "mode": 0o600, "bytes": 4, "sha256": sha}  # noqa: E731
    rows = {
        ".config/memory-os/http.token": f("t"), ".config/memory-os/cl100k_base.tiktoken": f("c"),
        ".config/memory-os/backup.key": f("k"), "memory-os-backups/qualify-410-witness.mosb": f("b"),
        ".local/share/memory-os/store/store.sqlite3": f("s1"), ".local/share/memory-os/store/store.sqlite3-wal": f("w"),
        ".local/share/memory-os/store/store.sqlite3-shm": f("h"),
        INSTALL_JSON: f("i"), JOURNAL: f("j"),
        PLIST: f("p"), "Library/Logs/memory-os/server.log": f("l1"),
        ".codex/config.toml": f("x1"), ".cursor/mcp.json": f("y1"),
    }
    rows.update(extra or {})
    return rows, f


def uninstalled(rows, f):
    after = dict(rows)
    del after[PLIST], after[".local/share/memory-os/store/store.sqlite3-wal"]
    del after[".local/share/memory-os/store/store.sqlite3-shm"]
    after.update({
        ".local/share/memory-os/store/store.sqlite3": f("s2"),  # checkpoint at close: bytes change, logic compared
        ".local/state/memory-os/uninstall/U1/uninstall.json": f("u"),
        PLIST_COPY: f("p"),  # exactly the bytes of the original plist
        JOURNAL: f("j-revoked"),
        "Library/Logs/memory-os/server.log": {**f("l2"), "bytes": 9}, ".codex/config.toml": f("x2"), ".cursor/mcp.json": f("y2"),
    })
    return after


def conservation(before, after, *, store_after=None, journal_after=None, stamp="U1", texts_after=None):
    """helper.data_conservation with the state copies the script takes (journal contents, field by field)."""
    texts_before = {JOURNAL: json.dumps(RECORDS), INSTALL_JSON: json.dumps({"ok": True})}
    after_texts = {JOURNAL: json.dumps(revoked() if journal_after is None else journal_after),
                   INSTALL_JSON: json.dumps({"ok": True}), **(texts_after or {})}
    return helper.data_conservation(before, after, STORE, dict(STORE) if store_after is None else store_after,
                                    helper.TEST_LABEL, CLIENTS, stamp=stamp, text_before=texts_before.get,
                                    text_after=after_texts.get)


STORE = {"store_instance_id": "id1", "logical_sha256": "L", "tables": {"items": 2}}
CLIENTS = [".codex/config.toml", ".cursor/mcp.json"]


def test_uninstall_protocol_mutations_are_admitted_and_data_is_preserved():
    before, f = home_inventory()
    row = conservation(before, uninstalled(before, f))
    assert row["unexpected_changes"] == [] and row["immutable_unchanged"] and row["journals_present"]
    assert row["store_identity_unchanged"] and row["store_logical_unchanged"]
    assert row["plist_removed_from_active"] and row["plist_journal_copy_kept"] and row["plist_journal_copies"] == [PLIST_COPY]
    assert row["immutable_files"] == {"token": 1, "resources": 1, "backups": 1, "key": 1} and row["immutable_witnessed"]
    reasons = {m["path"]: m["why"] for m in row["expected_mutations"]}
    assert "SQLite close transition" in reasons[".local/share/memory-os/store/store.sqlite3"]
    assert "field by field" in reasons[JOURNAL]


def test_blocker2_corrupted_install_json_and_journalled_plist_are_refused():
    """Counterexample of the review: install.json and the journalled plist corrupted, yet
    unexpected_changes == [], journals_present and plist_journal_copy_kept were all positive."""
    before, f = home_inventory()
    after = uninstalled(before, f)
    after[INSTALL_JSON] = f("corrupted")
    after[PLIST_COPY] = f("corrupted-plist")
    row = conservation(before, after, texts_after={INSTALL_JSON: "{corrupt"})
    assert [c["path"] for c in row["unexpected_changes"]] == [INSTALL_JSON]
    assert row["plist_journal_copy_kept"] is False and row["plist_journal_copies"] == []
    # Each corruption alone is caught too.
    after = uninstalled(before, f)
    after[INSTALL_JSON] = f("corrupted")
    assert conservation(before, after)["unexpected_changes"]
    after = uninstalled(before, f)
    after[PLIST_COPY] = f("corrupted-plist")
    assert conservation(before, after)["plist_journal_copy_kept"] is False
    # Any file ending in .plist elsewhere is not a kept copy.
    after = uninstalled(before, f)
    del after[PLIST_COPY]
    after[".local/state/memory-os/quickstart/S1/other.plist"] = f("p")
    row = conservation(before, after)
    assert row["plist_journal_copy_kept"] is False and row["unexpected_changes"]


@pytest.mark.parametrize("journal", [
    {**RECORDS, "stamp": "S2"},                                                # another field of the document changed
    {**revoked(), "records": revoked()["records"][:2]},                        # a record removed
    {**revoked(), "records": revoked()["records"] + [{"client": "x"}]},        # a record added
    revoked(by="other-stamp"),                                                 # revoked by something else than this uninstall
    {**RECORDS, "records": [{**RECORDS["records"][0], "revoked": True, "revoked_by": "U1", "digest": "changed"},
                            *RECORDS["records"][1:]]},                         # a revocation that also rewrites a field
    {**RECORDS, "records": [{**RECORDS["records"][0], "revoked": True, "revoked_by": "U1", "extra": 1},
                            *RECORDS["records"][1:]]},
    RECORDS,                                                                   # bytes changed but nothing revoked
    "not json",
])
def test_blocker2_a_journal_may_only_gain_revocations_of_this_uninstall(journal):
    before, f = home_inventory()
    row = conservation(before, uninstalled(before, f), journal_after=journal)
    assert [c["path"] for c in row["unexpected_changes"]] == [JOURNAL]
    assert helper.revocation_only(json.dumps(RECORDS), json.dumps(revoked()), "U1") is True
    assert helper.revocation_only(json.dumps(RECORDS), json.dumps(revoked()), None) is False
    assert helper.revocation_only(json.dumps(revoked()), json.dumps(revoked()), "U1") is False


def test_blocker2_absent_key_or_backups_do_not_prove_their_preservation():
    """Refused ambiguity 14: an empty kind is not witnessed; the gate needs non-empty key and backups."""
    for missing in (".config/memory-os/backup.key", "memory-os-backups/qualify-410-witness.mosb"):
        before, f = home_inventory()
        del before[missing]
        row = conservation(before, uninstalled(before, f))
        assert row["immutable_witnessed"] is False and row["immutable_unchanged"] is False
        assert 0 in row["immutable_files"].values()
    before, f = home_inventory({".config/memory-os/backup.key": {"type": "file", "mode": 0o600, "bytes": 0, "sha256": "e"}})
    assert conservation(before, uninstalled(before, f))["immutable_witnessed"] is False  # an empty file is no witness
    script = (SCRIPTS / "qualify_install_410.sh").read_text()
    assert "qualify-410-witness.mosb" in script and "backup.key" in script and "witness.json" in script
    assert script.index("witness.json") < script.index('--output "$D2/data_before.json"')  # written before the inventory


@pytest.mark.parametrize("change", [
    lambda a, f: a.update({".config/memory-os/http.token": f("changed")}),
    lambda a, f: a.pop(".config/memory-os/cl100k_base.tiktoken"),
    lambda a, f: a.update({"memory-os-backups/qualify-410-witness.mosb": f("changed")}),
    lambda a, f: a.pop("memory-os-backups/qualify-410-witness.mosb"),
    lambda a, f: a.update({".local/state/memory-os/quickstart/S2/journal.json": f("n")}),   # a journal appearing
    lambda a, f: a.update({"Library/Logs/memory-os/server.log": {**f("short"), "bytes": 1}}),  # a log shortened
    lambda a, f: a.update({".local/state/memory-os/health.jsonl": f("n")}),
    lambda a, f: a.pop(".config/memory-os/backup.key"),
    lambda a, f: a.pop(".local/state/memory-os/quickstart/S1/install.json"),   # a journal must stay
    lambda a, f: a.pop("Library/Logs/memory-os/server.log"),                    # a log must stay
    lambda a, f: a.update({"Documents/new.txt": f("n")}),
    lambda a, f: a.update({".gemini/config.json": f("n")}),                     # a client we did not create
    lambda a, f: a.pop(".local/share/memory-os/store/store.sqlite3") and a.update({"x": f("x")}),
])
def test_any_other_modification_refuses_the_uninstall_gate(change):
    before, f = home_inventory()
    after = uninstalled(before, f)
    change(after, f)
    row = conservation(before, after)
    assert row["unexpected_changes"] or not row["immutable_unchanged"] or not row["journals_present"]


def test_store_identity_or_logical_change_is_a_data_modification_not_a_close_transition():
    before, f = home_inventory()
    after = uninstalled(before, f)
    assert conservation(before, after, store_after={**STORE, "logical_sha256": "other"})["store_logical_unchanged"] is False
    assert conservation(before, after, store_after={**STORE, "store_instance_id": "id2"})["store_identity_unchanged"] is False
    assert conservation(before, after, store_after={**STORE, "tables": {"items": 1}})["store_logical_unchanged"] is False
    kept = dict(after)
    kept[PLIST] = f("p")
    assert conservation(before, kept)["plist_removed_from_active"] is False


def test_store_logical_digest_survives_a_checkpoint_and_sees_a_row_change(tmp_path):
    import sqlite3

    db = tmp_path / "store.sqlite3"
    con = sqlite3.connect(db)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE store_metadata(key TEXT PRIMARY KEY, value TEXT)")
    con.execute("CREATE TABLE items(id TEXT, text TEXT)")
    con.execute("INSERT INTO store_metadata VALUES('metadata', ?)", (json.dumps({"store_instance_id": "abc"}),))
    con.execute("INSERT INTO items VALUES('1', 'alpha')")
    con.commit()
    running = helper.store_logical(tmp_path)  # service still open: WAL present
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.close()
    stopped = helper.store_logical(tmp_path)
    assert running == stopped and running["store_instance_id"] == "abc" and running["tables"]["items"] == 1
    con = sqlite3.connect(db)
    con.execute("UPDATE items SET text='beta'")
    con.commit()
    con.close()
    assert helper.store_logical(tmp_path)["logical_sha256"] != stopped["logical_sha256"]


def test_granite_copy_is_checked_file_by_file(tmp_path):
    snapshot = tmp_path / "snapshots/rev1"
    snapshot.mkdir(parents=True)
    (snapshot / "config.json").write_bytes(b"abc")
    catalog = {"files": [{"path": "config.json", "bytes": 3, "sha256": hashlib.sha256(b"abc").hexdigest()}]}
    assert helper.verify_granite(tmp_path, catalog)["ok"] is True
    (snapshot / "config.json").write_bytes(b"abd")
    assert helper.verify_granite(tmp_path, catalog)["ok"] is False
    assert helper.verify_granite(tmp_path / "missing", catalog)["ok"] is False
    assert helper.verify_granite(tmp_path, {"files": []})["ok"] is False


# ---------- M1 and the assembled install report: producer and validator agree ----------

def test_m1_runs_from_venv_launch_to_apply_return_with_controls_timed_apart():
    line = "venv 100.0 103.0\npip_nodeps 103.0 105.0\npip_deps 105.0 125.0\ncontrol_preview 125.0 130.0\napply 130.0 160.0\n"
    timeline = helper.timeline(line)
    timing = {"clock": "monotonic", "download_bytes": 1681126, "steps_ms": {"resources": 2000}, "profile": "lexical"}
    m1 = helper.m1(timeline, {"timing": timing}, "lexical", "autonomous", 12345)
    # Blocker 10: a cache size is never presented as downloaded bytes; the download count is unmeasured.
    assert "pip_download_bytes" not in m1 and m1["pip_cache_bytes"] == 12345
    assert m1["pip_downloaded_bytes"] is None and m1["pip_downloaded_bytes_measured"] is False
    assert "not transferred bytes" in m1["pip_cache_bytes_nature"]
    reported = gate.validate_m1(m1, "lexical", "autonomous")
    assert reported["pip_cache_bytes_nature"] == m1["pip_cache_bytes_nature"] and reported["pip_downloaded_bytes"] is None
    refused(gate.validate_m1, {**m1, "pip_download_bytes": 12345}, "lexical", "autonomous")
    refused(gate.validate_m1, {**m1, "pip_downloaded_bytes": 12345}, "lexical", "autonomous")
    assert (m1["t0_epoch"], m1["t1_epoch"], m1["total_s"]) == (100.0, 160.0, 60.0)
    assert m1["pip_s"] == 22.0 and m1["controls_s"] == 5.0 and m1["net_s"] == 55.0 and m1["outside_model_s"] == 58.0
    assert m1["quickstart_download_bytes"] == 1681126  # N6 bytes: the only download actually measured
    assert gate.validate_m1(m1, "lexical", "autonomous")["label"] == "first installation"
    pth = helper.m1(timeline, {"timing": {**timing, "download_bytes": 0}}, "semantic", "pth", 0)
    assert pth["label"] == "reused-dependencies variant" and "model_pre_placed" in pth["protocol_deviations"]
    gate.validate_m1(pth, "semantic", "pth")
    refused(helper.timeline, "pip_deps 1 2\n")              # no T0 / T1
    refused(helper.timeline, "venv 1 2\nvenv 2 3\napply 3 4\n")  # a span appears once
    refused(helper.timeline, "venv 5 2\napply 3 4\n")


def client_rows(present, kind="relay"):
    return {key: {"path": rel, "state": "ok" if present else "missing-config", "entry_present": present,
                  "kind": kind if present else None, "file_sha256": SHA if present else None}
            for key, rel in helper.TEST_CLIENTS.items()}


def doctor_rows(overrides=None):
    rows = [{"area": area, "status": "PASS", "detail": "ok", "hint": ""} for area in helper.REQUIRED_DOCTOR_AREAS]
    rows += [{"area": "backup-key", "status": "WARN", "detail": "missing", "hint": ""}]
    for row in rows:
        row["status"] = (overrides or {}).get(row["area"], row["status"])
    return rows


def production_rows(pid=77, agent="a"):
    plist = {"state": "file", "sha256": "p"}
    return {"client_configs": {rel: {"state": "absent"} for rel in helper.REAL_CLIENT_CONFIGS},
            "launch_agents": {"state": "dir", "entries": {helper.PRODUCTION_LABEL + ".plist": plist,
                                                          "other.plist": {"state": "file", "sha256": agent}}},
            "protected_trees": {".config/memory-os": {"state": "dir",
                                                      "entries": {"http.token": {"state": "file", "sha256": "t"}}}},
            "production_plist": plist, "production_pid": pid, "production_label_state": "present",
            "sha256": hashlib.sha256(agent.encode()).hexdigest()}


def write_install_tree(d, profile="lexical"):
    """Files exactly as qualify_install_410.sh writes them, without running anything."""
    def put(rel, value):
        path = d / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value))

    locks = 36 if profile == "lexical" else 65
    absent = {"label_state": "absent", "port_state": "free"}
    inv = {"inventory_identical": True, "inventory_hashes_content": True, "inventory_entries": 4,
           "inventory_before_sha256": SHA, "inventory_after_sha256": SHA}
    put("meta.json", {"profile": profile, "variant": "autonomous", "source_commit": COMMIT, "run_tag": TAG,
                      "version": "4.1.0", "venv": str(d / "venv"), "label": helper.TEST_LABEL, "port": helper.TEST_PORT,
                      "wheel": {"name": "w.whl", "sha256": WHEEL_SHA}})
    put("constraints_check.json", {"constraints_sha256": LEX_SHA if profile == "lexical" else SEM_SHA, "mismatch": [],
                                   "unpinned": [], "ml_packages": [], "bootstrap": ["pip", "setuptools"], "missing": [],
                                   "pinned_and_equal": locks, "locks": locks, "ok": True})
    put("locations.json", {"locator_under_site_packages": True, "package_under_site_packages": True,
                           "package_path": "/v/site-packages/memory_os_v4/__init__.py", "reused_paths": [],
                           "candidate_origin_sha256": WHEEL_SHA, "version": "4.1.0"})
    put("pip_check.json", {"returncode": 0})
    put("pip_install_report.json", {"install": [
        {"metadata": {"name": f"p{i}", "version": "1"},
         "download_info": {"url": f"https://files.pythonhosted.org/p{i}.whl", "archive_info": {"hashes": {"sha256": SHA}}}}
        for i in range(locks)]})
    put("production_before.json", production_rows())
    put("production_after.json", production_rows())
    put("timeline.json", helper.timeline("venv 100 103\npip_deps 103 123\ncontrol_preview 123 128\napply 128 160\n"))
    put("pip_cache_bytes.json", {"bytes": 4096})
    put("safety_net.json", {"fired": False})
    put("shell_verdict.json", {"ok": True})
    for index, download in (("1", gate.CL100K_BYTES if profile == "lexical" else 0), ("2", 0)):
        p = "install" + index
        (d / ("home" + index)).mkdir(parents=True, exist_ok=True)
        timing = {"clock": "monotonic", "download_bytes": download, "profile": profile, "steps_ms": {"resources": 1000}}
        put(p + "/home.json", {"home": str(d / ("home" + index))})
        put(p + "/pre_state.json", absent)
        put(p + "/post_state.json", absent)
        put(p + "/apply.json", {"ok": True, "stamp": "S" + index})
        put(p + "/apply_rc.json", {"returncode": 0})
        put(p + "/install.json", {"ok": True, "timing": timing})
        put(p + "/service.json", service(profile))
        put(p + "/doctor.json", doctor_rows())
        put(p + "/doctor_rc.json", {"returncode": 0})
        put(p + "/clients_after_apply.json", client_rows(True))
    put("install1/preview.json", {"ok": True, "profile": profile})
    put("install1/preview_rc.json", {"returncode": 0})
    put("install1/preview_inventory.json", inv)
    put("install1/undo.json", {"ok": True})
    put("install1/undo_rc.json", {"returncode": 0})
    put("install1/undo_state.json", absent)
    put("install1/clients_before_apply.json", client_rows(False))
    put("install1/clients_after_undo.json", client_rows(False))
    put("install2/uninstall_preview.json", {"ok": True})
    put("install2/uninstall_preview_rc.json", {"returncode": 0})
    put("install2/uninstall_preview_inventory.json", inv)
    put("install2/uninstall.json", {"ok": True, "partial": False, "complete": False, "data_deleted": False,
                                    "clients": [], "stamp": "U1"})
    put("install2/uninstall_rc.json", {"returncode": 0})
    put("install2/uninstall_state.json", absent)
    put("install2/clients_after_uninstall.json", client_rows(False))
    put("install2/witness.json", {"backup": "memory-os-backups/qualify-410-witness.mosb",
                                  "key": ".config/memory-os/backup.key", "key_written_as_witness": True})
    before, f = home_inventory()
    put("install2/data_before.json", {"rows": before})
    put("install2/data_after.json", {"rows": uninstalled(before, f)})
    put("install2/store_before.json", STORE)
    put("install2/store_after.json", STORE)
    # Copies of the state folder taken by the script around uninstall --apply (journal contents).
    put("install2/state_before/quickstart/S1/journal.json", RECORDS)
    put("install2/state_after/quickstart/S1/journal.json", revoked())
    if profile == "semantic":
        put("install1/granite.json", {"ok": True})
        put("install2/granite.json", {"ok": True})


@pytest.mark.parametrize("profile", ["lexical", "semantic"])
def test_assembled_install_report_is_accepted_by_the_release_rules(tmp_path, profile):
    write_install_tree(tmp_path, profile)
    report = helper.assemble(tmp_path)
    assert report["ok"] is True and "error" not in report, report.get("error")
    measure = gate.validate_install(report, manifest(), profile)
    assert measure["total_s"] == 60.0 and report["install_2"]["uninstall"]["entries_removed"] == sorted(helper.TEST_CLIENT_FILES)
    assert report["install_1"]["doctor"]["returncode"] == 0 and report["install_1"]["doctor"]["missing_areas"] == []
    assert report["install_2"]["client_entries_created"] == sorted(helper.TEST_CLIENT_FILES)
    assert report["install_2"]["data_conservation"]["immutable_witnessed"] is True
    assert set(helper.REQUIRED_EVIDENCE) <= set(report["evidence_sha256"]) and "report.json" not in report["evidence_sha256"]
    compared = report["install_2"]["data_conservation"]["compared_documents"]
    assert compared == ["install2/state_after/quickstart/S1/journal.json", "install2/state_before/quickstart/S1/journal.json"]
    assert set(compared) <= set(report["evidence_sha256"]) and "proof.log" not in report["evidence_sha256"]
    gate.check_pieces("install_" + profile, report, tmp_path)  # what G15 replays, state documents included
    (tmp_path / compared[0]).unlink()
    refused(gate.check_pieces, "install_" + profile, report, tmp_path)


def put_json(path, value):
    path.write_text(json.dumps(value))


@pytest.mark.parametrize("damage", [
    lambda d: put_json(d / "install2/uninstall_state.json", {"label_state": "present", "port_state": "busy"}),
    lambda d: put_json(d / "install1/undo_state.json", {"label_state": "present", "port_state": "free"}),
    lambda d: put_json(d / "safety_net.json", {"fired": True}),
    lambda d: put_json(d / "shell_verdict.json", {"ok": False}),
    lambda d: put_json(d / "install2/clients_after_uninstall.json", client_rows(True)),         # entries still there
    lambda d: put_json(d / "install2/clients_after_uninstall.json", client_rows(True, "foreign")),
    lambda d: put_json(d / "install2/clients_after_apply.json", client_rows(True, "foreign")),  # not our relay entry
    lambda d: put_json(d / "install1/doctor.json", doctor_rows({"store": "FAIL"})),
    lambda d: put_json(d / "install1/doctor_rc.json", {"returncode": 9}),                        # blocker 3
    lambda d: put_json(d / "install2/doctor.json", doctor_rows()[:3]),                           # expected checks missing
    lambda d: put_json(d / "install1/service.json", {**service("lexical"), "commit": "f" * 40}),  # blocker 3
    lambda d: put_json(d / "install1/clients_after_undo.json", client_rows(True)),
    lambda d: put_json(d / "install2/uninstall.json", {"ok": True, "partial": True, "data_deleted": False, "stamp": "U1"}),
    lambda d: put_json(d / "install2/uninstall.json", {"ok": True, "partial": False, "data_deleted": False, "stamp": "U9"}),
    lambda d: put_json(d / "production_after.json", production_rows(pid=78)),
    lambda d: put_json(d / "production_after.json", production_rows(agent="changed")),          # blocker 5: content
    lambda d: put_json(d / "install2/store_after.json", {**STORE, "logical_sha256": "x"}),
    lambda d: put_json(d / "install2/state_after/quickstart/S1/journal.json", {**RECORDS, "stamp": "S9"}),  # blocker 2
    lambda d: (d / "install2/state_after/quickstart/S1/journal.json").unlink(),
    lambda d: put_json(d / "locations.json", {"locator_under_site_packages": True, "package_under_site_packages": True,
                                              "package_path": "/x", "reused_paths": [], "version": "4.1.0",
                                              "candidate_origin_sha256": SHA}),
    lambda d: (d / "install2/witness.json").unlink(),         # an absent proof is a failure
    lambda d: (d / "install1/install.json").unlink(),
    lambda d: (d / "timeline.json").write_text("{trunc"),     # a truncated proof is a failure
])
def test_assembled_install_report_fails_closed(tmp_path, damage):
    write_install_tree(tmp_path)
    damage(tmp_path)
    report = helper.assemble(tmp_path)
    if report["ok"]:
        refused(gate.validate_install, report, manifest(), "lexical")
    else:
        assert report["ok"] is False


# ---------- shell script: static guarantees (never executed) ----------

INSTALL = (SCRIPTS / "qualify_install_410.sh").read_text()


def test_install_script_parses_and_targets_only_the_test_label_and_port():
    done = subprocess.run(["/bin/zsh", "-n", str(SCRIPTS / "qualify_install_410.sh")], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert 'LABEL="local.memory-os-v4.qual410"' in INSTALL and "PORT=18765" in INSTALL
    commands = [line.strip() for line in INSTALL.splitlines() if "launchctl" in line and not line.strip().startswith("#")]
    assert commands == ['launchctl bootout "gui/$UID_N/$LABEL" || true']  # the only launchctl command, test label only
    assert "kickstart" not in INSTALL and "launchctl load" not in INSTALL and "pkill" not in INSTALL
    assert "--only-binary=:all:" in INSTALL and "env -i" in INSTALL and "--download-approved" in INSTALL


def test_safety_net_is_armed_only_after_the_proven_creation_of_the_service():
    lines = INSTALL.splitlines()
    armed = [i for i, line in enumerate(lines) if "SERVICE_OURS=1" in line and not line.lstrip().startswith("#")]
    assert len(armed) == 1 and "arc -eq 0" in lines[armed[0]]  # only after a successful attestation
    attest = max(i for i, line in enumerate(lines[:armed[0]]) if "attest-service" in line)
    assert attest < armed[0]
    cleanup = INSTALL[INSTALL.index("cleanup() {"):INSTALL.index("trap cleanup EXIT")]
    # Re-attested immediately before the stop; a pre-existing label is never unloaded.
    assert cleanup.index('"$SERVICE_OURS" = 1') < cleanup.index("attest-service") < cleanup.index("launchctl bootout")
    assert "SAFETY_NET=1" in cleanup and "VERDICT=1" in cleanup  # the net never turns a failed rollback into a success
    initial = INSTALL[INSTALL.index("VERDICT=1; SERVICE_OURS=0"):INSTALL.index("cleanup() {")]
    assert "SERVICE_OURS=0" in initial
    # Preconditions run before anything can arm the net.
    assert INSTALL.index('step "0. Préconditions') < INSTALL.index('step "4. Installation 1')


def test_install_script_requires_a_nominative_go_per_network_acquisition():
    assert "has_go N3 && has_go N6" in INSTALL and "has_go N5" in INSTALL
    assert "jamais de repli automatique sur .pth" in INSTALL
    # N6 once: only the first lexical installation passes --download-approved.
    assert INSTALL.count("--download-approved") == 1 and 'install_apply "$H2" "$D2"; after_apply' in INSTALL
    assert 'span apply install_apply "$H1" "$D1" --download-approved' in INSTALL


def test_run_tag_glob_refuses_only_the_exact_tag(tmp_path):
    script = ('T=$1; Q=$2; E=$3; L=$4; EXISTING=("$L"(N) $Q/*-"$T"(N) $Q/*-"$T".*(N) "$E"/410-*-"$T"(N)); '
              'print -r -- ${#EXISTING}')
    q, e = tmp_path / "Q", tmp_path / "E"
    q.mkdir()
    e.mkdir()

    def count():
        done = subprocess.run(["/bin/zsh", "-f", "-c", script, "--", "v1", str(q), str(e), str(tmp_path / "logs_v1")],
                              capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
        return int(done.stdout)

    assert count() == 0
    (q / "410-full-claude-v11").mkdir()
    (e / "410-install-lexical-claude-v12").mkdir()
    (e / "409-installed-claude-v1").mkdir()
    assert count() == 0  # other tags and other versions do not block
    (q / "410-perimeter-claude-v1.json").write_text("{}")
    assert count() == 1
    (e / "410-installed-claude-v1").mkdir()
    (q / "release-4.1.0-v1").mkdir()
    (tmp_path / "logs_v1").mkdir()
    assert count() == 4


# ---------- watching reads: proves that a destination was neither opened nor listed ----------

_WATCHED = []


def _audit(event, args):
    if _WATCHED and event in ("open", "os.scandir", "os.listdir") and args and isinstance(args[0], (str, bytes, os.PathLike)):
        path = os.path.realpath(os.fsdecode(args[0]))
        for watch in _WATCHED:
            if path == watch.root or path.startswith(watch.root + os.sep):
                watch.events.append((event, path))


sys.addaudithook(_audit)  # inert unless a Watch is active


class Watch:
    """Records every open() and directory listing at or below ``root`` (a FAKE sealed folder in tmp)."""

    def __init__(self, root):
        self.root, self.events = os.path.realpath(root), []

    def __enter__(self):
        _WATCHED.append(self)
        return self

    def __exit__(self, *exc):
        _WATCHED.remove(self)
        return False


def test_the_watcher_itself_sees_opens_and_listings(tmp_path):
    folder = tmp_path / "brief_v5_val"
    folder.mkdir()
    (folder / "f").write_text("x")
    with Watch(folder) as seen:
        (folder / "f").read_text()
        os.listdir(folder)
        list(os.scandir(folder))
        os.path.realpath(folder / "f")   # resolving reads link targets only: not an event
        (folder / "f").exists()
    assert sorted({event for event, _ in seen.events}) == ["open", "os.listdir", "os.scandir"]
    with Watch(folder) as seen:
        (tmp_path / "other").write_text("y")
    assert seen.events == []


# ---------- blocker 5: the protected real perimeter is fingerprinted by content ----------

def real_home(tmp_path):
    home = tmp_path / "real-home"
    agents = home / "Library/LaunchAgents"
    agents.mkdir(parents=True)
    (agents / (helper.PRODUCTION_LABEL + ".plist")).write_bytes(plistlib.dumps({"Label": helper.PRODUCTION_LABEL}))
    (agents / "com.example.other.plist").write_bytes(plistlib.dumps({"Label": "com.example.other"}))
    (home / ".config/memory-os").mkdir(parents=True)
    (home / ".config/memory-os/http.token").write_text("real-token")
    (home / ".cursor").mkdir()
    (home / ".cursor/mcp.json").write_text('{"mcpServers": {}}')
    (home / ".claude.json").write_text(json.dumps({"numStartups": 1, "mcpServers": {"a": {"command": "x"}},
                                                   "projects": {"/p": {"mcpServers": {}, "history": [1]}}}))
    return home


def snapshot(home):
    running = Done(0, "gui/501/%s = {\n\tpid = 77\n}\n" % helper.PRODUCTION_LABEL)
    return helper.production_snapshot(home, uid=501, run=lambda argv, **kw: running)


def test_protected_client_list_is_the_product_catalogue():
    from memory_os_v4.ops.clients import CLIENTS

    assert tuple(client.path for client in CLIENTS) == helper.REAL_CLIENT_CONFIGS
    for name in (".claude.json", ".gemini/settings.json", ".grok/config.toml", ".config/zed/settings.json",
                 ".config/devin/mcp_config.json", ".codeium/windsurf/mcp_config.json"):
        assert name in helper.REAL_CLIENT_CONFIGS  # the ones the review found missing
    assert {client.key: client.path for client in CLIENTS if client.key in helper.TEST_CLIENTS} == helper.TEST_CLIENTS
    assert helper.PROTECTED_TREES == (".config/memory-os",)
    assert tuple(helper.REQUIRED_DOCTOR_AREAS) == gate.REQUIRED_DOCTOR_AREAS


def test_blocker5_a_content_change_of_any_real_launch_agent_or_protected_file_is_seen(tmp_path):
    home = real_home(tmp_path)
    before = snapshot(home)
    assert all(helper.production_unchanged(before, snapshot(home)).values()) and before["production_pid"] == 77
    assert "real-token" not in json.dumps(before)  # fingerprints only
    assert before["client_configs"][".codex/config.toml"] == {"state": "absent"}  # absence is an explicit state
    assert before["client_configs"][".cursor/mcp.json"]["state"] == "file"
    assert before["launch_agents"]["state"] == "dir" and before["production_plist"]["state"] == "file"

    def changed(mutation, flag):
        mutation()
        flags = helper.production_unchanged(before, snapshot(home))
        assert flags[flag] is False, flag
        return flags

    other = home / "Library/LaunchAgents/com.example.other.plist"
    kept = other.read_bytes()
    stamp = other.stat()

    def same_name_other_content():  # same name, same size, same dates: only the content differs
        other.write_bytes(kept.replace(b"com.example.other", b"com.example.xther"))
        os.utime(other, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))

    changed(same_name_other_content, "real_launch_agents_unchanged")
    other.write_bytes(kept)
    changed(lambda: (home / "Library/LaunchAgents/new.plist").write_text("x"), "real_launch_agents_unchanged")
    (home / "Library/LaunchAgents/new.plist").unlink()
    changed(lambda: (home / "Library/LaunchAgents/sub").mkdir(), "real_launch_agents_unchanged")  # a folder appearing
    (home / "Library/LaunchAgents/sub").rmdir()
    changed(lambda: (home / ".config/memory-os/http.token").write_text("other-token"), "real_memory_os_config_unchanged")
    (home / ".config/memory-os/http.token").write_text("real-token")
    changed(lambda: (home / ".config/memory-os/backup.key").write_text("k"), "real_memory_os_config_unchanged")
    (home / ".config/memory-os/backup.key").unlink()
    changed(lambda: (home / ".cursor/mcp.json").write_text('{"mcpServers": {"memory-os-ai": {}}}'),
            "real_client_configs_unchanged")
    (home / ".cursor/mcp.json").write_text('{"mcpServers": {}}')
    (home / ".grok").mkdir()
    changed(lambda: (home / ".grok/config.toml").write_text("[mcp_servers.x]\n"), "real_client_configs_unchanged")
    (home / ".grok/config.toml").unlink()
    production = home / "Library/LaunchAgents" / (helper.PRODUCTION_LABEL + ".plist")
    kept = production.read_bytes()
    flags = changed(lambda: production.write_bytes(kept + b"\n"), "production_plist_unchanged")
    assert flags["real_launch_agents_unchanged"] is False
    production.write_bytes(kept)
    assert all(helper.production_unchanged(before, snapshot(home)).values())


def test_round2_blocker2_a_link_is_fingerprinted_with_the_content_of_its_target(tmp_path):
    """Round 2: a symbolic link gave only {"link": target}; changing the target's content kept the fingerprint."""
    home = real_home(tmp_path)
    elsewhere = tmp_path / "dotfiles"
    elsewhere.mkdir()
    (elsewhere / "agent.plist").write_text("<plist>one</plist>")
    (elsewhere / "cursor.json").write_text('{"mcpServers": {}}')
    (elsewhere / "tree").mkdir()
    (elsewhere / "tree" / "inner.txt").write_text("a")
    (home / "Library/LaunchAgents/linked.plist").symlink_to(elsewhere / "agent.plist")
    (home / "Library/LaunchAgents/linked-folder").symlink_to(elsewhere / "tree")  # os.walk would not follow it
    (home / ".cursor/mcp.json").unlink()
    (home / ".cursor/mcp.json").symlink_to(elsewhere / "cursor.json")
    before = snapshot(home)
    entry = before["launch_agents"]["entries"]["linked.plist"]
    assert entry["state"] == "link" and entry["content"]["state"] == "file" and "sha256" in entry["content"]
    assert before["launch_agents"]["entries"]["linked-folder"]["content"]["entries"]["inner.txt"]["state"] == "file"
    assert all(helper.production_unchanged(before, snapshot(home)).values())
    for target, text, flag in ((elsewhere / "agent.plist", "<plist>two</plist>", "real_launch_agents_unchanged"),
                               (elsewhere / "tree" / "inner.txt", "b", "real_launch_agents_unchanged"),
                               (elsewhere / "cursor.json", '{"mcpServers": {"x": {}}}', "real_client_configs_unchanged")):
        kept = target.read_text()
        target.write_text(text)  # the link itself (path of its target) is untouched
        assert helper.production_unchanged(before, snapshot(home))[flag] is False, target
        target.write_text(kept)
    assert all(helper.production_unchanged(before, snapshot(home)).values())
    # A dangling link is an explicit state, different from an absent path.
    (home / ".codex").mkdir()
    (home / ".codex/config.toml").symlink_to(tmp_path / "nowhere.toml")
    dangling = snapshot(home)["client_configs"][".codex/config.toml"]
    assert dangling["state"] == "link" and dangling["content"] == {"state": "absent"}
    # A link leading into a sealed folder is refused before its target is opened.
    sealed = tmp_path / "brief_v5_val"
    sealed.mkdir()
    (sealed / "x.plist").write_text("secret")
    (home / "Library/LaunchAgents/bad.plist").symlink_to(sealed / "x.plist")
    with Watch(sealed) as seen, pytest.raises(SystemExit):
        snapshot(home)
    assert seen.events == []


def test_round2_blocker2_a_walk_error_is_blocking_never_an_incomplete_inventory(tmp_path):
    """Round 2: os.walk without onerror could silently return an incomplete inventory, even {}."""
    home = real_home(tmp_path)
    before = snapshot(home)
    locked = home / "Library/LaunchAgents/locked"
    locked.mkdir()
    (locked / "inner.plist").write_text("x")
    os.chmod(locked, 0o000)
    try:
        with pytest.raises(helper.Unobservable):
            snapshot(home)
        with pytest.raises(helper.Unobservable):
            helper.inventory(home / "Library/LaunchAgents")
    finally:
        os.chmod(locked, 0o700)
    assert helper.production_unchanged(before, snapshot(home))["real_launch_agents_unchanged"] is False
    with pytest.raises(helper.Unobservable):  # a root that cannot be listed is not an empty inventory
        helper.inventory(tmp_path / "no-such-folder")
    # The protected folder replaced by a file, or missing: observed states, never "unchanged".
    for row in ({"state": "absent"}, {"state": "file", "sha256": "x"}):
        other = {**before, "launch_agents": row, "protected_trees": {".config/memory-os": row}}
        flags = helper.production_unchanged(other, other)
        assert not flags["real_launch_agents_unchanged"] and not flags["real_memory_os_config_unchanged"]


def claude_fingerprint(tmp_path, document):
    home = tmp_path / ("h%d" % len(list(tmp_path.iterdir())))
    home.mkdir()
    (home / ".claude.json").write_text(json.dumps(document))
    return helper.observe(home / ".claude.json", volatile=True)


def test_round2_blocker2_claude_mcp_perimeter_covers_enable_disable_and_distinguishes_shapes(tmp_path):
    """Round 2: disabledMcpServers changes were invisible; an empty object and an empty list hashed alike."""
    base = {"numStartups": 1, "mcpServers": {"a": {"command": "x"}}, "disabledMcpServers": ["a"],
            "projects": {"/p": {"mcpServers": {}, "enabledMcpjsonServers": [], "history": [1]}}}
    reference = claude_fingerprint(tmp_path, base)
    assert reference["state"] == "file" and reference["scope"] == "MCP perimeter only"
    assert reference["mcp_projection_version"] == helper.MCP_PROJECTION_VERSION and "sha256" not in reference
    same = copy.deepcopy(base)
    same["numStartups"] = 99
    same["projects"]["/p"]["history"].append(2)
    assert claude_fingerprint(tmp_path, same) == reference  # outside the MCP perimeter: not compared, not claimed
    changes = (
        lambda d: d.update(disabledMcpServers=[]),                                  # a global disable removed
        lambda d: d.update(disabledMcpServers=["a", "b"]),
        lambda d: d.pop("disabledMcpServers"),                                     # absence is not an empty list
        lambda d: d.update(mcpServers=[]),                                         # [] is not {}
        lambda d: d["projects"]["/p"].update(mcpServers=[]),
        lambda d: d["projects"]["/p"].update(disabledMcpjsonServers=["x"]),
        lambda d: d["projects"]["/p"].update(enabledMcpjsonServers=["x"]),
        lambda d: d["projects"]["/p"].update(enableAllProjectMcpServers=True),
        lambda d: d.update(enableAllProjectMcpServers=False),
        lambda d: d["projects"]["/p"].pop("enabledMcpjsonServers"),
        lambda d: d["projects"].update({"/q": {}}),                                # a project appearing
        lambda d: d["projects"].update({"/q": d["projects"].pop("/p")}),            # the same settings under another path
        lambda d: d["mcpServers"]["a"].update(command="y"),
    )
    for change in changes:
        document = copy.deepcopy(base)
        change(document)
        assert claude_fingerprint(tmp_path, document) != reference, change
    assert set(helper.MCP_KEYS) == {"mcpServers", "disabledMcpServers", "enabledMcpjsonServers",
                                    "disabledMcpjsonServers", "enableAllProjectMcpServers"}
    rows = helper.mcp_projection({"mcpServers": {}, "projects": {"/p": {}}})
    assert {"path": ["mcpServers"], "present": True, "value": {}} in rows
    assert {"path": ["disabledMcpServers"], "present": False, "value": None} in rows
    assert {"path": ["projects", "/p", "mcpServers"], "present": False, "value": None} in rows
    for invalid in ([], "text", {"projects": []}, {"projects": {"/p": []}}):  # must be a valid JSON object
        with pytest.raises(helper.Unobservable):
            claude_fingerprint(tmp_path, invalid)


def test_blocker5_claude_code_config_is_covered_through_its_mcp_sections(tmp_path):
    home = real_home(tmp_path)
    before = snapshot(home)
    config = home / ".claude.json"
    data = json.loads(config.read_text())
    data["numStartups"] = 99
    data["projects"]["/p"]["history"].append(2)  # what a running Claude Code rewrites all the time
    config.write_text(json.dumps(data))
    assert all(helper.production_unchanged(before, snapshot(home)).values())
    data["projects"]["/p"]["mcpServers"]["memory-os-ai"] = {"command": "relay"}
    config.write_text(json.dumps(data))
    assert helper.production_unchanged(before, snapshot(home))["real_client_configs_unchanged"] is False


def test_blocker5_unreadable_is_a_refusal_and_absence_is_not_unchanged(tmp_path):
    home = real_home(tmp_path)
    before = snapshot(home)
    (home / ".claude.json").write_text("{not json")
    with pytest.raises(helper.Unobservable):  # distinguish absence from impossibility of reading
        snapshot(home)
    (home / ".claude.json").unlink()
    after = snapshot(home)
    assert after["client_configs"][".claude.json"] == {"state": "absent"}
    assert helper.production_unchanged(before, after)["real_client_configs_unchanged"] is False
    # A perimeter that was never observed is not "unchanged".
    empty = {**before, "launch_agents": None, "production_plist": None, "production_pid": None}
    flags = helper.production_unchanged(empty, empty)
    assert not flags["real_launch_agents_unchanged"] and not flags["production_plist_unchanged"]
    assert not flags["production_pid_unchanged"]
    partial = {**before, "client_configs": {".cursor/mcp.json": {"state": "absent"}}}
    assert helper.production_unchanged(partial, partial)["real_client_configs_unchanged"] is False
    unknown = {**before, "client_configs": {rel: None for rel in helper.REAL_CLIENT_CONFIGS}}
    assert helper.production_unchanged(unknown, unknown)["real_client_configs_unchanged"] is False


# ---------- blocker 6: sealed folders refused on every read path, resolved form included ----------

def test_blocker6_an_outside_alias_does_not_bypass_the_refusal(tmp_path):
    sealed = tmp_path / "brief_v5_val"
    sealed.mkdir()
    (sealed / "seed.tiktoken").write_text("x")
    alias = tmp_path / "innocent-name"
    alias.symlink_to(sealed)
    for call in (helper.refuse_forbidden, ):
        with pytest.raises(SystemExit):
            call(alias / "seed.tiktoken")   # text is clean, destination is not
        with pytest.raises(SystemExit):
            call(alias)
    refused(gate.refuse_forbidden, alias / "seed.tiktoken")
    # The command the shell runs on its inputs before reading any of them.
    with pytest.raises(SystemExit):
        helper.main(["refuse-forbidden", str(tmp_path / "ok"), str(alias / "seed.tiktoken")])
    assert helper.main(["refuse-forbidden", str(tmp_path / "ok")]) == 0
    with pytest.raises(SystemExit):
        helper.main(["verify-granite", "--cache", str(alias), "--catalog", str(tmp_path / "c"), "--output", str(tmp_path / "o")])
    with pytest.raises(SystemExit):
        helper.main(["inventory", "--root", str(alias), "--output", str(tmp_path / "o")])
    with pytest.raises(SystemExit):
        helper.verify_granite(alias, {"files": []})
    with pytest.raises(SystemExit):
        helper.hf_cache_files_present(alias)


def test_blocker6_a_link_met_while_walking_an_inventory_cannot_lead_inside(tmp_path):
    sealed = tmp_path / "elsewhere" / "brient_v5_val"
    sealed.mkdir(parents=True)
    home = tmp_path / "home"
    home.mkdir()
    (home / "ok.txt").write_text("x")
    assert "ok.txt" in helper.inventory(home)
    (home / "shortcut").symlink_to(sealed)
    with pytest.raises(SystemExit):
        helper.inventory(home)
    (home / "shortcut").unlink()
    (home / "safe").symlink_to(tmp_path / "elsewhere")  # an ordinary link is recorded, never followed
    assert helper.inventory(home)["safe"]["type"] == "link"


def test_blocker6_every_script_checks_resolved_paths_before_reading():
    lines = INSTALL.splitlines()
    guard = next(i for i, line in enumerate(lines) if "refuse-forbidden $INPUTS" in line)
    first_read = next(i for i, line in enumerate(lines) if "shasum -a 256" in line)
    assert guard < first_read  # resolved check before the wheel or the seed are read
    assert 'INPUTS=("$WHEEL" "$P" "$SEED")' in INSTALL and "GRANITE" in lines[guard - 1] and "PTH_PY" in lines[guard - 1]
    wheel = (SCRIPTS / "qualify_lexical_wheel_410.py").read_text()
    assert wheel.index("refuse_forbidden(given)") < wheel.index("json.loads(args.manifest.read_text())")
    source = (SCRIPTS / "release_410_checks.py").read_text()
    assert "refuse_forbidden(getattr(args, mode))" in source and "hub = refuse_forbidden(" in source
    for name in ("qualify_sqlite_release_410.py", "prepare_release_410_envs.py", "make_release_410.py"):
        text = (SCRIPTS / name).read_text()
        main = text[text.index("def main("):]
        assert main.index("refuse_forbidden(g") < main.index("json.loads("), name  # before the first proof is read
    with pytest.raises(SystemExit):
        perimeter.main(["--candidate", QUAL_BASE, "--output", "/nonexistent/brief_v5_val/report.json"])


# ---------- minor reserve: the MiniLM precondition needs a complete offline cache ----------

def hf_cache(root, *, files=("config.json", "modules.json", "tokenizer.json", "tokenizer_config.json", "model.safetensors"),
             revision="a" * 40, ref=True):
    model = root / "models--sentence-transformers--all-MiniLM-L6-v2"
    (model / "blobs").mkdir(parents=True)
    (model / "snapshots" / revision).mkdir(parents=True)
    for index, name in enumerate(files):
        blob = model / "blobs" / ("%040d" % index)
        blob.write_text("content of " + name)
        (model / "snapshots" / revision / name).symlink_to(blob)
    if ref:
        (model / "refs").mkdir()
        (model / "refs/main").write_text(revision)
    return model


def test_minilm_precondition_needs_references_snapshot_blobs_tokenizer_and_weights(tmp_path):
    assert helper.hf_cache_files_present(hf_cache(tmp_path / "ok"))["ok"] is True
    # The lone config.json that used to be enough.
    lone = helper.hf_cache_files_present(hf_cache(tmp_path / "lone", files=("config.json",)))
    assert lone["ok"] is False and "missing model weights" in lone["problems"]
    assert helper.hf_cache_files_present(hf_cache(tmp_path / "noref", ref=False))["ok"] is False
    model = hf_cache(tmp_path / "dangling")
    next((model / "blobs").iterdir()).unlink()  # a snapshot entry whose blob is gone
    assert helper.hf_cache_files_present(model)["ok"] is False
    model = hf_cache(tmp_path / "empty")
    (model / "blobs" / ("%040d" % 4)).write_text("")  # empty weights
    assert helper.hf_cache_files_present(model)["ok"] is False
    model = hf_cache(tmp_path / "outside")
    link = model / "snapshots" / ("a" * 40) / "model.safetensors"
    link.unlink()
    (tmp_path / "foreign.bin").write_text("weights elsewhere")
    link.symlink_to(tmp_path / "foreign.bin")
    assert helper.hf_cache_files_present(model)["ok"] is False  # must resolve inside blobs/
    assert helper.hf_cache_files_present(tmp_path / "absent")["ok"] is False


def test_preconditions_require_offline_mode_and_the_twelve_checks():
    m = manifest()
    m["invariants"] = {"x": 1}
    keys = ("tree_clean", "rc_py_runtime_ok", "build_tools_present", "hf_offline", "minilm_cache_present",
            "granite_cache_conforms", "cl100k_conforms", "baseline_interpreter_matches_plist", "run_tag_unused",
            "locks_absent", "test_label_absent", "test_port_free")
    proof = {"format": "qualify-410-preconditions-v1", "ok": True, "source_commit": COMMIT, "run_tag": TAG,
             "checks": {key: True for key in keys}, "invariants": {"x": 1}}
    gate.validate_preconditions(proof, m)
    for key in keys:
        refused(gate.validate_preconditions, {**proof, "checks": {**proof["checks"], key: False}}, m)
    source = (SCRIPTS / "release_410_checks.py").read_text()
    assert 'os.environ.get("HF_HUB_OFFLINE") == "1" and os.environ.get("TRANSFORMERS_OFFLINE") == "1"' in source
    assert "hf_cache_files_present" in source and "len(checks) == 12" in source
    label = helper.hf_cache_files_present(Path("/nonexistent-model-cache"))["label"]
    assert "non vides" in label and "G4" in label and "complet" not in label  # no overclaim: G4 is the real proof


def test_install_script_reads_client_entries_through_the_installed_product():
    assert "from memory_os_v4.ops.clients import CLIENTS, classify, read_entry" in INSTALL
    assert INSTALL.count("clients_state ") == 4 and "client-state" not in INSTALL
    assert 'cp -R "$H2/.local/state/memory-os" "$D2/state_before"' in INSTALL
    assert INSTALL.index('"$D2/state_before"') < INSTALL.index("uninstall --apply") < INSTALL.index('"$D2/state_after"')
    assert "--variant \"$VARIANT\" --output \"$P/constraints_check.json\"" in INSTALL


# ---------- round 2, blocker 1: the guard runs before EVERY open and EVERY descent ----------

def sealed_fixture(tmp_path):
    """A FAKE sealed folder in tmp (never the real ones) holding one file and one sub-folder."""
    sealed = tmp_path / "outside" / "brief_v5_val"
    (sealed / "pb").mkdir(parents=True)
    (sealed / "secret.txt").write_text("a" * 40)
    (sealed / "pb" / "inputs.jsonl").write_text("{}")
    return sealed


def test_round2_blocker1_allowed_cache_with_a_forbidden_descendant_is_never_read(tmp_path):
    sealed = sealed_fixture(tmp_path)
    # refs/main of an allowed cache is a link to a sealed file.
    model = hf_cache(tmp_path / "cache", ref=False)
    (model / "refs").mkdir()
    (model / "refs/main").symlink_to(sealed / "secret.txt")
    with Watch(sealed) as seen, pytest.raises(SystemExit):
        helper.hf_cache_files_present(model)
    assert seen.events == []
    # A snapshot entry of an allowed cache links into the sealed folder.
    model = hf_cache(tmp_path / "cache2")
    entry = model / "snapshots" / ("a" * 40) / "model.safetensors"
    entry.unlink()
    entry.symlink_to(sealed / "secret.txt")
    with Watch(sealed) as seen, pytest.raises(SystemExit):
        helper.hf_cache_files_present(model)
    assert seen.events == []
    # verify_granite: the snapshots folder, or one of its files, leads inside.
    granite = tmp_path / "granite"
    (granite / "snapshots/rev1").mkdir(parents=True)
    (granite / "snapshots/rev1/config.json").symlink_to(sealed / "secret.txt")
    catalog = {"files": [{"path": "config.json", "bytes": 40, "sha256": hashlib.sha256(b"a" * 40).hexdigest()}]}
    with Watch(sealed) as seen, pytest.raises(SystemExit):
        helper.verify_granite(granite, catalog)  # the hash would even have matched
    assert seen.events == []
    linked = tmp_path / "granite2"
    linked.mkdir()
    (linked / "snapshots").symlink_to(sealed)
    with Watch(sealed) as seen, pytest.raises(SystemExit):
        helper.verify_granite(linked, catalog)
    assert seen.events == []


def test_round2_blocker1_inventory_refuses_a_forbidden_named_folder_before_descending(tmp_path):
    home = tmp_path / "home"
    (home / "ok" / "deeper").mkdir(parents=True)
    (home / "ok" / "file.txt").write_text("x")
    ordinary = home / "ok" / "deeper" / "brient_v5_val"   # an ORDINARY sub-folder with a sealed name
    ordinary.mkdir()
    (ordinary / "inside.txt").write_text("y")
    with Watch(ordinary) as seen, pytest.raises(SystemExit):
        helper.inventory(home)
    assert seen.events == []                              # neither listed nor read
    with Watch(ordinary) as seen, pytest.raises(SystemExit):
        helper.tree_bytes(home)
    assert seen.events == []
    with Watch(ordinary) as seen, pytest.raises(SystemExit):
        helper.observe(home)
    assert seen.events == []
    for call in (helper.sha256_file, helper.read_text, helper.load):
        with Watch(ordinary) as seen, pytest.raises(SystemExit):
            call(ordinary / "inside.txt")
        assert seen.events == []


def test_round2_blocker1_gate_reads_constructed_paths_only_through_the_guard(tmp_path):
    sealed = sealed_fixture(tmp_path)
    directory = tmp_path / "release"
    (directory / "decisions").mkdir(parents=True)
    (directory / "evidence-link.json").symlink_to(sealed / "secret.txt")
    (directory / "decisions" / "launch.json").symlink_to(sealed / "pb" / "inputs.jsonl")
    sha = hashlib.sha256(b"a" * 40).hexdigest()
    m = {"evidence": {"x": {"path": "evidence-link.json", "sha256": sha}},
         "decisions": {"launch": {"path": "decisions/launch.json", "sha256": sha}}}
    for call, args in ((gate.evidence, (m, directory, "x")), (gate.decision, (m, directory, "launch")),
                       (gate.digest, (directory / "evidence-link.json",)), (gate.load_json, (directory / "evidence-link.json",)),
                       (gate.read_text, (directory / "decisions" / "launch.json",)),
                       (gate.verify_pieces, ({"evidence_sha256": {"evidence-link.json": sha}}, directory, ()))):
        with Watch(sealed) as seen:
            refused(call, *args)
        assert seen.events == [], call
    # The module has no unguarded read left.
    source = (SCRIPTS / "release_410_checks.py").read_text()
    body = source[source.index("def load_json("):]
    assert ".read_text()" not in body and ".read_bytes()" not in body and "json.loads(Path(" not in body
    helper_source = (SCRIPTS / "qualify_install_410_helper.py").read_text()
    assert helper_source.count("os.walk(") == 1 and "rglob(" not in helper_source  # one guarded walk only


def test_round2_blocker1_the_dispatcher_refuses_a_manifest_or_proof_leading_inside(tmp_path, capsys):
    sealed = sealed_fixture(tmp_path)
    (sealed / "manifest.json").write_text(json.dumps({"version": "4.1.0"}))
    alias = tmp_path / "release"
    alias.symlink_to(sealed)
    with Watch(sealed) as seen:
        assert verifier.main([str(alias / "manifest.json")]) == 1              # the manifest itself
        good = tmp_path / "manifest.json"
        good.write_text(json.dumps({"version": "4.0.5"}))
        assert verifier.main([str(good), "--record-decision", str(alias / "secret.txt")]) == 1
        assert verifier.main([str(good), "--record-external-proof", str(alias / "pb" / "inputs.jsonl")]) == 1
        assert verifier.main([str(sealed / "manifest.json")]) == 1
    assert seen.events == [] and "REFUS" in capsys.readouterr().out
    source = (SCRIPTS / "verify_release_manifest.py").read_text()
    main = source[source.index("def main("):]
    assert main.index("refuse_forbidden(value)") < main.index("json.loads(args.manifest.read_text())")


# ---------- round 3 ----------

def test_round3_blocker3_a_masked_detection_failure_is_never_a_short_command_exception():
    """Round 3 counterexample: ps failing made the probe report "process absent" and exit 0; with an upper
    bound of 2.6 s the summary accepted none_short_command (first check) or overlap_only (second check)."""
    def step(observation):
        rows = m3_rows("lint1", 100.0, observation=observation, command_seconds=2.1)   # upper bound 2.6 s
        assert rows[-1]["mono"] - rows[-1]["command"]["start_mono"] == pytest.approx(2.6)
        return rows

    for observation in ("none_short_command", "overlap_only"):
        rows = step(observation)
        assert m3(rows)["observation"] == {"lint1": observation}          # a real, detected case is admitted
        blind = {k: v for k, v in rows[1].items() if k != "detection"}     # what the old probe wrote on a ps failure
        refused(m3, [rows[0], blind, rows[-1]])
        refused(m3, [rows[0], {**rows[1], "detection": "impossible"}, rows[-1]])
        refused(m3, [rows[0], rows[1], {**rows[-1], "probe_rc": 1}])       # the probe now fails: never an exception
    assert gate.SHORT_COMMAND_MAX_S == 10.0
    refused(m3, m3_rows("lint1", 100.0, observation="none_short_command", command_seconds=9.6))   # 10.1 s upper bound
    assert m3(m3_rows("lint1", 100.0, observation="none_short_command", command_seconds=9.4))["observation"]


def test_round3_blocker3_a_probe_that_only_saw_the_relay_before_exec_is_never_credited():
    """Round 3 counterexample (scheduling): marker written at 100.4, relay descheduled before exec; the probe
    runs entirely on the live relay (101.0-101.2); exec at 101.3; command over at 101.4; "after" at 103.0."""
    base = {"step": "full", "free_bytes": 10**10, "swap_bytes": 1}
    before = {**base, "phase": "before", "t": 1.0, "mono": 100.0}
    after = {**base, "phase": "after", "t": 4.0, "mono": 103.0, "probe_rc": 0, "supervisor_rc": 0,
             "command": {"started": True, "pid": 4242, "identity": IDENTITY, "start_mono": 100.4, "start_t": 1.4}}
    on_relay = {**base, "phase": "during", "t": 2.2, "mono": 101.2, "detection": "ok", "command_pid": 4242,
                "command_identity": IDENTITY, "identity_verified": True,
                "command_start_mono": 100.4, "probe_start_mono": 101.0, "probe_end_mono": 101.2,
                "alive_at_probe_start": True, "alive_at_probe_end": True}
    # As the previous probe wrote it (no notion of exec): the pid was alive at both ends, yet it was the relay.
    refused(m3, [before, on_relay, after])
    refused(m3, [before, {**on_relay, "post_exec_at_probe_start": False}, after])
    # What the corrected probe writes for this schedule: no credit, an explicit flagged exception.
    missed = {**on_relay, "phase": "missed", "reason": "command_ended_before_first_probe", "probe_start_mono": 101.5,
              "probe_end_mono": 101.6, "mono": 101.6, "post_exec_at_probe_start": False, "alive_at_probe_start": False,
              "alive_at_probe_end": False}
    summary = m3([before, missed, after])
    assert summary["observation"] == {"full": "none_short_command"} and summary["during_samples"] == {"full": 0}
    assert summary["command_seconds_upper_bound"]["full"] == pytest.approx(2.6)  # an upper bound, published apart
    assert m3([before, {**on_relay, "post_exec_at_probe_start": True}, after])["observation"] == {"full": "during"}


def granite_cache(tmp_path, record):
    hub = tmp_path / "hub"
    model = hub / ("models--" + record["model_id"].replace("/", "--"))
    snapshot = model / "snapshots" / record["revision"]
    snapshot.mkdir(parents=True)
    (model / "refs").mkdir()
    (model / "refs" / "main").write_text(record["revision"])
    return hub, model, snapshot


def test_round3_blocker1_granite_precondition_guards_every_path_before_provision_reads(tmp_path):
    """Round 3 counterexample: ``refs`` of an allowed cache is a link to a sealed folder holding an ordinary
    ``main`` (the engine only checks the final file for being a link), or a snapshot file links to a sealed
    target of the expected size (the engine would open it to hash it)."""
    from memory_os_v4.ops import provision

    record = provision.catalog()
    sealed = tmp_path / "sealed" / "brief_v5_val"
    sealed.mkdir(parents=True)
    (sealed / "main").write_text(record["revision"])
    smallest = min(record["files"], key=lambda item: item["bytes"])
    (sealed / "target.bin").write_bytes(b"x" * smallest["bytes"])  # the expected size: the engine would hash it

    called = []

    class Spy:  # the real engine, observed: plan must not even be entered
        catalog = staticmethod(provision.catalog)

        @staticmethod
        def plan(**kwargs):
            called.append(kwargs)
            return provision.plan(**kwargs)

    # (1) the parent folder ``refs`` is the link
    hub, model, snapshot = granite_cache(tmp_path / "a", record)
    (model / "refs" / "main").unlink()
    (model / "refs").rmdir()
    (model / "refs").symlink_to(sealed)
    with Watch(sealed) as seen:
        refused(gate.guarded_plan, Spy, resources=("granite",), cache_dir=hub)
    assert seen.events == [] and called == []
    # (2) a snapshot file links to a sealed target
    hub, model, snapshot = granite_cache(tmp_path / "b", record)
    target = snapshot / smallest["path"]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(sealed / "target.bin")
    with Watch(sealed) as seen:
        refused(gate.guarded_plan, Spy, resources=("granite",), cache_dir=hub)
    assert seen.events == [] and called == []
    # (3) the snapshot folder itself, or the table, leads inside
    hub, model, snapshot = granite_cache(tmp_path / "c", record)
    snapshot.rmdir()
    snapshot.symlink_to(sealed)
    with Watch(sealed) as seen:
        refused(gate.guarded_plan, Spy, resources=("granite",), cache_dir=hub)
        refused(gate.guarded_plan, Spy, resources=("cl100k",), table_path=sealed / "cl100k_base.tiktoken")
        alias = tmp_path / "alias"
        alias.symlink_to(sealed)
        refused(gate.guarded_plan, Spy, resources=("cl100k",), table_path=alias / "cl100k_base.tiktoken")
    assert seen.events == [] and called == []
    # A clean (incomplete) cache reaches the unchanged engine, which answers "not ready".
    hub, model, snapshot = granite_cache(tmp_path / "d", record)
    assert gate.guarded_plan(Spy, resources=("granite",), cache_dir=hub)["ready"] is False and len(called) == 1
    refused(gate.guarded_plan, Spy, resources=("granite",))          # no implicit cache location
    paths = gate.provision_paths(record, resources=("granite",), cache_dir=hub)
    assert model / "refs" in paths and model / "refs" / "main" in paths
    assert all(snapshot / item["path"] in paths for item in record["files"])
    source = (SCRIPTS / "release_410_checks.py").read_text()
    assert source.count("provision.plan(") == 1 and "return provision.plan(**kwargs)" in source  # one guarded call site
    assert source.count("= guarded_plan(provision,") == 2


def test_round3_blocker2_an_inaccessible_configuration_is_unobservable_never_absent(tmp_path, monkeypatch):
    """Round 3 counterexample: os.path.lexists turns a PermissionError into False, so observe() answered
    "absent" and two identical observations gave real_client_configs_unchanged = True."""
    home = real_home(tmp_path)
    before = snapshot(home)
    target = str(home / ".codex/config.toml")
    real_lstat = os.lstat

    def lstat(path, *args, **kwargs):
        if os.fspath(path) == target:
            raise PermissionError(13, "Permission denied", target)
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", lstat)
    with pytest.raises(helper.Unobservable):
        helper.observe(target)
    with pytest.raises(helper.Unobservable):   # the snapshot is refused: nothing can be declared unchanged
        snapshot(home)
    for error in (OSError(1, "Operation not permitted"), OSError(5, "Input/output error")):
        monkeypatch.setattr(os, "lstat", lambda path, *a, _e=error, **k: (_ for _ in ()).throw(_e))
        with pytest.raises(helper.Unobservable):
            helper.lstat_state(target)
    monkeypatch.setattr(os, "lstat", real_lstat)
    # Established absence: no such entry, or a parent that is not a folder.
    assert helper.observe(target) == {"state": "absent"}
    assert helper.observe(home / ".cursor/mcp.json/child") == {"state": "absent"}  # ENOTDIR
    assert all(helper.production_unchanged(before, snapshot(home)).values())
    # The same with a real inaccessible parent.
    (home / ".grok").mkdir()
    os.chmod(home / ".grok", 0o000)
    try:
        with pytest.raises(helper.Unobservable):
            helper.observe(home / ".grok/config.toml")
        with pytest.raises(helper.Unobservable):
            snapshot(home)
    finally:
        os.chmod(home / ".grok", 0o700)
    source = (SCRIPTS / "qualify_install_410_helper.py").read_text()
    body = source[source.index("def observe("):source.index("def production_snapshot(")]
    assert "lexists" not in body and "os.path.isdir" not in body and "lstat_state(path)" in body


# ---------- round 4: a reused pid is not the command ----------

def test_round4_a_foreign_process_reusing_the_pid_is_never_credited():
    """Round 4 counterexample: the command ends and is reaped; a foreign process takes the same pid and is
    alive at both ends of a later probe: 12.6 s upper bound, during = 1 was accepted."""
    rows = m3_rows("full", 100.0, command_seconds=12.1)
    assert rows[-1]["mono"] - rows[-1]["command"]["start_mono"] == pytest.approx(12.6)
    assert m3(rows)["during_samples"] == {"full": 1}                    # the genuine case: identity bound
    foreign = [1795344120, 7]                                           # another process, same pid 4242
    # What the previous probe wrote: pid alive at both ends, no notion of identity.
    blind = {k: v for k, v in rows[1].items() if k not in ("command_identity", "identity_verified")}
    refused(m3, [rows[0], blind, rows[-1]])
    refused(m3, [rows[0], {**rows[1], "command_identity": foreign}, rows[-1]])
    refused(m3, [rows[0], {**rows[1], "identity_verified": False}, rows[-1]])
    # The command itself must carry the identity recorded by the relay before exec.
    for broken in ({k: v for k, v in rows[-1]["command"].items() if k != "identity"},
                   {**rows[-1]["command"], "identity": [0, 0]}, {**rows[-1]["command"], "identity": [1795344108]},
                   {**rows[-1]["command"], "identity": "Sun Nov 22 10:27:01 2026"},     # second precision is not enough
                   {**rows[-1]["command"], "identity": [1795344108, 1_000_000]}):
        refused(m3, [rows[0], rows[1], {**rows[-1], "command": broken}])
    # Too long for the short-command exception, and the exception rows are bound to the identity too.
    refused(m3, [rows[0], rows[-1]])
    short = m3_rows("lint1", 100.0, observation="none_short_command", command_seconds=0.1)
    refused(m3, [short[0], {**short[1], "command_identity": foreign}, short[-1]])
    overlap = m3_rows("lint1", 100.0, observation="overlap_only", command_seconds=0.1)
    refused(m3, [overlap[0], {**overlap[1], "command_identity": foreign}, overlap[-1]])

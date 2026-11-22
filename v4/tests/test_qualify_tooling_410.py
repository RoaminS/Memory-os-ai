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
        "pytest_pins_sha256": SHA, "qualification_source_sha256": {
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
        d.update(acquisition=name[-2:], dossier={"reference": "N.json", "sha256": SHA}, operations=["pip"],
                 acquisitions=["wheels"],
                 sources=["openaipublic.blob.core.windows.net"] if name == "network_N6" else ["pypi.org", "files.pythonhosted.org"])
    if name == "launch":
        d.update(chain_sha256=CHAIN_SHA, authorises_publication=False, authorises_push=False,
                 authorises_production_switch=False)
    d.update(changes)
    return d


def check(d, **kw):
    return gate.check_decision(d, decision=d["decision"], commit=COMMIT, run_tag=TAG, chain_sha256=CHAIN_SHA, **kw)


@pytest.mark.parametrize("name", sorted(gate.DECISIONS))
def test_every_decision_of_section_10_has_an_accepted_explicit_form(name):
    assert check(decision(name))["answer"] == "oui"
    assert check(decision(name, answer="non"))["answer"] == "non"  # a no is recorded; it authorises nothing


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
    ("network_N2", {"operations": []}),
    ("g13_p_a", {"status": "pass"}), ("g13_p_a", {"reason": "court"}), ("g13_p_a", {"evidence": []}),
    ("g13_p_a", {"replay": True}), ("g13_p_a", {"result_attributed_to": COMMIT}),
    ("g13_p_a", {"claims_result_on_candidate": True}), ("g13_p_a", {"release_ready_effect": "may_satisfy_G12"}),
    ("g12_waiver", {"status": "transfer_accepted"}), ("g12_waiver", {"claims_result_on_candidate": True}),
    ("g12_waiver", {"scope": ""}), ("g12_external_proof", {"proof": {"reference": "pb"}}),
    ("d1_resign", {"measure": None}),
])
def test_decisions_refuse_anything_implicit_unbound_or_widened(name, changes):
    refused(check, decision(name, **changes))


def test_unknown_decision_and_wrong_id_are_refused():
    refused(gate.check_decision, decision("launch"), decision="go", commit=COMMIT, run_tag=TAG)
    refused(gate.check_decision, decision("launch"), decision="launchd_nice", commit=COMMIT, run_tag=TAG)
    refused(gate.check_decision, decision("launch"), decision="launch", commit=COMMIT, run_tag=TAG)  # no chain sha


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
           "version": "4.1.0", "retrieval_profile": gate.LEXICAL_MARKER if profile == "lexical" else gate.SEMANTIC_MARKER}
    if profile == "lexical":
        row["ml_libraries_open"] = 0
    return row


def zero_write():
    return {"ok": True, "returncode": 0, "inventory_identical": True, "inventory_hashes_content": True,
            "inventory_before_sha256": SHA, "inventory_after_sha256": SHA, "inventory_entries": 9}


def install_block(profile, home, download):
    clean = {"label_absent": True, "port_free": True}
    return {"home": home, "pre": dict(clean), "post": dict(clean),
            "apply": {"ok": True, "returncode": 0, "demo": True, "profile": profile, "download_bytes": download},
            "service": service(profile), "doctor": {"fail": 0, "checks": 12}}


def install_proof(profile="lexical", variant="autonomous"):
    m = manifest()
    locks = m["profiles"][profile]["locks"]
    env = {"constraints_sha256": m["profiles"][profile]["constraints_sha256"], "locator_under_site_packages": True,
           "package_under_site_packages": True, "pip_check_ok": True, "mismatch": [], "unpinned": [],
           "bootstrap": ["pip", "setuptools"], "pinned_and_equal": locks, "ml_packages": []}
    if variant == "autonomous":
        env.update(wheels_only=True, distributions=[
            {"name": f"p{i}", "sha256": SHA, "url": f"https://files.pythonhosted.org/p{i}.whl"} for i in range(locks)])
    else:
        env.update(reused_paths=[{"path": "/site"}], candidate_origin_sha256=WHEEL_SHA)
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
                                  "store_identity_unchanged": True, "store_logical_unchanged": True,
                                  "checked_after_stop": True, "journals_present": True, "unexpected_changes": []})
    timing = {"clock": "monotonic", "download_bytes": one["apply"]["download_bytes"], "profile": profile}
    deviations = ["non_blank_machine", "local_wheel"] + (["model_pre_placed"] if profile == "semantic" else [])
    proof = {
        "format": "qualify-410-install-v1", "ok": True, "profile": profile, "variant": variant,
        "source_commit": COMMIT, "run_tag": TAG, "version": "4.1.0", "wheel": {"sha256": WHEEL_SHA},
        "label": gate.TEST_LABEL, "port": gate.TEST_PORT, "environment": env, "install_1": one, "install_2": two,
        "m1": {"t0_epoch": 100.0, "t1_epoch": 160.0, "total_s": 60.0, "pip_s": 20.0, "controls_s": 5.0, "net_s": 55.0,
               "outside_model_s": 58.0, "install_json_timing": timing, "pip_download_bytes": 1000,
               "quickstart_download_bytes": timing["download_bytes"], "protocol_deviations": deviations,
               "label": "reused-dependencies variant" if variant == "pth" else "first installation"},
        "production": {"unchanged": True, "pid_before": 77, "pid_after": 77, "snapshot_before_sha256": SHA,
                       "snapshot_after_sha256": SHA},
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
    (("install_1", "doctor", "fail"), 1), (("install_1", "undo", "plist_removed"), False),
    (("install_1", "undo", "label_absent"), False), (("install_1", "undo", "client_entries_restored"), False),
    (("install_2", "home"), "/p/home1"), (("install_2", "uninstall_preview", "inventory_identical"), False),
    (("install_2", "uninstall", "partial"), True), (("install_2", "uninstall", "ok"), False),
    (("install_2", "uninstall", "entries_removed"), [".cursor/mcp.json"]), (("install_2", "client_entries_created"), []),
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


def test_pth_variant_must_attest_paths_and_candidate_origin_and_is_semantic_only():
    proof, m = install_proof("semantic", "pth")
    refused(gate.validate_install, mutate(copy.deepcopy(proof), ("environment", "reused_paths"), []), m, "semantic")
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
            "pytest_pins_sha256": SHA, "pip_check_ok": True, "runtime_recheck_ok": True, "returncode": 0,
            "counts": {"tests": 80, "failures": 0, "errors": 0, "skipped": 0},
            "test_files": {n: SHA for n in gate.LEXICAL_WHEEL_FILES}}


@pytest.mark.parametrize("path,value", [
    (("outside_checkout",), False), (("pythonpath_set",), True), (("package_under_site_packages",), False),
    (("find_spec", "torch"), "present"), (("find_spec",), {"torch": None}), (("pytest_pins_sha256",), WHEEL_SHA),
    (("runtime_recheck_ok",), False), (("counts", "skipped"), 1), (("counts", "failures"), 1), (("counts", "tests"), 0),
    (("returncode",), 1), (("wheel_sha256",), SHA), (("test_files", "lexical_fixture_410.py"), WHEEL_SHA),
    (("test_files",), {"test_lexical_profile.py": SHA}),
])
def test_lexical_wheel_proof(path, value):
    m = manifest()
    gate.validate_lexical_wheel(lexical_wheel_proof(), m)
    refused(gate.validate_lexical_wheel, mutate(lexical_wheel_proof(), path, value), m)


def lexical_store_proof():
    ranks = {q: ["fact a", "fact b"] for q in worker.LEXICAL_QUERIES}
    return {"format": "qualify-410-lexical-store-v1", "ok": True, "source_commit": COMMIT, "run_tag": TAG,
            "wheel_sha256": WHEEL_SHA, "scripts_sha256": {"runner": SHA}, "package_under_site_packages": True,
            "fixed_data_sha256": SHA, "marker": dict(gate.LEXICAL_MARKER), "backup": {"ok": True}, "restore": {"ok": True},
            "rankings": {k: copy.deepcopy(ranks) for k in ("after_write", "after_reopen", "after_restore")},
            "opposite_profile_open": {"refused": True, "code": "store_profile_mismatch", "requested": "semantic",
                                      "inventory_before_sha256": SHA, "inventory_after_sha256": SHA}}


@pytest.mark.parametrize("path,value", [
    (("rankings", "after_restore", worker.LEXICAL_QUERIES[0]), ["fact b", "fact a"]),
    (("rankings", "after_reopen", worker.LEXICAL_QUERIES[1]), []), (("rankings",), {"after_write": {}}),
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
    return p, m


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


def perimeter_report():
    return {"format": "qualify-410-brief-perimeter-v1", "git_only": True, "sealed_inputs_read": False,
            "brief_executed": False, "automatic_transfer": False, "conclusion": "decision_of_romain_required",
            "commits": {"p_a_proof": gate.P_A_PROOF_COMMIT, "intermediate": gate.P_A_INTERMEDIATE, "candidate": COMMIT},
            "closures": {r: {"files": {"a.py": SHA}} for r in ("p_a_proof", "intermediate", "candidate")},
            "union": {"a.py": {}}, "callers": {p: {} for p in perimeter.CALLERS}, "tool_sha256": SHA}


@pytest.mark.parametrize("path,value", [
    (("automatic_transfer",), True), (("conclusion",), "transfer"), (("git_only",), False),
    (("sealed_inputs_read",), True), (("brief_executed",), True), (("commits", "candidate"), "f" * 40),
    (("commits", "p_a_proof"), COMMIT), (("callers",), {}), (("union",), {}), (("tool_sha256",), WHEEL_SHA),
])
def test_perimeter_report_never_concludes_a_transfer(path, value):
    m = manifest()
    gate.validate_perimeter(perimeter_report(), m)
    refused(gate.validate_perimeter, mutate(perimeter_report(), path, value), m)


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

def samples(steps=gate.CHAIN_STEPS):
    rows, t = [], 1000.0
    for name in steps:
        for phase in ("before", "during", "after"):
            t += 1
            rows.append(json.dumps({"t": t, "phase": phase, "step": name, "free_bytes": int(15e9 - t), "swap_bytes": int(t)}))
    return rows


def test_m3_summary_reports_sampled_extrema_and_requires_usable_samples():
    summary = gate.m3_summary(samples())
    assert summary["min_free_bytes_observed"] < 15e9 and summary["max_swap_bytes_observed"] > 1000
    assert set(summary["step_seconds"]) == set(gate.CHAIN_STEPS) and "not guaranteed" in summary["nature"]
    refused(gate.m3_summary, [])
    refused(gate.m3_summary, [json.dumps({"t": 1, "phase": "before", "step": "full", "free_bytes": None, "swap_bytes": 1})])
    refused(gate.m3_summary, [json.dumps({"t": 1, "phase": "before", "step": "full", "free_bytes": 1, "swap_bytes": 1})])


def exit_proof(steps=None):
    steps = list(steps or [s for s in gate.CHAIN_STEPS if s not in gate.OPTIONAL_STEPS])
    return {"format": "qualify-410-exit-checks-v1", "source_commit": COMMIT, "run_tag": TAG, "chain_sha256": CHAIN_SHA,
            "supervisor_sha256": gate.SUPERVISOR_SHA256, "steps_ok": steps, "test_label_absent": True,
            "test_port_free": True, "no_suspect_process": True, "production_plist_unchanged": True,
            "production_pid_unchanged": True, "real_launch_agents_unchanged": True,
            "real_client_configs_unchanged": True, "lock_409_absent": True, "m3": gate.m3_summary(samples(steps)),
            "m3_samples_sha256": SHA}


def test_exit_checks_require_every_step_in_order_and_every_control():
    m = manifest()
    gate.validate_exit_checks(exit_proof(), m)
    gate.validate_exit_checks(exit_proof(gate.CHAIN_STEPS), m)  # optional steps present, in order
    required = [s for s in gate.CHAIN_STEPS if s not in gate.OPTIONAL_STEPS]
    refused(gate.validate_exit_checks, exit_proof(required[:-1]), m)            # a step missing
    refused(gate.validate_exit_checks, exit_proof(required[1:] + required[:1]), m)  # out of order
    refused(gate.validate_exit_checks, exit_proof(required + ["rec_external"]), m)  # optional step out of place
    for key in ("test_label_absent", "test_port_free", "no_suspect_process", "production_plist_unchanged",
                "production_pid_unchanged", "real_launch_agents_unchanged", "real_client_configs_unchanged",
                "lock_409_absent"):
        refused(gate.validate_exit_checks, {**exit_proof(), key: False}, m)
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
    inputs = tmp_path / "inputs"
    inputs.mkdir()

    def put(name, value):
        target = inputs / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value))
        return target

    def run(**options):
        return gate.main(Args(path, **options))

    def load():
        return json.loads(path.read_text())

    return type("Release", (), {"dir": directory, "path": path, "put": staticmethod(put), "run": staticmethod(run),
                                "load": staticmethod(load), "inputs": inputs})


def record_everything(release, *, variant="autonomous", g12="waiver"):
    """Every proof and decision of a full run, in chain order. Returns the sha of the recorded proofs."""
    m = manifest()
    pre = ["launch", "launchd_nice", "network_N1", "network_N2", "network_N3", "network_N4", "network_N6",
           "network_N5" if variant == "autonomous" else "semantic_pth_variant"]
    for name in pre:
        assert release.run(record_decision=release.put(f"d/{name}.json", decision(name))) == 0
    assert release.run(record_source_proof=release.put("source.json", {"ok": True})) == 0
    for profile in ("lexical", "semantic"):
        folder = release.inputs / ("res-" + profile)
        folder.mkdir()
        report, pip = resolution(m, profile, folder)
        pip.rename(folder / "pip-report.json")
        assert release.run(record_resolution_proof=release.put(f"res-{profile}/report.json", report)) == 0
    proof, _ = install_proof("lexical")
    lexical_install = release.put("install-lexical.json", proof)
    assert release.run(record_install_proof=lexical_install) == 0
    assert release.run(record_lexical_proof=release.put("lexical-wheel.json", lexical_wheel_proof())) == 0
    assert release.run(record_lexical_proof=release.put("lexical-store.json", lexical_store_proof())) == 0
    proof, _ = install_proof("semantic", variant)
    assert release.run(record_install_proof=release.put("install-semantic.json", proof)) == 0
    copy_report, _ = copy_proof()
    assert release.run(record_copy_proof=release.put("copy.json", copy_report)) == 0
    report = release.put("perimeter.json", perimeter_report())
    assert release.run(record_perimeter_report=report) == 0
    sha = {"perimeter": gate.digest(report), "install_lexical": gate.digest(lexical_install)}
    assert release.load()["gates"]["G13"]["state"] == "pending"  # recorded, never concluded
    assert release.run(record_decision=release.put("d/g13_p_a.json", decision(
        "g13_p_a", evidence=[{"reference": "perimeter", "sha256": sha["perimeter"]}]))) == 0
    assert release.run(record_decision=release.put("d/d1_resign.json", decision(
        "d1_resign", measure={"reference": "install", "sha256": sha["install_lexical"]}))) == 0
    if g12 == "waiver":
        assert release.run(record_decision=release.put("d/g12_waiver.json", decision("g12_waiver"))) == 0
    elif g12 == "proof":
        external = release.put("pb.json", external_proof())
        assert release.run(record_decision=release.put("d/g12_external_proof.json", decision(
            "g12_external_proof", proof={"reference": "pb", "sha256": gate.digest(external)}))) == 0
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
    assert len(m["resolved_distributions"]["lexical"]) == 37
    assert release.run() == 0 and release.load()["release_ready"] is False  # plain check: read-only
    assert release.run(finalize=release.put("exit.json", {**exit_proof(), "no_suspect_process": False})) == 1
    assert release.load()["release_ready"] is False
    assert release.run(finalize=release.put("exit-ok.json", exit_proof())) == 0
    m = release.load()
    assert m["release_ready"] is True and m["gates"]["G15"]["state"] == "pass" and m["measures"]["M3"]["samples"] > 0
    # A manifest alone is not a success: CHAIN_RESULT=PASS must exist and match.
    result = release.dir / "CHAIN_RESULT"
    assert release.run(check_final=result) == 1
    result.write_text(f"PASS {COMMIT} {SHA} {TAG} 2026-11-23 10:00:00\n")
    assert release.run(check_final=result) == 1
    (release.dir / "CHAIN_RESULT.invalid").write_text(m["chain_result_expected"] + "\n")
    assert release.run(check_final=release.dir / "CHAIN_RESULT.invalid") == 1
    result.write_text(m["chain_result_expected"] + " 2026-11-23 10:00:00\n")
    assert release.run(check_final=result) == 0
    # Nothing can be recorded or finalised again afterwards.
    assert release.run(finalize=release.put("exit-again.json", exit_proof())) == 1
    assert release.run(record_decision=release.put("d/m2.json", decision("m2_lexical"))) == 1


def finalize(release):
    return release.run(finalize=release.put("exit-%d.json" % len(list(release.inputs.iterdir())), exit_proof()))


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
    path = release.put("install.json", proof)
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
    assert verifier.main([str(release.path), "--record-install-proof", str(release.put("s.json", proof))]) == 0
    assert release.load()["release_ready"] is False and "install_semantic" in release.load()["evidence"]


# ---------- tooling inventory ----------

def test_tool_inventory_exists_and_seals_the_external_chain_and_supervisor(tmp_path):
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
    chain = tmp_path / gate.CHAIN_NAME
    chain.write_text("#!/bin/zsh\n")
    assert maker.external_tool(chain, gate.CHAIN_NAME)["sha256"] == gate.digest(chain)
    refused(maker.external_tool, chain, gate.SUPERVISOR_NAME)
    refused(maker.external_tool, Path(gate.CHAIN_NAME), gate.CHAIN_NAME)
    refused(maker.external_tool, SCRIPTS / "make_release_410.py", "make_release_410.py")  # must live outside the repo
    link = tmp_path / "link" / gate.CHAIN_NAME
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

def home_inventory(extra=None):
    f = lambda sha: {"type": "file", "mode": 0o600, "bytes": 4, "sha256": sha}  # noqa: E731
    label = helper.TEST_LABEL
    rows = {
        ".config/memory-os/http.token": f("t"), ".config/memory-os/cl100k_base.tiktoken": f("c"),
        ".config/memory-os/backup.key": f("k"), "memory-os-backups/a.mosb": f("b"),
        ".local/share/memory-os/store/store.sqlite3": f("s1"), ".local/share/memory-os/store/store.sqlite3-wal": f("w"),
        ".local/share/memory-os/store/store.sqlite3-shm": f("h"),
        ".local/state/memory-os/quickstart/S1/install.json": f("i"), ".local/state/memory-os/quickstart/S1/journal.json": f("j"),
        "Library/LaunchAgents/%s.plist" % label: f("p"), "Library/Logs/memory-os/server.log": f("l1"),
        ".codex/config.toml": f("x1"), ".cursor/mcp.json": f("y1"),
    }
    rows.update(extra or {})
    return rows, f


def uninstalled(rows, f):
    after = dict(rows)
    label = helper.TEST_LABEL
    del after["Library/LaunchAgents/%s.plist" % label], after[".local/share/memory-os/store/store.sqlite3-wal"]
    del after[".local/share/memory-os/store/store.sqlite3-shm"]
    after.update({
        ".local/share/memory-os/store/store.sqlite3": f("s2"),  # checkpoint at close: bytes change, logic compared
        ".local/state/memory-os/uninstall/U1/uninstall.json": f("u"),
        ".local/state/memory-os/uninstall/U1/%s.plist" % label: f("p"),
        ".local/state/memory-os/quickstart/S1/journal.json": f("j-revoked"),
        "Library/Logs/memory-os/server.log": f("l2"), ".codex/config.toml": f("x2"), ".cursor/mcp.json": f("y2"),
    })
    return after


STORE = {"store_instance_id": "id1", "logical_sha256": "L", "tables": {"items": 2}}
CLIENTS = [".codex/config.toml", ".cursor/mcp.json"]


def test_uninstall_protocol_mutations_are_admitted_and_data_is_preserved():
    before, f = home_inventory()
    row = helper.data_conservation(before, uninstalled(before, f), STORE, dict(STORE), helper.TEST_LABEL, CLIENTS)
    assert row["unexpected_changes"] == [] and row["immutable_unchanged"] and row["journals_present"]
    assert row["store_identity_unchanged"] and row["store_logical_unchanged"]
    assert row["plist_removed_from_active"] and row["plist_journal_copy_kept"]
    assert row["immutable_files"] == {"token": 1, "resources": 1, "backups": 1, "key": 1}
    reasons = {m["path"]: m["why"] for m in row["expected_mutations"]}
    assert "SQLite close transition" in reasons[".local/share/memory-os/store/store.sqlite3"]
    assert "revocation" in reasons[".local/state/memory-os/quickstart/S1/journal.json"]


@pytest.mark.parametrize("change", [
    lambda a, f: a.update({".config/memory-os/http.token": f("changed")}),
    lambda a, f: a.pop(".config/memory-os/cl100k_base.tiktoken"),
    lambda a, f: a.update({"memory-os-backups/a.mosb": f("changed")}),
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
    row = helper.data_conservation(before, after, STORE, dict(STORE), helper.TEST_LABEL, CLIENTS)
    assert row["unexpected_changes"] or not row["immutable_unchanged"] or not row["journals_present"]


def test_store_identity_or_logical_change_is_a_data_modification_not_a_close_transition():
    before, f = home_inventory()
    after = uninstalled(before, f)
    row = helper.data_conservation(before, after, STORE, {**STORE, "logical_sha256": "other"}, helper.TEST_LABEL, CLIENTS)
    assert row["store_logical_unchanged"] is False
    row = helper.data_conservation(before, after, STORE, {**STORE, "store_instance_id": "id2"}, helper.TEST_LABEL, CLIENTS)
    assert row["store_identity_unchanged"] is False
    row = helper.data_conservation(before, after, STORE, {**STORE, "tables": {"items": 1}}, helper.TEST_LABEL, CLIENTS)
    assert row["store_logical_unchanged"] is False
    kept = dict(after)
    kept["Library/LaunchAgents/%s.plist" % helper.TEST_LABEL] = f("p")
    assert helper.data_conservation(before, kept, STORE, STORE, helper.TEST_LABEL, CLIENTS)["plist_removed_from_active"] is False


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
    assert (m1["t0_epoch"], m1["t1_epoch"], m1["total_s"]) == (100.0, 160.0, 60.0)
    assert m1["pip_s"] == 22.0 and m1["controls_s"] == 5.0 and m1["net_s"] == 55.0 and m1["outside_model_s"] == 58.0
    assert m1["pip_download_bytes"] == 12345 != m1["quickstart_download_bytes"] == 1681126  # N3 bytes vs N6 bytes
    assert gate.validate_m1(m1, "lexical", "autonomous")["label"] == "first installation"
    pth = helper.m1(timeline, {"timing": {**timing, "download_bytes": 0}}, "semantic", "pth", 0)
    assert pth["label"] == "reused-dependencies variant" and "model_pre_placed" in pth["protocol_deviations"]
    gate.validate_m1(pth, "semantic", "pth")
    refused(helper.timeline, "pip_deps 1 2\n")              # no T0 / T1
    refused(helper.timeline, "venv 1 2\nvenv 2 3\napply 3 4\n")  # a span appears once
    refused(helper.timeline, "venv 5 2\napply 3 4\n")


def write_install_tree(d, profile="lexical"):
    """Files exactly as qualify_install_410.sh writes them, without running anything."""
    def put(rel, value):
        path = d / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    locks = 36 if profile == "lexical" else 65
    absent = {"label_state": "absent", "port_state": "free"}
    inv = {"inventory_identical": True, "inventory_hashes_content": True, "inventory_entries": 4,
           "inventory_before_sha256": SHA, "inventory_after_sha256": SHA}
    put("meta.json", {"profile": profile, "variant": "autonomous", "source_commit": COMMIT, "run_tag": TAG,
                      "version": "4.1.0", "venv": str(d / "venv"), "label": helper.TEST_LABEL, "port": helper.TEST_PORT,
                      "wheel": {"name": "w.whl", "sha256": WHEEL_SHA}})
    put("constraints_check.json", {"constraints_sha256": LEX_SHA if profile == "lexical" else SEM_SHA, "mismatch": [],
                                   "unpinned": [], "ml_packages": [], "bootstrap": ["pip", "setuptools"],
                                   "pinned_and_equal": locks, "locks": locks, "ok": True})
    put("locations.json", {"locator_under_site_packages": True, "package_under_site_packages": True,
                           "package_path": "/v/site-packages/memory_os_v4/__init__.py", "reused_paths": [],
                           "candidate_origin_sha256": WHEEL_SHA})
    put("pip_check.json", {"returncode": 0})
    put("pip_install_report.json", {"install": [
        {"metadata": {"name": f"p{i}", "version": "1"},
         "download_info": {"url": f"https://files.pythonhosted.org/p{i}.whl", "archive_info": {"hashes": {"sha256": SHA}}}}
        for i in range(locks)]})
    prod = {"files": {}, "launch_agents": ["a.plist"], "production_pid": 77, "production_label_state": "present", "sha256": SHA}
    put("production_before.json", prod)
    put("production_after.json", prod)
    put("timeline.json", helper.timeline("venv 100 103\npip_deps 103 123\ncontrol_preview 123 128\napply 128 160\n"))
    put("pip_cache_bytes.json", {"bytes": 4096})
    put("safety_net.json", {"fired": False})
    put("shell_verdict.json", {"ok": True})
    created = {rel: {"sha256": SHA, "mentions_test_service": True} for rel in helper.TEST_CLIENT_FILES}
    none = {rel: None for rel in helper.TEST_CLIENT_FILES}
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
        put(p + "/doctor.json", [{"area": "store", "status": "PASS"}, {"area": "token", "status": "WARN"}])
        put(p + "/doctor_rc.json", {"returncode": 0})
        put(p + "/clients_after_apply.json", created)
    put("install1/preview.json", {"ok": True, "profile": profile})
    put("install1/preview_rc.json", {"returncode": 0})
    put("install1/preview_inventory.json", inv)
    put("install1/undo.json", {"ok": True})
    put("install1/undo_rc.json", {"returncode": 0})
    put("install1/undo_state.json", absent)
    put("install1/clients_before_apply.json", none)
    put("install1/clients_after_undo.json", none)
    put("install2/uninstall_preview.json", {"ok": True})
    put("install2/uninstall_preview_rc.json", {"returncode": 0})
    put("install2/uninstall_preview_inventory.json", inv)
    put("install2/uninstall.json", {"ok": True, "partial": False, "complete": False, "data_deleted": False, "clients": []})
    put("install2/uninstall_rc.json", {"returncode": 0})
    put("install2/uninstall_state.json", absent)
    put("install2/clients_after_uninstall.json", {rel: {"sha256": "z" * 64, "mentions_test_service": False}
                                                  for rel in helper.TEST_CLIENT_FILES})
    before, f = home_inventory()
    put("install2/data_before.json", {"rows": before})
    put("install2/data_after.json", {"rows": uninstalled(before, f)})
    put("install2/store_before.json", STORE)
    put("install2/store_after.json", STORE)
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
    assert report["install_1"]["doctor"] == {"returncode": 0, "checks": 2, "fail": 0}
    assert "meta.json" in report["evidence_sha256"] and "report.json" not in report["evidence_sha256"]


@pytest.mark.parametrize("damage", [
    lambda d: (d / "install2/uninstall_state.json").write_text(json.dumps({"label_state": "present", "port_state": "busy"})),
    lambda d: (d / "install1/undo_state.json").write_text(json.dumps({"label_state": "present", "port_state": "free"})),
    lambda d: (d / "safety_net.json").write_text(json.dumps({"fired": True})),
    lambda d: (d / "shell_verdict.json").write_text(json.dumps({"ok": False})),
    lambda d: (d / "install2/clients_after_uninstall.json").write_text(json.dumps(
        {rel: {"sha256": SHA, "mentions_test_service": True} for rel in helper.TEST_CLIENT_FILES})),
    lambda d: (d / "install1/doctor.json").write_text(json.dumps([{"area": "store", "status": "FAIL"}])),
    lambda d: (d / "install1/clients_after_undo.json").write_text(json.dumps({".cursor/mcp.json": {"sha256": SHA}})),
    lambda d: (d / "install2/uninstall.json").write_text(json.dumps({"ok": True, "partial": True, "data_deleted": False})),
    lambda d: (d / "production_after.json").write_text(json.dumps({"files": {}, "launch_agents": [], "production_pid": 78,
                                                                   "sha256": WHEEL_SHA})),
    lambda d: (d / "install2/store_after.json").write_text(json.dumps({**STORE, "logical_sha256": "x"})),
    lambda d: (d / "install1/install.json").unlink(),         # an absent proof is a failure
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

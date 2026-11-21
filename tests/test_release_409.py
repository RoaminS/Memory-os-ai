"""Model-free 4.0.9 RC checks: health-only engine boundary, no proof transfer.

Synthetic inputs only. The only real-data access is read-only git on this
checkout (diff/show/archive of a pinned candidate commit).
"""

import copy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import pytest  # noqa: E402
import release_409_checks as gate  # noqa: E402
from release_copy_worker import QUERIES  # noqa: E402

BASE = "9ecb4d06f68e2c7efddf77aad66f4ed5f147f356"
# 4.0.9 hygiene candidate (health fix + version bump + docs). Pinned so this
# test keeps proving the rule on real bytes even after later commits.
CANDIDATE = "2602a5c0fa5877aec2aff8e985d124b2b13ae340"
PB_INPUT_SHA = "70ab2c61172f5401ea12b68a2b3566e593c25689a42e8f9b2eac21d2a4d05de0"
INIT = "v4/src/memory_os_v4/__init__.py"
HEALTH = "v4/src/memory_os_v4/ops/health.py"


def proof(commit="c"):
    profile = {
        "device": "mps",
        "dtype": "native",
        "batch_size": 32,
        "max_seq_length": 512,
        "parameter_devices": ["mps"],
        "model_revision": "revision",
        "encoder_source_sha256": "encoder",
    }
    unchanged = {
        "ok": True,
        "from_schema": 8,
        "to_schema": 8,
        "store_instance_id": "i",
        "tables_preserved": {"facts": {"rows": 2, "sha256": "a"}},
    }
    row = {
        "ok": True,
        "store_instance_id": "i",
        "total": 2,
        "search": {q: ["id"] for q in QUERIES},
        "search_hit_counts": {q: 1 for q in QUERIES},
        "encoding_profile": profile,
        "cache_misses": 0,
        "corpus_encoded_texts": 0,
        "startup_seconds": 1,
        "migration": None,
        "schema_check": unchanged,
        "version": "4.0.9",
        "migration_seconds": 1,
        "journal": {"generation": "g", "coverage_start": 1, "events": 0},
        "authority": {"sha256": "a", "schema_version": 8},
    }
    caps = {
        "store": {"backend": "sqlite_s1", "instance_id": "i"},
        "work_state": True,
        "retrieval_ready": True,
        "projects": {"registry_enabled": False},
        "policies": {
            "write_mode": "read_only",
            "bm25_profile": "scoped_literal",
            "lexical_guard_k": 3,
        },
        "runtime": {"ok": True},
        "brief": {"tokenizer": {"ready": True}},
        "readiness_audit": {
            "max_reuse_seconds": 1.0,
            "live_checks_every_request": True,
            "untracked_memory_fault_detection_seconds": 1.0,
        },
    }
    manifest = {
        "version": "4.0.9",
        "source_commit": commit,
        "artifacts": [],
        "copy_scripts_sha256": {},
    }
    p = {
        "ok": True,
        "version": "4.0.9",
        "baseline_version": "4.0.7",
        "source_commit": commit,
        "artifacts": [],
        "source_sql_writes": False,
        "production_modified": False,
        "scripts_sha256": {},
        "expected_instance_id": "i",
        "workers": {
            n: copy.deepcopy(row)
            for n in ("baseline", "wheel", "sdist", "wheel-warm", "sdist-warm")
        },
        "seconds": 1,
        "search_identical_to_baseline": True,
        "authority_identical": True,
        "rankings_identical_between_artifacts_and_reopen": True,
        "warm_encoded_texts": 0,
        "doctor": {
            "ok": True,
            "ready": {
                "version": "4.0.9",
                "commit": commit,
                "identity_source": "embedded_descriptor",
                "encoding_profile": profile,
                "capabilities": caps,
            },
        },
        "s4": {
            n: {
                "backup": {
                    "ok": True,
                    "qualified": True,
                    "source_sql_writes": False,
                    "store_instance_id": "i",
                    "verified": True,
                    "archive": "private.mosb",
                    "archive_sha256": "sha",
                },
                "drill": {
                    "ok": True,
                    "private_reopen_verified": True,
                    "verified": True,
                    "forget_reconciliation": "complete",
                    "restored_from_instance_id": "i",
                    "level": "service",
                    "archive": "private.mosb",
                    "archive_sha256": "sha",
                    "server_check": {
                        "match": True,
                        "version": "4.0.9",
                        "commit": commit,
                        "corpus_reencodings": 0,
                        "private_only": True,
                        "production_modified": False,
                    },
                },
            }
            for n in ("wheel", "sdist")
        },
    }
    p["workers"]["baseline"]["version"] = "4.0.7"
    return p, manifest


# --------------------------------------------------------------------------
# Engine boundary (design decision 2)
# --------------------------------------------------------------------------

def base_init():
    return subprocess.check_output(
        ["git", "show", BASE + ":" + INIT], cwd=ROOT, text=True
    )


def fake_git(monkeypatch, names, new_init, health_blob=None):
    """Mocked git: diff -> names; show BASE -> real base bytes; show other -> new_init bytes;
    rev-parse <commit>:health.py -> health_blob (defaults to the pinned reviewed blob)."""
    old = base_init()
    calls = []
    real = subprocess.check_output
    blob = gate.HEALTH_BLOB if health_blob is None else health_blob

    def query(argv, **kwargs):
        if argv[:1] != ["git"]:
            return real(argv, **kwargs)
        calls.append(list(argv))
        if argv[1] == "diff":
            return "".join(n + "\n" for n in names)
        if argv[1] == "rev-parse":
            return blob + "\n"
        assert argv[1] == "show" and "text" not in kwargs  # bytes, never text-normalised
        return (old if argv[2].startswith(BASE + ":") else new_init).encode()

    monkeypatch.setattr(subprocess, "check_output", query)
    return calls, old


def test_real_candidate_health_fix_and_version_line_is_accepted():
    assert subprocess.check_output(
        ["git", "rev-parse", CANDIDATE + "^{commit}"], cwd=ROOT, text=True
    ).strip() == CANDIDATE
    engine = gate.verify_engine_version_only(ROOT, CANDIDATE, "4.0.9")
    assert engine == {
        "base": BASE,
        "allowed_changed_paths": [INIT, HEALTH],
        "observed_changed_paths": [INIT, HEALTH],
        "version_file_change": ["4.0.8", "4.0.9"],
        "health_blob": gate.HEALTH_BLOB,
    }


def test_health_fix_other_than_reviewed_blob_is_refused(monkeypatch):
    fake_git(monkeypatch, [INIT, HEALTH], base_init().replace('"4.0.8"', '"4.0.9"'), health_blob="0" * 40)
    with pytest.raises(ValueError, match="reviewed 4.0.9 health fix"):
        gate.verify_engine_version_only(ROOT, "c" * 40, "4.0.9")


def _real_repo(tmp_path, new_bytes):
    """Real git (no mock): base commit with the 4.0.8 __init__, candidate with new_bytes."""
    def git(*a):
        return subprocess.check_output(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "core.autocrlf=false",
                                        *a], cwd=tmp_path, text=True).strip()
    git("init", "-q")
    init = tmp_path / INIT
    init.parent.mkdir(parents=True)
    init.write_bytes(subprocess.check_output(["git", "show", BASE + ":" + INIT], cwd=ROOT))
    health = tmp_path / HEALTH
    health.parent.mkdir(parents=True, exist_ok=True)
    health.write_bytes(subprocess.check_output(["git", "show", BASE + ":" + HEALTH], cwd=ROOT))
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    base = git("rev-parse", "HEAD")
    init.write_bytes(new_bytes)
    health.write_bytes(subprocess.check_output(["git", "show", gate.HEALTH_BLOB], cwd=ROOT))
    git("add", "-A")
    git("commit", "-q", "-m", "candidate")
    return base, git("rev-parse", "HEAD")


def test_real_git_crlf_version_file_is_refused_and_lf_bump_accepted(tmp_path, monkeypatch):
    original = subprocess.check_output(["git", "show", BASE + ":" + INIT], cwd=ROOT)
    bump = original.replace(b'__version__ = "4.0.8"', b'__version__ = "4.0.9"')
    for name, data, accepted in (("lf", bump, True), ("crlf", bump.replace(b"\n", b"\r\n"), False)):
        repo = tmp_path / name
        repo.mkdir()
        base, candidate = _real_repo(repo, data)
        monkeypatch.setattr(gate, "FROZEN_BASE", base)
        if accepted:
            assert gate.verify_engine_version_only(repo, candidate, "4.0.9")["observed_changed_paths"] == [INIT, HEALTH]
        else:
            with pytest.raises(ValueError, match="__version__ line"):
                gate.verify_engine_version_only(repo, candidate, "4.0.9")


def test_installed_relay_case_runs_brief_and_outage_status_for_409():
    import importlib
    case = importlib.import_module("installed_relay_case")
    assert {"4.0.8", "4.0.9"} <= case.COMPACT_BRIEF_AND_LOCAL_STATUS_VERSIONS


def test_rule_lists_both_sides_of_renames_and_uses_frozen_base(monkeypatch):
    calls, _ = fake_git(monkeypatch, [INIT, HEALTH],
                        base_init().replace('"4.0.8"', '"4.0.9"'))
    gate.verify_engine_version_only(ROOT, "c" * 40, "4.0.9")
    diff = calls[0]
    assert diff[:4] == ["git", "diff", "--no-renames", "--name-only"]
    assert diff[4] == BASE and diff[5] == "c" * 40 and diff[-2:] == ["--", "v4/src"]


def test_version_only_bump_without_health_change_is_refused(monkeypatch):
    # The reviewed health fix is an unconditional part of 4.0.9: a candidate reverting it refuses.
    fake_git(monkeypatch, [INIT], base_init().replace('"4.0.8"', '"4.0.9"'))
    with pytest.raises(ValueError, match="health fix missing"):
        gate.verify_engine_version_only(ROOT, "c" * 40, "4.0.9")


@pytest.mark.parametrize(
    "extra",
    [
        "v4/src/memory_os_v4/brief.py",
        "v4/src/memory_os_v4/brief_selection.py",
        "v4/src/memory_os_v4/brief_tokens.py",
        "v4/src/memory_os_v4/text.py",
        "v4/src/memory_os_v4/bm25.py",
        "v4/src/memory_os_v4/store.py",
        "v4/src/memory_os_v4/sqlite_store.py",
        "v4/src/memory_os_v4/sqlite_engine.py",
        "v4/src/memory_os_v4/ops/doctor.py",
        "v4/src/memory_os_v4/resources/new.json",
    ],
)
def test_any_extra_engine_path_is_refused(monkeypatch, extra):
    fake_git(monkeypatch, [INIT, HEALTH, extra],
             base_init().replace('"4.0.8"', '"4.0.9"'))
    with pytest.raises(ValueError, match="beyond version/health"):
        gate.verify_engine_version_only(ROOT, "c" * 40, "4.0.9")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s: s.replace('"4.0.8"', '"4.0.9"') + "EXTRA = 1\n",
        lambda s: s.replace('"4.0.8"', '"4.0.9"').replace("__all__", "__all__ ", 1),
        lambda s: s.replace('__version__ = "4.0.8"', '__version__ = "4.0.9"  # x'),
        lambda s: s.replace('__version__ = "4.0.8"\n', '__version__ = "4.0.9"'),
        lambda s: s.replace('"4.0.8"', '"4.0.10"'),
        lambda s: s.replace('"4.0.8"', '"4.0.9"').replace("\n", "\r\n"),
        lambda s: s,
    ],
    ids=["appended", "other-line", "comment", "no-newline", "wrong-version",
         "crlf", "unchanged-but-listed"],
)
def test_non_version_change_in_init_is_refused(monkeypatch, mutate):
    fake_git(monkeypatch, [INIT, HEALTH], mutate(base_init()))
    with pytest.raises(ValueError, match="__version__ line"):
        gate.verify_engine_version_only(ROOT, "c" * 40, "4.0.9")


def test_health_only_without_version_bump_is_refused(monkeypatch):
    fake_git(monkeypatch, [HEALTH], base_init())
    with pytest.raises(ValueError, match="not bumped"):
        gate.verify_engine_version_only(ROOT, "c" * 40, "4.0.9")


@pytest.mark.parametrize("version", ["4.0.8", "4.0.9rc1", "4.0.10", "4.1.0", ""])
def test_version_other_than_409_is_refused(version):
    with pytest.raises(ValueError, match="Wrong RC version"):
        gate.verify_engine_version_only(ROOT, CANDIDATE, version)


def test_short_commit_is_refused():
    with pytest.raises(ValueError, match="Complete source SHA"):
        gate.verify_engine_version_only(ROOT, CANDIDATE[:7], "4.0.9")


def test_408_tooling_still_refuses_the_409_candidate():
    import release_408_checks as old

    with pytest.raises(ValueError):
        old.verify_engine_version_only(ROOT, CANDIDATE, "4.0.8")
    with pytest.raises(ValueError, match="Wrong RC version"):
        old.validate_local({"version": "4.0.9", "source_commit": CANDIDATE}, ROOT)


def test_proof_transfer_record_transfers_nothing_from_408():
    t = gate.PROOF_TRANSFER
    assert t["transferred_from_4_0_8"] == []
    assert set(t["redo_on_final_commit"]) == {
        "full_source", "artifacts", "production_copy_s4", "p_b_recollection",
        "installed_relays",
    }
    assert "brief" in t["p_a"] and "identical" in t["p_a"]


# --------------------------------------------------------------------------
# Copy proof (same schema8 contract as 4.0.8)
# --------------------------------------------------------------------------

def test_valid_copy_preserves_same_schema8_contract():
    p, m = proof()
    gate.validate_copy(p, m)


@pytest.mark.parametrize(
    "target", ["baseline", "wheel", "sdist", "wheel-warm", "sdist-warm"]
)
def test_empty_equality_is_never_a_qualified_search(target):
    p, m = proof()
    p["workers"][target]["search"] = {q: [] for q in QUERIES}
    p["workers"][target]["search_hit_counts"] = {q: 0 for q in QUERIES}
    with pytest.raises(ValueError):
        gate.validate_copy(p, m)


@pytest.mark.parametrize(
    "field,value",
    [("cache_misses", 1), ("corpus_encoded_texts", 1), ("startup_seconds", 121)],
)
def test_cache_or_time_deviation_refused(field, value):
    p, m = proof()
    p["workers"]["wheel"][field] = value
    with pytest.raises(ValueError):
        gate.validate_copy(p, m)


@pytest.mark.parametrize(
    "what",
    ["reset-journal", "authority", "schema", "missing-s4", "old-baseline",
     "migration", "candidate-408", "doctor-408"],
)
def test_no_migration_does_not_waive_authority_or_s4(what):
    p, m = proof()
    if what == "reset-journal":
        p["workers"]["wheel"]["journal"]["events"] = 0
        p["workers"]["baseline"]["journal"]["events"] = 7
    elif what == "authority":
        p["workers"]["wheel"]["authority"]["sha256"] = "other"
    elif what == "schema":
        p["workers"]["baseline"]["schema_check"]["from_schema"] = 7
    elif what == "missing-s4":
        p["s4"] = {}
    elif what == "old-baseline":
        p["baseline_version"] = "4.0.8"
    elif what == "candidate-408":
        p["workers"]["sdist"]["version"] = "4.0.8"
    elif what == "doctor-408":
        p["doctor"]["ready"]["version"] = "4.0.8"
    else:
        p["workers"]["wheel"]["migration"] = {"ok": True}
    with pytest.raises((ValueError, KeyError)):
        gate.validate_copy(p, m)


@pytest.mark.parametrize("bad", ["archive", "version", "corpus_reencodings", "level"])
def test_s4_old_or_unlinked_service_proof_never_qualifies(bad):
    p, m = proof()
    drill = p["s4"]["wheel"]["drill"]
    if bad == "archive":
        drill["archive"] = "some-other.mosb"
    elif bad == "level":
        drill["level"] = "files"
    else:
        drill["server_check"][bad] = "4.0.8" if bad == "version" else 1
    with pytest.raises(ValueError):
        gate.validate_copy(p, m)


def test_copy_budget_keeps_s4_reserve_even_with_three_gb_override(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from qualify_sqlite_release_409 import start_disk_budget

    monkeypatch.setenv("MEMORY_V4_MIN_FREE_BYTES", "3000000000")
    monkeypatch.delenv("MEMORY_V4_SQLITE_STORAGE_POLICY", raising=False)
    assert start_disk_budget(tmp_path, 5_200_000_000)["required_start_free_bytes"] == 7_000_000_000
    fake = SimpleNamespace(glob=lambda pattern: [SimpleNamespace(
        is_file=lambda: True, stat=lambda: SimpleNamespace(st_size=200_000_000))])
    assert start_disk_budget(fake, 5_200_000_000)["required_start_free_bytes"] == 9_000_000_000


def test_copy_plan_format_is_409_on_both_sides():
    runner = (ROOT / "scripts/qualify_sqlite_release_409.py").read_text()
    worker = (ROOT / "scripts/sqlite_release_worker_409.py").read_text()
    assert '"sqlite-release-409-v1"' in runner and '"sqlite-release-409-v1"' in worker
    assert "408" not in runner and "408" not in worker
    assert 'with_name("sqlite_release_worker_409.py")' in runner
    assert '"baseline_version": "4.0.7"' in runner


# --------------------------------------------------------------------------
# Tool inventory (design decision 1)
# --------------------------------------------------------------------------

def test_every_new_release_tool_is_attested():
    import make_release_409 as make

    wanted = {
        "make_release_409.py", "release_409_checks.py", "prepare_release_409_envs.py",
        "qualify_sqlite_release_409.py", "sqlite_release_worker_409.py",
        "record_release_409_gate.py", "score_release_409_gate.py",
        "source_release_checks_409.py", "schema8_unchanged.py",
        "verify_release_manifest.py", "qualify_405_test_isolation.py",
    }
    assert wanted <= set(make.TOOLS)
    assert len(set(make.TOOLS)) == len(make.TOOLS)
    assert all((make.ROOT / "scripts" / name).is_file() for name in make.TOOLS)
    # Only the generic qualify_405_test_isolation.py keeps an older number.
    assert not [n for n in make.TOOLS if re.search(r"_40[6-8](_|\.py)", n)]
    assert make.BASE == BASE == gate.FROZEN_BASE


def test_tool_inventory_mirrors_408_with_only_versioned_names_renamed():
    import make_release_408 as old
    import make_release_409 as make

    assert [n.replace("_408", "_409") for n in old.TOOLS] == make.TOOLS


def test_exact_tool_inventory_cannot_be_downgraded_or_substituted():
    import make_release_409 as make

    hashes = {name: gate.digest(make.ROOT / "scripts" / name) for name in make.TOOLS}
    gate.verify_tooling(make.ROOT, hashes)
    for change in ("missing", "extra", "changed", "old-408"):
        altered = dict(hashes)
        if change == "missing":
            altered.pop("schema8_unchanged.py")
        elif change == "extra":
            altered["unsealed.py"] = "0" * 64
        elif change == "old-408":
            altered.pop("release_409_checks.py")
            altered["release_408_checks.py"] = gate.digest(
                make.ROOT / "scripts/release_408_checks.py")
        else:
            altered["qualify_sqlite_release_409.py"] = "0" * 64
        with pytest.raises(ValueError):
            gate.verify_tooling(make.ROOT, altered)


def test_versioned_409_scripts_import_only_409_checks():
    for path in (ROOT / "scripts").glob("*_409*.py"):
        text = path.read_text()
        assert "release_408" not in text and "_408" not in text, path.name


# --------------------------------------------------------------------------
# Procedure (design decision 7)
# --------------------------------------------------------------------------

def test_procedure_uses_fresh_409_paths_and_existing_scripts():
    import make_release_409 as make

    text = (ROOT / "v4/docs/QUALIFY_4_0_9.md").read_text()
    scripts = set(re.findall(r"scripts/([A-Za-z0-9_]+\.py)", text))
    assert scripts and all((ROOT / "scripts" / s).is_file() for s in scripts)
    assert not [s for s in scripts if "_408" in s or "_407" in s]
    assert {s for s in scripts if "_409" in s} <= set(make.TOOLS)
    assert "$HOME/memory-os-local/codex_runs/release-409-hygiene" in text
    assert "Documents/Codex/2026-10-09" not in text
    assert 'RC_PY="$HOME/memory-os-local/qual-envs/407-installed-v2/wheel/bin/python"' in text
    assert "venv-4.0.7/bin/python" in text
    assert PB_INPUT_SHA in text
    assert "qualify_409_c1_chain.sh" in text
    outputs = set(re.findall(r"\.local-qualification/([A-Za-z0-9_.\-]+)", text))
    outputs |= set(re.findall(r"qual-envs/([A-Za-z0-9_.\-]+)", text)) - {"407-installed-v2"}
    assert outputs
    for name in outputs:
        assert name.startswith(("409-", "release-4.0.9-v1")), name
        assert "408" not in name


# --------------------------------------------------------------------------
# Verifier dispatch and synthetic manifest round trip (design decision 3)
# --------------------------------------------------------------------------

def test_rc_verifier_dispatch_is_fail_closed(tmp_path):
    import verify_release_manifest as v

    path = tmp_path / "manifest.json"
    path.write_text('{"version":"4.0.9","release_ready":false}')
    original = path.read_bytes()
    assert v.main([str(path)]) == 1
    assert path.read_bytes() == original


def test_verifier_routes_409_and_keeps_408_routing(monkeypatch, tmp_path):
    import release_408_checks
    import verify_release_manifest as v

    seen = []
    monkeypatch.setattr(gate, "main", lambda args: seen.append("409") or 7)
    monkeypatch.setattr(release_408_checks, "main", lambda args: seen.append("408") or 8)
    monkeypatch.setattr(gate, "validate_local", lambda *a, **k: seen.append("v409"))
    monkeypatch.setattr(release_408_checks, "validate_local", lambda *a, **k: seen.append("v408"))
    for version, code in (("4.0.9", 7), ("4.0.8", 8)):
        path = tmp_path / (version + ".json")
        path.write_text(json.dumps({"version": version}))
        assert v.main([str(path)]) == code
        v.validate_local({"version": version}, tmp_path)
    assert seen == ["409", "v409", "408", "v408"]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")
    return path


TCP_OK = {"ok": True, "expected_cases": 32, "observed_cases": 32,
          "missing": [], "unexpected": [], "unsuccessful": []}


@pytest.fixture
def release(tmp_path):
    """A make_release_409-shaped manifest built from synthetic evidence only."""
    import make_release_409 as make

    out = tmp_path / "release-4.0.9-v1"
    (out / "artifacts").mkdir(parents=True)
    (out / "evidence").mkdir()
    artifacts = []
    for name in ("memory_os_ai-4.0.9-py3-none-any.whl", "memory_os_ai-4.0.9.tar.gz"):
        f = out / "artifacts" / name
        f.write_bytes(b"synthetic " + name.encode())
        artifacts.append({"name": name, "bytes": f.stat().st_size, "sha256": gate.digest(f)})
    steps = ("direct_build", "sdist_build", "extra_markers", "install-0", "install-1",
             "smoke-0", "smoke-1", "quickstart-preview-0", "quickstart-preview-1")
    art = write(out / "evidence/artifacts.json", {
        "ok": True, "artifact_checked": True, "source_commit": CANDIDATE,
        "identity": {"commit": CANDIDATE},
        "artifact_sha256": {"direct/" + artifacts[0]["name"]: artifacts[0]["sha256"],
                            "via-sdist/" + artifacts[1]["name"]: artifacts[1]["sha256"]},
        "steps": {k: {"ok": True} for k in steps},
    })
    source_sha = {"v4/src/memory_os_v4/ops/health.py": "0" * 64}
    full = write(out / "evidence/full.json", {
        "ok": True, "tcp_qualified": True, "client_configs_unchanged": True,
        "source_sha256": source_sha,
        "commands": [
            {"name": "v3", "returncode": 0,
             "counts": {"tests": 700, "errors": 0, "failures": 0, "skipped": 0}},
            {"name": "v4", "returncode": 0, "tcp_evidence": TCP_OK,
             "counts": {"tests": 2000, "errors": 0, "failures": 0, "skipped": 0}},
        ],
    })
    twine = out / "evidence/twine-check-strict.log"
    twine.write_text("Checking a: PASSED\nChecking b: PASSED\n")
    m = {
        "version": "4.0.9",
        "source_commit": CANDIDATE,
        "release_ready": False,
        "open_blockers": [],
        "frozen_functional_base": make.BASE,
        "engine_diff": gate.verify_engine_version_only(ROOT, CANDIDATE, "4.0.9"),
        "proof_transfer": gate.PROOF_TRANSFER,
        "checks": {"production_copy": "pending", "installed_relays": "pending",
                   "gate_b_rc": "pending"},
        "artifacts": artifacts,
        "evidence": {
            "artifacts": {"path": "evidence/artifacts.json", "sha256": gate.digest(art)},
            "full": {"path": "evidence/full.json", "sha256": gate.digest(full)},
        },
        "qualification_tooling_sha256": {
            n: gate.digest(ROOT / "scripts" / n) for n in make.TOOLS},
        "qualification_source_sha256": source_sha,
        "source_expected_v3_tests": 700,
        "copy_scripts_sha256": {"runner": "r"},
        "installed_relay_scripts_sha256": {"runner": "a", "case": "b"},
        "twine_evidence": {"path": "evidence/twine-check-strict.log",
                           "sha256": gate.digest(twine)},
    }
    gate.validate_local(m, out, require_source=False, require_copy=False)
    path = write(out / "manifest.json", json.loads(json.dumps(m)))
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    return path, inputs


def manifest_of(path):
    return json.loads(path.read_text())


def test_synthetic_manifest_round_trip_keeps_guard_closed_until_relays(release):
    import verify_release_manifest as v

    path, inputs = release
    m = manifest_of(path)
    # Without any proof the final verification refuses and changes nothing.
    before = path.read_bytes()
    assert v.main([str(path)]) == 1
    assert path.read_bytes() == before

    source = write(inputs / "source.json", {
        "ok": True, "client_configs_unchanged": True, "version": "4.0.9",
        "source_commit": CANDIDATE, "source_sha256": m["qualification_source_sha256"],
        "tcp_evidence": TCP_OK,
        "checks": {name: {"passed": n, "failed": 0, "errors": 0, "skipped": 0}
                   for name, n in (("source_tcp", 32), ("cached_semantic", 1),
                                   ("v3_cached", 700))},
    })
    assert v.main([str(path), "--record-source-proof", str(source)]) == 0
    assert manifest_of(path)["release_ready"] is False

    p, _ = proof(CANDIDATE)
    p["artifacts"] = m["artifacts"]
    p["scripts_sha256"] = m["copy_scripts_sha256"]
    copy_proof = write(inputs / "copy.json", p)
    assert v.main([str(path), "--record-copy-proof", str(copy_proof)]) == 0
    assert manifest_of(path)["release_ready"] is False
    assert manifest_of(path)["checks"]["production_copy"] == "passed"

    archive = subprocess.check_output(
        ["git", "archive", CANDIDATE, "v4/src", "pyproject.toml"], cwd=ROOT)
    gates = {k: True for k in ("all_40_pass", "unexpected_invalidations_0",
                               "partial_success_0", "hard_safety_0",
                               "legit_replacements_all", "collection_integrity")}
    gate_proof = write(inputs / "gate.json", {
        "candidate_commit": CANDIDATE, "complete": True, "production_modified": False,
        "neural_calls": 0, "inputs_sha256": PB_INPUT_SHA,
        "source_archive_sha256": sha(archive),
        "binding": {"candidate_commit": CANDIDATE, "inputs_sha256": PB_INPUT_SHA},
        "scorer_result": {"gates": gates, "passed": 40, "total": 40, "verdict": True,
                          "replay": True, "collection_issues": [],
                          "failure_categories": [],
                          "inputs_sha256_recomputed": PB_INPUT_SHA},
    })
    assert v.main([str(path), "--record-gate-proof", str(gate_proof)]) == 0
    assert manifest_of(path)["release_ready"] is False
    assert v.main([str(path)]) == 1  # relays still pending

    from verify_release_manifest import CASE_CHECKS

    cases = []
    for kind, artifact in zip(("wheel", "sdist"), m["artifacts"]):
        cases.append({"kind": kind, "ok": True, "exit_code": 0, "facts": 2,
                      "version": "4.0.9", "source_commit": CANDIDATE,
                      "artifact_sha256": artifact["sha256"],
                      "local_status_during_outage": True, "handshake_version": "4.0.9",
                      **{c: True for c in CASE_CHECKS}})
    relay = {"ok": True, "source_commit": CANDIDATE,
             "scripts_sha256": m["installed_relay_scripts_sha256"], "cases": cases}
    stale = copy.deepcopy(relay)
    stale["cases"][1]["handshake_version"] = "4.0.8"
    stale_path = write(inputs / "relay-408.json", stale)
    assert v.main([str(path), "--record-relay-proof", str(stale_path)]) == 1
    assert manifest_of(path)["release_ready"] is False

    relay_path = write(inputs / "relay.json", relay)
    assert v.main([str(path), "--record-relay-proof", str(relay_path)]) == 0
    assert manifest_of(path)["release_ready"] is True
    assert v.main([str(path)]) == 0

    # Any later byte change on a sealed artifact or tampered boundary refuses.
    final = manifest_of(path)
    final["engine_diff"]["observed_changed_paths"].append("v4/src/memory_os_v4/brief.py")
    write(path, final)
    assert v.main([str(path)]) == 1
    final["engine_diff"]["observed_changed_paths"].pop()
    final["proof_transfer"]["transferred_from_4_0_8"] = ["production_copy_s4"]
    write(path, final)
    assert v.main([str(path)]) == 1


def test_synthetic_manifest_refuses_408_gate_and_foreign_commit(release):
    import verify_release_manifest as v

    path, inputs = release
    original = path.read_bytes()
    bad = write(inputs / "gate.json", {
        "candidate_commit": BASE, "complete": True, "production_modified": False,
        "neural_calls": 0, "inputs_sha256": PB_INPUT_SHA})
    assert v.main([str(path), "--record-gate-proof", str(bad)]) == 1
    assert path.read_bytes() == original
    m = manifest_of(path)
    m["source_commit"] = BASE
    write(path, m)
    with pytest.raises(ValueError):
        gate.validate_local(m, path.parent, require_source=False, require_copy=False)


def test_full_proof_record_keeps_guard_closed(release):
    import verify_release_manifest as v

    path, inputs = release
    m = manifest_of(path)
    full = json.loads((path.parent / m["evidence"]["full"]["path"]).read_text())
    proof_path = write(inputs / "full.json", full)
    assert v.main([str(path), "--record-full-proof", str(proof_path)]) == 0
    after = manifest_of(path)
    assert after["release_ready"] is False and after["checks"]["full"] == "passed"
    assert v.main([str(path), "--record-full-proof", str(proof_path)]) == 1


# --------------------------------------------------------------------------
# Installed environments (offline, mocked)
# --------------------------------------------------------------------------

def test_env_preparation_executes_actual_sdist_install_offline(tmp_path, monkeypatch):
    import prepare_release_409_envs as prep
    from memory_os_v4.ops import provision

    monkeypatch.setattr(prep, "validate_local", lambda *a, **k: None)
    monkeypatch.setattr(prep, "ROOT", tmp_path / "checkout")
    monkeypatch.setattr(provision, "guard", lambda path: None)
    wheel = tmp_path / "wheel"
    wheel.mkdir()
    (wheel / "__init__.py").write_text("")
    dist = tmp_path / "wheel-1.dist-info"
    dist.mkdir()
    monkeypatch.setattr(prep.subprocess, "check_output",
                        lambda *a, **k: json.dumps([str(wheel), str(dist)]))

    def create(builder, path):
        (path / "lib/python3.11/site-packages").mkdir(parents=True)
        (path / "bin").mkdir()
        (path / "bin/python").touch()

    monkeypatch.setattr(prep.venv.EnvBuilder, "create", create)
    commands = []
    monkeypatch.setattr(prep, "command", lambda cmd, *a: commands.append(cmd))
    release_dir = tmp_path / "release"
    (release_dir / "artifacts").mkdir(parents=True)
    artifacts = []
    for name in ("pkg.whl", "pkg.tar.gz"):
        f = release_dir / "artifacts" / name
        f.write_bytes(name.encode())
        artifacts.append({"name": name, "sha256": prep.digest(f)})
    m = {"version": "4.0.9", "source_commit": "c", "artifacts": artifacts}
    path = release_dir / "manifest.json"
    path.write_text(json.dumps(m))
    out = tmp_path / "external-venvs"
    monkeypatch.setattr(sys, "argv", ["prep", "--manifest", str(path), "--output", str(out),
                                      "--build-python", sys.executable])
    assert prep.main() == 0
    install = [c for c in commands if "install" in c]
    assert len(install) == 2
    assert install[0][-1].endswith(".whl") and install[1][-1].endswith(".tar.gz")
    assert all("--no-index" in c and "--no-deps" in c and "--no-build-isolation" in c
               for c in install)
    identity = [c for c in commands if "-c" in c][0][-1]
    assert "'4.0.9'" in identity
    assert json.loads((out / "environments.json").read_text())["ok"] is True

"""Closed 4.1.0 qualification rules (v4/docs/QUALIFY_4_1_0.md, revision 6). Fail closed.

Invariants of §1, gate states, admissible decisions, commit/artifact binding. No recording
command raises ``release_ready``: only the final verification (G15) does, after the exit
checks, and a manifest alone is never a success without a matching ``CHAIN_RESULT=PASS``.
This module never runs P-A or P-B and never reads or lists the sealed validation folders:
its only reference to them is the guard that refuses them.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from subprocess import SubprocessError

RELEASE_VERSION = "4.1.0"
HYGIENE_ANCESTOR = "83ddeadaf8a6b6baef2cd04950e9a50036533471"  # 4.0.9 hygiene, carried by 4.1.0
P_A_PROOF_COMMIT = "ceedb5cfdfd0d61929276dbcd1417ea077044412"
P_A_INTERMEDIATE = "9e41ea40141f8ed4d7ed12a92a1900d2409272c3"
SCHEMA_VERSION = 8
MIGRATION_CHECKSUMS = [
    (1, "54e6fec963090c02f40f6e10d3b5fa35872cfe20dcdf795be8c90b04dae0d751"),
    (2, "4f49cbdfb4f02a0b66e60a0eb55202e1f681b00bc8d532e06303a26fc95fcda8"),
    (3, "0163af0cb61a33d4a48270e215a5d7edf9e0045779caedb67336a101fd7da2f3"),
    (4, "01da599ca7d40b1b81de34c3b416f97df3ac7b5d2207e3fb5f6ca4db41278dfc"),
    (5, "015ee4f350ff4d98ea939f1df54d36c92cec243b42d7bc89258726f3bfe051be"),
    (6, "3e1c85a2f4ae0e92e6280a372df03eed6c606bdfa0888bfb8ea95385ed433823"),
    (7, "6eb4c999718d2e660c9cc88ff340dbfef113bc5d46c8ee2528720d739fedeab1"),
    (8, "c318fdc44abef5abd88dc61fba5d937b92ac96ec540a5a9db01a1db7bc99d86a"),
]
EMBED_FILE = "v4/src/memory_os_v4/embed.py"
EMBED_SHA256 = "c2c335c468d7dedf7cff0ca2ce281c83d7c12fc9c65fd2c5b93adbbfcc5d60a7"  # LEXICAL_PROFILE §6
STORE_FILE = "v4/src/memory_os_v4/sqlite_store.py"
RESOURCES = "v4/src/memory_os_v4/resources/"
# Semantic constraints and catalogue: byte-identical to 83ddead.
SEMANTIC_FROZEN = (RESOURCES + "qualified_runtime_constraints_v1.txt", RESOURCES + "qualified_runtime_v1.json")
PROFILES = {
    "lexical": {"extras": ["ops", "brief"], "constraints": RESOURCES + "qualified_runtime_constraints_lexical_v1.txt",
                "locks": 36},
    "semantic": {"extras": ["ops", "brief", "semantic"],
                 "constraints": RESOURCES + "qualified_runtime_constraints_v1.txt", "locks": 65},
}
LEXICAL_MARKER = {"name": "lexical", "policy": "lex-4.1.0-v1"}
SEMANTIC_MARKER = {"name": "semantic", "policy": None}
ML_MODULES = ("torch", "sentence_transformers", "transformers", "faiss")
SUPERVISOR_NAME = "qualify_409_step_supervisor.py"
SUPERVISOR_SHA256 = "483c0e8d5d951adbb64c2607c782ee36e2a6af547d05df8bb07b95754e8bded2"
CHAIN_NAME = "qualify_410_v1_chain.sh"
TEST_LABEL = "local.memory-os-v4.qual410"
TEST_PORT = 18765
PRODUCTION_LABEL = "com.romain.memory-os-v4"
PRODUCTION_PORT = 8765
CL100K_BYTES = 1681126
CL100K_SHA256 = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"
CL100K_URL = "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken"
GRANITE_HOME_BYTES = 220_227_876
TEST_CLIENT_FILES = (".codex/config.toml", ".cursor/mcp.json")  # the two clients every installation requests
REQUIRED_DOCTOR_AREAS = ("token", "local-runtime", "server", "security", "ready", "service", "store", "storage",
                         "store-profile", "relay-journals", "client:codex", "client:cursor")
PYTEST_PINS = "qualify_410_pytest_pins.txt"
PYPI_HOSTS = ("pypi.org", "files.pythonhosted.org")
# Romain's permanent rule: nothing reads or lists these folders. Names only; never opened.
FORBIDDEN_NAMES = ("brief_v5_val", "brient_v5_val")
RUN_TAG_RE = r"v[0-9]+"

GATES = tuple("G%d" % i for i in range(1, 16))
STATES = ("pending", "pass", "fail", "transfer_accepted", "waived_by_romain")
# §4: "pass" everywhere; G13 only by a recorded decision; G12 "pass" or an explicit waiver.
ADMISSIBLE = {gate: ("pass",) for gate in GATES}
ADMISSIBLE["G13"] = ("transfer_accepted", "waived_by_romain")
ADMISSIBLE["G12"] = ("pass", "waived_by_romain")
RELAY_CASES = (("wheel", "semantic"), ("sdist", "semantic"), ("wheel", "lexical"))
RELAY_CHECKS = ("installed", "ready", "roundtrip", "failure_reported", "reconnected", "no_replay",
                "clean_shutdown", "local_status_during_outage")
P_B_GATES = ("all_40_pass", "unexpected_invalidations_0", "partial_success_0", "hard_safety_0",
             "legit_replacements_all", "collection_integrity")
NETWORK = ("N1", "N2", "N3", "N4", "N5", "N6")
DOSSIER_FORMAT = "qualify-410-network-dossier-v1"
PYPI_INDEX = "https://pypi.org/simple"
# What each acquisition EFFECTIVELY does (the scripts are tested against these parameters). A GO names a
# dossier; the dossier must be exactly this description for the run: nothing wider, nothing else.
NETWORK_SPEC = {
    "N1": {"tool": "scripts/qualify_runtime_resolution.py", "operation": "pip install --dry-run", "profile": "lexical",
           "installs": False},
    "N2": {"tool": "scripts/qualify_runtime_resolution.py", "operation": "pip install --dry-run", "profile": "semantic",
           "installs": False},
    "N3": {"tool": "scripts/qualify_install_410.sh", "operation": "pip install", "profile": "lexical", "installs": True},
    "N4": {"tool": "scripts/qualify_lexical_wheel_410.py", "operation": "pip install -r " + PYTEST_PINS,
           "profile": "lexical", "installs": True},
    "N5": {"tool": "scripts/qualify_install_410.sh", "operation": "pip install", "profile": "semantic", "installs": True},
    "N6": {"tool": "scripts/qualify_install_410.sh", "operation": "memory-os-admin quickstart --download-approved",
           "profile": "lexical", "installs": False},
}
DECISION_FORMAT = "qualify-410-decision-v1"
# id -> (bound to RUN_TAG, section of §10)
DECISIONS = {
    "g13_p_a": (False, 1), "g12_external_proof": (False, 2), "g12_waiver": (False, 3),
    "semantic_pth_variant": (True, 4), **{"network_" + n: (True, 5) for n in NETWORK},
    "m2_lexical": (False, 6), "d1_resign": (False, 7), "launchd_nice": (True, 8), "launch": (True, 9),
}
# Chain steps, in order: none skipped, none resumed (§2). The chain writes exactly this list.
CHAIN_STEPS = (
    "preconditions", "full", "lint1", "lint2", "build", "manifest", "rec_decisions_pre", "source_checks",
    "rec_source", "resolve_lexical", "rec_resolve_lexical", "resolve_semantic", "rec_resolve_semantic",
    "install_lexical", "rec_install_lexical", "lexical_wheel", "rec_lexical_wheel", "lexical_store",
    "rec_lexical_store", "envs", "install_semantic", "rec_install_semantic", "copy", "rec_copy",
    "perimeter", "rec_perimeter", "await_d1", "rec_decisions_post", "rec_external", "relays", "rec_relay",
)
# Present only when Romain supplied the matching input (bounded wait for D1; external P-B proof).
OPTIONAL_STEPS = ("await_d1", "rec_external")


def digest(p):
    return hashlib.sha256(read_bytes(p)).hexdigest()


def require(ok, message):
    if not ok:
        raise ValueError(message)


def is_sha(value, length=64):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{%d}" % length, value) is not None


def refuse_forbidden(path):
    """Refuse any path at or below a sealed validation folder, before touching the filesystem.

    The lexical check runs first (no stat, no listing); the resolved form is checked next so a
    link placed outside cannot lead back inside.
    """
    text = os.path.abspath(os.fspath(path))
    require(not any(name in Path(text).parts for name in FORBIDDEN_NAMES),
            "Sealed validation folder refused (never read or listed)")
    resolved = os.path.realpath(text)
    require(not any(name in Path(resolved).parts for name in FORBIDDEN_NAMES),
            "Path resolves into a sealed validation folder: refused")
    return Path(text)


def read_bytes(path):
    """Every read of this module goes through the guard first: arguments, constructed paths, link targets."""
    return refuse_forbidden(path).read_bytes()


def read_text(path):
    return refuse_forbidden(path).read_text()


def load_json(path):
    return json.loads(read_text(path))


def exact_pins(text):
    rows = {}
    for raw in text.splitlines():
        line = raw.split("#")[0].strip()
        if line:
            found = re.fullmatch(r"([A-Za-z0-9_.\-]+)==([^\s;]+)", line)
            require(found is not None, "Only exact pins are allowed: " + line)
            rows[re.sub(r"[-_.]+", "-", found.group(1)).lower()] = found.group(2)
    require(rows, "Empty pin file")
    return rows


def network_context(root):
    """Effective parameters of the acquisitions, from the candidate tree (sealed in the manifest later)."""
    root = Path(root)
    pins = root / "scripts" / PYTEST_PINS
    return {"pins_sha256": digest(pins), "pins": exact_pins(read_text(pins)),
            "constraints": {name: digest(root / spec["constraints"]) for name, spec in PROFILES.items()}}


def network_dossier(acquisition, *, commit, run_tag, context):
    """The one dossier a GO Nn may authorise for this candidate and run: exact operation, sources, inputs."""
    require(acquisition in NETWORK_SPEC, "Unknown acquisition")
    spec = NETWORK_SPEC[acquisition]
    dossier = {"format": DOSSIER_FORMAT, "acquisition": acquisition, "source_commit": commit, "run_tag": run_tag,
               "tool": spec["tool"], "operation": spec["operation"], "installs": spec["installs"],
               "attempts": 1, "retries_or_widening_covered": False}
    if acquisition == "N6":
        dossier.update(sources=["openaipublic.blob.core.windows.net"], url=CL100K_URL, bytes=CL100K_BYTES,
                       sha256=CL100K_SHA256, times=1, where="first lexical installation only")
    else:
        profile = spec["profile"]
        dossier.update(sources=sorted(PYPI_HOSTS), index_url=PYPI_INDEX, only_binary=True, profile=profile,
                       constraints_sha256=context["constraints"][profile])
        if acquisition == "N4":
            dossier.update(pins_sha256=context["pins_sha256"], pins=context["pins"])
        else:
            dossier.update(extras=PROFILES[profile]["extras"], environments=1 if spec["installs"] else 0)
    return dossier


def evidence(m, directory, name):
    e = m["evidence"][name]
    p = Path(e["path"])
    require(not p.is_absolute() and ".." not in p.parts, "Unsafe evidence path")
    p = directory / p
    require(digest(p) == e["sha256"], "Evidence changed: " + name)
    return load_json(p)


def _git(root, *args, text=True):
    return subprocess.check_output(["git", *args], cwd=root, text=text)


def _schema_from_tree(root):
    """SCHEMA_VERSION and migration checksums of the checked-out engine (tree proven = commit)."""
    import sys

    sys.path.insert(0, str(Path(root) / "v4/src"))
    try:
        from memory_os_v4 import sqlite_store
    finally:
        sys.path.pop(0)
    require(Path(sqlite_store.__file__).resolve() == (Path(root) / STORE_FILE).resolve(),
            "Schema invariants must come from this checkout")
    return sqlite_store.SCHEMA_VERSION, [tuple(row) for row in sqlite_store.schema_checksums()]


def locks(text):
    return [line for line in (raw.split("#")[0].strip() for raw in text.splitlines())
            if re.fullmatch(r"[A-Za-z0-9_.\-]+==[^\s;]+", line)]


def verify_invariants(root, commit, *, git=_git, schema=_schema_from_tree):
    """§1 invariants, from Git objects at ``commit`` (never an engine-diff rule: 21 files changed)."""
    require(is_sha(commit, 40), "Complete source SHA required")
    root = Path(root)
    try:
        git(root, "merge-base", "--is-ancestor", HYGIENE_ANCESTOR, commit)
    except subprocess.CalledProcessError:
        raise ValueError("83ddead (4.0.9 hygiene) is not an ancestor of the candidate")

    def blob(path, revision=commit):
        return git(root, "show", revision + ":" + path, text=False)

    require(hashlib.sha256(blob(EMBED_FILE)).hexdigest() == EMBED_SHA256, "embed.py bytes changed (LEXICAL_PROFILE §6)")
    frozen = {}
    for path in SEMANTIC_FROZEN:
        data = blob(path)
        require(data == blob(path, HYGIENE_ANCESTOR), "Semantic constraints/catalogue differ from 83ddead: " + path)
        frozen[path] = hashlib.sha256(data).hexdigest()
    profiles = {}
    for name, spec in PROFILES.items():
        data = blob(spec["constraints"])
        count = len(locks(data.decode()))
        require(count == spec["locks"], f"{name} constraints: {count} locks, {spec['locks']} expected")
        profiles[name] = {"extras": spec["extras"], "constraints": spec["constraints"],
                          "constraints_sha256": hashlib.sha256(data).hexdigest(), "locks": count}
    import tomllib

    versions = {
        "pyproject.toml": tomllib.loads(blob("pyproject.toml").decode())["project"]["version"],
        "v4/pyproject.toml": tomllib.loads(blob("v4/pyproject.toml").decode())["project"]["version"],
    }
    server = json.loads(blob("server.json"))
    versions["server.json"] = server["version"]
    for index, package in enumerate(server["packages"]):
        versions["server.json#packages[%d]" % index] = package["version"]
    for path in ("v4/src/memory_os_v4/__init__.py", "src/memory_os_ai/__init__.py"):
        found = re.findall(r'^__version__ = "([^"]+)"$', blob(path).decode(), flags=re.M)
        require(len(found) == 1, "Ambiguous __version__ in " + path)
        versions[path] = found[0]
    require(set(versions.values()) == {RELEASE_VERSION}, "Version is not 4.1.0 everywhere: " + json.dumps(versions))
    # The schema numbers are computed from the checkout: it must be the commit's exact blob.
    require(git(root, "rev-parse", commit + ":" + STORE_FILE).strip()
            == git(root, "hash-object", STORE_FILE).strip(), "sqlite_store.py differs from the candidate commit")
    version, sums = schema(root)
    require(version == SCHEMA_VERSION and sums == MIGRATION_CHECKSUMS, "Schema 8 / migration checksums changed")
    return {"hygiene_ancestor": HYGIENE_ANCESTOR, "schema_version": version,
            "migration_checksums": [list(row) for row in sums], "embed_sha256": EMBED_SHA256,
            "semantic_frozen_sha256": frozen, "profiles": profiles, "versions": versions}


def verify_tooling(root, hashes):
    from make_release_410 import TOOLS

    require(isinstance(hashes, dict) and set(hashes) == set(TOOLS), "Incomplete tool inventory")
    for path, sha in hashes.items():
        require(Path(path).name == path and digest(Path(root) / "scripts" / path) == sha, "Tool changed: " + path)


def verify_external_tooling(record):
    """Chain and supervisor live outside the repository: sealed by path and SHA-256."""
    require(isinstance(record, dict) and set(record) == {"chain", "supervisor"}, "External tooling inventory")
    for name, expected in (("chain", CHAIN_NAME), ("supervisor", SUPERVISOR_NAME)):
        row = record[name]
        path = Path(row["path"])
        require(path.is_absolute() and path.name == expected and digest(path) == row["sha256"],
                "External tool changed: " + name)
    require(record["supervisor"]["sha256"] == SUPERVISOR_SHA256, "Supervisor is not the byte-identical 4.0.9 one")


def new_gates():
    return {gate: {"state": "pending", "evidence": [], "decision": None} for gate in GATES}


def set_gate(m, gate, state, *, name=None, decision=None):
    """Gate transitions are monotonic: a failure or a missing measure never becomes a success."""
    require(gate in GATES and state in STATES, "Unknown gate/state")
    row = m["gates"][gate]
    require(row["state"] in ("pending", state), f"{gate} already {row['state']}: no transition to {state}")
    require(state in ("pass", "fail", "pending") or decision is not None,
            f"{gate}: {state} requires a recorded decision of Romain")
    row["state"] = state
    if name and name not in row["evidence"]:
        row["evidence"].append(name)
    if decision:
        row["decision"] = decision


# ---------- artifacts, source (G1-G4) ----------

def artifact(m, suffix):
    return next(a for a in m["artifacts"] if a["name"].endswith(suffix))


def validate_preconditions(p, m):
    require(p.get("format") == "qualify-410-preconditions-v1" and p.get("ok") is True
            and p.get("source_commit") == m["source_commit"] and p.get("run_tag") == m["run_tag"],
            "Preconditions proof missing or for another candidate/run")
    for key in ("tree_clean", "rc_py_runtime_ok", "build_tools_present", "hf_offline", "minilm_cache_present",
                "granite_cache_conforms", "cl100k_conforms", "baseline_interpreter_matches_plist",
                "run_tag_unused", "locks_absent", "test_label_absent", "test_port_free"):
        require(p.get("checks", {}).get(key) is True, "Precondition not established: " + key)
    require(p.get("invariants") == m["invariants"], "Preconditions invariants differ from the manifest")


def validate_full(full, m):
    require(full.get("ok") is True and full.get("suite") == "full" and full.get("tcp_qualified") is True
            and full.get("client_configs_unchanged") is True, "Full/TCP/isolation pending")
    require(full["source_sha256"] == m["qualification_source_sha256"], "Full source mismatch")
    commands = {x["name"]: x for x in full["commands"]}
    for name in ("v3", "v4"):
        c = commands[name]
        # G2: zero failure, zero error, zero skip; never an automatic tolerance.
        require(c["returncode"] == 0 and c["counts"]["tests"] >= (700 if name == "v3" else 2000)
                and all(c["counts"][k] == 0 for k in ("errors", "failures", "skipped")), "Suite incomplete: " + name)
    return commands


EXPECTED_TCP_EVIDENCE = {"ok": True, "expected_cases": 32, "observed_cases": 32, "missing": [], "unexpected": [],
                         "unsuccessful": []}


def validate_lints(p, m, directory):
    require(p.get("source_commit") == m["source_commit"] and p.get("run_tag") == m["run_tag"], "Lint proof binding")
    rows = p.get("lints", [])
    require([r.get("name") for r in rows] == ["lint1", "lint2"], "Exactly two lints required")
    for row in rows:
        require(row.get("returncode") == 0 and is_sha(row.get("log_sha256")), "Lint failed: " + str(row.get("name")))
        log = directory / "evidence" / (row["name"] + ".log")
        require(digest(log) == row["log_sha256"], "Lint log changed")


def validate_local(m, directory, *, require_copy=True, require_source=True, **kw):
    require(m.get("version") == RELEASE_VERSION, "Wrong release version")
    require(is_sha(m.get("source_commit"), 40), "Complete source SHA required")
    require(re.fullmatch(RUN_TAG_RE, str(m.get("run_tag"))) is not None, "RUN_TAG missing")
    require(not m.get("open_blockers"), "Open blockers")
    require(set(m.get("gates", {})) == set(GATES)
            and all(row.get("state") in STATES for row in m["gates"].values()), "Gate inventory/state")
    root = Path(__file__).resolve().parents[1]
    require(m.get("invariants") == verify_invariants(root, m["source_commit"]), "Invariants record differs")
    require(m.get("profiles") == m["invariants"]["profiles"], "Profile record differs")
    verify_tooling(root, m["qualification_tooling_sha256"])
    verify_external_tooling(m["external_tooling_sha256"])
    require(m.get("pytest_pins_sha256") == digest(root / "scripts" / PYTEST_PINS), "Frozen pytest pins changed")
    wanted = {f"memory_os_ai-{RELEASE_VERSION}-py3-none-any.whl", f"memory_os_ai-{RELEASE_VERSION}.tar.gz"}
    require(len(m["artifacts"]) == 2 and {a["name"] for a in m["artifacts"]} == wanted, "Artifact inventory")
    for a in m["artifacts"]:
        p = directory / "artifacts" / a["name"]
        require(p.is_file() and digest(p) == a["sha256"] and p.stat().st_size == a["bytes"], "Artifact changed")
    art = evidence(m, directory, "artifacts")
    require(art.get("ok") is True and art.get("artifact_checked") is True
            and art["source_commit"] == m["source_commit"] and art["identity"]["commit"] == m["source_commit"],
            "Installed artifact identity missing")
    # Every artifact hash is tied to the bytes built from this commit: nothing from an earlier build.
    require(art["artifact_sha256"]["direct/" + artifact(m, ".whl")["name"]] == artifact(m, ".whl")["sha256"],
            "Wheel differs from qualification")
    require(art["artifact_sha256"]["via-sdist/" + artifact(m, ".tar.gz")["name"]] == artifact(m, ".tar.gz")["sha256"],
            "Sdist differs from qualification")
    require(all(k in art["steps"] for k in ("direct_build", "sdist_build", "extra_markers", "install-0", "install-1",
                                            "smoke-0", "smoke-1", "quickstart-preview-0", "quickstart-preview-1")),
            "Installed smokes incomplete")
    require(m.get("entry_points_checked") is True, "Entry points (memory-os-constraints) not checked")
    tw = directory / m["twine_evidence"]["path"]
    require(digest(tw) == m["twine_evidence"]["sha256"] and read_text(tw).count("PASSED") == 2
            and "FAILED" not in read_text(tw), "Twine strict missing")
    validate_preconditions(evidence(m, directory, "preconditions"), m)
    commands = validate_full(evidence(m, directory, "full"), m)
    validate_lints(evidence(m, directory, "lints"), m, directory)
    if require_source:
        from qualify_405_test_isolation import EXPECTED_TCP

        tcp = commands["v4"]["tcp_evidence"]
        # 32 exact identities, multiplicities and outcomes: the count alone never qualifies.
        require(len(EXPECTED_TCP) == 32 and tcp == EXPECTED_TCP_EVIDENCE, "32 TCP exact names incomplete")
        source = evidence(m, directory, "source")
        require(source.get("ok") is True and source.get("client_configs_unchanged") is True
                and source["version"] == m["version"] and source["source_commit"] == m["source_commit"]
                and source["source_sha256"] == m["qualification_source_sha256"], "Source/semantic proof mismatch")
        require(source.get("tcp_evidence") == tcp, "Source TCP manifest mismatch")
        for name, count in [("source_tcp", 32), ("cached_semantic", 1),
                            ("v3_cached", commands["v3"]["counts"]["tests"])]:
            require(source["checks"][name] == {"passed": count, "failed": 0, "errors": 0, "skipped": 0},
                    "Source proof incomplete: " + name)
    if require_copy:
        validate_copy(evidence(m, directory, "copy"), m)


# ---------- G5 ----------

def resolved_distributions(pip_report, m, profile):
    """URL and SHA-256 of every resolved distribution, from the hashed pip report itself."""
    rows = []
    wheel = artifact(m, ".whl")
    for item in pip_report.get("install", []):
        name, version = item["metadata"]["name"], item["metadata"]["version"]
        info = item.get("download_info", {})
        url, sha = info.get("url", ""), info.get("archive_info", {}).get("hashes", {}).get("sha256")
        require(is_sha(sha), "Resolved distribution without SHA-256: " + name)
        if re.sub(r"[-_.]+", "-", name).lower() == "memory-os-ai":
            require(url.startswith("file://") and sha == wheel["sha256"], "Candidate wheel is not the sealed one")
        else:
            host = re.match(r"https://([^/]+)/", url)
            require(host is not None and host.group(1) == "files.pythonhosted.org" and url.endswith(".whl"),
                    "Distribution outside the authorised wheel source: " + name)
        rows.append({"name": name, "version": version, "url": url, "sha256": sha})
    require(len(rows) == m["profiles"][profile]["locks"] + 1, "Resolved set is not the profile lock set")
    return sorted(rows, key=lambda r: r["name"].lower())


def validate_resolution(report, pip_report_path, m, profile):
    require(profile in PROFILES and report.get("profile") == profile and report.get("ok") is True,
            "Resolution proof failed or for another profile")
    require(report.get("installed") is False and report.get("model_calls") == 0, "A resolution installs nothing")
    require(report.get("wheel_sha256") == artifact(m, ".whl")["sha256"]
            and report.get("constraints_sha256") == m["profiles"][profile]["constraints_sha256"],
            "Resolution not bound to the sealed wheel/constraints")
    require(report.get("mismatches") == {} and report.get("unexpected_packages") == []
            and report.get("duplicates") == [] and report.get("ml_packages") == []
            and report.get("scope_ok") is True and report.get("wheel_ok") is True, "Resolution drift")
    require(report.get("expected_packages") == report.get("resolved_packages") == m["profiles"][profile]["locks"] + 1,
            "Resolution package count")
    require(digest(pip_report_path) == report.get("pip_report_sha256"), "pip report changed")
    return resolved_distributions(load_json(Path(pip_report_path)), m, profile)


# ---------- G7, G8, M1 ----------

def _clean_state(row, where):
    require(isinstance(row, dict) and row.get("label_absent") is True and row.get("port_free") is True,
            "Test label/port not recognised absent: " + where)


def _zero_write(row, where):
    require(row.get("ok") is True and row.get("returncode") == 0 and row.get("inventory_identical") is True
            and is_sha(row.get("inventory_before_sha256"))
            and row.get("inventory_before_sha256") == row.get("inventory_after_sha256")
            and row.get("inventory_hashes_content") is True and row.get("inventory_entries", 0) > 0,
            "Preview wrote, or its content-hash inventory is missing: " + where)


def _service(row, profile, m, where):
    marker = LEXICAL_MARKER if profile == "lexical" else SEMANTIC_MARKER
    require(row.get("present") is True and row.get("belongs_to_run") is True
            and row.get("plist_under_home") is True and row.get("program_is_run_venv") is True
            and row.get("listener_is_service_pid") is True and row.get("ready") is True
            and row.get("label") == TEST_LABEL and row.get("port") == TEST_PORT
            and row.get("version") == m["version"] and row.get("commit") == m["source_commit"]
            and row.get("retrieval_profile") == marker,
            "Test service not attested as this run's candidate, with the expected profile: " + where)
    if profile == "lexical":
        require(row.get("ml_libraries_open") == 0, "ML library opened by the lexical server")


def _doctor(row, where):
    """doctor: exit code 0, zero FAIL, and the expected inventory of checks actually present."""
    statuses = row.get("statuses", {})
    require(row.get("returncode") == 0 and row.get("fail") == 0 and row.get("checks", 0) >= len(REQUIRED_DOCTOR_AREAS),
            "doctor failed (exit code or FAIL): " + where)
    require(set(REQUIRED_DOCTOR_AREAS) <= set(row.get("areas", [])) and row.get("missing_areas") == []
            and set(statuses) == set(REQUIRED_DOCTOR_AREAS) and all(v in ("PASS", "WARN") for v in statuses.values())
            and statuses.get("client:codex") == statuses.get("client:cursor") == "PASS"
            and statuses.get("server") == statuses.get("ready") == statuses.get("store-profile") == "PASS",
            "doctor did not run its expected checks: " + where)


def validate_install(p, m, profile):
    require(p.get("format") == "qualify-410-install-v1" and p.get("ok") is True and p.get("profile") == profile,
            "Install proof failed or for another profile")
    require(p.get("source_commit") == m["source_commit"] and p.get("run_tag") == m["run_tag"]
            and p.get("version") == m["version"] and p.get("wheel", {}).get("sha256") == artifact(m, ".whl")["sha256"],
            "Install proof not bound to the candidate/run/wheel")
    require(p.get("label") == TEST_LABEL and p.get("port") == TEST_PORT
            and not str(p.get("label")).startswith(PRODUCTION_LABEL) and p.get("port") != PRODUCTION_PORT,
            "Test label/port")
    variant = p.get("variant")
    require(variant == "autonomous" or (profile == "semantic" and variant == "pth"), "Unknown install variant")
    env = p["environment"]
    require(env.get("constraints_sha256") == m["profiles"][profile]["constraints_sha256"]
            and env.get("locator_under_site_packages") is True and env.get("package_under_site_packages") is True
            and env.get("pip_check_ok") is True and env.get("mismatch") == [] and env.get("missing", []) == []
            and set(env.get("bootstrap", [])) <= {"pip", "setuptools", "wheel"}
            and env.get("pinned_and_equal") == m["profiles"][profile]["locks"], "Installed runtime differs from the locks")
    # No package outside the list in an autonomous environment. The .pth variant reuses a wider
    # environment: its other packages are listed, not refused (reduced coverage, decision 4).
    require(isinstance(env.get("unpinned"), list) and (variant == "pth" or env["unpinned"] == []),
            "Package outside the lock list")
    if profile == "lexical":
        require(env.get("ml_packages") == [], "ML package in the lexical environment")
    # Identity of the installed package: the sealed wheel, at the release version, in both variants.
    require(env.get("candidate_origin_sha256") == artifact(m, ".whl")["sha256"]
            and env.get("package_version") == m["version"], "Installed package is not the sealed candidate wheel")
    if variant == "autonomous":
        rows = env.get("distributions", [])
        # Wheels only; installed bytes tied to the recorded URL and SHA-256.
        require(env.get("wheels_only") is True and len(rows) == m["profiles"][profile]["locks"]
                and all(is_sha(r.get("sha256")) and str(r.get("url", "")).startswith("https://files.pythonhosted.org/")
                        and str(r["url"]).endswith(".whl") for r in rows), "Installed wheels not tied to URL/SHA-256")
    else:
        require(env.get("reused_paths") and all(r.get("path") and r.get("distributions") for r in env["reused_paths"]),
                ".pth variant must attest reused paths and versions")
    one, two = p["install_1"], p["install_2"]
    require(one.get("home") != two.get("home") and one.get("home") and two.get("home"), "Two disposable HOMEs required")
    for name, block in (("install_1", one), ("install_2", two)):
        _clean_state(block.get("pre"), name + " before")
        _clean_state(block.get("post"), name + " after")
        require(block["apply"].get("ok") is True and block["apply"].get("returncode") == 0
                and block["apply"].get("demo") is True and block["apply"].get("profile") == profile,
                "quickstart --apply --demo not ok: " + name)
        _service(block["service"], profile, m, name)
        _doctor(block["doctor"], name)
        # Exactly the two requested clients, as structured relay entries.
        require(sorted(block.get("client_entries_created", [])) == sorted(TEST_CLIENT_FILES),
                "The two requested client entries were not both created: " + name)
    # N6 is exercised once, in the first lexical installation; every other install downloads nothing.
    expected_download = CL100K_BYTES if profile == "lexical" else 0
    require(one["apply"].get("download_bytes") == expected_download and two["apply"].get("download_bytes") == 0,
            "Resource download bytes differ from N6")
    _zero_write(one["preview"], "quickstart preview")
    undo = one["undo"]
    require(undo.get("ok") is True and undo.get("returncode") == 0 and undo.get("with_service") is True
            and undo.get("label_absent") is True and undo.get("port_free") is True and undo.get("plist_removed") is True
            and undo.get("client_entries_restored") is True, "Rollback incomplete")
    _zero_write(two["uninstall_preview"], "uninstall preview")
    un = two["uninstall"]
    created = two.get("client_entries_created", [])
    # complete=false alone is not a failure; a kept service or a kept expected entry is.
    require(un.get("ok") is True and un.get("partial") is False and un.get("returncode") == 0
            and sorted(created) == sorted(TEST_CLIENT_FILES)
            and sorted(un.get("entries_removed", [])) == sorted(created)
            and un.get("label_absent") is True and un.get("port_free") is True
            and un.get("plist_removed_from_active") is True and un.get("plist_journal_copy_kept") is True
            and un.get("data_deleted") is False, "Uninstall kept the service or an expected entry")
    data = two["data_conservation"]
    counts = data.get("immutable_files", {})
    # Witnesses: an absent key or backup proves nothing about its preservation.
    require(data.get("immutable_witnessed") is True and set(counts) == {"token", "resources", "backups", "key"}
            and all(isinstance(v, int) and v >= 1 for v in counts.values()), "Immutable files not witnessed (empty kind)")
    require(data.get("immutable_unchanged") is True and set(data.get("immutable_kinds", [])) >=
            {"token", "resources", "backups", "key"} and data.get("store_identity_unchanged") is True
            and data.get("store_logical_unchanged") is True and data.get("checked_after_stop") is True
            and data.get("journals_present") is True and data.get("unexpected_changes") == [],
            "User data not preserved by uninstall")
    require(p.get("safety_net_fired") is False, "The safety net fired: a rollback failure is never a success")
    prod = p["production"]
    require(all(prod.get(key) is True for key in PRODUCTION_FLAGS), "Protected real files changed or not observed")
    require(prod.get("unchanged") is True and prod.get("pid_before") and prod.get("pid_before") == prod.get("pid_after")
            and is_sha(prod.get("snapshot_before_sha256"))
            and prod.get("snapshot_before_sha256") == prod.get("snapshot_after_sha256"), "Production state changed")
    return validate_m1(p["m1"], profile, variant)


PRODUCTION_FLAGS = ("production_plist_unchanged", "production_pid_unchanged", "real_launch_agents_unchanged",
                    "real_client_configs_unchanged", "real_memory_os_config_unchanged")


def validate_m1(m1, profile, variant):
    """M1 is measured and reported, never a threshold; it must be usable."""
    numbers = ("total_s", "pip_s", "controls_s", "net_s", "outside_model_s")
    require(all(isinstance(m1.get(k), (int, float)) and m1[k] >= 0 for k in numbers) and m1["total_s"] > 0,
            "M1 measure unusable")
    require(abs(m1["total_s"] - (m1["t1_epoch"] - m1["t0_epoch"])) < 1e-3
            and abs(m1["net_s"] - (m1["total_s"] - m1["controls_s"])) < 1e-3, "M1 gross/net totals inconsistent")
    require(isinstance(m1.get("install_json_timing"), dict) and m1["install_json_timing"].get("clock") == "monotonic"
            and m1.get("quickstart_download_bytes") == m1["install_json_timing"].get("download_bytes"),
            "M1 timing/download record incomplete")
    # pip's transferred bytes are NOT measured. A cache size is never presented as a download count.
    require("pip_download_bytes" not in m1 and isinstance(m1.get("pip_cache_bytes"), int)
            and m1.get("pip_cache_bytes_nature") and m1.get("pip_downloaded_bytes_measured") is False
            and m1.get("pip_downloaded_bytes") is None, "M1: pip cache size must be reported as such, downloads as unmeasured")
    deviations = set(m1.get("protocol_deviations", []))
    require({"non_blank_machine", "local_wheel"} <= deviations
            and (profile == "lexical" or "model_pre_placed" in deviations), "M1 protocol deviations not written")
    wanted = "reused-dependencies variant" if variant == "pth" else "first installation"
    require(m1.get("label") == wanted, "M1 label")
    return {k: m1[k] for k in (*numbers, "pip_downloaded_bytes", "pip_downloaded_bytes_measured", "pip_cache_bytes",
                               "pip_cache_bytes_nature", "quickstart_download_bytes", "label")}


# ---------- G6, G11 ----------

def validate_lexical_wheel(p, m, pins=None):
    require(p.get("format") == "qualify-410-lexical-wheel-v1" and p.get("ok") is True
            and p.get("source_commit") == m["source_commit"] and p.get("run_tag") == m["run_tag"]
            and p.get("wheel_sha256") == artifact(m, ".whl")["sha256"], "Lexical wheel proof binding")
    require(p.get("outside_checkout") is True and p.get("pythonpath_set") is False
            and p.get("package_under_site_packages") is True
            and p.get("find_spec") == {name: None for name in ML_MODULES}, "Lexical wheel not proven installed/ML-free")
    require(p.get("pytest_pins_sha256") == m["pytest_pins_sha256"] and p.get("pip_check_ok") is True
            and p.get("runtime_recheck_ok") is True, "pytest closure (N4) or runtime re-check")
    # N4: versions AND URL/SHA-256 of exactly the frozen closure.
    pins = exact_pins(read_text(Path(__file__).with_name(PYTEST_PINS))) if pins is None else pins
    rows = p.get("pytest_distributions")
    require(isinstance(rows, list) and {r.get("name"): r.get("version") for r in rows} == pins and len(rows) == len(pins)
            and all(is_sha(r.get("sha256")) and str(r.get("url", "")).startswith("https://files.pythonhosted.org/")
                    and str(r["url"]).endswith(".whl") for r in rows),
            "pytest closure not recorded with URL and SHA-256 (N4)")
    counts = p.get("counts", {})
    require(p.get("returncode") == 0 and counts.get("tests", 0) > 0
            and all(counts.get(k) == 0 for k in ("failures", "errors", "skipped")), "Lexical wheel tests")
    require(sorted(p.get("test_files", {})) == sorted(LEXICAL_WHEEL_FILES)
            and all(m["qualification_source_sha256"].get("v4/tests/" + n) == sha
                    for n, sha in p["test_files"].items()), "Lexical test files differ from the candidate")


LEXICAL_WHEEL_FILES = ("test_lexical_profile.py", "test_lexical_capabilities_410.py", "lexical_fixture_410.py",
                       "lexical_frozen_83ddead.py")


def validate_lexical_store(p, m):
    require(p.get("format") == "qualify-410-lexical-store-v1" and p.get("ok") is True
            and p.get("source_commit") == m["source_commit"] and p.get("run_tag") == m["run_tag"]
            and p.get("wheel_sha256") == artifact(m, ".whl")["sha256"], "Lexical store proof binding")
    require(p.get("scripts_sha256") == m["lexical_store_scripts_sha256"], "Lexical store tooling mismatch")
    require(p.get("package_under_site_packages") is True and p.get("fixed_data_sha256")
            and p.get("fixed_data_sha256") == m["lexical_store_fixed_data_sha256"], "Fixed data/queries differ")
    from sqlite_release_worker_410 import LEXICAL_FACTS, LEXICAL_QUERIES

    moments = p.get("rankings", {})
    require(set(moments) == {"after_write", "after_reopen", "after_restore"}
            and moments["after_write"] == moments["after_reopen"] == moments["after_restore"],
            "Rankings differ between the three moments")
    # Exactly the fixed queries, each returning fixed facts: one arbitrary query proves nothing.
    require(list(moments["after_write"]) == list(LEXICAL_QUERIES) and p.get("added") == len(LEXICAL_FACTS)
            and all(isinstance(hits, list) and hits and len(set(hits)) == len(hits) and set(hits) <= set(LEXICAL_FACTS)
                    for hits in moments["after_write"].values()),
            "Rankings are not those of the fixed queries over the fixed facts")
    require(p.get("marker") == LEXICAL_MARKER and p.get("restored_marker") == LEXICAL_MARKER
            and p.get("backup", {}).get("ok") is True and p.get("restore", {}).get("ok") is True
            and p["backup"].get("archive_sha256") and p["restore"].get("archive_sha256") == p["backup"]["archive_sha256"],
            "Marker (store and restored copy) / backup / restore")
    refusal = p.get("opposite_profile_open", {})
    require(refusal.get("refused") is True and refusal.get("code") == "store_profile_mismatch"
            and refusal.get("requested") == "semantic" and is_sha(refusal.get("inventory_before_sha256"))
            and refusal.get("inventory_before_sha256") == refusal.get("inventory_after_sha256"),
            "Semantic open of the lexical store not refused without mutation")


# ---------- G9, G10 ----------

def ordered_ids(search):
    return {q: row.get("ids") if isinstance(row, dict) else row for q, row in search.items()}


def validate_copy(p, m):
    from release_copy_worker import QUERIES
    from search_qualification import search_hit_counts

    require(p.get("ok") is True and p["version"] == m["version"] and p["baseline_version"] == "4.0.7"
            and p["source_commit"] == m["source_commit"] and p["artifacts"] == m["artifacts"]
            and p.get("run_tag") == m["run_tag"] and p.get("production_modified") is False
            and p.get("source_sql_writes") is False, "Copy identity/outcome")
    require(p["scripts_sha256"] == m["copy_scripts_sha256"], "Copy tooling mismatch")
    budget = p["disk_budget"]
    # §5: the copy starts only above max(9.2 GB, dynamic budget), F measured immediately before.
    require(budget["required_start_free_bytes"] == max(9_200_000_000, budget["dynamic_budget_bytes"])
            and budget["observed_start_free_bytes"] >= budget["required_start_free_bytes"]
            and budget["source_footprint_bytes"] > 0, "Copy start budget")
    rows = p["workers"]
    require(set(rows) == {"baseline", "wheel", "sdist", "wheel-warm", "sdist-warm"}, "Copy worker inventory")
    base = rows["baseline"]
    ident = p["expected_instance_id"]
    for name, r in rows.items():
        search_hit_counts(r["search"], QUERIES, worker=name, reported=r["search_hit_counts"])
        require(len(QUERIES) == 10 and r.get("ok") is True and r["total"] == base["total"]
                and r["store_instance_id"] == ident and ordered_ids(r["search"]) == ordered_ids(base["search"])
                and r["encoding_profile"] == base["encoding_profile"] and r["cache_misses"] == 0
                and r["corpus_encoded_texts"] == 0 and r["startup_seconds"] <= 120, "Worker mismatch: " + name)
        unchanged = r["schema_check"]
        require(r["version"] == ("4.0.7" if name == "baseline" else m["version"]), "Worker version mismatch")
        require(r["authority"] == base["authority"] and r["authority"]["schema_version"] == 8,
                "Authority differs from baseline")
        require(unchanged["ok"] is True and unchanged["from_schema"] == unchanged["to_schema"] == 8
                and unchanged["store_instance_id"] == ident and unchanged["tables_preserved"]
                and unchanged == base["schema_check"] and r["migration"] is None,
                "Schema8 exact inventory changed or migration attempted")
        require(r["journal"] == base["journal"] and r["journal"] is not None, "Journal history/generation changed")
        if name != "baseline":
            require(r.get("retrieval_profile") == "semantic", "Candidate worker profile not attested semantic")
    require(p["authority_identical"] is True and p["rankings_identical_between_artifacts_and_reopen"] is True
            and p["warm_encoded_texts"] == 0, "Copy equality flags missing")
    profile = base["encoding_profile"]
    require(profile["device"] == "mps" and profile["dtype"] == "native" and profile["batch_size"] == 32
            and profile["max_seq_length"] == 512 and profile["parameter_devices"] == ["mps"]
            and profile.get("model_revision") and profile.get("encoder_source_sha256") == EMBED_SHA256,
            "Production profile not attested")
    require(p["seconds"] <= 900 and p["search_identical_to_baseline"] is True, "Copy budget/rankings")
    doctor = p["doctor"]
    ready = doctor["ready"]
    caps = ready["capabilities"]
    require(doctor["ok"] is True and ready["version"] == m["version"] and ready["commit"] == m["source_commit"]
            and ready["identity_source"] == "embedded_descriptor" and ready["encoding_profile"] == profile
            and caps["store"]["backend"] == "sqlite_s1" and caps["store"]["instance_id"] == ident
            and caps["work_state"] is True and caps["retrieval_ready"] is True
            and caps["projects"]["registry_enabled"] is False and caps["policies"]["write_mode"] == "read_only"
            and caps["policies"]["bm25_profile"] == "scoped_literal" and caps["policies"]["lexical_guard_k"] == 3
            and caps["runtime"]["ok"] is True and caps["brief"]["tokenizer"]["ready"] is True
            and caps.get("retrieval_profile") == SEMANTIC_MARKER, "Private ready/doctor policy")
    audit = caps.get("readiness_audit", {})
    require(audit.get("max_reuse_seconds") == 1.0 and audit.get("live_checks_every_request") is True
            and audit.get("untracked_memory_fault_detection_seconds") == 1.0, "Readiness cache policy missing")
    # 4.1.0 addition: the lexical profile is refused on this semantic copy, with no write at all.
    refusal = p["lexical_open_refusal"]
    require(refusal.get("refused") is True and refusal.get("code") == "store_profile_mismatch"
            and refusal.get("requested") == "lexical" and is_sha(refusal.get("inventory_before_sha256"))
            and refusal.get("inventory_before_sha256") == refusal.get("inventory_after_sha256")
            and refusal.get("files_created") == [], "Lexical open of the semantic copy not refused without write")
    for name in ("wheel", "sdist"):
        s = p["s4"][name]
        _s4_drill(s["backup"], s["drill"], m, ident, name)
    # G10 "same archive, two artifacts": ONE sealed archive restored by the wheel AND by the sdist.
    common = p["s4_common"]
    sealed = p["s4"]["wheel"]["backup"]
    require(common.get("archive") == sealed["archive"] and common.get("archive_sha256") == sealed["archive_sha256"]
            and is_sha(common.get("archive_sha256")) and common.get("archive_sha256_after") == common["archive_sha256"]
            and set(common.get("drills", {})) == {"wheel", "sdist"}, "S4 common sealed archive missing")
    for name, drill in common["drills"].items():
        _s4_drill(sealed, drill, m, ident, "common archive by " + name)
        require(drill.get("artifact_sha256") == artifact(m, ".whl" if name == "wheel" else ".tar.gz")["sha256"],
                "Common archive drill not attributed to its artifact: " + name)


def _s4_drill(b, d, m, ident, where):
    service = d.get("server_check", {})
    require(b.get("ok") is True and b["qualified"] is True and b["source_sql_writes"] is False
            and b["store_instance_id"] == ident and b["verified"] is True and d.get("ok") is True
            and d.get("private_reopen_verified") is True and d.get("verified") is True
            and d.get("forget_reconciliation") == "complete" and d.get("restored_from_instance_id") == ident,
            "S4 backup/private reopening: " + where)
    require(d.get("level") == "service" and d.get("archive") == b.get("archive")
            and d.get("archive_sha256") == b.get("archive_sha256") and b.get("archive_sha256")
            and service.get("match") is True and service.get("version") == m["version"]
            and service.get("commit") == m["source_commit"] and service.get("corpus_reencodings") == 0
            and service.get("private_only") is True and service.get("production_modified") is False,
            "S4 must qualify this artifact/backup at service level: " + where)


# ---------- G13 ----------

PERIMETER_ROLES = ("p_a_proof", "intermediate", "candidate")
PERIMETER_PACKAGE = "v4/src/memory_os_v4/"
PERIMETER_CALLERS = (PERIMETER_PACKAGE + "server.py", PERIMETER_PACKAGE + "memory.py")
PERIMETER_DEPENDENCIES = ("pyproject.toml", "v4/pyproject.toml")


def perimeter_inventory(root, commit):
    """The perimeter recomputed from the Git objects of the three commits (never from a sealed input)."""
    from check_brief_perimeter_410 import report

    return report(Path(root), commit)


def _closure_delta(older, newer):
    a, b = older["files"], newer["files"]
    common = sorted(set(a) & set(b))
    return {"joined": sorted(set(b) - set(a)), "left": sorted(set(a) - set(b)),
            "changed_among_common": [p for p in common if a[p] != b[p]],
            "identical_among_common": [p for p in common if a[p] == b[p]]}


def validate_perimeter(p, m, recompute=None):
    """The report is an inventory. It never concludes a transfer on its own.

    Every cell is checked (a SHA-256, or an admissible historical absence), every comparison is
    re-derived, and the whole inventory must equal the one recomputed from Git at the three commits.
    """
    roles = set(PERIMETER_ROLES)
    require(p.get("format") == "qualify-410-brief-perimeter-v1" and p.get("git_only") is True
            and p.get("sealed_inputs_read") is False and p.get("brief_executed") is False, "Perimeter report format")
    commits = p.get("commits", {})
    require(commits == {"p_a_proof": P_A_PROOF_COMMIT, "intermediate": P_A_INTERMEDIATE, "candidate": m["source_commit"]},
            "Perimeter commits differ")
    require(p.get("automatic_transfer") is False and p.get("conclusion") == "decision_of_romain_required",
            "A perimeter report must never conclude a transfer")
    closures = p.get("closures")
    require(isinstance(closures, dict) and set(closures) == roles and isinstance(p.get("union"), dict) and p["union"],
            "Perimeter inventory incomplete")
    for role, row in closures.items():
        files = row.get("files")
        require(isinstance(files, dict) and files and row.get("count") == len(files)
                and all(isinstance(path, str) and path.startswith(PERIMETER_PACKAGE) and path.endswith(".py")
                        and is_sha(sha) for path, sha in files.items()), "Closure cells invalid: " + role)
        roots = row.get("roots")
        require(isinstance(roots, list) and roots and set(roots) <= set(files)
                and all(re.fullmatch(re.escape(PERIMETER_PACKAGE) + r"brief[^/]*\.py", r) for r in roots),
                "Closure roots invalid: " + role)
    # Union: every file of any closure, at the three commits; a file may be absent at a commit (historical
    # absence, sha null) only where it is NOT in that commit's closure.
    require(set(p["union"]) == {path for row in closures.values() for path in row["files"]},
            "Union is not the union of the three closures")
    for path, cells in p["union"].items():
        require(isinstance(cells, dict) and set(cells) == roles, "Union must give every file at the three commits")
        for role, cell in cells.items():
            inside = path in closures[role]["files"]
            require(isinstance(cell, dict) and set(cell) == {"sha256", "in_closure"} and cell["in_closure"] is inside
                    and (cell["sha256"] is None or is_sha(cell["sha256"]))
                    and (not inside or cell["sha256"] == closures[role]["files"][path]),
                    "Union cell invalid: %s at %s" % (path, role))
    for name, older, newer in (("p_a_proof_to_intermediate", "p_a_proof", "intermediate"),
                               ("intermediate_to_candidate", "intermediate", "candidate")):
        require(p.get(name) == _closure_delta(closures[older], closures[newer]), "Comparison not derived from the closures: " + name)
    # Callers, dependencies and resources, inventoried apart: valid cells and exact sets.
    for section, wanted in (("callers", set(PERIMETER_CALLERS)), ("dependencies", set(PERIMETER_DEPENDENCIES)),
                            ("resources", None)):
        rows = p.get(section)
        require(isinstance(rows, dict) and rows and (wanted is None or set(rows) == wanted),
                "Perimeter inventory incomplete: " + section)
        for path, row in rows.items():
            shas = row.get("sha256") if isinstance(row, dict) else None
            require(isinstance(shas, dict) and set(shas) == roles
                    and all(v is None or is_sha(v) for v in shas.values()) and any(shas.values()),
                    "Perimeter cell invalid: %s %s" % (section, path))
            require(row.get("changed_p_a_proof_to_intermediate") is (shas["p_a_proof"] != shas["intermediate"])
                    and row.get("changed_intermediate_to_candidate") is (shas["intermediate"] != shas["candidate"])
                    and set(row) == {"sha256", "changed_p_a_proof_to_intermediate", "changed_intermediate_to_candidate"},
                    "Perimeter change flags not derived from the cells: " + path)
            if section == "resources":
                require(path.startswith(PERIMETER_PACKAGE + "resources/"), "Not a packaged resource: " + path)
            else:
                require(is_sha(shas["candidate"]), "Caller/dependency absent at the candidate: " + path)
    require(any(path.endswith("/provision_v1.json") for path in p["resources"]) and p.get("limits"),
            "Perimeter resources/limits missing")
    require(p.get("tool_sha256") == m["qualification_tooling_sha256"]["check_brief_perimeter_410.py"],
            "Perimeter tool differs from the sealed one")
    # Exact sets and values: the same inventory, recomputed now from the Git objects.
    root = Path(__file__).resolve().parents[1]
    require(p == (recompute or perimeter_inventory)(root, m["source_commit"]),
            "Perimeter report differs from the inventory recomputed from Git objects")


# ---------- G12 ----------

def validate_external_p_b(p, m):
    """External P-B proof supplied by Romain, recorded as is; bound to the final SHA."""
    require(p.get("candidate_commit") == m["source_commit"] and p.get("complete") is True
            and p.get("production_modified") is False, "External P-B proof not bound to the candidate")
    require(is_sha(p.get("inputs_sha256")) and is_sha(p.get("scorer_sha256")) and is_sha(p.get("replay_sha256")),
            "External P-B proof must carry input, scorer and replay SHA-256")
    score = p.get("scorer_result", {})
    require(set(score.get("gates", {})) == set(P_B_GATES) and all(v is True for v in score["gates"].values()),
            "Six P-B results required")
    require(score.get("passed") == 40 and score.get("total") == 40 and score.get("verdict") is True
            and score.get("replay") is True and not score.get("collection_issues")
            and not score.get("failure_categories") and score.get("inputs_sha256_recomputed") == p["inputs_sha256"],
            "External P-B proof is not 40/40")


# ---------- G14 ----------

def validate_relay(proof, m):
    require(proof.get("ok") is True and proof.get("source_commit") == m["source_commit"]
            and proof.get("run_tag") == m["run_tag"], "Relay proof missing/failed/another source")
    require(proof.get("scripts_sha256") == m["installed_relay_scripts_sha256"], "Relay scripts differ")
    cases = proof.get("cases", [])
    # Exactly three cases: semantic wheel, semantic sdist, lexical wheel.
    require([(c.get("kind"), c.get("profile")) for c in cases] == list(RELAY_CASES), "Exactly three relay cases")
    for case in cases:
        art = artifact(m, ".whl" if case["kind"] == "wheel" else ".tar.gz")
        lexical = case["profile"] == "lexical"
        require(case.get("ok") is True and case.get("exit_code") == 0 and case.get("facts") == 2
                and case.get("version") == m["version"] and case.get("source_commit") == m["source_commit"]
                and case.get("artifact_sha256") == art["sha256"] and case.get("handshake_version") == m["version"]
                and all(case.get(check) is True for check in RELAY_CHECKS),
                "Installed case incomplete: %s/%s" % (case["kind"], case["profile"]))
        require(case.get("profile_attested") == (LEXICAL_MARKER if lexical else SEMANTIC_MARKER)
                and case.get("store_backend") == ("sqlite_s1" if lexical else case.get("store_backend")),
                "Profile not attested in the relay case")
        if lexical:
            require(case.get("embed_model_option_removed") is True
                    and case.get("find_spec") == {name: None for name in ML_MODULES},
                    "Lexical relay case must run SQLite, without encoder option, in the ML-free environment")
        brief = case.get("brief", {})
        require(brief.get("ok") is True and 0 < brief.get("bytes", 0) <= 6000 and 0 < brief.get("tokens", 0) <= 1500,
                "Relay brief/local status missing")


# ---------- decisions (§10) ----------

def check_decision(d, *, decision, commit, run_tag=None, chain_sha256=None, dossier_path=None, context=None):
    """One explicit yes/no of Romain, bound to the final SHA (and RUN_TAG). Absence is never consent.

    A network GO is checked against its dossier FILE (read and hashed here) and against the effective
    parameters of the acquisition (``context``): operation, sources, constraints, pins.
    """
    require(decision in DECISIONS, "Unknown decision")
    require(isinstance(d, dict) and d.get("format") == DECISION_FORMAT and d.get("decision") == decision,
            "Decision file format/id")
    require(d.get("decided_by") == "Romain" and d.get("answer") in ("oui", "non")
            and isinstance(d.get("statement"), str) and len(d["statement"].strip()) >= 40
            and isinstance(d.get("decided_at"), str) and d["decided_at"], "Decision must be an explicit, stated yes/no")
    require(d.get("source_commit") == commit and is_sha(commit, 40), "Decision bound to another candidate")
    if DECISIONS[decision][0]:
        require(run_tag is not None and d.get("run_tag") == run_tag, "Decision bound to another RUN_TAG")
    if d["answer"] == "non":
        return d
    if decision in ("g13_p_a", "g12_waiver"):
        gate = "G13" if decision == "g13_p_a" else "G12"
        states = ("waived_by_romain", "transfer_accepted") if gate == "G13" else ("waived_by_romain",)
        require(d.get("status") in states, gate + ": inadmissible status")
        require(isinstance(d.get("reason"), str) and len(d["reason"].strip()) >= 40
                and isinstance(d.get("scope"), str) and d["scope"].strip()
                and isinstance(d.get("evidence"), list)
                and all(is_sha(e.get("sha256")) and e.get("reference") for e in d["evidence"])
                and d.get("release_ready_effect") == "may_satisfy_" + gate, "Waiver must state scope, reason, evidence, effect")
        require(d.get("claims_result_on_candidate") is False,
                "A waiver never claims the P-A/P-B result on this candidate")
        if gate == "G13":
            require(d.get("replay") is False and d.get("result_attributed_to") == P_A_PROOF_COMMIT
                    and len(d["evidence"]) >= 1, "G13: no replay, 40/40 stays attributed to ceedb5c")
    if decision in ("g12_external_proof", "d1_resign"):
        key = "proof" if decision == "g12_external_proof" else "measure"
        require(isinstance(d.get(key), dict) and d[key].get("reference") and is_sha(d[key].get("sha256")),
                "Decision must identify its %s by reference and SHA-256" % key)
    if decision.startswith("network_"):
        acquisition = decision.split("_")[1]
        named = d.get("dossier", {})
        require(d.get("acquisition") == acquisition and isinstance(named, dict)
                and isinstance(named.get("reference"), str) and Path(named["reference"]).name == named["reference"]
                and is_sha(named.get("sha256")), "Network GO must name its dossier file by name and SHA-256")
        require(dossier_path is not None and context is not None, "Network GO cannot be checked without its dossier")
        path = refuse_forbidden(dossier_path)
        require(path.is_file() and digest(path) == named["sha256"], "Dossier absent or different from the approved SHA-256")
        expected = network_dossier(acquisition, commit=commit, run_tag=run_tag, context=context)
        require(load_json(path) == expected,
                "Dossier is not the exact description of what %s does in this run" % acquisition)
        # The GO itself restates sources and operation: exactly those of the dossier, nothing wider or narrower.
        require(isinstance(d.get("sources"), list) and sorted(d["sources"]) == expected["sources"]
                and d.get("operations") == [expected["operation"]] and d.get("acquisitions") == [acquisition],
                "Network GO sources/operations differ from the effective acquisition")
    if decision == "launch":
        require(chain_sha256 is not None and d.get("chain_sha256") == chain_sha256,
                "Launch GO bound to another chain script")
        require(d.get("authorises_publication") is False and d.get("authorises_push") is False
                and d.get("authorises_production_switch") is False, "A launch GO authorises nothing else")
    return d


def required_decisions(m, directory):
    """Administrative decisions G15 requires (§10), given what this run actually did."""
    wanted = ["launch", "launchd_nice", "d1_resign", "g13_p_a", "network_N1", "network_N2", "network_N3",
              "network_N4", "network_N6"]
    variant = evidence(m, directory, "install_semantic")["variant"]
    wanted.append("network_N5" if variant == "autonomous" else "semantic_pth_variant")
    wanted.append("g12_external_proof" if m["gates"]["G12"]["state"] == "pass" else "g12_waiver")
    return wanted


def decision(m, directory, name):
    row = m["decisions"][name]
    p = Path(row["path"])
    require(not p.is_absolute() and ".." not in p.parts, "Unsafe decision path")
    require(digest(directory / p) == row["sha256"], "Decision changed: " + name)
    return load_json((directory / p))


# ---------- M3, exit checks, final verification ----------

SHORT_COMMAND_MAX_S = 10.0  # above this, "no probe could start in time" is not a credible exception
M3_OBSERVATIONS = ("during", "overlap_only", "none_short_command")


def m3_summary(lines):
    """M3 (QUALIFY_4_1_0 §4): sampled extrema, never guaranteed peaks; durations on a monotonic clock.

    The bounds of every command and of every probe are recorded apart. Per step:
    - ``during``: a probe that started after the command started, with the command still running when the
      probe ended (overlap demonstrated at both ends);
    - ``overlap_only``: the command was running when the probe started and had ended before the probe
      finished; kept as an overlap observation with its bounds, flagged as a short-command exception;
    - ``none_short_command``: the probe never saw the executed command running (it had already finished,
      or the probe only ever saw the pre-exec relay); flagged, no sample is invented, and only admitted
      when ``command_seconds_upper_bound`` is at most SHORT_COMMAND_MAX_S (10 s).
    ``command_seconds_upper_bound`` runs from the start bound recorded before ``exec`` to the observation
    taken after the supervisor returned and the probe ended: an upper bound, not an exact duration.
    A probe finished before the command started, or limited to the relay before ``exec``, never counts.
    A failed probe (non-zero status, even without any error marker) or an impossible detection makes M3
    unusable: neither is ever a short-command exception.
    """
    rows = [json.loads(line) for line in lines if line.strip()]
    number = (int, float)
    require(rows and all(isinstance(r, dict) and isinstance(r.get("t"), number) and isinstance(r.get("mono"), number)
                         and isinstance(r.get("free_bytes"), int) and isinstance(r.get("swap_bytes"), int)
                         and r.get("phase") in ("before", "during", "missed", "after") for r in rows),
            "M3 samples unusable")
    steps = {}
    for r in rows:
        if r.get("step"):
            steps.setdefault(r["step"], {}).setdefault(r["phase"], []).append(r)
    durations, commands, observations, counts, exceptions = {}, {}, {}, {}, {}
    for name, phases in steps.items():
        require(len(phases.get("before", [])) == 1 and len(phases.get("after", [])) == 1,
                "M3: step without exactly one before/after sample: " + name)
        before, after = phases["before"][0], phases["after"][0]
        require(after["mono"] >= before["mono"], "M3: step bounds inverted: " + name)
        require(after.get("probe_rc") == 0 and isinstance(after.get("supervisor_rc"), int),
                "M3: the probe of this step failed or its status is unknown: " + name)
        cmd = after.get("command")
        require(isinstance(cmd, dict) and cmd.get("started") is True and isinstance(cmd.get("pid"), int)
                and isinstance(cmd.get("start_mono"), number) and before["mono"] <= cmd["start_mono"] <= after["mono"],
                "M3: command bounds missing: " + name)
        # Process identity established by the relay BEFORE exec (kernel start time, microseconds) and kept by
        # exec. A pid alone is not an identity: it can be reused once the command is reaped.
        identity = cmd.get("identity")
        require(isinstance(identity, list) and len(identity) == 2 and all(type(v) is int for v in identity)
                and identity[0] > 0 and 0 <= identity[1] < 1_000_000, "M3: command identity missing: " + name)

        def bound(r):  # a probe of THIS command, started after it started, finished before the step closed
            return (r.get("detection") == "ok" and r.get("identity_verified") is True
                    and r.get("command_identity") == identity and r.get("command_pid") == cmd["pid"]
                    and r.get("command_start_mono") == cmd["start_mono"]
                    and isinstance(r.get("probe_start_mono"), number) and isinstance(r.get("probe_end_mono"), number)
                    and cmd["start_mono"] <= r["probe_start_mono"] <= r["probe_end_mono"] <= after["mono"])

        # The start marker is written BEFORE exec: a probe that only saw the relay proves nothing about the
        # command. Only a probe that identified the executed command (post-exec) at its start is credited.
        probes = [r for r in phases.get("during", []) if bound(r) and r.get("alive_at_probe_start") is True
                  and r.get("post_exec_at_probe_start") is True]
        inside = [r for r in probes if r.get("alive_at_probe_end") is True]
        upper = after["mono"] - cmd["start_mono"]
        if inside:
            observations[name] = "during"
        elif probes:
            observations[name] = exceptions[name] = "overlap_only"
        else:
            missed = [r for r in phases.get("missed", []) if bound(r) and r.get("alive_at_probe_start") is False
                      and r.get("reason") == "command_ended_before_first_probe"]
            require(missed and upper <= SHORT_COMMAND_MAX_S,
                    "M3: no probe overlapped the command, and no admissible short-command exception: " + name)
            observations[name] = exceptions[name] = "none_short_command"
        durations[name], commands[name], counts[name] = after["mono"] - before["mono"], upper, len(inside)
    return {"samples": len(rows), "min_free_bytes_observed": min(r["free_bytes"] for r in rows),
            "max_swap_bytes_observed": max(r["swap_bytes"] for r in rows), "clock": "monotonic",
            "step_seconds": durations, "command_seconds_upper_bound": commands, "observation": observations,
            "during_samples": counts, "short_command_exceptions": exceptions,
            "total_seconds": max(r["mono"] for r in rows) - min(r["mono"] for r in rows),
            "nature": "sampled extrema, not guaranteed peaks"}


def validate_exit_checks(x, m):
    require(x.get("format") == "qualify-410-exit-checks-v1" and x.get("source_commit") == m["source_commit"]
            and x.get("run_tag") == m["run_tag"], "Exit checks for another candidate/run")
    require(x.get("chain_sha256") == m["external_tooling_sha256"]["chain"]["sha256"]
            and x.get("supervisor_sha256") == SUPERVISOR_SHA256, "Exit checks produced by other tooling")
    done = x.get("steps_ok", [])
    require([s for s in done if s not in OPTIONAL_STEPS] == [s for s in CHAIN_STEPS if s not in OPTIONAL_STEPS]
            and [s for s in CHAIN_STEPS if s in done] == done,
            "A chain step is missing, skipped or out of order")
    for key in ("test_label_absent", "test_port_free", "no_suspect_process", *PRODUCTION_FLAGS, "lock_409_absent"):
        require(x.get(key) is True, "Exit check failed: " + key)
    require(is_sha(x.get("production_before_sha256")) and x.get("production_before_sha256") == x.get("production_after_sha256"),
            "Protected perimeter fingerprint changed")
    summary = x.get("m3", {})
    require(is_sha(x.get("m3_samples_sha256")) and summary.get("samples", 0) > 0
            and isinstance(summary.get("min_free_bytes_observed"), int)
            and isinstance(summary.get("max_swap_bytes_observed"), int)
            and set(summary.get("step_seconds", {})) >= set(done)
            and summary.get("clock") == "monotonic"
            and all(summary.get("observation", {}).get(step) in M3_OBSERVATIONS for step in done)
            # Every short-command exception is flagged explicitly, in the proof and in the summary.
            and {step: kind for step, kind in summary["observation"].items() if kind != "during"}
            == summary.get("short_command_exceptions")
            and isinstance(x.get("m3_samples_path"), str), "M3 measures unusable")


CHAIN_RESULT_RE = r"PASS ([0-9a-f]{40}) ([0-9a-f]{64}) (v[0-9]+) [0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}\n"


def check_chain_result(text, m):
    """One complete line, nothing before or after: a truncated or extended marker is refused."""
    found = re.fullmatch(CHAIN_RESULT_RE, text)
    require(found is not None, "CHAIN_RESULT truncated, extended or malformed")
    require(" ".join(("PASS",) + found.groups()) == m["chain_result_expected"],
            "CHAIN_RESULT does not match this manifest")


REQUIRED_PIECES = {
    "lexical_wheel": ("lexical-wheel.xml", "lexical-wheel.log", "pytest-pip-report.json", "pytest-install.log",
                      "probe.log", "pip-check.log"),
    "lexical_store": ("plan.json", "lexical-store.json", "lexical-store.log", "semantic-refusal.json",
                      "semantic-refusal.log"),
    "copy": ("plan.json", "snapshot.json", "snapshot.log", "lexical-refusal.json", "lexical-refusal.log",
             *(name + ext for name in ("baseline", "wheel", "sdist", "wheel-warm", "sdist-warm") for ext in (".json", ".log"))),
}


S4_LOGS = ("s4-wheel/s4.log", "s4-sdist/s4.log", "s4-common-sdist/s4.log")


def required_pieces(name, report=None):
    """Pieces a report must seal, sub-folders included; some depend on the report itself."""
    report = report or {}
    if name.startswith("install_"):
        from qualify_install_410_helper import REQUIRED_EVIDENCE

        compared = ((report.get("install_2") or {}).get("data_conservation") or {}).get("compared_documents") or []
        return (*REQUIRED_EVIDENCE, *compared)  # the state documents used for the G8 comparison
    if name == "copy":
        archive = (report.get("s4_common") or {}).get("archive")
        return (*REQUIRED_PIECES[name], *S4_LOGS, *(["s4-wheel/backups/" + archive] if archive else []))
    if name == "lexical_store":
        archive = (report.get("backup") or {}).get("archive")
        return (*REQUIRED_PIECES[name], *(["backups/" + archive] if archive else []))
    return REQUIRED_PIECES[name]


def piece_bindings(name, report):
    """Sealed pieces whose SHA-256 must be the one the report states elsewhere (archives)."""
    if name == "copy":
        return {"s4-wheel/backups/" + report["s4_common"]["archive"]: report["s4_common"]["archive_sha256"]}
    if name == "lexical_store":
        return {"backups/" + report["backup"]["archive"]: report["backup"]["archive_sha256"]}
    if name.startswith("install_"):
        compared = report["install_2"]["data_conservation"].get("compared_documents") or []
        require(any("/state_before/" in rel for rel in compared) and any("/state_after/" in rel for rel in compared),
                "The state documents of the G8 comparison are not sealed")
    return {}


def check_pieces(name, report, origin):
    verify_pieces(report, origin, required_pieces(name, report))
    for rel, sha in piece_bindings(name, report).items():
        require(is_sha(sha) and report["evidence_sha256"].get(rel) == sha, "Sealed archive differs from the report: " + rel)


def verify_pieces(report, origin, required):
    """The detailed supporting files a report seals: all required ones listed, each still at its SHA-256."""
    inventory = report.get("evidence_sha256")
    require(isinstance(inventory, dict) and set(required) <= set(inventory), "Report does not seal its required pieces")
    origin = refuse_forbidden(origin)
    for rel, sha in inventory.items():
        part = Path(rel)
        require(not part.is_absolute() and ".." not in part.parts and is_sha(sha), "Unsafe piece in a report")
        path = refuse_forbidden(origin / part)
        require(path.is_file() and not path.is_symlink() and digest(path) == sha, "Sealed piece absent or changed: " + rel)


def manifest_context(m):
    """Effective acquisition parameters as sealed in the manifest (pins read from the hashed tooling file)."""
    pins = Path(__file__).with_name(PYTEST_PINS)
    require(digest(pins) == m["pytest_pins_sha256"], "Frozen pytest pins changed")
    return {"pins_sha256": m["pytest_pins_sha256"], "pins": exact_pins(read_text(pins)),
            "constraints": {name: m["profiles"][name]["constraints_sha256"] for name in PROFILES}}


def chain_stamp(m):
    return "%s %s %s" % (m["source_commit"], m["external_tooling_sha256"]["chain"]["sha256"], m["run_tag"])


def recorded_decision(m, directory, name):
    """A recorded decision, re-checked exactly as when it was accepted (dossier included)."""
    row = m["decisions"][name]
    dossier = None
    if row.get("dossier"):
        part = Path(row["dossier"]["path"])
        require(not part.is_absolute() and ".." not in part.parts, "Unsafe dossier path")
        dossier = directory / part
        require(digest(dossier) == row["dossier"]["sha256"], "Sealed dossier changed: " + name)
    return check_decision(decision(m, directory, name), decision=name, commit=m["source_commit"],
                          run_tag=m["run_tag"], chain_sha256=m["external_tooling_sha256"]["chain"]["sha256"],
                          dossier_path=dossier, context=manifest_context(m))


def validate_everything(m, directory):
    """Every proof, its sealed pieces and every decision, bound to the same commit and artifacts (G15)."""
    validate_local(m, directory)
    for profile in PROFILES:
        name = "resolution_" + profile
        validate_resolution(evidence(m, directory, name), directory / m["evidence"][name + "_pip_report"]["path"],
                            m, profile)
        require(digest(directory / m["evidence"][name + "_pip_report"]["path"])
                == m["evidence"][name + "_pip_report"]["sha256"], "pip report evidence changed")
        validate_install(evidence(m, directory, "install_" + profile), m, profile)
    validate_lexical_wheel(evidence(m, directory, "lexical_wheel"), m)
    validate_lexical_store(evidence(m, directory, "lexical_store"), m)
    validate_perimeter(evidence(m, directory, "perimeter"), m)
    validate_relay(evidence(m, directory, "relays"), m)
    # The detailed supporting files are replayed too, from where each harness left them.
    for name in ("install_lexical", "install_semantic", "lexical_wheel", "lexical_store", "copy"):
        check_pieces(name, evidence(m, directory, name), m["evidence"][name]["origin"])
    if m["gates"]["G12"]["state"] == "pass":
        validate_external_p_b(evidence(m, directory, "external_p_b"), m)
    for name in required_decisions(m, directory):
        require(name in m["decisions"], "Required decision of Romain missing: " + name)
        require(recorded_decision(m, directory, name)["answer"] == "oui", "Required decision answered no: " + name)
        require(not name.startswith("network_") or m["decisions"][name].get("dossier"), "Network GO without sealed dossier")
    d1 = decision(m, directory, "d1_resign")
    require(d1["measure"]["sha256"] == m["evidence"]["install_lexical"]["sha256"],
            "D1 re-signature is not on this run's lexical M1 measure")
    g13 = decision(m, directory, "g13_p_a")
    require(m["evidence"]["perimeter"]["sha256"] in {e["sha256"] for e in g13["evidence"]}
            and m["gates"]["G13"]["state"] == g13["status"] and m["gates"]["G13"]["decision"] == "g13_p_a",
            "G13 decision does not reference this perimeter report")
    if m["gates"]["G12"]["state"] == "pass":
        require(decision(m, directory, "g12_external_proof")["proof"]["sha256"]
                == m["evidence"]["external_p_b"]["sha256"], "G12 decision references another proof")
    else:
        require(m["gates"]["G12"]["state"] == "waived_by_romain" and m["gates"]["G12"]["decision"] == "g12_waiver",
                "G12 pending: neither a conforming external proof nor an accepted waiver")
    for gate in GATES[:-1]:
        state = m["gates"][gate]["state"]
        require(state in ADMISSIBLE[gate], f"{gate} is {state}: not admissible for release_ready")
    require(set(m.get("measures", {})) == {"M1", "M2", "M3", "M4"} and set(m["measures"]["M1"]) == set(PROFILES),
            "Measures M1-M4 not reported")


def validate_samples(m, directory, exit_checks):
    """The sealed M3 samples themselves: same bytes, and the same summary when recomputed."""
    row = m["evidence"]["m3_samples"]
    path = directory / row["path"]
    require(digest(path) == row["sha256"] == exit_checks["m3_samples_sha256"], "M3 samples changed")
    require(m3_summary(read_text(path).splitlines()) == exit_checks["m3"], "M3 summary differs from its samples")


def write(path, m):
    Path(path).write_text(json.dumps(m, indent=2) + "\n")


def store(m, directory, name, source, *, target=None):
    """Copy a proof beside the manifest, as is; one recording per name, earlier evidence kept."""
    target = directory / (target or (name.replace("_", "-") + "-proof.json"))
    require(name not in m["evidence"] and not target.exists(), "Proof already recorded: " + name)
    target.write_bytes(read_bytes(Path(source)))
    m["evidence"][name] = {"path": target.name, "sha256": digest(target),
                           "origin": str(Path(source).resolve().parent)}  # where its sealed pieces stay
    return target


RECORD_OPTIONS = ("record_source_proof", "record_copy_proof", "record_relay_proof", "record_resolution_proof",
                  "record_install_proof", "record_lexical_proof", "record_perimeter_report",
                  "record_external_proof", "record_decision", "finalize", "check_final")
UNSUPPORTED = ("record_full_proof", "record_gate_proof", "copy_review", "record_brief_source", "record_brief_exact")


def main(args):
    path = args.manifest
    directory = path.parent
    try:
        m = load_json(path)
        chosen = [name for name in RECORD_OPTIONS if getattr(args, name, None)]
        require(len(chosen) <= 1 and not any(getattr(args, name, None) for name in UNSUPPORTED),
                "Unsupported mixed proof")
        mode = chosen[0] if chosen else None
        if mode:
            refuse_forbidden(getattr(args, mode))  # every proof, decision or marker path, before any read
        if mode not in ("finalize", "check_final"):
            require(m.get("release_ready") is False, "Manifest already finalised: nothing can be recorded")
        if mode == "record_source_proof":
            store(m, directory, "source", args.record_source_proof)
            validate_local(m, directory, require_copy=False, require_source=True)
            set_gate(m, "G4", "pass", name="source")
        elif mode == "record_resolution_proof":
            source = Path(args.record_resolution_proof)
            report = load_json(source)
            profile = report.get("profile")
            validate_local(m, directory, require_copy=False, require_source=False)
            rows = validate_resolution(report, source.parent / "pip-report.json", m, profile)
            store(m, directory, "resolution_" + profile, source)
            store(m, directory, "resolution_%s_pip_report" % profile, source.parent / "pip-report.json",
                  target="resolution-%s-pip-report.json" % profile)
            m.setdefault("resolved_distributions", {})[profile] = rows
            if all("resolution_" + p in m["evidence"] for p in PROFILES):
                set_gate(m, "G5", "pass")
            m["gates"]["G5"]["evidence"].append("resolution_" + profile)
        elif mode == "record_install_proof":
            report = load_json(Path(args.record_install_proof))
            profile = report.get("profile")
            require(profile in PROFILES, "Install proof profile")
            validate_local(m, directory, require_copy=False, require_source=False)
            measure = validate_install(report, m, profile)
            check_pieces("install_" + profile, report, Path(args.record_install_proof).resolve().parent)
            store(m, directory, "install_" + profile, args.record_install_proof)
            m["measures"]["M1"][profile] = {**measure, "proof_sha256": m["evidence"]["install_" + profile]["sha256"]}
            for gate in ("G7", "G8"):
                m["gates"][gate]["evidence"].append("install_" + profile)
                if all("install_" + p in m["evidence"] for p in PROFILES):
                    set_gate(m, gate, "pass")
        elif mode == "record_lexical_proof":
            report = load_json(Path(args.record_lexical_proof))
            validate_local(m, directory, require_copy=False, require_source=False)
            kind = report.get("format")
            if kind == "qualify-410-lexical-wheel-v1":
                validate_lexical_wheel(report, m)
                check_pieces("lexical_wheel", report, Path(args.record_lexical_proof).resolve().parent)
                store(m, directory, "lexical_wheel", args.record_lexical_proof)
                set_gate(m, "G6", "pass", name="lexical_wheel")
            else:
                validate_lexical_store(report, m)
                check_pieces("lexical_store", report, Path(args.record_lexical_proof).resolve().parent)
                store(m, directory, "lexical_store", args.record_lexical_proof)
                set_gate(m, "G11", "pass", name="lexical_store")
        elif mode == "record_copy_proof":
            report = load_json(Path(args.record_copy_proof))
            validate_copy(report, m)
            check_pieces("copy", report, Path(args.record_copy_proof).resolve().parent)
            store(m, directory, "copy", args.record_copy_proof)
            validate_local(m, directory, require_copy=True, require_source=True)
            set_gate(m, "G9", "pass", name="copy")
            set_gate(m, "G10", "pass", name="copy")
        elif mode == "record_perimeter_report":
            validate_local(m, directory, require_copy=False, require_source=False)
            validate_perimeter(load_json(Path(args.record_perimeter_report)), m)
            store(m, directory, "perimeter", args.record_perimeter_report)
            # Recorded, never concluded: G13 stays pending until a reasoned decision of Romain.
            m["gates"]["G13"]["evidence"].append("perimeter")
        elif mode == "record_external_proof":
            source = refuse_forbidden(args.record_external_proof)
            validate_local(m, directory, require_copy=False, require_source=False)
            validate_external_p_b(load_json(source), m)
            store(m, directory, "external_p_b", source)
            set_gate(m, "G12", "pass", name="external_p_b")
        elif mode == "record_decision":
            source = refuse_forbidden(args.record_decision)
            d = load_json(source)
            name = d.get("decision")
            dossier = None
            if isinstance(name, str) and name.startswith("network_") and d.get("answer") == "oui":
                dossier = source.parent / str((d.get("dossier") or {}).get("reference"))
            check_decision(d, decision=name, commit=m["source_commit"], run_tag=m["run_tag"],
                           chain_sha256=m["external_tooling_sha256"]["chain"]["sha256"],
                           dossier_path=dossier, context=manifest_context(m))
            require(name not in m["decisions"], "Decision already recorded: " + name)
            target = directory / "decisions" / (name + ".json")
            require(not target.exists(), "Decision file exists")
            # Every refusal comes before the first write: a refused decision leaves no trace to trip over.
            if d["answer"] == "oui" and name == "g13_p_a":
                require("perimeter" in m["evidence"]
                        and m["evidence"]["perimeter"]["sha256"] in {e["sha256"] for e in d["evidence"]},
                        "G13 decision must reference the recorded perimeter report")
                set_gate(m, "G13", d["status"], decision=name)
            if d["answer"] == "oui" and name == "g12_waiver":
                # transfer_accepted does not exist for G12; a waiver never replaces a conforming proof.
                require(m["gates"]["G12"]["state"] == "pending", "G12 waiver only without a conforming proof")
                set_gate(m, "G12", "waived_by_romain", decision=name)
            target.parent.mkdir(mode=0o700, exist_ok=True)
            target.write_bytes(read_bytes(source))
            m["decisions"][name] = {"path": "decisions/" + target.name, "sha256": digest(target),
                                    "answer": d["answer"], "section": DECISIONS[name][1]}
            if dossier is not None:  # the approved dossier is sealed with the GO, and re-checked at G15
                sealed = directory / "decisions" / (name + ".dossier.json")
                sealed.write_bytes(read_bytes(dossier))
                m["decisions"][name]["dossier"] = {"path": "decisions/" + sealed.name, "sha256": digest(sealed)}
            if name == "m2_lexical":
                m["measures"]["M2"] = {"state": "non mesuré", "wished_by_romain": d["answer"] == "oui",
                                       "effect_on_release_ready": "none"}
        elif mode == "record_relay_proof":
            validate_local(m, directory)
            validate_relay(load_json(Path(args.record_relay_proof)), m)
            store(m, directory, "relays", args.record_relay_proof, target="installed-relay-proof.json")
            set_gate(m, "G14", "pass", name="relays")
        elif mode == "finalize":
            # G15: replayed last, never skipped; it alone raises release_ready, exit checks done.
            require(m.get("release_ready") is False and m["gates"]["G15"]["state"] == "pending",
                    "Final verification already ran")
            exit_checks = load_json(Path(args.finalize))
            validate_exit_checks(exit_checks, m)
            validate_everything(m, directory)
            samples = refuse_forbidden(exit_checks["m3_samples_path"])
            require(digest(samples) == exit_checks["m3_samples_sha256"]
                    and m3_summary(read_text(samples).splitlines()) == exit_checks["m3"],
                    "M3 samples do not match the exit checks")
            store(m, directory, "m3_samples", samples, target="m3-samples.jsonl")
            store(m, directory, "exit_checks", args.finalize)
            m["measures"]["M3"] = {**exit_checks["m3"], "samples_sha256": exit_checks["m3_samples_sha256"]}
            set_gate(m, "G15", "pass", name="exit_checks")
            m["release_ready"] = True
            m["chain_result_expected"] = "PASS " + chain_stamp(m)
            write(path, m)
            print("G15 passed: release_ready=true. Not a success without a matching CHAIN_RESULT=PASS. "
                  "No publication, push or production switch is authorised.")
            return 0
        elif mode == "check_final":
            # A manifest alone is not a success: CHAIN_RESULT=PASS must exist and match.
            require(m.get("release_ready") is True and m["gates"]["G15"]["state"] == "pass", "Not finalised")
            validate_everything(m, directory)
            validate_exit_checks(evidence(m, directory, "exit_checks"), m)
            validate_samples(m, directory, evidence(m, directory, "exit_checks"))
            result = Path(args.check_final)
            require(result.name == "CHAIN_RESULT" and result.is_file() and not result.is_symlink(),
                    "CHAIN_RESULT absent or invalidated")
            check_chain_result(read_text(result), m)
            print("Run qualified: manifest and CHAIN_RESULT=PASS agree. Publication, push and production "
                  "switch each need a separate GO from Romain.")
            return 0
        else:
            # Read-only consistency check: never raises the guard.
            validate_local(m, directory, require_copy="copy" in m["evidence"], require_source="source" in m["evidence"])
            print(json.dumps({"release_ready": m.get("release_ready"),
                              "gates": {g: row["state"] for g, row in m["gates"].items()}}))
            return 0
        m["release_ready"] = False  # no recording command ever raises the guard
        write(path, m)
        print("Recorded; release_ready stays false. No publication.")
        return 0
    except (OSError, KeyError, TypeError, ValueError, AssertionError, IndexError, StopIteration,
            SubprocessError) as exc:
        print("REFUS: " + type(exc).__name__ + ": " + str(exc))
        return 1


# ---------- chain-facing commands (run by the chain; none records anything in a manifest) ----------

def provision_paths(record, *, resources, cache_dir=None, table_path=None):
    """Every path ``provision.plan`` will stat, read or hash for these resources, parents first."""
    paths = []
    if "granite" in resources:
        require(cache_dir is not None, "An explicit Granite cache folder is required")
        cache = Path(cache_dir).expanduser().absolute()
        model = cache / ("models--" + record["model_id"].replace("/", "--"))
        snapshot = model / "snapshots" / record["revision"]
        paths += [cache, model, model / "refs", model / "refs" / "main", model / "snapshots", snapshot]
        for item in record["files"]:
            target = snapshot / item["path"]
            paths += [*reversed([parent for parent in target.parents if snapshot in parent.parents]), target]
    if "cl100k" in resources:
        require(table_path is not None, "An explicit cl100k table path is required")
        table = Path(table_path).expanduser().absolute()
        paths += [table.parent, table]
    return paths


def guarded_plan(provision, *, resources, cache_dir=None, table_path=None):
    """``provision.plan`` (engine, unchanged), called only AFTER the guard has seen everything it will read.

    The engine checks whether ``refs/main`` itself is a link, not whether a PARENT folder is one, and it
    hashes snapshot files wherever they lead. So each constructed path is checked here first, in its
    lexical and in its resolved form (a link carried by any parent, or by the file, is followed by the
    resolution, never by an open). Nothing is read before this loop completes.
    """
    for path in provision_paths(provision.catalog(), resources=resources, cache_dir=cache_dir, table_path=table_path):
        refuse_forbidden(path)
    kwargs = {"resources": tuple(resources)}
    if cache_dir is not None:
        kwargs["cache_dir"] = cache_dir
    if table_path is not None:
        kwargs["table_path"] = table_path
    return provision.plan(**kwargs)


def preconditions(a):
    """§9: checked by the chain before any step. Any gap is a STOP. Reads production, writes nothing there."""
    import importlib.util
    import sys
    import qualify_install_410_helper as helper
    from qualify_sqlite_release_410 import baseline_from_plist

    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "v4/src"))
    from memory_os_v4.ops import provision
    from memory_os_v4.ops.qualified_runtime import inspect_current

    report = {"format": "qualify-410-preconditions-v1", "ok": False, "source_commit": a.source_commit,
              "run_tag": a.run_tag, "checks": {}}
    checks = report["checks"]
    try:
        require(re.fullmatch(RUN_TAG_RE, a.run_tag) is not None, "Invalid RUN_TAG")
        checks["tree_clean"] = (_git(root, "rev-parse", "HEAD").strip() == a.source_commit
                                and not _git(root, "status", "--porcelain", "--untracked-files=no").strip())
        report["invariants"] = verify_invariants(root, a.source_commit)
        # RC_PY is never recreated in the chain: full inspect_current of the semantic profile.
        rc_py = Path(sys.executable)
        runtime = inspect_current(extras=("ops", "brief"), profile="semantic")
        checks["rc_py_runtime_ok"] = runtime["ok"] is True and Path(a.rc_py) == rc_py
        report["rc_py"] = {"path": str(rc_py), "inspect_current": runtime}
        tools = all(os.access(p, os.X_OK) for p in (a.build_python, a.ruff))
        probe = subprocess.run([a.build_python, "-B", "-c", "import build, setuptools, wheel"], capture_output=True)
        checks["build_tools_present"] = tools and probe.returncode == 0 and importlib.util.find_spec("twine") is not None
        # Offline stays on: a missing model is a STOP, never an implicit download.
        checks["hf_offline"] = os.environ.get("HF_HUB_OFFLINE") == "1" and os.environ.get("TRANSFORMERS_OFFLINE") == "1"
        hub = refuse_forbidden(os.environ.get("HF_HUB_CACHE")
                               or Path(os.environ.get("HF_HOME", Path.home() / ".cache/huggingface")) / "hub")
        # "Cache present, files non-empty" (reference, snapshot, blobs, tokenizer, weights): not a lone
        # config.json, and nothing more is claimed. Validity and loading are proven by G4's cached test only.
        report["minilm_cache"] = helper.hf_cache_files_present(hub / "models--sentence-transformers--all-MiniLM-L6-v2")
        checks["minilm_cache_present"] = report["minilm_cache"]["ok"] is True
        checks["granite_cache_conforms"] = guarded_plan(provision, resources=("granite",),
                                                        cache_dir=a.granite_hub)["ready"] is True
        checks["cl100k_conforms"] = guarded_plan(provision, resources=("cl100k",), table_path=a.cl100k)["ready"] is True
        baseline, _ = baseline_from_plist(a.production_plist, a.baseline_python)
        checks["baseline_interpreter_matches_plist"] = os.access(baseline, os.X_OK)
        report["existing_run_paths"] = [p for p in a.fresh if os.path.lexists(refuse_forbidden(p))]
        checks["run_tag_unused"] = bool(a.fresh) and not report["existing_run_paths"]
        checks["locks_absent"] = not os.path.lexists(a.lock_409)
        state = helper.service_state(TEST_LABEL, TEST_PORT)
        checks["test_label_absent"] = state["label_state"] == "absent"
        checks["test_port_free"] = state["port_state"] == "free"
        report["ok"] = all(v is True for v in checks.values()) and len(checks) == 12
    except (OSError, ValueError, KeyError, RuntimeError, SubprocessError) as exc:
        report["error"] = type(exc).__name__ + ": " + str(exc)
    with open(refuse_forbidden(a.output), "x") as stream:
        stream.write(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"ok": report["ok"], "checks": checks, "error": report.get("error")}))
    return 0 if report["ok"] else 1


def exit_checks(a):
    """Exit controls that condition release_ready: finished BEFORE the final verification raises it."""
    import qualify_install_410_helper as helper

    m = load_json(Path(a.manifest))
    samples = Path(a.samples)
    state = helper.service_state(TEST_LABEL, TEST_PORT)
    before = load_json(Path(a.production_before))
    now = helper.production_snapshot(Path.home())
    report = {
        "format": "qualify-410-exit-checks-v1", "source_commit": m["source_commit"], "run_tag": m["run_tag"],
        "chain_sha256": a.chain_sha256, "supervisor_sha256": digest(a.supervisor),
        "steps_ok": [s for s in a.steps.split(",") if s],
        "test_label_absent": state["label_state"] == "absent", "test_port_free": state["port_state"] == "free",
        "no_suspect_process": a.suspects.strip() == "",
        **helper.production_unchanged(before, now),
        "production_before_sha256": before["sha256"], "production_after_sha256": now["sha256"],
        "lock_409_absent": not os.path.lexists(a.lock_409),
        "m3": m3_summary(read_text(samples).splitlines()), "m3_samples_sha256": digest(samples),
        "m3_samples_path": str(samples.resolve()),
    }
    with open(refuse_forbidden(a.output), "x") as stream:
        stream.write(json.dumps(report, indent=2) + "\n")
    validate_exit_checks(report, m)
    print("Exit checks passed.")
    return 0


def cli(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("preconditions")
    for name in ("--source-commit", "--run-tag", "--rc-py", "--build-python", "--ruff", "--granite-hub", "--cl100k",
                 "--production-plist", "--baseline-python", "--lock-409", "--output"):
        s.add_argument(name, required=True)
    s.add_argument("--fresh", action="append", default=[], help="path bearing this RUN_TAG: must not exist")
    s = sub.add_parser("check-decision")
    for name in ("--file", "--decision", "--source-commit"):
        s.add_argument(name, required=True)
    s.add_argument("--run-tag")
    s.add_argument("--chain-sha256")
    s.add_argument("--expect-sha256", help="SHA-256 this decision file had when first accepted in this run")
    s.add_argument("--manifest", help="once recorded: the file must still be the sealed one")
    s = sub.add_parser("network-dossier", help="write the exact dossier a GO Nn may authorise (nothing is acquired)")
    for name in ("--acquisition", "--source-commit", "--run-tag", "--output"):
        s.add_argument(name, required=True)
    s = sub.add_parser("m3-summary")
    s.add_argument("--samples", required=True)
    s = sub.add_parser("exit-checks")
    for name in ("--manifest", "--samples", "--steps", "--chain-sha256", "--supervisor", "--production-before",
                 "--lock-409", "--output"):
        s.add_argument(name, required=True)
    s.add_argument("--suspects", default="")
    a = parser.parse_args(argv)
    try:
        if a.command == "preconditions":
            return preconditions(a)
        if a.command == "check-decision":
            # A GO must exist, be bound to this candidate/run, and say yes. Absence is never consent.
            root = Path(__file__).resolve().parents[1]
            source = refuse_forbidden(a.file)
            d = load_json(source)
            dossier = None
            if a.decision.startswith("network_") and d.get("answer") == "oui":
                dossier = source.parent / str((d.get("dossier") or {}).get("reference"))
            d = check_decision(d, decision=a.decision, commit=a.source_commit, run_tag=a.run_tag,
                               chain_sha256=a.chain_sha256, dossier_path=dossier, context=network_context(root))
            require(d["answer"] == "oui", "Decision answered no: " + a.decision)
            # The same sealed decision from the first check to G15: it may not change during the run.
            require(a.expect_sha256 in (None, digest(source)), "Decision file changed since it was accepted")
            if a.manifest:
                recorded = load_json(Path(a.manifest))["decisions"].get(a.decision)
                require(recorded is None or recorded["sha256"] == digest(source),
                        "Decision file differs from the one sealed in the manifest")
                require(recorded is None or dossier is None or recorded["dossier"]["sha256"] == digest(dossier),
                        "Dossier differs from the one sealed in the manifest")
            print("GO " + a.decision + " sha256=" + digest(source))
            return 0
        if a.command == "network-dossier":
            root = Path(__file__).resolve().parents[1]
            dossier = network_dossier(a.acquisition, commit=a.source_commit, run_tag=a.run_tag,
                                      context=network_context(root))
            with open(refuse_forbidden(a.output), "x") as stream:
                stream.write(json.dumps(dossier, indent=2, sort_keys=True) + "\n")
            print("dossier " + a.acquisition + " sha256=" + digest(a.output))
            return 0
        if a.command == "m3-summary":
            print(json.dumps(m3_summary(read_text(Path(a.samples)).splitlines()), indent=2))
            return 0
        return exit_checks(a)
    except (OSError, KeyError, TypeError, ValueError, AssertionError, SubprocessError) as exc:
        print("REFUS: " + type(exc).__name__ + ": " + str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(cli())

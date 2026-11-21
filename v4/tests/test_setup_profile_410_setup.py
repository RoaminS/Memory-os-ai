"""SETUP_4_1_0.md §2: the install profile is explicit, never guessed, never switched.

Lexical = ``[ops,brief]`` + lexical constraints, no encoder variable, no Granite download.
Semantic = today's service definition, byte for byte.
"""
from __future__ import annotations

import json
import plistlib

import pytest

from memory_os_v4.ops import quickstart as qs
from setup_fixture_410 import install, prepare, setup_context, tree

ENCODER_ENV = {"MEMORY_V4_EMBED_MODEL", "MEMORY_V4_EMBED_MAX_SEQ_LENGTH", "MEMORY_V4_DEVICE",
               "MEMORY_V4_EMBED_DTYPE", "MEMORY_V4_EMBED_BATCH_SIZE", "HF_HUB_CACHE"}
SEMANTIC_ENV_KEYS = {
    "MEMORY_V4_DIR", "MEMORY_V4_TOKEN_FILE", "MEMORY_V4_URL", "MEMORY_V4_SERVICE_LABEL",
    "MEMORY_V4_STORE_BACKEND", "MEMORY_V4_WORK_STATE", "MEMORY_V4_EMBED_MODEL",
    "MEMORY_V4_EMBED_MAX_SEQ_LENGTH", "MEMORY_V4_MAINTENANCE_MODE", "MEMORY_V4_DEVICE",
    "MEMORY_V4_EMBED_DTYPE", "MEMORY_V4_EMBED_BATCH_SIZE", "MEMORY_V4_BM25_PROFILE",
    "MEMORY_V4_LEXICAL_GUARD_K", "TOKENIZERS_PARALLELISM", "MEMORY_V4_OPS_DIR",
    "HF_HUB_CACHE", "MEMORY_V4_CL100K_PATH", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"}


@pytest.fixture
def c(tmp_path, monkeypatch):
    return setup_context(tmp_path, monkeypatch)


# ---- selection ----

def test_new_install_without_a_profile_is_refused_not_defaulted(c):
    before = tree(c.root)
    plan = qs.prepare(c.settings, home=c.home, python=c.python, clients=("cursor",), run=c.system)
    assert plan["ok"] is False and plan["blocker_codes"] == ["setup_profile_required"]
    assert plan["profile"] is None
    assert tree(c.root) == before


def test_unknown_profile_is_refused(c):
    with pytest.raises(qs.QuickstartError) as refusal:
        prepare(c, profile="hybrid")
    assert refusal.value.code == "setup_profile_unknown"


def test_lexical_with_the_jsonl_backend_is_refused(c):
    plan = prepare(c, profile="lexical", backend="jsonl")
    assert plan["ok"] is False and "setup_profile_backend_conflict" in plan["blocker_codes"]


@pytest.mark.parametrize("profile", ["lexical", "semantic"])
def test_explicit_profile_is_recorded_in_the_plan(c, profile):
    plan = prepare(c, profile=profile)
    assert plan["ok"] is True and plan["profile"] == profile and plan["blocker_codes"] == []


def test_the_profile_is_never_chosen_from_installed_packages(c, monkeypatch):
    """Semantic requested without the ML closure: refusal naming the fix, no lexical fallback."""
    from memory_os_v4.ops import qualified_runtime
    monkeypatch.setattr(qualified_runtime, "inspect_current", lambda **kw: {
        "ok": False, "state": "missing_dependency", "profile_id": "granite-macos-arm64-py311-v1",
        "mismatches": {"torch": {"expected": "2.10.0", "actual": None, "state": "missing"}}})
    plan = prepare(c, profile="semantic")
    assert plan["ok"] is False and plan["profile"] == "semantic"
    assert "runtime_unqualified" in plan["blocker_codes"]
    text = " ".join(plan["blockers"])
    assert "memory-os-ai[ops,brief,semantic]" in text and "qualified_runtime_constraints_v1.txt" in text
    assert plan["service"]["env"].get("MEMORY_V4_RETRIEVAL_PROFILE") != "lexical"


@pytest.mark.parametrize("profile", ["lexical", "semantic"])
def test_runtime_is_inspected_with_the_catalog_of_the_requested_profile(c, profile):
    prepare(c, profile=profile)
    assert c.seen.runtime_calls == [{"profile": profile, "extras": ("ops", "brief")}]


def test_lexical_drift_names_the_lexical_constraints(c, monkeypatch):
    from memory_os_v4.ops import qualified_runtime
    monkeypatch.setattr(qualified_runtime, "inspect_current", lambda **kw: {
        "ok": False, "state": "drift", "profile_id": "lexical-macos-arm64-py311-v1",
        "mismatches": {"numpy": {"expected": "2.4.2", "actual": "2.0.0", "state": "drift"}}})
    plan = prepare(c, profile="lexical")
    text = " ".join(plan["blockers"])
    assert "runtime_unqualified" in plan["blocker_codes"]
    assert "qualified_runtime_constraints_lexical_v1.txt" in text and "[semantic]" not in text


# ---- resources ----

def test_lexical_plans_the_tokenizer_table_only(c):
    plan = prepare(c, profile="lexical")
    assert c.seen.plan_calls == [{"resources": ("cl100k",)}]
    assert plan["provision"]["resources"] == ["cl100k"]
    assert "HF_HUB_CACHE" not in plan["provision"]["env"]


def test_semantic_still_plans_granite_and_the_table(c):
    plan = prepare(c, profile="semantic")
    assert [tuple(call.get("resources", ("granite", "cl100k"))) for call in c.seen.plan_calls] == [("granite", "cl100k")]
    assert plan["provision"]["resources"] == ["granite", "cl100k"]


def test_real_plan_for_the_table_alone_never_mentions_granite(tmp_path, monkeypatch):
    """Green today: ``provision.plan`` already knows how to plan the table without the model."""
    from memory_os_v4.ops import provision
    before = tree(tmp_path)
    plan = provision.plan(resources=("cl100k",), cache_dir=tmp_path / "hub", table_path=tmp_path / "t/cl100k")
    assert plan["resources"] == ["cl100k"] and [f["resource"] for f in plan["files"]] == ["cl100k"]
    assert plan["download_bytes"] == provision.catalog()["cl100k"]["bytes"] < 2_000_000
    assert "HF_HUB_CACHE" not in plan["env"] and "huggingface.co" not in json.dumps(plan)
    assert tree(tmp_path) == before


# ---- service definition ----

def test_lexical_service_selects_the_profile_and_carries_no_encoder_setting(c):
    plan = prepare(c, profile="lexical")
    env = plan["service"]["env"]
    assert env["MEMORY_V4_RETRIEVAL_PROFILE"] == "lexical"
    assert env["MEMORY_V4_STORE_BACKEND"] == "sqlite_s1" and env["MEMORY_V4_WORK_STATE"] == "true"
    assert not ENCODER_ENV & set(env)
    assert env["MEMORY_V4_BM25_PROFILE"] == "scoped_literal" and env["MEMORY_V4_LEXICAL_GUARD_K"] == "3"
    assert plistlib.loads(plan["service"]["plist"].encode())["EnvironmentVariables"] == env


def test_lexical_service_environment_opens_a_valid_lexical_config(c, monkeypatch):
    """The plist environment alone must satisfy the engine's own lexical validation."""
    from memory_os_v4.config import Config
    plan = prepare(c, profile="lexical")
    for name in list(__import__("os").environ):
        if name.startswith("MEMORY_V4_"):
            monkeypatch.delenv(name)
    for name, value in plan["service"]["env"].items():
        monkeypatch.setenv(name, value)
    config = Config.from_env()
    config.validate_retrieval_profile()
    assert config.retrieval_profile == "lexical" and config.use_dense is False


# Frozen from cd83511 (ops/quickstart.py identical to the 4.0.9 base 83ddead): literal values,
# not the output of the function under test.
FROZEN_HOME = "/nonexistent-home-410"
FROZEN_PYTHON = "/opt/memory-os/venv/bin/python"
FROZEN_RESOURCE_ENV = {"HF_HUB_CACHE": "/c/hub", "MEMORY_V4_CL100K_PATH": "/c/cl100k_base.tiktoken",
                       "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
FROZEN_SEMANTIC_PLIST_SHA256 = "bea151cf0886a3feef30aef3934c386ab663e2ab2fa348e33f43e7356afb2066"
FROZEN_SEMANTIC_PLIST = {
    "Label": "test.memory-os",
    "ProgramArguments": [FROZEN_PYTHON, "-m", "memory_os_v4.server", "--transport", "http", "--host", "127.0.0.1",
                         "--port", "43219", "--warm"],
    "EnvironmentVariables": {
        "MEMORY_V4_DIR": FROZEN_HOME + "/.local/share/memory-os/store",
        "MEMORY_V4_TOKEN_FILE": FROZEN_HOME + "/.config/memory-os/http.token",
        "MEMORY_V4_URL": "http://127.0.0.1:43219/mcp/",
        "MEMORY_V4_SERVICE_LABEL": "test.memory-os",
        "MEMORY_V4_STORE_BACKEND": "sqlite_s1",
        "MEMORY_V4_WORK_STATE": "true",
        "MEMORY_V4_EMBED_MODEL": "ibm-granite/granite-embedding-97m-multilingual-r2",
        "MEMORY_V4_EMBED_MAX_SEQ_LENGTH": "512",
        "MEMORY_V4_MAINTENANCE_MODE": "disabled",
        "MEMORY_V4_DEVICE": "auto",
        "MEMORY_V4_EMBED_DTYPE": "native",
        "MEMORY_V4_EMBED_BATCH_SIZE": "32",
        "MEMORY_V4_BM25_PROFILE": "scoped_literal",
        "MEMORY_V4_LEXICAL_GUARD_K": "3",
        "TOKENIZERS_PARALLELISM": "false",
        "MEMORY_V4_OPS_DIR": FROZEN_HOME + "/.local/state/memory-os/ops",
        **FROZEN_RESOURCE_ENV,
    },
    "KeepAlive": True,
    "RunAtLoad": True,
    "StandardErrorPath": FROZEN_HOME + "/.local/state/memory-os/ops/logs/server.err",
}


def frozen_settings():
    from pathlib import Path
    from memory_os_v4.ops.settings import OpsSettings
    home = Path(FROZEN_HOME)
    return home, OpsSettings("http://127.0.0.1:43219/mcp/", home / ".config/memory-os/http.token",
                             home / ".local/share/memory-os/store", home / "memory-os-backups",
                             home / ".config/memory-os/backup.key", home / ".local/state/memory-os/ops",
                             "test.memory-os")


def test_semantic_service_definition_equals_the_frozen_4_0_9_reference():
    """Green today, and must stay: keys, values, order and bytes of the historical plist."""
    import hashlib
    home, settings = frozen_settings()
    service = qs._service_plan(settings, home, FROZEN_PYTHON, dict(FROZEN_RESOURCE_ENV))
    document = plistlib.loads(service["plist"].encode())
    assert document == FROZEN_SEMANTIC_PLIST
    assert list(document["EnvironmentVariables"]) == list(FROZEN_SEMANTIC_PLIST["EnvironmentVariables"])
    assert hashlib.sha256(service["plist"].encode()).hexdigest() == FROZEN_SEMANTIC_PLIST_SHA256
    assert set(service["env"]) == SEMANTIC_ENV_KEYS and "MEMORY_V4_RETRIEVAL_PROFILE" not in service["env"]


def test_explicit_semantic_profile_yields_the_same_frozen_bytes():
    import hashlib
    home, settings = frozen_settings()
    service = qs._service_plan(settings, home, FROZEN_PYTHON, dict(FROZEN_RESOURCE_ENV), profile="semantic")
    assert hashlib.sha256(service["plist"].encode()).hexdigest() == FROZEN_SEMANTIC_PLIST_SHA256


def test_lexical_service_differs_from_the_frozen_reference_only_by_the_documented_variables():
    home, settings = frozen_settings()
    service = qs._service_plan(settings, home, FROZEN_PYTHON, {"MEMORY_V4_CL100K_PATH": "/c/cl100k_base.tiktoken",
                                                                 "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"},
                               profile="lexical")
    frozen = FROZEN_SEMANTIC_PLIST["EnvironmentVariables"]
    expected = {k: v for k, v in frozen.items() if k not in ENCODER_ENV}
    expected["MEMORY_V4_RETRIEVAL_PROFILE"] = "lexical"
    assert service["env"] == expected
    document = plistlib.loads(service["plist"].encode())
    assert {k: v for k, v in document.items() if k != "EnvironmentVariables"} == {
        k: v for k, v in FROZEN_SEMANTIC_PLIST.items() if k != "EnvironmentVariables"}


def test_semantic_plan_service_equals_the_legacy_definition(c):
    plan = prepare(c, profile="semantic")
    legacy = qs._service_plan(c.settings, c.home, c.python, plan["provision"]["env"])
    assert plan["service"]["plist"] == legacy["plist"]


# ---- existing installation: the profile is read, never converted ----

def owned_plist(c, profile):
    plan = prepare(c, profile=profile)
    path = c.home / "Library/LaunchAgents/test.memory-os.plist"
    path.parent.mkdir(parents=True)
    path.write_text(plan["service"]["plist"])
    c.settings.token_file.parent.mkdir(parents=True, exist_ok=True)
    c.settings.token_file.write_text("t" * 43 + "\n")
    c.settings.token_file.chmod(0o600)
    c.system.loaded = True
    return plan


@pytest.mark.parametrize("profile", ["lexical", "semantic"])
def test_existing_owned_service_gives_its_profile_when_none_is_requested(c, profile):
    owned_plist(c, profile)
    plan = qs.prepare(c.settings, home=c.home, python=c.python, clients=("cursor",), run=c.system)
    assert plan["ok"] is True and plan["profile"] == profile and plan["service"]["state"] == "owned"


@pytest.mark.parametrize("installed,requested", [("lexical", "semantic"), ("semantic", "lexical")])
def test_requesting_another_profile_than_the_installed_one_is_refused(c, installed, requested):
    owned_plist(c, installed)
    before = tree(c.root)
    plan = prepare(c, profile=requested)
    assert plan["ok"] is False and "setup_profile_conflict" in plan["blocker_codes"]
    assert tree(c.root) == before


# ---- readiness of the started service ----

def ready_payload(c, profile):
    from memory_os_v4.config import LEXICAL_MARKER, LEXICAL_POLICY
    from memory_os_v4.ops import provision
    from memory_os_v4.ops.qualified_runtime import catalog
    lexical = profile == "lexical"
    encoding = ({"state": "absent_by_profile", "model": None, "profile": None, "loaded": False} if lexical else
                {"requested_device": "auto", "model": provision.catalog()["model_id"],
                 "profile": {"dtype": "native", "batch_size": 32, "max_seq_length": 512,
                             "model_revision": provision.catalog()["revision"],
                             "runtime": catalog()["versions"]}})
    return {"version": c.identity["version"], "commit": c.identity["commit"], "ready": True, "reason": "ready",
            "capabilities": {
                "retrieval_profile": dict(LEXICAL_MARKER) if lexical else {"name": "semantic", "policy": None},
                "store": {"backend": "sqlite_s1", "instance_id": "synthetic", "sqlite_version": "3.53.4"},
                "work_state": True,
                "encoding": encoding,
                "policies": {"bm25_profile": "scoped_literal", "lexical_guard_k": 3,
                             "ranking_policy": LEXICAL_POLICY if lexical else None},
                "brief": {"tokenizer": {"ready": True}}}}


def real_ready(monkeypatch, payload):
    """The module's own ``_ready`` (the fixture replaced it), fed with one HTTP response."""
    import importlib.util
    from pathlib import Path
    from memory_os_v4.ops import auth
    spec = importlib.util.spec_from_file_location("memory_os_v4.ops._qs_ready_410", Path(qs.__file__))
    original = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(original)
    monkeypatch.setattr(auth, "http_get", lambda *a, **kw: (200, json.dumps(payload).encode()))
    return original


def test_lexical_ready_needs_no_encoder_runtime_nor_granite_revision(c, monkeypatch):
    payload = ready_payload(c, "lexical")
    original = real_ready(monkeypatch, payload)
    assert original._ready(c.settings, c.identity, profile="lexical") == payload


@pytest.mark.parametrize("profile", ["lexical", "semantic"])
@pytest.mark.parametrize("version,code", [("3.51.2", "sqlite_runtime_too_old"), ("3.9.0", "sqlite_runtime_too_old"),
                                          (None, "sqlite_runtime_unattested"), ("garbage", "sqlite_runtime_unattested")])
def test_ready_requires_the_running_server_to_attest_a_fit_sqlite(c, monkeypatch, profile, version, code):
    payload = ready_payload(c, profile)
    if version is None:
        del payload["capabilities"]["store"]["sqlite_version"]
    else:
        payload["capabilities"]["store"]["sqlite_version"] = version
    original = real_ready(monkeypatch, payload)
    with pytest.raises(original.QuickstartError) as refusal:
        original._ready(c.settings, c.identity, profile=profile)
    assert refusal.value.code == code


def test_engine_capabilities_publish_the_sqlite_version_of_the_server_process(tmp_path):
    """Real lexical engine (no ML): the deployed facts name the library the server loaded."""
    import sqlite3
    from memory_os_v4 import Config, MemoryV4, capabilities
    with MemoryV4(config=Config.lexical(data_dir=str(tmp_path / "store"))) as engine:
        facts = capabilities.deployed(engine, {})
    assert facts["store"]["sqlite_version"] == sqlite3.sqlite_version


@pytest.mark.parametrize("damage", ["marker-missing", "marker-policy", "encoder-present", "tokenizer",
                                    "bm25", "guard", "ranking-policy", "semantic-service"])
def test_lexical_ready_refuses_a_service_that_is_not_exactly_lexical(c, monkeypatch, damage):
    payload = ready_payload(c, "lexical")
    caps = payload["capabilities"]
    if damage == "marker-missing":
        del caps["retrieval_profile"]
    elif damage == "marker-policy":
        caps["retrieval_profile"]["policy"] = "lex-9.9.9"
    elif damage == "encoder-present":
        caps["encoding"] = {"model": "x", "profile": {}, "loaded": True}
    elif damage == "tokenizer":
        caps["brief"]["tokenizer"]["ready"] = False
    elif damage == "bm25":
        caps["policies"]["bm25_profile"] = "legacy"
    elif damage == "guard":
        caps["policies"]["lexical_guard_k"] = 0
    elif damage == "ranking-policy":
        caps["policies"]["ranking_policy"] = None
    else:
        payload = ready_payload(c, "semantic")
    original = real_ready(monkeypatch, payload)
    with pytest.raises(original.QuickstartError) as refusal:
        original._ready(c.settings, c.identity, profile="lexical")
    assert refusal.value.code == "service_profile_mismatch"


def test_semantic_ready_still_accepts_the_4_0_9_response(c, monkeypatch):
    """Green today: the semantic readiness contract is not weakened."""
    payload = ready_payload(c, "semantic")
    original = real_ready(monkeypatch, payload)
    assert original._ready(c.settings, c.identity) == payload


def test_semantic_ready_refuses_a_lexical_service(c, monkeypatch):
    """Green today (refused as an encoder mismatch); the code becomes stable with the contract."""
    original = real_ready(monkeypatch, ready_payload(c, "lexical"))
    with pytest.raises(original.QuickstartError):
        original._ready(c.settings, c.identity)


# ---- end to end through the fakes ----

def test_lexical_install_downloads_no_model_and_journals_its_profile(c):
    result = install(c, profile="lexical")
    assert result["ok"] is True and result["profile"] == "lexical"
    (preview, consent), = c.seen.apply_calls
    assert preview["resources"] == ["cl100k"] and consent == {"download_approved": False}
    assert c.seen.ready_calls and all(call.get("profile") == "lexical" for call in c.seen.ready_calls)
    plist = plistlib.loads((c.home / "Library/LaunchAgents/test.memory-os.plist").read_bytes())
    assert plist["EnvironmentVariables"]["MEMORY_V4_RETRIEVAL_PROFILE"] == "lexical"
    assert not ENCODER_ENV & set(plist["EnvironmentVariables"])
    assert result["ops_env"]["MEMORY_V4_RETRIEVAL_PROFILE"] == "lexical"


def test_install_journal_carries_the_timing_needed_by_clause_d1(c, monkeypatch):
    """Injected clock: every duration is the one the fake step declared, none can be invented."""
    from memory_os_v4.ops import sqlite_runtime

    def wal_probe(directory, **kwargs):
        c.clock.advance(10)
        return {"ok": True, "code": None, "journal_mode": "wal"}
    monkeypatch.setattr(sqlite_runtime, "wal_probe", wal_probe)
    result = install(c, profile="lexical", clients=("claude-code",), which=lambda name: "/usr/local/bin/claude")
    timing = result["timing"]
    assert set(timing) == {"profile", "download_bytes", "steps_ms", "total_s", "clock"}
    assert timing["profile"] == "lexical" and timing["download_bytes"] == 0 and timing["clock"] == "monotonic"
    assert timing["steps_ms"] == pytest.approx({"sqlite": 10, "resources": 2000, "token": 0, "service": 0,
                                                "service_ready": 3000, "clients": 40, "checks": 1200})
    assert timing["total_s"] == pytest.approx(6.25)


# ---- command line ----

def run_cli(monkeypatch, capsys, argv):
    from memory_os_v4.ops import cli
    seen = {}

    def fake_prepare(settings, **kwargs):
        seen.update(kwargs)
        return {"ok": True, "blockers": [], "blocker_codes": [], "profile": kwargs.get("profile")}
    monkeypatch.setattr(qs, "prepare", fake_prepare)
    code = cli.main(argv)
    return code, seen, capsys.readouterr().out


@pytest.mark.parametrize("profile", ["lexical", "semantic"])
def test_cli_passes_the_explicit_profile(monkeypatch, capsys, profile):
    code, seen, out = run_cli(monkeypatch, capsys, ["quickstart", "--profile", profile, "--clients", "cursor"])
    assert code == 0 and seen["profile"] == profile and json.loads(out)["profile"] == profile


def test_cli_without_profile_passes_none_and_never_a_default(monkeypatch, capsys):
    code, seen, _ = run_cli(monkeypatch, capsys, ["quickstart", "--clients", "cursor"])
    assert "profile" in seen and seen["profile"] is None


def test_cli_rejects_an_unknown_profile_at_parsing(monkeypatch, capsys):
    from memory_os_v4.ops import cli
    with pytest.raises(SystemExit) as stop:
        cli.main(["quickstart", "--profile", "auto"])
    assert stop.value.code == 2


def test_cli_refusal_carries_the_stable_code(monkeypatch, capsys):
    from memory_os_v4.ops import cli

    def refuse(settings, **kwargs):
        raise qs.QuickstartError("Unknown profile", code="setup_profile_unknown")
    monkeypatch.setattr(qs, "prepare", refuse)
    assert cli.main(["quickstart", "--profile", "lexical"]) == 1
    assert json.loads(capsys.readouterr().out)["code"] == "setup_profile_unknown"


# ---- doctor ----

def deployed(profile, *, runtime_actual=None):
    from memory_os_v4 import __version__
    from memory_os_v4.config import LEXICAL_MARKER
    from memory_os_v4.ops.qualified_runtime import expected_versions
    lexical = profile == "lexical"
    actual = runtime_actual or expected_versions(profile=profile, extras=("ops", "brief"))
    encoding = ({"state": "absent_by_profile", "model": None, "profile": None, "loaded": False} if lexical
                else {"model": "m", "requested_device": "auto",
                      "profile": {"device": "mps", "runtime": {
                          key: actual.get(key) for key in ("torch", "sentence-transformers", "transformers", "numpy")}}})
    return {"version": __version__, "commit": "a" * 40,
            "encoding_profile": encoding["profile"],
            "capabilities": {"schema_version": 1,
                             "retrieval_profile": dict(LEXICAL_MARKER) if lexical else {"name": "semantic", "policy": None},
                             "engine": {"version": __version__, "commit": "a" * 40},
                             "store": {"backend": "sqlite_s1", "instance_id": "i", "sqlite_version": "3.53.4"},
                             "encoding": encoding, "projects": {"registry_sha256": None},
                             "runtime": {"actual": actual},
                             "brief": {"compact": True, "tokenizer": {"ready": True}}, "policies": {}}}


def test_doctor_accepts_a_lexical_deployment(monkeypatch):
    from memory_os_v4.ops import doctor
    for name in ("SERVER_VERSION", "BACKEND", "DEVICE", "REGISTRY_SHA256"):
        monkeypatch.delenv("MEMORY_V4_EXPECT_" + name, raising=False)
    by = {check.area: check for check in doctor._capabilities(deployed("lexical"))}
    assert by["capabilities"].status == "PASS"
    assert by["encoder-runtime"].status == "PASS" and "absent by profile" in by["encoder-runtime"].detail
    assert by["server-runtime"].status == "PASS" and "lexical" in by["server-runtime"].detail
    assert by["store-profile"].status == "PASS" and "lex-4.1.0-v1" in by["store-profile"].detail
    assert not any(check.status == "FAIL" for check in by.values())


@pytest.mark.parametrize("state,word", [("missing", "missing dependency"), ("drift", "drift")])
def test_doctor_distinguishes_a_missing_encoder_dependency_from_a_drift(monkeypatch, state, word):
    from memory_os_v4.ops import doctor
    from memory_os_v4.ops.qualified_runtime import expected_versions
    actual = dict(expected_versions(extras=("ops", "brief")))
    actual["torch"] = None if state == "missing" else "0.0.1"
    by = {check.area: check for check in doctor._capabilities(deployed("semantic", runtime_actual=actual))}
    assert by["encoder-runtime"].status == "FAIL" and word in by["encoder-runtime"].detail
    if state == "missing":
        assert "memory-os-ai[ops,brief,semantic]" in by["encoder-runtime"].hint


def test_doctor_flags_a_malformed_profile_marker():
    from memory_os_v4.ops import doctor
    info = deployed("lexical")
    info["capabilities"]["retrieval_profile"] = {"name": "lexical", "policy": "lex-0"}
    by = {check.area: check for check in doctor._capabilities(info)}
    assert by["store-profile"].status == "FAIL"


@pytest.mark.parametrize("info,environ,expected", [
    ({"capabilities": {"retrieval_profile": {"name": "lexical", "policy": "lex-4.1.0-v1"}}}, {}, ("lexical", "deployed")),
    ({"capabilities": {"retrieval_profile": {"name": "semantic", "policy": None}}},
     {"MEMORY_V4_RETRIEVAL_PROFILE": "lexical"}, ("semantic", "deployed")),
    (None, {"MEMORY_V4_RETRIEVAL_PROFILE": "lexical"}, ("lexical", "environment")),
    (None, {}, ("semantic", "engine-default")),
    ({"capabilities": {}}, {}, ("semantic", "engine-default")),
])
def test_doctor_inspects_its_own_runtime_with_the_installation_profile(info, environ, expected):
    from memory_os_v4.ops import doctor
    assert doctor.installation_profile(info, environ) == expected


@pytest.mark.parametrize("version,status,word", [
    ("3.53.4", "PASS", "running server"), ("3.51.3", "PASS", "running server"),
    ("3.51.2", "FAIL", "sqlite_runtime_too_old"), (None, "WARN", "not attested by the running server"),
])
def test_doctor_separates_the_server_sqlite_from_the_interpreter_of_the_command(version, status, word):
    from memory_os_v4.ops import doctor
    info = deployed("lexical")
    if version is None:
        del info["capabilities"]["store"]["sqlite_version"]
    else:
        info["capabilities"]["store"]["sqlite_version"] = version
    by = {check.area: check for check in doctor._capabilities(info)}
    check = by["server-sqlite-runtime"]
    assert check.status == status and word in check.detail
    assert "this interpreter" not in check.detail

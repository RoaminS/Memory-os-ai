import hashlib
import json
import time

import pytest

from memory_os_v4 import Config, MemoryV4, __version__
from memory_os_v4.capabilities import readiness_snapshot
from memory_os_v4.ops import doctor
from memory_os_v4.ops.connection_proof import artifact, inspect_proofs
from memory_os_v4.ops.settings import OpsSettings


@pytest.mark.parametrize("backend", ["jsonl", "sqlite_s1"])
def test_deployed_backend_and_registry_without_loading_model(tmp_path, backend):
    registry = tmp_path / "registry.json"
    registry.write_text('{"schema_version":1,"projects":[]}')
    registry.chmod(0o600)
    with MemoryV4(
        config=Config(
            data_dir=str(tmp_path / "store"),
            embed_model="hash",
            store_backend=backend,
            project_registry=str(registry),
        )
    ) as e:
        build = {"version": __version__, "commit": "a" * 40}
        ok, reason, caps = readiness_snapshot(e, build)
        assert ok and reason == "ready"
        assert caps["store"]["backend"] == backend and caps["store"]["atomic_mutations"] == (
            backend == "sqlite_s1"
        )
        assert caps["projects"]["registry_sha256"] == hashlib.sha256(registry.read_bytes()).hexdigest()
        assert caps["encoding"]["profile"]["device"] == "hash"
        assert caps["client_relays"] == "unknown_to_server"


@pytest.mark.parametrize(
    "field,value",
    [("SERVER_VERSION", "9.9.9"), ("BACKEND", "sqlite_s1"), ("DEVICE", "mps"), ("REGISTRY_SHA256", "other")],
)
def test_doctor_explicit_mismatch(tmp_path, monkeypatch, field, value):
    with MemoryV4(config=Config(data_dir=str(tmp_path / "store"), embed_model="hash")) as e:
        build = {"version": __version__, "commit": "a" * 40}
        _, _, caps = readiness_snapshot(e, build)
        info = {**build, "capabilities": caps, "encoding_profile": caps["encoding"]["profile"]}
        assert doctor._capabilities(info)[0].status == "PASS"
        monkeypatch.setenv("MEMORY_V4_EXPECT_" + field, value)
        assert doctor._capabilities(info)[0].status == "FAIL"


def test_missing_connection_is_unknown_and_fresh_artifact_required(tmp_path, monkeypatch):
    s = OpsSettings(
        url="http://127.0.0.1:9/mcp/",
        token_file=tmp_path / "token",
        store_dir=tmp_path / "store",
        backup_dir=tmp_path / "backups",
        backup_key=tmp_path / "key",
        ops_dir=tmp_path / "ops",
        service_label=None,
    )
    monkeypatch.delenv("MEMORY_V4_CLIENT_PROOFS", raising=False)
    assert all(r["status"] == "WARN" for r in inspect_proofs(s))
    p = tmp_path / "proofs.json"
    monkeypatch.setenv("MEMORY_V4_CLIENT_PROOFS", str(p))
    record = {
        "client_id": "codex",
        "endpoint_sha256": hashlib.sha256(s.url.encode()).hexdigest(),
        "observed_at": time.time(),
        "successful_control": True,
        "relay": artifact(),
        "server": {"version": __version__, "commit": "a" * 40, "store_instance_id": "test"},
    }
    p.write_text(json.dumps({"schema_version": 1, "connections": [record]}))
    assert {r["area"]: r["status"] for r in inspect_proofs(s, record["server"])}["connection:codex"] == "PASS"
    record["observed_at"] -= 301
    p.write_text(json.dumps({"schema_version": 1, "connections": [record]}))
    assert all(r["status"] == "WARN" for r in inspect_proofs(s))

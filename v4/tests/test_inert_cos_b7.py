"""Inert legacy variables never license mutations or suppress health review."""
import json
from pathlib import Path
import plistlib

import pytest

from memory_os_v4 import Config, MemoryV4
from memory_os_v4.config import DEPRECATED_COS_ENV
from memory_os_v4.fact_updates import mutation_policy
from memory_os_v4.ops import health, quickstart
from memory_os_v4.ops.settings import OpsSettings


@pytest.mark.parametrize("value", ["0", "0.8", "1.01", "invalid", "nan"])
def test_inert_env_accepted_with_notice_and_cannot_change_config_or_mutations(tmp_path, monkeypatch, caplog, value):
    for name in DEPRECATED_COS_ENV:
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("MEMORY_V4_DIR", str(tmp_path))
    monkeypatch.setenv("MEMORY_V4_EMBED_MODEL", "hash")
    cfg = Config.from_env()
    assert cfg.dedup_cos == Config().dedup_cos and cfg.supersede_cos == Config().supersede_cos
    assert "ignored" in caplog.text
    with MemoryV4(config=cfg) as engine:
        engine.add_facts([{"text": "Same opaque subject has value A", "project": "P", "item_id": "a", "entities": ["subject"]},
                          {"text": "Same opaque subject has value B", "project": "P", "item_id": "b", "entities": ["subject"]}])
        assert engine.store.get("a").invalidated_by is None
        assert engine.store.get("b").invalidated_by is None


def settings(root):
    return OpsSettings("http://127.0.0.1:8999/mcp/", root / "token", root / "store", root / "backups",
                       root / "key", root / "ops", "private.test")


@pytest.mark.parametrize("cos", ["0", "1.01", "invalid"])
def test_health_first_observation_and_repeats_never_acknowledge_supersessions(tmp_path, monkeypatch, cos):
    s = settings(tmp_path)
    s.store_dir.mkdir()
    (s.store_dir / "items.jsonl").write_text(json.dumps({"id": "old", "kind": "fact", "text": "old",
        "valid_until": 1, "invalidated_by": "new"}) + "\n")
    monkeypatch.setenv("MEMORY_V4_SUPERSEDE_COS", cos)
    monkeypatch.setattr(health, "http_get", lambda url, settings, **kw: (200, json.dumps({"capabilities": {
        "policies": {"mutations": mutation_policy()}}}).encode()))
    for _ in range(2):
        report = health.report(s)
        assert report["superseded_new"] == ["old"]
        assert report["mutation_policy"] == mutation_policy()
        assert any("unacknowledged" in msg for msg in report["anomalies"])
        assert not any("while confined" in msg for msg in report["anomalies"])
        assert json.loads((s.ops_dir / "known_supersessions.json").read_text())["ids"] == []
    assert (s.ops_dir / "known_supersessions.json").stat().st_mode & 0o777 == 0o600


def test_new_quickstart_plist_has_real_profile_and_no_cos(tmp_path):
    s = settings(tmp_path)
    plan = quickstart._service_plan(s, tmp_path, str(Path("/usr/bin/python3")), {}, backend="sqlite")
    env = plistlib.loads(plan["plist"].encode())["EnvironmentVariables"]
    assert not set(DEPRECATED_COS_ENV) & env.keys()
    assert env["MEMORY_V4_MAINTENANCE_MODE"] == "disabled"
    assert env["MEMORY_V4_LEXICAL_GUARD_K"] == "3"

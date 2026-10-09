"""Tests for ops.clients (client registry/classification/snippets), ops.doctor and setup's automatic token."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("cryptography")

from memory_os_v4.ops import auth, cli, clients, doctor  # noqa: E402
from memory_os_v4.ops.settings import OpsSettings  # noqa: E402

RELAY = {"command": "/py", "args": ["-m", "memory_os_v4.ops.relay"]}


@pytest.fixture()
def settings(tmp_path: Path) -> OpsSettings:
    store = tmp_path / "store"
    store.mkdir()
    (store / "items.jsonl").write_text(json.dumps({"id": "a", "kind": "fact", "text": "A", "project": "p"}) + "\n")
    return OpsSettings(url="http://127.0.0.1:9/mcp/", token_file=tmp_path / "cfg/http.token", store_dir=store,
                       backup_dir=tmp_path / "backups", backup_key=tmp_path / "cfg/backup.key",
                       ops_dir=tmp_path / "ops", service_label=None)


@pytest.mark.parametrize("entry,kind", [
    (None, "absent"),
    (RELAY, "relay"),
    ({"command": "/py", "args": ["/x/stdio_relay.py"]}, "relay"),
    ({"command": "memory-os-relay"}, "relay"),
    ({"type": "http", "url": "http://127.0.0.1:8765/mcp/"}, "direct-http"),
    ({"command": "/py", "args": ["-m", "memory_os_ai.server"]}, "legacy-v3"),
    ({"command": "something-else"}, "other"),
])
def test_classify(entry, kind):
    assert clients.classify(entry) == kind


def _write(home: Path, client_key: str, content: str) -> None:
    client = next(c for c in clients.CLIENTS if c.key == client_key)
    path = client.config_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_read_entry_for_every_format(tmp_path):
    _write(tmp_path, "claude-code", json.dumps({"mcpServers": {"memory-os-ai": RELAY}}))
    _write(tmp_path, "vscode", json.dumps({"servers": {"memory-os-ai": {"type": "http", "url": "http://x"}}}))
    _write(tmp_path, "codex", '[mcp_servers.memory-os-ai]\ncommand = "/py"\nargs = ["-m", "memory_os_ai.server"]\n')
    _write(tmp_path, "grok", '[mcp_servers.memory-os-ai]\ncommand = "/py"\nargs = ["/x/stdio_relay.py"]\n')
    _write(tmp_path, "antigravity", "")
    _write(tmp_path, "cursor", "{broken")
    got = {c.key: (lambda st, e: (st, clients.classify(e)))(*clients.read_entry(c, tmp_path)) for c in clients.CLIENTS}
    assert got["claude-code"] == ("ok", "relay")
    assert got["vscode"] == ("ok", "direct-http")
    assert got["codex"] == ("ok", "legacy-v3")
    assert got["grok"] == ("ok", "relay")
    assert got["antigravity"] == ("ok", "absent")
    assert got["cursor"][0] == "unreadable"
    assert got["windsurf"][0] == "missing-config"


def test_snippets_point_to_relay_and_never_contain_a_secret():
    env = {"MEMORY_V4_URL": "http://127.0.0.1:8765/mcp/", "MEMORY_V4_TOKEN_FILE": "/home/x/.config/memory-os/http.token"}
    for client in clients.CLIENTS:
        text = clients.snippet(client, "/py", env)
        assert "memory_os_v4.ops.relay" in text and "Bearer" not in text
        if client.fmt in ("mcpServers", "vscode"):
            entry = json.loads(text)["servers" if client.fmt == "vscode" else "mcpServers"]["memory-os-ai"]
            assert clients.classify(entry) == "relay"


def test_doctor_reports_failures_and_client_states(settings, tmp_path):
    _write(tmp_path, "claude-code", json.dumps({"mcpServers": {"memory-os-ai": RELAY}}))
    _write(tmp_path, "vscode", json.dumps({"servers": {"memory-os-ai": {"command": "/py", "args": ["-m", "memory_os_ai.server"]}}}))
    checks = doctor.run(settings, home=tmp_path)
    by = {c.area: c for c in checks}
    assert by["token"].status == "FAIL" and "token create" in by["token"].hint
    assert by["server"].status == "FAIL"  # nothing listens on port 9
    assert by["store"].status == "PASS" and "1 live items" in by["store"].detail
    assert by["client:claude-code"].status == "PASS"
    assert by["client:vscode"].status == "WARN" and "legacy-v3" in by["client:vscode"].detail
    assert by["client:windsurf"].status == "PASS"  # not installed is fine
    text = doctor.render(checks)
    assert "fail" in text and "→" in text


def test_doctor_flags_invalid_store(settings, tmp_path):
    (settings.store_dir / "items.jsonl").write_text('{"id":"a","kind":"unknown","text":"t","project":"p"}\n')
    assert {c.area: c for c in doctor.run(settings, home=tmp_path)}["store"].status == "FAIL"


def test_doctor_never_prints_the_token(settings, tmp_path, monkeypatch, capsys):
    auth.create_token(settings.token_file)
    monkeypatch.setattr(OpsSettings, "from_env", classmethod(lambda cls: settings))
    monkeypatch.setattr(doctor.Path, "home", classmethod(lambda cls: tmp_path))
    cli.main(["doctor"])
    cli.main(["doctor", "--json"])
    out = capsys.readouterr().out
    assert auth.read_token(settings.token_file) not in out
    assert isinstance(json.loads(out[out.index("["):]), list)


def test_setup_creates_missing_token_and_lists_every_client(settings, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(OpsSettings, "from_env", classmethod(lambda cls: settings))
    assert not settings.token_file.exists()
    assert cli.main(["setup"]) == 0
    out = capsys.readouterr().out
    assert settings.token_file.exists() and "token: created" in out
    assert auth.read_token(settings.token_file) not in out
    for client in clients.CLIENTS:
        assert client.name in out
    assert cli.main(["setup"]) == 0 and "exists and is valid" in capsys.readouterr().out

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
def settings(tmp_path: Path, monkeypatch) -> OpsSettings:
    monkeypatch.setenv("MEMORY_V4_RELAY_JOURNAL_DIR", str(tmp_path / "ops/relay"))
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
    assert by["client:windsurf"].status == "WARN"  # config absence does not prove installation
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


@pytest.mark.parametrize("content", ['[]', 'null', '{"mcpServers": []}', '{"mcpServers": {"memory-os-ai": 1}}', '\xff'])
def test_malformed_client_config_never_crashes(tmp_path, content):
    _write(tmp_path, "cursor", content)
    client = next(c for c in clients.CLIENTS if c.key == "cursor")
    assert clients.read_entry(client, tmp_path)[0] == "unreadable"
    assert doctor._clients(tmp_path)


def test_read_errors_and_non_list_args_are_reported(tmp_path, monkeypatch):
    client = clients.CLIENTS[0]
    monkeypatch.setattr(Path, "read_text", lambda *a, **kw: (_ for _ in ()).throw(PermissionError()))
    assert clients.read_entry(client, tmp_path) == ("unreadable", None)
    assert clients.classify({"args": 1}) == "other"


@pytest.mark.parametrize("body,expected", [
    ({"ok": True, "ready": True, "version": "4.0.2", "commit": "a" * 40,
      "commit_source": "deployment_manifest", "source_dirty": None}, "PASS"),
    ({}, "FAIL"), ([], "FAIL"), ({"ready": False}, "FAIL"),
    ({"ok": True, "ready": True, "version": "4.0.2", "commit": "a" * 40,
      "commit_source": "checkout", "source_dirty": True}, "FAIL"),
])
def test_ready_requires_valid_identity(settings, monkeypatch, body, expected):
    monkeypatch.setattr(doctor, "http_get", lambda *a: (200, json.dumps(body).encode()))
    monkeypatch.setattr(doctor, "_get_without_token", lambda *a: 401)
    assert {c.area: c for c in doctor._server(settings)}["ready"].status == expected


def test_grok_toml_propagates_non_secret_settings():
    import tomllib
    client = next(c for c in clients.CLIENTS if c.key == "grok")
    env = {"MEMORY_V4_URL": "http://127.0.0.1:9999/mcp/", "MEMORY_V4_TOKEN_FILE": "/tmp/custom.token"}
    entry = tomllib.loads(clients.snippet(client, "/py", env))["mcp_servers"][clients.SERVER]
    assert entry["env"] == {"MEMORY_V4_CLIENT_ID": "grok", **env}
    assert clients.classify(entry) == "relay"


def test_doctor_checks_sqlite_receipts_without_engine_or_reencoding(settings):
    from memory_os_v4 import Config, MemoryV4
    (settings.store_dir / "items.jsonl").unlink()
    with MemoryV4(config=Config(data_dir=str(settings.store_dir), store_backend="sqlite_s1", embed_model="hash")) as engine:
        engine.add_facts([{"text": "A synthetic fact", "item_id": "a"}], project="P")
    check = doctor._store(settings)
    assert check.status == "PASS" and "SQLite" in check.detail and "receipts" in check.detail


@pytest.mark.parametrize("key,root", [("zed", "context_servers"), ("devin", "mcpServers")])
def test_new_clients_roundtrip_and_doctor_no_config_writes(tmp_path, key, root):
    client = next(c for c in clients.CLIENTS if c.key == key)
    value = clients.snippet(client, "/py", {"MEMORY_V4_TOKEN_FILE": "/synthetic/token"})
    entry = json.loads(value)[root]["memory-os-ai"]
    assert entry["env"]["MEMORY_V4_CLIENT_ID"] == key
    if key == "zed":
        assert entry["source"] == "custom"
    _write(tmp_path, key, value)
    before = client.config_path(tmp_path).read_bytes()
    assert clients.read_entry(client, tmp_path) == ("ok", entry)
    checks = {c.area: c for c in doctor._clients(tmp_path)}
    assert checks[f"client:{key}"].status == "PASS"
    assert "handshake not checked" in checks[f"client:{key}"].detail
    assert client.config_path(tmp_path).read_bytes() == before


def test_zed_jsonc_preserves_quoted_urls_comments_and_trailing_commas(tmp_path):
    text = '''// user configuration
    { "context_servers": { "memory-os-ai": {
        "source": "custom", "command": "memory-os-relay", "args": [],
        "env": { "MEMORY_V4_URL": "http://localhost:8765/mcp/", }, /* comment */
    }, }, }'''
    _write(tmp_path, "zed", text)
    client = next(c for c in clients.CLIENTS if c.key == "zed")
    state, entry = clients.read_entry(client, tmp_path)
    assert state == "ok" and clients.classify(entry) == "relay"
    assert entry["env"]["MEMORY_V4_URL"] == "http://localhost:8765/mcp/"


def test_doctor_reports_orphan_uncertainty(settings, tmp_path):
    from memory_os_v4.ops.request_journal import RelayJournal
    j = RelayJournal(settings.ops_dir / "relay", settings.url)
    j.append({"request_id": "test", "scope": "p", "operation": "memory_block_set",
              "raw_args_sha256": "a" * 64, "store_instance_id": "store"}, "sending")
    j.close()
    before = j.path.read_bytes()
    checks = {c.area: c for c in doctor.run(settings, home=tmp_path)}
    assert checks["relay-journals"].status == "WARN"
    assert "1 orphan uncertain" in checks["relay-journals"].detail
    assert j.path.read_bytes() == before


@pytest.mark.parametrize("source", [None, "builtin"])
def test_zed_noncustom_entry_is_not_a_working_relay(tmp_path, source):
    _write(tmp_path, "zed", json.dumps({"context_servers": {"memory-os-ai": {**RELAY, "source": source}}}))
    client = next(c for c in clients.CLIENTS if c.key == "zed")
    assert clients.read_entry(client, tmp_path)[0] == "unreadable"

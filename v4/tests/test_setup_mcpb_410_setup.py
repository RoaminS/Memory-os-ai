"""SETUP_4_1_0.md §5.4: the Claude Desktop ``.mcpb`` bundle.

One generic, reproducible archive: it launches the token-reading relay of an installed
environment, asks for paths only, and never contains or requests the bearer token.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import zipfile

import pytest

from setup_fixture_410 import tree


def bundle():
    from memory_os_v4.ops import mcpb
    return mcpb


def test_manifest_launches_the_relay_of_the_installed_environment():
    from memory_os_v4 import __version__
    manifest = bundle().manifest()
    assert manifest["name"] == "memory-os-ai" and manifest["version"] == __version__
    assert manifest["manifest_version"] == bundle().MANIFEST_VERSION
    assert {"display_name", "description", "author", "server", "user_config", "compatibility"} <= set(manifest)
    server = manifest["server"]
    assert server["type"] == "python" and server["entry_point"] == "server/main.py"
    launch = server["mcp_config"]
    assert launch["command"] == "${user_config.python}"
    assert launch["args"] == ["-m", "memory_os_v4.ops.relay"]
    assert launch["env"] == {"MEMORY_V4_CLIENT_ID": "claude-desktop",
                             "MEMORY_V4_TOKEN_FILE": "${user_config.token_file}",
                             "MEMORY_V4_URL": "${user_config.url}"}


def test_manifest_asks_for_paths_and_never_for_the_token():
    config = bundle().manifest()["user_config"]
    assert set(config) == {"python", "token_file", "url"}
    assert config["python"]["type"] == "file" and config["python"]["required"] is True
    assert config["python"]["default"] == "${HOME}/.memory-os/venv/bin/python"
    assert config["token_file"]["type"] == "file"
    assert config["token_file"]["default"] == "${HOME}/.config/memory-os/http.token"
    assert config["url"] == {**config["url"], "type": "string", "default": "http://127.0.0.1:8765/mcp/"}
    assert not any(field.get("sensitive") for field in config.values())
    text = json.dumps(bundle().manifest()).lower()
    assert "bearer" not in text and "authorization" not in text and "paste" not in text


def test_manifest_is_scoped_to_the_qualified_platform():
    compatibility = bundle().manifest()["compatibility"]
    assert compatibility["platforms"] == ["darwin"]
    assert compatibility["runtimes"] == {"python": ">=3.11,<3.12"}


def test_manifest_and_archive_hold_no_machine_specific_value(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "someone"))
    monkeypatch.setenv("MEMORY_V4_TOKEN_FILE", str(tmp_path / "someone/secret.token"))
    data = bundle().render()
    assert str(tmp_path).encode() not in data and b"/Users/" not in data
    assert os.environ["USER"].encode() not in data if os.environ.get("USER", "") not in ("", "root") else True


def test_archive_content_is_exactly_the_manifest_and_a_stdlib_launcher():
    archive = zipfile.ZipFile(io.BytesIO(bundle().render()))
    assert archive.namelist() == ["manifest.json", "server/main.py"]
    assert json.loads(archive.read("manifest.json")) == bundle().manifest()
    launcher = archive.read("server/main.py").decode()
    assert "memory_os_v4.ops.relay" in launcher
    compile(launcher, "server/main.py", "exec")
    assert "token" not in launcher.lower() or "MEMORY_V4_TOKEN_FILE" in launcher


def test_archive_is_byte_reproducible(monkeypatch):
    first = bundle().render()
    monkeypatch.setenv("TZ", "Pacific/Kiritimati")
    monkeypatch.setattr("time.time", lambda: 4_102_444_800.0)
    old = os.umask(0o077)
    try:
        second = bundle().render()
    finally:
        os.umask(old)
    assert first == second
    for info in zipfile.ZipFile(io.BytesIO(first)).infolist():
        assert info.date_time == (1980, 1, 1, 0, 0, 0)
        assert info.compress_type == zipfile.ZIP_STORED  # no dependency on the zlib build
        assert info.external_attr >> 16 == 0o100644 and info.create_system == 3
        assert info.extra == b"" and info.comment == b""


def test_build_writes_once_and_reports_the_digest(tmp_path):
    target = tmp_path / "out/memory-os-ai.mcpb"
    target.parent.mkdir()
    report = bundle().build(target)
    data = target.read_bytes()
    assert data == bundle().render()
    assert report == {"ok": True, "path": str(target), "bytes": len(data),
                      "sha256": hashlib.sha256(data).hexdigest(),
                      "files": ["manifest.json", "server/main.py"], "token_in_bundle": False}
    assert target.stat().st_mode & 0o777 == 0o644


def test_build_never_overwrites(tmp_path):
    target = tmp_path / "memory-os-ai.mcpb"
    target.write_bytes(b"someone else's file")
    with pytest.raises(bundle().McpbError) as refusal:
        bundle().build(target)
    assert refusal.value.code == "mcpb_output_exists" and target.read_bytes() == b"someone else's file"


@pytest.mark.parametrize("case", ["symlink-parent", "missing-parent", "wrong-suffix"])
def test_build_refuses_an_unsafe_destination(tmp_path, case):
    real = tmp_path / "real"
    real.mkdir()
    if case == "symlink-parent":
        (tmp_path / "link").symlink_to(real)
        target = tmp_path / "link/memory-os-ai.mcpb"
    elif case == "missing-parent":
        target = tmp_path / "absent/memory-os-ai.mcpb"
    else:
        target = real / "memory-os-ai.zip"
    before = tree(tmp_path)
    with pytest.raises(bundle().McpbError) as refusal:
        bundle().build(target)
    assert refusal.value.code == "mcpb_output_unsafe" and tree(tmp_path) == before


def test_cli_previews_by_default_and_writes_only_with_apply(tmp_path, monkeypatch, capsys):
    from memory_os_v4.ops import cli
    target = tmp_path / "memory-os-ai.mcpb"
    before = tree(tmp_path)
    assert cli.main(["mcpb", "--output", str(target)]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert tree(tmp_path) == before and preview["writes"] == "none (preview)"
    assert preview["sha256"] == hashlib.sha256(bundle().render()).hexdigest()
    assert preview["manifest"] == bundle().manifest()
    assert cli.main(["mcpb", "--output", str(target), "--apply"]) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["sha256"] == preview["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
    assert cli.main(["mcpb", "--output", str(target), "--apply"]) == 1
    assert json.loads(capsys.readouterr().out)["code"] == "mcpb_output_exists"


def test_relay_module_is_the_entry_the_bundle_names():
    """Green today: the launch target exists and is the relay every other client uses."""
    from memory_os_v4.ops import clients, relay
    assert callable(relay.main)
    assert clients.relay_command("/py") == ("/py", ["-m", "memory_os_v4.ops.relay"])

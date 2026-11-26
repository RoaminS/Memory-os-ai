"""4.1.3 Lot 2 (D3, D4): the attested cl100k_base table ships in the package; tiktoken range with a self-test."""
import hashlib
from pathlib import Path
import tomllib

import pytest

from memory_os_v4 import brief_tokens
from memory_os_v4.ops import provision

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.delenv("MEMORY_V4_CL100K_PATH", raising=False)
    brief_tokens._load.cache_clear()
    yield
    brief_tokens._load.cache_clear()


def test_the_embedded_table_is_attested_and_used_by_default():
    path = brief_tokens.embedded_table()
    assert path is not None and path.name == "cl100k_base.tiktoken"
    raw = path.read_bytes()
    assert len(raw) == brief_tokens.TABLE_BYTES and hashlib.sha256(raw).hexdigest() == brief_tokens.TABLE_SHA256
    status = brief_tokens.status()
    assert status["ready"] and status["table_source"] == "embedded", status
    assert brief_tokens.counter()("hello world") == 2


def test_an_explicit_override_is_used_and_never_silently_replaced(tmp_path, monkeypatch):
    copy = tmp_path / "table.tiktoken"
    copy.write_bytes(brief_tokens.embedded_table().read_bytes())
    monkeypatch.setenv("MEMORY_V4_CL100K_PATH", str(copy))
    assert brief_tokens.status() == {**brief_tokens.status(), "ready": True, "table_source": "explicit"}
    monkeypatch.setenv("MEMORY_V4_CL100K_PATH", str(tmp_path / "missing.tiktoken"))
    status = brief_tokens.status()
    assert not status["ready"] and status["table_source"] == "explicit"
    with pytest.raises(brief_tokens.TokenizerUnavailable):
        brief_tokens.counter()


def test_a_tampered_embedded_table_is_refused(tmp_path, monkeypatch):
    raw = bytearray(brief_tokens.embedded_table().read_bytes())
    raw[100] ^= 1
    tampered = tmp_path / "cl100k_base.tiktoken"
    tampered.write_bytes(bytes(raw))
    monkeypatch.setattr(brief_tokens, "embedded_table", lambda: tampered)
    assert not brief_tokens.status()["ready"]


def test_the_self_test_refuses_an_implementation_that_counts_differently(monkeypatch):
    text, ids = brief_tokens.SELF_TEST[0]
    monkeypatch.setattr(brief_tokens, "SELF_TEST", ((text, ids + (1,)),))
    status = brief_tokens.status()
    assert not status["ready"] and status["reason"] == "tokenizer_unavailable"


@pytest.mark.parametrize("version, accepted", [
    ("0.12.0", True), ("0.12", True), ("0.13.4", True), ("0.99.99", True),
    ("0.11.9", False), ("1.0.0", False), ("2.1", False), ("0.12.0rc1", False), ("0.12.0+local", False),
    ("0.12.0.post1", False), ("", False), ("x", False),
])
def test_the_tiktoken_range_is_enforced(version, accepted):
    assert brief_tokens._supported(version) is accepted


def test_an_unsupported_tiktoken_version_makes_the_tokenizer_unavailable(monkeypatch):
    monkeypatch.setattr(brief_tokens.importlib.metadata, "version", lambda name: "1.0.0")
    assert not brief_tokens.status()["ready"]


def test_every_self_test_vector_matches_the_qualified_implementation():
    encoding = brief_tokens._load(str(brief_tokens.embedded_table()))
    for text, expected in brief_tokens.SELF_TEST:
        assert tuple(encoding.encode_ordinary(text)) == expected


def test_provisioning_treats_the_embedded_table_as_present(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    preview = provision.plan(resources=("cl100k",))
    item = preview["files"][0]
    assert item["present"] and item["table_source"] == "embedded"
    assert item["target"] == str(brief_tokens.embedded_table())
    assert preview["ready"] and preview["download_bytes"] == 0 and preview["table_path"] is None
    assert "MEMORY_V4_CL100K_PATH" not in preview["env"]           # nothing to write in any configuration
    applied = provision.apply(preview)                             # no download, nothing modified
    assert applied["mode"] == "apply" and applied["downloaded_bytes"] == 0 and applied["modified"] is False
    assert not (tmp_path / "config").exists()


def test_an_explicit_table_keeps_the_4_1_2_provisioning(tmp_path, monkeypatch):
    table = tmp_path / "explicit.tiktoken"
    monkeypatch.setenv("MEMORY_V4_CL100K_PATH", str(table))
    preview = provision.plan(resources=("cl100k",))
    assert preview["files"][0]["table_source"] == "explicit" and not preview["files"][0]["present"]
    assert preview["env"]["MEMORY_V4_CL100K_PATH"] == str(table) and preview["download_bytes"] > 0


def test_an_installation_without_the_table_falls_back_to_the_consented_download(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(brief_tokens, "embedded_table", lambda: None)
    preview = provision.plan(resources=("cl100k",))
    assert preview["files"][0]["table_source"] == "download" and preview["download_bytes"] > 0
    assert preview["env"]["MEMORY_V4_CL100K_PATH"] == str(tmp_path / "config/memory-os/cl100k_base.tiktoken")


def test_packaging_ships_the_table_with_its_licence():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert project["project"]["license"] == "LGPL-3.0-or-later AND MIT"
    assert "THIRD_PARTY_NOTICES.md" in project["project"]["license-files"]
    assert "resources/*.tiktoken" in project["tool"]["setuptools"]["package-data"]["memory_os_v4"]
    manifest = (ROOT / "MANIFEST.in").read_text()
    assert "THIRD_PARTY_NOTICES.md" in manifest and "*.tiktoken" in manifest
    notice = (ROOT / "THIRD_PARTY_NOTICES.md").read_text()
    assert brief_tokens.TABLE_SHA256 in notice and "MIT License" in notice and "Copyright (c) 2022 OpenAI" in notice

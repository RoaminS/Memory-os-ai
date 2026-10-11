"""Attest client MCP entries without treating unrelated app state as damage."""
import importlib.util
import json
from pathlib import Path
import sys


def module():
    scripts = Path(__file__).resolve().parents[2] / 'scripts'
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    spec = importlib.util.spec_from_file_location('config_attestation', scripts / 'qualify_405_test_isolation.py')
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_app_rewrites_ignore_unrelated_state_but_detect_entry_changes(tmp_path, monkeypatch):
    m = module()
    repo = tmp_path / 'repo'
    repo.mkdir()
    monkeypatch.setattr(m, 'ROOT', repo)
    home = tmp_path / 'home'
    home.mkdir()
    config = home / '.claude.json'
    entry = {'command': 'python', 'args': ['-m', 'memory_os_v4.ops.relay'], 'env': {'X': 'sensitive-fixture-value'}}
    config.write_text(json.dumps({'mcpServers': {'memory-os-ai': entry}, 'session': 1}))
    before = m.snapshots(home)
    config.unlink()  # app replacement changes inode and formatting
    config.write_text(json.dumps({'session': 2, 'mcpServers': {'memory-os-ai': entry}}, indent=2))
    assert m.snapshots(home) == before
    assert 'sensitive-fixture-value' not in json.dumps(before)
    entry['args'] = ['-m', 'memory_os_ai.server']
    config.write_text(json.dumps({'mcpServers': {'memory-os-ai': entry}}))
    assert m.snapshots(home) != before


def test_repository_files_strict_and_malformed_clients_fail_closed(tmp_path, monkeypatch):
    m = module()
    monkeypatch.setattr(m, 'ROOT', tmp_path)
    (tmp_path / '.mcp.json').write_text('{}')
    before = m.snapshots(tmp_path)
    (tmp_path / '.mcp.json').write_text('{ }')
    assert m.snapshots(tmp_path) != before
    (tmp_path / '.claude.json').write_text('{malformed')
    assert m.snapshots(tmp_path)[str(tmp_path / '.claude.json')]['kind'] == 'unreadable'

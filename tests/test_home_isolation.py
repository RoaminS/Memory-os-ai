"""No encoder or user configuration is touched by these regressions."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def guard_module():
    spec = importlib.util.spec_from_file_location('guard_case', ROOT / 'scripts/test_home_guard.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_real_setup_all_writes_only_isolated_home_and_project(tmp_path, monkeypatch, isolated_test_home):
    from memory_os_ai import setup

    project = tmp_path / 'project'
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setattr(sys, 'argv', ['setup', 'all'])
    monkeypatch.setattr(setup.platform, 'system', lambda: 'Darwin')
    setup.main()  # Exercise the original TARGETS callbacks, not attribute mocks.
    desktop = isolated_test_home / 'Library/Application Support/Claude/claude_desktop_config.json'
    assert json.loads(desktop.read_text())['mcpServers']['memory-os-ai']['args'] == ['-m', 'memory_os_ai.server']
    assert (isolated_test_home / '.codex/mcp.json').exists()
    assert (isolated_test_home / '.memory-os-ai').is_dir()
    assert (project / '.mcp.json').exists()
    assert (project / '.vscode/mcp.json').exists()


@pytest.mark.parametrize('operation', ['write', 'os_open', 'rename', 'unlink', 'mkdir', 'symlink',
                                       'dir_fd', 'open_dir_fd', 'sqlite', 'file_descriptor'])
def test_guard_blocks_real_home_and_symlink_escape_in_fresh_process(tmp_path, operation):
    protected = tmp_path / 'operator'
    protected.mkdir()
    sentinel = protected / 'config.json'
    sentinel.write_text('user-config')
    alias = tmp_path / 'alias'
    alias.symlink_to(protected, target_is_directory=True)
    program = '''
import importlib.util, os, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location('guard', sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
protected, alias = Path(sys.argv[2]), Path(sys.argv[3])
read_fd = os.open(protected / 'config.json', os.O_RDONLY)
g = m.HomeWriteGuard(protected); g.install()
try:
    op = sys.argv[4]
    if op == 'write': (alias / 'config.json').write_text('overwritten')
    elif op == 'os_open': os.open(alias / 'config.json', os.O_WRONLY | os.O_TRUNC)
    elif op == 'rename': os.replace(alias / 'config.json', alias / 'other.json')
    elif op == 'unlink': (alias / 'config.json').unlink()
    elif op == 'mkdir': (alias / 'new').mkdir()
    elif op == 'symlink': (alias / 'new').symlink_to('/tmp')
    elif op == 'dir_fd':
        fd = os.open(protected, os.O_RDONLY)
        try: os.unlink('config.json', dir_fd=fd)
        finally: os.close(fd)
    elif op == 'open_dir_fd':
        fd = os.open(protected, os.O_RDONLY)
        try: os.open('config.json', os.O_WRONLY | os.O_TRUNC, dir_fd=fd)
        finally: os.close(fd)
    elif op == 'sqlite':
        import sqlite3
        sqlite3.connect(protected / 'new.sqlite')
    elif op == 'file_descriptor': os.fchmod(read_fd, 0o600)
except PermissionError:
    assert len(g.violations) == 1
else: raise AssertionError('write was not blocked')
assert (protected / 'config.json').read_text() == 'user-config'
'''
    result = subprocess.run([sys.executable, '-B', '-c', program, str(ROOT / 'scripts/test_home_guard.py'),
                             str(protected), str(alias), operation], env=os.environ.copy(),
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert sentinel.read_text() == 'user-config'


def test_swallowed_write_still_fails_pytest_and_guard_loads_before_collection(tmp_path):
    case = tmp_path / 'nested'
    case.mkdir()
    (case / 'scripts').mkdir()
    (case / 'scripts/test_home_guard.py').write_bytes((ROOT / 'scripts/test_home_guard.py').read_bytes())
    protected = tmp_path / 'sentinel-home'
    protected.mkdir()
    sentinel = protected / 'config.json'
    sentinel.write_text('unchanged')
    # Only this isolated subprocess uses a synthetic operator home for its guard.
    source = (ROOT / 'conftest.py').read_text().replace(
        'REAL_HOME = support.operator_home()', f'REAL_HOME = Path({str(protected)!r})')
    (case / 'conftest.py').write_text(source)
    (case / 'test_case.py').write_text(f'''
import os
from pathlib import Path
assert Path.home() != Path({str(protected)!r})  # Collection-time isolation.
def test_swallowed_error():
    try: Path({str(sentinel)!r}).write_text('bad')
    except Exception: pass
def test_environment():
    assert os.environ['HOME'] == str(Path.home())
''')
    env = {**os.environ, 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'}
    result = subprocess.run([sys.executable, '-B', '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
                             '--basetemp=' + str(tmp_path / 'nested-temp'), str(case)],
                            cwd=case, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'Test attempted a write under real HOME' in result.stdout
    assert '2 passed' in result.stdout and '1 error' in result.stdout
    assert sentinel.read_text() == 'unchanged'


def test_sdist_manifest_ships_both_isolation_files():
    manifest = (ROOT / 'MANIFEST.in').read_text()
    assert 'include conftest.py' in manifest
    assert 'recursive-include scripts *.py' in manifest

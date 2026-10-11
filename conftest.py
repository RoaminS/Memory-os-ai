"""Mandatory shared isolation for both suites, including tests in the sdist."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil
import sys
import tempfile

import pytest

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('memory_test_home_guard', ROOT / 'scripts/test_home_guard.py')
support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(support)
REAL_HOME = support.operator_home()
SESSION_HOME = Path(tempfile.mkdtemp(prefix='memory-os-pytest-home-'))
GUARD = support.HomeWriteGuard(REAL_HOME, (SESSION_HOME, ROOT / '.local-qualification', ROOT / '.pytest_cache'))
GUARD.install()
sys.dont_write_bytecode = True


def set_home(path, setter):
    for name, value in {
        'HOME': path, 'USERPROFILE': path, 'APPDATA': path / 'AppData/Roaming',
        'LOCALAPPDATA': path / 'AppData/Local', 'XDG_CONFIG_HOME': path / '.config',
        'XDG_CACHE_HOME': path / '.cache', 'XDG_DATA_HOME': path / '.local/share',
        'XDG_STATE_HOME': path / '.local/state',
    }.items():
        setter(name, str(value))


# Preserve read-only offline model discovery without using the fake HOME cache.
os.environ.setdefault('HF_HOME', str(REAL_HOME / '.cache/huggingface'))
os.environ.setdefault('HF_HUB_CACHE', str(Path(os.environ['HF_HOME']) / 'hub'))
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
set_home(SESSION_HOME, os.environ.__setitem__)
os.environ['MEMORY_WORKSPACE'] = str(ROOT)  # Read-only fixtures/selfcheck docs; writes stay guarded.
for name, leaf in {'MEMORY_CACHE_DIR': 'v3-store', 'MEMORY_V4_DIR': 'v4-store',
                   'MEMORY_V4_OPS_DIR': 'ops'}.items():
    os.environ[name] = str(SESSION_HOME / leaf)
for name in ('MEMORY_V4_TOKEN_FILE', 'MEMORY_V4_PROJECT_REGISTRY'):
    os.environ.pop(name, None)


@pytest.fixture(autouse=True)
def isolated_test_home(monkeypatch):
    # Separate from tmp_path: store tests assert its exhaustive file inventory.
    home = Path(tempfile.mkdtemp(prefix='case-', dir=SESSION_HOME))
    set_home(home, monkeypatch.setenv)
    # setup caches HOME at import time; redirect it for every individual test.
    module = sys.modules.get('memory_os_ai.setup')
    if module is not None:
        monkeypatch.setattr(module, '_HOME', str(home))
    offset = len(GUARD.violations)
    yield home
    if len(GUARD.violations) > offset:
        pytest.fail('\n'.join(GUARD.violations[offset:]), pytrace=False)


def pytest_sessionfinish(session, exitstatus):
    if GUARD.violations:
        # Also catches collection/session-fixture writes and swallowed exceptions.
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
        session.config.pluginmanager.get_plugin('terminalreporter').write_line(
            '\n'.join(GUARD.violations), red=True)
    shutil.rmtree(SESSION_HOME)


# Child conftests also bootstrap this module when -c/--confcutdir excludes ROOT.
sys.modules.setdefault('memory_home_isolation_pytest', sys.modules[__name__])

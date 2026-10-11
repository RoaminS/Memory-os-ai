"""Exercise the v4-specific pytest entry point without any model or transport."""
import os
from pathlib import Path
import subprocess
import sys

isolation = sys.modules['memory_home_isolation_pytest']
assert Path.home() != isolation.REAL_HOME  # Also required during collection.


def test_home_is_private():
    assert Path.home() != isolation.REAL_HOME
    assert Path.home().is_dir()
    assert os.environ['HOME'] == os.environ['USERPROFILE'] == str(Path.home())
    assert os.environ['XDG_CONFIG_HOME'] == str(Path.home() / '.config')


def test_v4_config_with_confcutdir_keeps_shared_guard(tmp_path):
    root = Path(__file__).resolve().parents[2]
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}
    env.pop('PYTEST_ADDOPTS', None)
    command = [sys.executable, '-B', '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
               '-c', str(root / 'v4/pyproject.toml'), '--confcutdir=' + str(root / 'v4'),
               '--basetemp=' + str(tmp_path / 'subtest'),
               str(Path(__file__).resolve()) + '::test_home_is_private']
    result = subprocess.run(command, cwd=root / 'v4', env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '1 passed' in result.stdout

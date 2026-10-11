"""Load shared isolation even with v4/pyproject.toml or --confcutdir=v4."""
import importlib.util
from pathlib import Path
import sys

name = 'memory_home_isolation_pytest'
if name not in sys.modules:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[2] / 'conftest.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
_home_isolation = sys.modules[name]


def pytest_configure(config):
    if not config.pluginmanager.is_registered(_home_isolation):
        config.pluginmanager.register(_home_isolation, 'memory-home-isolation')

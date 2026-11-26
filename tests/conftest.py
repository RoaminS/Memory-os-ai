"""Shared home isolation for the root test suite (tests of scripts, release tooling and evaluation).

4.1.3 (D2): the synthetic v3 encoder fixture moved with the v3 package and its tests to legacy/v3/tests/; nothing
in this suite imports the legacy ``memory_os_ai`` package any more.
"""
import importlib.util
from pathlib import Path
import sys


def _shared_home_isolation():
    name = 'memory_home_isolation_pytest'
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / 'conftest.py')
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


_home_isolation = _shared_home_isolation()


def pytest_configure(config):
    if not config.pluginmanager.is_registered(_home_isolation):
        config.pluginmanager.register(_home_isolation, 'memory-home-isolation')

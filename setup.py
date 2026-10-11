"""Setuptools hooks; pyproject.toml remains the single package metadata source."""
from pathlib import Path
import sys

from setuptools import setup

# PEP 517 build_meta executes this file without adding the source root to sys.path.
# Resolve only our sibling hook, including in a Git-free extracted sdist.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from memory_os_build import AttestedBuildPy, AttestedSdist  # noqa: E402

setup(cmdclass={"build_py": AttestedBuildPy, "sdist": AttestedSdist})

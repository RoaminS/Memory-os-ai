"""Setuptools hooks; pyproject.toml remains the single package metadata source."""
from setuptools import setup

from memory_os_build import AttestedBuildPy, AttestedSdist

setup(cmdclass={"build_py": AttestedBuildPy, "sdist": AttestedSdist})

"""Exercise dependency transport in a real fresh interpreter, without downloads."""
import importlib.util
import os
from pathlib import Path
import subprocess

import pytest


def runtime_script():
    path = Path(__file__).resolve().parents[2] / "scripts/make_brief_test_runtime.py"
    spec = importlib.util.spec_from_file_location("brief_runtime", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def packages(tmp_path, *, extension=True):
    base, source = tmp_path / "base", tmp_path / "packages"
    base.mkdir()
    source.mkdir()
    (source / "tiktoken").mkdir()
    (source / "tiktoken/__init__.py").write_text("from tiktoken_ext import proof\n")
    (source / "tiktoken-0.12.0.dist-info").mkdir()
    if extension:
        (source / "tiktoken_ext").mkdir()
        (source / "tiktoken_ext/__init__.py").write_text("proof = 'extension-imported'\n")
    return base, source


def test_fresh_runtime_imports_extension_without_host_pythonpath(tmp_path):
    base, source = packages(tmp_path)
    output = tmp_path / "runtime"
    runtime_script().create_runtime(output, base, source)
    env = {**os.environ, "PYTHONPATH": "", "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run([str(output / "bin/python"), "-I", "-B", "-c",
                             "import tiktoken; print(tiktoken.proof)"],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "extension-imported"
    assert not list(source.rglob("__pycache__"))


def test_missing_extension_refused_before_creating_runtime(tmp_path):
    base, source = packages(tmp_path, extension=False)
    output = tmp_path / "runtime"
    with pytest.raises(ValueError, match="complete tiktoken"):
        runtime_script().create_runtime(output, base, source)
    assert not output.exists()

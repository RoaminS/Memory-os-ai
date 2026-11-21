"""SETUP_4_1_0.md §9: what a new user copies, and what the release gates resolve.

README install commands, deeplinks, ``server.json``, the network resolution gate and the
constraints locator must all name 4.1.0, one profile, and the constraints of that profile.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import re
import subprocess
import sys
import tomllib
import urllib.parse
from pathlib import Path

import pytest

from memory_os_v4 import __version__
from memory_os_v4.ops.qualified_runtime import CONSTRAINTS, expected_versions, ml_closure

ROOT = Path(__file__).resolve().parents[2]
README = (ROOT / "README.md").read_text(encoding="utf-8")
ML_MODULES = ("torch", "sentence_transformers", "transformers", "faiss")
BLOCKER = (
    "import sys\n"
    "class Block:\n"
    "    def find_spec(self, name, path=None, target=None):\n"
    "        if name.split('.')[0] in BLOCKED:\n"
    "            raise ImportError('blocked by the test: ' + name)\n"
    "sys.meta_path.insert(0, Block())\n"
)


def script(name, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(name + "_410_setup", ROOT / f"scripts/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def isolated(code, blocked):
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(ROOT / "v4/src"), "PYTHONDONTWRITEBYTECODE": "1",
           "HOME": "/nonexistent-home-for-test"}
    return subprocess.run([sys.executable, "-B", "-c", f"BLOCKED = {tuple(blocked)!r}\n" + BLOCKER + code],
                          capture_output=True, text=True, timeout=120, env=env)


# ---- server.json: the relay only (green: pinned) ----

def test_server_json_launches_the_relay_without_the_semantic_extra():
    document = json.loads((ROOT / "server.json").read_text())
    package, = document["packages"]
    assert document["version"] == package["version"] == __version__
    named, positional = package["runtimeArguments"]
    assert named["value"] == f"memory-os-ai[ops,brief]=={__version__}" and positional["value"] == "memory-os-relay"
    assert "semantic" not in json.dumps(document)
    assert not any(variable.get("isSecret") for variable in package["environmentVariables"])


def test_relay_imports_no_ml_module():
    done = isolated("import memory_os_v4.ops.relay as relay\n"
                    "assert callable(relay.main)\n"
                    f"assert not [m for m in {ML_MODULES!r} if m in sys.modules]\n"
                    "print('ok')", ML_MODULES)
    assert done.returncode == 0 and done.stdout.strip() == "ok", done.stderr[-2000:]


def test_admin_cli_parses_without_any_ml_module():
    done = isolated("from memory_os_v4.ops import cli, quickstart, doctor, provision, clients\n"
                    f"assert not [m for m in {ML_MODULES!r} if m in sys.modules]\n"
                    "print('ok')", ML_MODULES)
    assert done.returncode == 0 and done.stdout.strip() == "ok", done.stderr[-2000:]


def test_both_constraints_files_are_packaged_resources():
    package_data = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["setuptools"]["package-data"]
    assert "resources/*.txt" in package_data["memory_os_v4"]
    assert CONSTRAINTS["lexical"].name == "qualified_runtime_constraints_lexical_v1.txt"
    assert CONSTRAINTS["semantic"].name == "qualified_runtime_constraints_v1.txt"
    assert all(path.is_file() for path in CONSTRAINTS.values())


# ---- README ----

def test_readme_no_longer_installs_a_previous_release():
    assert "4.0.9" not in "\n".join(line for line in README.splitlines() if "pip install" in line or "uvx" in line)
    assert not re.search(r"memory-os-ai\[[a-z,]+\]==(?!" + re.escape(__version__) + r")", README)


def test_readme_gives_the_lexical_install_with_its_constraints():
    block = README[README.index("## Install on macOS"):]
    assert f'"memory-os-ai[ops,brief]=={__version__}"' in block
    assert "memory-os-constraints lexical" in block
    assert re.search(r"pip install -c .*memory-os-constraints lexical.*memory-os-ai\[ops,brief\]==", block)
    assert "quickstart --profile lexical" in block
    lexical = block[block.index("quickstart --profile lexical"):][:400]
    assert "--download-approved" in lexical  # the 1.7 MB tokenizer table still needs its consent


def test_readme_gives_the_semantic_install_with_extra_and_constraints():
    block = README[README.index("## Install on macOS"):]
    assert f'"memory-os-ai[ops,brief,semantic]=={__version__}"' in block
    assert re.search(r"pip install -c .*memory-os-constraints semantic.*memory-os-ai\[ops,brief,semantic\]==", block)
    assert "quickstart --profile semantic" in block


def test_readme_states_what_each_profile_downloads():
    block = README[README.index("## Install on macOS"):]
    assert "no embedding model" in block and "221.9 MB" in block
    assert "uninstall" in block and "never deletes" in block


def test_readme_deeplinks_pin_the_current_release():
    cursor = re.search(r"cursor\.com/link/mcp/install\?name=memory-os-ai&config=([A-Za-z0-9%_=+/-]+)", README)
    config = json.loads(base64.b64decode(urllib.parse.unquote(cursor.group(1))))
    assert f"memory-os-ai[ops,brief]=={__version__}" in config["args"] and config["args"][-1] == "memory-os-relay"
    vscode = re.search(r"vscode\.dev/redirect/mcp/install\?name=memory-os-ai&config=([^)\s]+)", README)
    config = json.loads(urllib.parse.unquote(vscode.group(1)))
    assert f"memory-os-ai[ops,brief]=={__version__}" in config["args"]
    assert "semantic" not in json.dumps(config)


# ---- constraints locator: usable before any dependency is installed ----

def test_constraints_locator_needs_no_third_party_package():
    done = isolated("import memory_os_constraints as locator\n"
                    "print(locator.path('lexical'))\nprint(locator.path('semantic'))\n"
                    "assert 'memory_os_v4' not in sys.modules and 'numpy' not in sys.modules\n",
                    ("numpy", "cryptography", "tiktoken", "mcp", "packaging", *ML_MODULES))
    assert done.returncode == 0, done.stderr[-2000:]
    assert done.stdout.split() == [str(CONSTRAINTS["lexical"]), str(CONSTRAINTS["semantic"])]


@pytest.mark.parametrize("argv,code", [([], 2), (["hybrid"], 2), (["lexical", "extra"], 2)])
def test_constraints_locator_requires_exactly_one_known_profile(argv, code):
    done = isolated(f"import memory_os_constraints as locator\nraise SystemExit(locator.main({argv!r}))", ())
    assert done.returncode == code and str(CONSTRAINTS["lexical"]) not in done.stdout


def test_constraints_locator_is_an_installed_command():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert project["project"]["scripts"]["memory-os-constraints"] == "memory_os_constraints:main"
    assert "memory_os_constraints" in project["tool"]["setuptools"]["py-modules"]


# ---- release gates ----

@pytest.mark.parametrize("profile,requirement", [("lexical", "[ops,brief]"), ("semantic", "[ops,brief,semantic]")])
def test_resolution_gate_installs_the_profile_extras_under_its_constraints(monkeypatch, tmp_path, profile, requirement):
    gate = script("qualify_runtime_resolution", monkeypatch)
    wheel = tmp_path / f"memory_os_ai-{__version__}-py3-none-any.whl"
    argv = gate.resolution_command("/py", wheel, tmp_path / "out", profile=profile)
    assert argv[:5] == ["/py", "-B", "-m", "pip", "install"] and "--dry-run" in argv
    assert argv[-1] == str(wheel) + requirement
    assert argv[argv.index("-c") + 1] == str(CONSTRAINTS[profile])
    assert argv.count("-c") == 1


def test_resolution_gate_has_no_default_profile(monkeypatch, tmp_path):
    gate = script("qualify_runtime_resolution", monkeypatch)
    with pytest.raises(TypeError):
        gate.resolution_command("/py", tmp_path / "w.whl", tmp_path / "out")
    with pytest.raises(ValueError):
        gate.resolution_command("/py", tmp_path / "w.whl", tmp_path / "out", profile="hybrid")


def report_for(versions):
    return {"environment": {"sys_platform": "darwin", "platform_machine": "arm64", "python_version": "3.11"},
            "install": [{"metadata": {"name": name, "version": version}} for name, version in versions.items()]}


def test_resolution_check_compares_with_the_catalog_of_the_profile(monkeypatch):
    check = script("check_runtime_resolution", monkeypatch).check
    lexical = {**expected_versions(profile="lexical", extras=("ops", "brief")), "memory-os-ai": __version__}
    assert check(report_for(lexical), __version__, profile="lexical")["ok"] is True
    assert check(report_for(lexical), __version__, profile="semantic")["ok"] is False


def test_lexical_resolution_refuses_any_package_of_the_ml_closure(monkeypatch):
    check = script("check_runtime_resolution", monkeypatch).check
    lexical = {**expected_versions(profile="lexical", extras=("ops", "brief")), "memory-os-ai": __version__}
    semantic = expected_versions(profile="semantic", extras=("ops", "brief"))
    name = sorted(ml_closure(extras=("ops", "brief")))[0]
    result = check(report_for({**lexical, name: semantic[name]}), __version__, profile="lexical")
    assert result["ok"] is False and result["ml_packages"] == [name]


def test_semantic_resolution_check_is_unchanged(monkeypatch):
    """Green today: the semantic gate keeps comparing with the historical catalog by default."""
    check = script("check_runtime_resolution", monkeypatch).check
    semantic = {**expected_versions(extras=("ops", "brief")), "memory-os-ai": __version__}
    assert check(report_for(semantic), __version__)["ok"] is True
    assert check(report_for({**semantic, "left-pad": "1.0"}), __version__)["unexpected_packages"] == ["left-pad"]

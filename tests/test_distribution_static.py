"""Packaging contracts checked offline, without invoking a build or an encoder."""

import ast
from pathlib import Path
import re
import tomllib

import pytest

ROOT = Path(__file__).resolve().parents[1]


def data_files(root, entries):
    files = set()
    for patterns in entries.values():
        for pattern in patterns:
            matches = list(root.glob(pattern))
            if not matches or any(not path.is_file() for path in matches):
                raise ValueError(f"data-files must match regular files: {pattern}")
            files.update(str(path.relative_to(root)) for path in matches)
    return files


def metadata():
    return tomllib.loads((ROOT / "pyproject.toml").read_text())


def test_all_declared_data_files_exist_as_regular_files():
    files = data_files(ROOT, metadata()["tool"]["setuptools"]["data-files"])
    assert {"docs/releases/v4.0.7.md", "docs/releases/v4.0.8.md"} <= files


@pytest.mark.parametrize("pattern", ["missing.md", "missing-*.md", "directory"])
def test_data_files_missing_literal_glob_or_directory_is_refused(tmp_path, pattern):
    (tmp_path / "directory").mkdir()
    with pytest.raises(ValueError, match="regular files"):
        data_files(tmp_path, {"share/doc": [pattern]})


def test_console_scripts_resolve_to_source_functions_without_importing_server():
    scripts = metadata()["project"]["scripts"]
    assert set(scripts) == {
        "memory-os-ai", "memory-os-v4",       # 4.1.3 (D2): memory-os-ai-v3 is no longer shipped
        "memory-os-relay", "memory-os-admin",
        "memory-os-constraints",  # 4.1.0 setup contract §9: stdlib-only constraints locator
    }
    for entry in scripts.values():
        module, function = entry.split(":")
        candidates = [ROOT / base / (module.replace(".", "/") + ".py")
                      for base in ("v4/src",)]
        sources = [path for path in candidates if path.is_file()]
        assert len(sources) == 1, entry
        tree = ast.parse(sources[0].read_text())
        assert any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and node.name == function for node in tree.body), entry


def test_sdist_manifest_covers_release_tooling_helpers_resources_and_data(monkeypatch):
    from setuptools._distutils.filelist import FileList

    monkeypatch.syspath_prepend(str(ROOT))
    import memory_os_build

    # Use the actual source inventory plus setuptools' template interpreter;
    # neither builds a distribution nor scans qualification evidence folders.
    inventory = memory_os_build.source_inventory(ROOT)
    selected = FileList()
    selected.set_allfiles(sorted(inventory))
    monkeypatch.chdir(ROOT)
    for line in (ROOT / "MANIFEST.in").read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            selected.process_template_line(line)
    included = set(selected.files) | set(memory_os_build.INPUTS)
    # Setuptools automatically includes discovered package Python sources before
    # applying MANIFEST.in. Match that declared discovery (no setup/build call).
    discovery = metadata()["tool"]["setuptools"]["packages"]["find"]
    assert discovery["where"] == ["v4/src"]               # 4.1.3 (D2): no legacy v3 package
    assert set(discovery["include"]) == {"memory_os_v4*"}
    for base, package in (("v4/src", "memory_os_v4"),):
        included.update(str(path.relative_to(ROOT))
                        for path in (ROOT / base / package).rglob("*.py"))
    required = data_files(ROOT, metadata()["tool"]["setuptools"]["data-files"])
    required.update(name for name in inventory if name.startswith(
        ("scripts/", "v4/src/memory_os_v4/", "tests/", "v4/tests/")
    ))
    required.update({"setup.py", "memory_os_build.py", "conftest.py"})
    assert not (required - included), sorted(required - included)
    assert required <= inventory.keys()


def test_runbook_script_paths_and_static_gate_are_present(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    import qualify_distribution_d1a as qualify
    import make_release_408 as make

    assert "tests/test_distribution_static.py" in qualify.UNITS
    text = (ROOT / "v4/docs/QUALIFY_4_0_8.md").read_text()
    for script in re.findall(r"scripts/([A-Za-z0-9_]+\.py)", text):
        assert (ROOT / "scripts" / script).is_file(), script
    assert len(set(make.TOOLS)) == len(make.TOOLS)
    assert all((ROOT / "scripts" / name).is_file() for name in make.TOOLS)

"""4.1.3 freeze (Q1 of decision D13): the release tag and the release assets.

- ``scripts/install.sh`` is pinned to ``v`` + the version of both ``pyproject.toml`` files, and every one-command URL
  of the README and of the release notes names that same tag;
- ``scripts/release_assets_413.py`` refuses a directory that is not exactly the asset set of one version, and writes
  a ``SHA256SUMS`` that covers every asset (``install.sh`` and ``memory_os_bootstrap.py`` included, never itself),
  which the checksum step of ``install.sh`` accepts. Temporary directories only; no real asset is touched.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
import tomllib
import zipfile

import pytest

import memory_os_v4
from test_bootstrap_411 import bs, make_pin
from test_install_sh_413 import MARKER_BOOTSTRAP, Release

ROOT = Path(__file__).resolve().parents[2]
INSTALL = ROOT / "scripts" / "install.sh"
V = memory_os_v4.__version__
TAG = f"v{V}"
ONE_COMMAND_URL = re.compile(r"https://github\.com/RoaminS/Memory-os-ai/releases/download/([^/\s\"']+)/install\.sh")


def _load():
    spec = importlib.util.spec_from_file_location("release_assets_413", ROOT / "scripts" / "release_assets_413.py")
    module = importlib.util.module_from_spec(spec)
    before, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = before
    return module


ra = _load()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- the freeze check

def test_the_release_tag_of_install_sh_is_the_version_of_both_pyproject_files():
    versions = {tomllib.loads((ROOT / name).read_text())["project"]["version"]
                for name in ("pyproject.toml", "v4/pyproject.toml")}
    assert versions == {V}
    assert ra.release_tag(INSTALL.read_text()) == TAG
    assert f'  RELEASE_TAG="{TAG}"\n' in INSTALL.read_text()


@pytest.mark.parametrize("document", ["README.md", f"docs/releases/v{V}.md"])
def test_the_one_command_urls_name_the_release_tag(document):
    text = (ROOT / document).read_text()
    assert ONE_COMMAND_URL.findall(text) and set(ONE_COMMAND_URL.findall(text)) == {TAG}


def test_no_documentation_names_another_install_sh_release():
    """Every ``releases/download/<tag>/install.sh`` of the shipped documentation is the current tag."""
    seen = {}
    for path in [ROOT / "README.md", ROOT / "CHANGELOG.md", *sorted((ROOT / "docs").rglob("*.md"))]:
        for tag in ONE_COMMAND_URL.findall(path.read_text()):
            seen.setdefault(tag, []).append(path.relative_to(ROOT).as_posix())
    assert set(seen) == {TAG}, seen


def test_the_readme_docker_line_builds_the_wheel_of_this_version():
    assert f"--build-arg WHEEL=memory_os_ai-{V}-py3-none-any.whl " in (ROOT / "README.md").read_text()


def test_the_helper_reads_the_pin_with_the_markers_of_the_bootstrap():
    assert (ra.PIN_BEGIN, ra.PIN_END, ra.P9_KIND, ra.PACKAGE) == (bs.PIN_BEGIN, bs.PIN_END, bs.P9_KIND, bs.PACKAGE)


# ---------------------------------------------------------------- the asset helper

def make_wheel(directory: Path, version: str = V) -> Path:
    path = directory / f"memory_os_ai-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("memory_os_v4/__init__.py", f'__version__ = "{version}"\n')
        archive.writestr(f"memory_os_ai-{version}.dist-info/RECORD", "")
    return path


def release_pin(wheel: Path, version: str = V) -> dict:
    pin = make_pin(wheel, state="release")
    pin["version"] = version
    assert bs.load_pin(json.dumps(pin))["ok"]
    return pin


def assemble(directory: Path, *, version: str = V, tag: str | None = None, bootstrap: str | None = None) -> dict:
    """The six assets of ``version`` (SHA256SUMS not yet written): the real bootstrap with a filled release pin."""
    directory.mkdir()
    wheel = make_wheel(directory, version)
    (directory / f"memory_os_ai-{version}.tar.gz").write_bytes(b"sdist of " + version.encode())
    pin = release_pin(wheel, version)
    text = bs.fill_pin((ROOT / "scripts" / "memory_os_bootstrap.py").read_text(), pin) if bootstrap is None \
        else bootstrap + "\n" + bs.render_pin_block(pin) + "\n"
    (directory / "memory_os_bootstrap.py").write_text(text)
    manifest = {"schema_version": 1, "kind": bs.P9_KIND, "artifact": {"sha256": pin["wheel"]["sha256"]}, "cells": []}
    (directory / f"memory-os-ai-{version}.p9-manifest.json").write_text(json.dumps(manifest))
    script = INSTALL.read_text().replace(f'  RELEASE_TAG="{TAG}"', f'  RELEASE_TAG="{"v" + version if tag is None else tag}"')
    (directory / "install.sh").write_text(script)
    return pin


def test_the_correct_set_gives_the_exact_sha256sums(tmp_path):
    assets = tmp_path / "assets"
    assemble(assets)
    names = sorted(p.name for p in assets.iterdir())
    expected = "".join(f"{sha((assets / name).read_bytes())}  {name}\n" for name in names)
    result = ra.write(assets)
    written = (assets / "SHA256SUMS").read_text()
    assert written == expected == result["sums"] and result["version"] == V and result["tag"] == TAG
    assert names == sorted(["install.sh", "memory_os_bootstrap.py", f"memory-os-ai-{V}.p9-manifest.json",
                            f"memory_os_ai-{V}-py3-none-any.whl", f"memory_os_ai-{V}.tar.gz"])
    assert [line.split("  ")[1] for line in written.splitlines()] == names       # sorted, one line per asset
    assert "SHA256SUMS" not in written and written.count(" install.sh\n") == 1   # never itself; install.sh in it
    assert written.count(" memory_os_bootstrap.py\n") == 1
    assert all(re.fullmatch(r"[0-9a-f]{64}  \S+", line) for line in written.splitlines())
    with pytest.raises(ra.AssetError, match="already present"):        # never overwritten
        ra.write(assets)


def test_check_writes_nothing_and_main_reports(tmp_path, capsys):
    assets = tmp_path / "assets"
    assemble(assets)
    before = sorted(p.name for p in assets.iterdir())
    assert ra.main([str(assets), "--check"]) == 0 and sorted(p.name for p in assets.iterdir()) == before
    out = capsys.readouterr()
    assert out.out == ra.check(assets)["sums"] and "nothing written" in out.err
    assert ra.main([str(assets)]) == 0 and (assets / "SHA256SUMS").read_text() == out.out


@pytest.mark.parametrize("tag", ["v4.1.2", "v4.1.30", "4.1.3", "latest", ""])
def test_a_release_tag_other_than_the_version_is_refused(tmp_path, tag):
    assets = tmp_path / "assets"
    assert tag != TAG
    assemble(assets, tag=tag)
    with pytest.raises(ra.AssetError, match="RELEASE_TAG"):
        ra.write(assets)
    assert not (assets / "SHA256SUMS").exists()
    assert ra.main([str(assets)]) == 1 and not (assets / "SHA256SUMS").exists()


def test_two_release_tag_lines_are_refused(tmp_path):
    assets = tmp_path / "assets"
    assemble(assets)
    script = assets / "install.sh"
    script.write_text(script.read_text().replace(f'  RELEASE_TAG="{TAG}"\n', f'  RELEASE_TAG="{TAG}"\n  RELEASE_TAG="v0.0.1"\n'))
    with pytest.raises(ra.AssetError, match="exactly one RELEASE_TAG"):
        ra.check(assets)


@pytest.mark.parametrize("missing", ["install.sh", "memory_os_bootstrap.py", "p9", "sdist", "wheel"])
def test_a_missing_asset_is_refused(tmp_path, missing):
    assets = tmp_path / "assets"
    assemble(assets)
    name = {"p9": f"memory-os-ai-{V}.p9-manifest.json", "sdist": f"memory_os_ai-{V}.tar.gz",
            "wheel": f"memory_os_ai-{V}-py3-none-any.whl"}.get(missing, missing)
    (assets / name).unlink()
    with pytest.raises(ra.AssetError, match="missing|exactly one"):
        ra.write(assets)
    assert not (assets / "SHA256SUMS").exists()


@pytest.mark.parametrize("extra,words", [
    ("notes.md", "extra"), (".DS_Store", "extra"), ("INSTALL.SH.bak", "extra"),
    ("PROOF-COMPLEMENT-SHA256SUMS", "extra"),
    ("memory_os_ai-4.1.2-py3-none-any.whl", "exactly one wheel"),
    ("memory_os_ai-4.1.2.tar.gz", "exactly one sdist"),
    ("memory-os-ai-4.1.2.p9-manifest.json", "exactly one P9 manifest"),
])
def test_an_extra_or_duplicated_asset_is_refused(tmp_path, extra, words):
    assets = tmp_path / "assets"
    assemble(assets)
    (assets / extra).write_bytes(b"x")
    with pytest.raises(ra.AssetError, match=words):
        ra.write(assets)
    assert not (assets / "SHA256SUMS").exists()


def test_a_link_or_a_directory_is_refused(tmp_path):
    assets = tmp_path / "assets"
    assemble(assets)
    outside = tmp_path / "outside.sh"
    outside.write_text((assets / "install.sh").read_text())
    (assets / "install.sh").unlink()
    (assets / "install.sh").symlink_to(outside)
    with pytest.raises(ra.AssetError, match="not a regular file"):
        ra.check(assets)
    (assets / "install.sh").unlink()
    (assets / "install.sh").mkdir()
    with pytest.raises(ra.AssetError, match="not a regular file"):
        ra.check(assets)


def test_the_wheel_and_the_sdist_must_name_the_same_version(tmp_path):
    assets = tmp_path / "assets"
    assemble(assets)
    (assets / f"memory_os_ai-{V}.tar.gz").rename(assets / "memory_os_ai-9.9.9.tar.gz")
    with pytest.raises(ra.AssetError, match="different versions"):
        ra.check(assets)


@pytest.mark.parametrize("change,words", [
    (lambda pin: pin.update(state="test"), "not a release pin"),
    (lambda pin: pin.update(version="9.9.9"), "not a release pin"),
    (lambda pin: pin["wheel"].update(sha256="0" * 64), "pinned wheel is not"),
    (lambda pin: pin["wheel"].update(size=pin["wheel"]["size"] + 1), "pinned wheel is not"),
    (lambda pin: pin["wheel"].update(filename="memory_os_ai-9.9.9-py3-none-any.whl"), "pinned wheel is not"),
])
def test_a_bootstrap_not_pinned_to_this_wheel_is_refused(tmp_path, change, words):
    assets = tmp_path / "assets"
    pin = assemble(assets)
    change(pin)
    text = (ROOT / "scripts" / "memory_os_bootstrap.py").read_text()
    start, end = text.index(bs.PIN_BEGIN + "\nRELEASE_PIN"), text.index("\n" + bs.PIN_END + "\n")
    (assets / "memory_os_bootstrap.py").write_text(text[:start] + bs.render_pin_block(pin) + text[end + len(bs.PIN_END) + 1:])
    with pytest.raises(ra.AssetError, match=words):
        ra.check(assets)


def test_the_template_bootstrap_of_the_repository_is_refused(tmp_path):
    assets = tmp_path / "assets"
    assemble(assets)
    (assets / "memory_os_bootstrap.py").write_text((ROOT / "scripts" / "memory_os_bootstrap.py").read_text())
    with pytest.raises(ra.AssetError, match="not a release pin"):
        ra.check(assets)


@pytest.mark.parametrize("manifest", [
    {"schema_version": 1, "kind": "memory-os-ai-p9-manifest", "artifact": {"sha256": "0" * 64}, "cells": []},
    {"schema_version": 2, "kind": "memory-os-ai-p9-manifest", "artifact": {"sha256": None}, "cells": []},
    [],
])
def test_a_p9_manifest_that_does_not_name_the_wheel_is_refused(tmp_path, manifest):
    assets = tmp_path / "assets"
    assemble(assets)
    (assets / f"memory-os-ai-{V}.p9-manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ra.AssetError, match="not a P9 manifest"):
        ra.check(assets)


def test_install_sh_accepts_the_produced_sha256sums(tmp_path):
    """The checksum step of install.sh (exactly one line for memory_os_bootstrap.py, its SHA-256) accepts the
    file the helper writes, and the verified bootstrap is run."""
    assets = tmp_path / "assets"
    assemble(assets, bootstrap=MARKER_BOOTSTRAP)      # a bootstrap that records its run, with a release pin block
    ra.write(assets)
    sums = (assets / "SHA256SUMS").read_text()
    bootstrap = (assets / "memory_os_bootstrap.py").read_bytes()
    r = Release(tmp_path, uname="Darwin", cell="retained")
    r.publish(bootstrap, sums=sums)
    done = r.run()
    assert done.returncode == 0, done.stderr
    assert f"memory_os_bootstrap.py verified: SHA-256 {sha(bootstrap)}" in done.stdout
    assert r.ran()["argv"] == ["--interactive", "--uv", str(r.machine.uv)]

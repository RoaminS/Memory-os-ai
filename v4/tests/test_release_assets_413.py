"""4.1.3 freeze (Q1 of decision D13): the release tag and the release assets.

- ``scripts/install.sh`` is pinned to ``v`` + the version of both ``pyproject.toml`` files, and every one-command URL
  of the README and of the release notes names that same tag;
- ``scripts/release_assets_413.py`` refuses a directory that is not exactly the five files of one version, judges the
  bootstrap pin and the P9 manifest with the readers of the bootstrap TEMPLATE (``load_pin``, ``proof_state``: PASS
  for every cell and profile of the pin), never executes the bootstrap ASSET, and writes a ``SHA256SUMS`` covering
  the five files (``install.sh`` and ``memory_os_bootstrap.py`` included, never itself: six assets with it), which the
  checksum step of ``install.sh`` accepts. Temporary directories only; no real asset is touched.
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


def test_the_helper_judges_with_the_template_of_this_repository():
    assert ra.TEMPLATE == ROOT / "scripts" / "memory_os_bootstrap.py"
    template = ra.load_template()
    assert template is not bs and (template.PIN_BEGIN, template.PIN_END) == (bs.PIN_BEGIN, bs.PIN_END)
    assert template.load_pin()["code"] == "release_pin_unfilled"          # the repository copy is the template


# ---------------------------------------------------------------- the asset helper

def make_wheel(directory: Path, version: str = V) -> Path:
    path = directory / f"memory_os_ai-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("memory_os_v4/__init__.py", f'__version__ = "{version}"\n')
        archive.writestr(f"memory_os_ai-{version}.dist-info/RECORD", "")
    return path


CELLS = (f"macos-arm64-py{sys.version_info[0]}{sys.version_info[1]}", "ubuntu2404-x86_64-py311")


def release_pin(wheel: Path, version: str = V) -> dict:
    """A release pin of ``wheel`` (locks fingerprinted by the template's ``lock_fingerprint``), valid for load_pin."""
    pin = make_pin(wheel, cells=CELLS, profiles=("lexical", "semantic"), state="release")
    pin["version"] = version
    assert bs.load_pin(json.dumps(pin))["ok"]
    return pin


def pairs(pin: dict) -> list:
    return sorted((cell_id, profile) for cell_id, profiles in pin["cells"].items() for profile in profiles)


def p9_manifest(pin: dict, state: str = "PASS") -> dict:
    """A P9 manifest of the pinned wheel: one entry per (cell, profile), lock_sha256 = the pin's fingerprint."""
    return {"schema_version": 1, "kind": bs.P9_KIND, "artifact": {"sha256": pin["wheel"]["sha256"]},
            "cells": [{"cell_id": cell_id, "profile": profile, "state": state, "means": "synthetic test entry",
                       "lock_sha256": pin["cells"][cell_id][profile]["lock_sha256"]} for cell_id, profile in pairs(pin)]}


def put_pin(directory: Path, pin: dict) -> None:
    """The template text with ``pin`` spliced in as is (no validation: for the refusal cases)."""
    text = (ROOT / "scripts" / "memory_os_bootstrap.py").read_text()
    start, end = text.index(bs.PIN_BEGIN + "\nRELEASE_PIN"), text.index("\n" + bs.PIN_END + "\n")
    (directory / "memory_os_bootstrap.py").write_text(text[:start] + bs.render_pin_block(pin)
                                                      + text[end + len(bs.PIN_END) + 1:])


def assemble(directory: Path, *, version: str = V, tag: str | None = None, bootstrap: str | None = None) -> dict:
    """The five files of ``version`` (SHA256SUMS not yet written; six assets with it): the real bootstrap with a
    filled release pin, and a P9 manifest that proves every cell and profile of that pin."""
    directory.mkdir()
    wheel = make_wheel(directory, version)
    (directory / f"memory_os_ai-{version}.tar.gz").write_bytes(b"sdist of " + version.encode())
    pin = release_pin(wheel, version)
    text = bs.fill_pin((ROOT / "scripts" / "memory_os_bootstrap.py").read_text(), pin) if bootstrap is None \
        else bootstrap + "\n" + bs.render_pin_block(pin) + "\n"
    (directory / "memory_os_bootstrap.py").write_text(text)
    (directory / f"memory-os-ai-{version}.p9-manifest.json").write_text(json.dumps(p9_manifest(pin)))
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
    assert [(p["cell_id"], p["profile"], p["state"]) for p in result["proofs"]] == \
        [(cell_id, profile, "PASS") for cell_id, profile in pairs(json.loads(ra.pin_text(
            (assets / "memory_os_bootstrap.py").read_text(), bs)))] and len(result["proofs"]) >= 3
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


REFUSED_PIN = "the bootstrap refuses its own pin"


@pytest.mark.parametrize("change,words", [
    (lambda pin: pin.update(state="test"), "not a release pin of .*state 'test'"),
    (lambda pin: pin.update(state="template"), REFUSED_PIN + ": release_pin_unfilled"),
    (lambda pin: pin.update(version="9.9.9"), REFUSED_PIN),
    (lambda pin: pin.update(cells="invalide"), REFUSED_PIN + ": release_pin_invalid"),
    (lambda pin: pin.update(cells={"debian-12-arm64-py311": {}}), "names no cell and profile"),
    (lambda pin: pin.update(cells={}), "names no cell and profile"),
    (lambda pin: pin["cells"][CELLS[1]].update(lexical={"constraints": [], "artifacts": []}), REFUSED_PIN),
    (lambda pin: pin["cells"][CELLS[1]]["lexical"].update(lock_sha256="0" * 64), REFUSED_PIN),
    (lambda pin: pin["wheel"].pop("content_sha256"), REFUSED_PIN + ": release_pin_invalid"),
    (lambda pin: pin.update(schema_version=2), REFUSED_PIN),
    (lambda pin: pin["wheel"].update(sha256="0" * 64), "pinned wheel is not"),
    (lambda pin: pin["wheel"].update(size=pin["wheel"]["size"] + 1), "pinned wheel is not"),
    (lambda pin: pin["wheel"].update(filename=f"memory_os_ai-{V}-py3-none-other.whl"), "pinned wheel is not"),
])
def test_a_bootstrap_pin_that_the_template_refuses_or_that_pins_another_wheel_is_refused(tmp_path, change, words):
    assets = tmp_path / "assets"
    pin = assemble(assets)
    change(pin)
    put_pin(assets, pin)
    with pytest.raises(ra.AssetError, match=words):
        ra.check(assets)
    assert ra.main([str(assets)]) == 1 and not (assets / "SHA256SUMS").exists()


def test_the_template_bootstrap_of_the_repository_is_refused(tmp_path):
    assets = tmp_path / "assets"
    assemble(assets)
    (assets / "memory_os_bootstrap.py").write_text((ROOT / "scripts" / "memory_os_bootstrap.py").read_text())
    with pytest.raises(ra.AssetError, match=REFUSED_PIN + ": release_pin_unfilled"):
        ra.check(assets)


def test_a_bootstrap_without_exactly_one_pin_block_is_refused(tmp_path):
    assets = tmp_path / "assets"
    assemble(assets)
    text = (assets / "memory_os_bootstrap.py").read_text()
    (assets / "memory_os_bootstrap.py").write_text(text + text[text.index(bs.PIN_BEGIN):])
    with pytest.raises(ra.AssetError, match="not found exactly once"):
        ra.check(assets)


def test_the_bootstrap_asset_is_read_as_text_and_never_run(tmp_path):
    witness = tmp_path / "asset-ran"
    assets = tmp_path / "assets"
    assemble(assets, bootstrap=f"open({str(witness)!r}, 'w').close()\nraise SystemExit(3)\n")
    assert ra.write(assets)["version"] == V and not witness.exists()


@pytest.mark.parametrize("change,reason", [
    (lambda m, pin: m.pop("cells"), "manifest_malformed"),
    (lambda m, pin: m.update(cells=None), "manifest_malformed"),
    (lambda m, pin: m.update(cells=[{"cell_id": 1}]), "manifest_malformed"),
    (lambda m, pin: m.update(schema_version=True), "manifest_unreadable"),
    (lambda m, pin: m.update(schema_version=2), "manifest_unreadable"),
    (lambda m, pin: m.update(kind="other"), "manifest_unreadable"),
    (lambda m, pin: m.update(artifact={"sha256": "0" * 64}), "artifact_mismatch"),
    (lambda m, pin: m.pop("artifact"), "manifest_malformed"),
    (lambda m, pin: m.update(cells=[e for e in m["cells"] if e["cell_id"] != CELLS[1]]), "cell_absent_from_manifest"),
    (lambda m, pin: m["cells"][-1].update(lock_sha256="0" * 64), "lock_mismatch"),
    (lambda m, pin: m["cells"][-1].update(state="FAIL"), "manifest_matches_artifact_cell_and_lock"),
    (lambda m, pin: m["cells"][-1].update(state="UNKNOWN"), "state_not_proven"),
    (lambda m, pin: m["cells"].append({**m["cells"][-1], "state": "FAIL"}), "manifest_ambiguous"),
])
def test_a_p9_manifest_that_does_not_prove_every_cell_and_profile_is_refused(tmp_path, change, reason):
    """Judged by the template's proof_state: PASS with manifest_matches_artifact_cell_and_lock, or refused."""
    assets = tmp_path / "assets"
    pin = assemble(assets)
    manifest = p9_manifest(pin)
    change(manifest, pin)
    (assets / f"memory-os-ai-{V}.p9-manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ra.AssetError, match=r"is not proven for this wheel and lock .*reason " + reason):
        ra.check(assets)
    assert ra.main([str(assets)]) == 1 and not (assets / "SHA256SUMS").exists()


@pytest.mark.parametrize("content", [b"not json", b"[]", b"\xff\xfe"])
def test_an_unreadable_p9_manifest_is_refused(tmp_path, content):
    assets = tmp_path / "assets"
    assemble(assets)
    (assets / f"memory-os-ai-{V}.p9-manifest.json").write_bytes(content)
    with pytest.raises(ra.AssetError, match="manifest_unreadable"):
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

"""CONTRAT_4_1_1 §5, §6, §9 (lot L5): per-cell lexical locks/manifests and OS-specific client paths.

No network, no real service managers, disposable HOME only.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib
from types import SimpleNamespace

import pytest

from memory_os_v4.ops import cell, cli, clients, doctor, qualified_runtime, quickstart as qs, uninstall
from setup_fixture_410 import install, prepare, setup_context, tree

ROOT = Path(__file__).resolve().parents[2]
RESOURCES = qualified_runtime.PROFILE.parent
UBUNTU = 'ID=ubuntu\nVERSION_ID="24.04"\n'
RETAINED_CELLS = ("macos-arm64-py311", "macos-arm64-py312", "macos-arm64-py313", "ubuntu2404-arm64-py311",
                  "ubuntu2404-x86_64-py311")
# 4.1.3: VS Code is supported on Linux through the Copilot user configuration (~/.copilot/mcp-config.json)
LINUX_SUPPORTED = ("claude-code", "codex", "vscode", "cursor", "antigravity", "gemini-cli", "grok", "zed", "devin")
LINUX_UNSUPPORTED = ("windsurf", "claude-desktop")


def mac_cell(py=(3, 11, 14)) -> dict:
    return cell.detect(
        sys_platform="darwin", machine="arm64", python_version=py, implementation="CPython",
        sqlite_version="3.53.4", os_release=None, kernel_release=None, environ={},
        which=lambda name: None, exists=lambda p: str(p) == "/bin/launchctl",
    )


def linux_cell(**overrides) -> dict:
    facts = {
        "sys_platform": "linux", "machine": "aarch64", "python_version": (3, 11, 18),
        "implementation": "CPython", "sqlite_version": "3.53.1", "os_release": UBUNTU,
        "kernel_release": "6.8.0-88-generic\n", "environ": {"XDG_RUNTIME_DIR": "/run/user/1000"},
        "which": lambda name: "/usr/bin/" + name,
        "exists": lambda p: str(p) in {"/run/systemd/system", "/run/user/1000/systemd"},
    }
    facts.update(overrides)
    return cell.detect(**facts)


CELL_REPORTS = {
    "macos-arm64-py311": lambda: mac_cell((3, 11, 14)),
    "macos-arm64-py312": lambda: mac_cell((3, 12, 16)),
    "macos-arm64-py313": lambda: mac_cell((3, 13, 18)),
    "ubuntu2404-arm64-py311": linux_cell,
    "ubuntu2404-x86_64-py311": lambda: linux_cell(machine="x86_64"),
}


def load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"test_l5_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def synthetic_wheel(name: str, version: str, tags: str = "py3-none-any") -> dict:
    """One synthetic admitted wheel ``{filename, sha256, size, url}`` (frozen manifest schema)."""
    filename = f"{name.replace('-', '_')}-{version}-{tags}.whl"
    return {
        "filename": filename,
        "sha256": hashlib.sha256(f"{name}:{version}:{tags}".encode()).hexdigest(),
        "size": 1024,
        "url": f"https://files.pythonhosted.org/packages/ab/cd/{filename}",
    }


def synthetic_frozen_texts(expected: dict[str, str]) -> tuple[str, str]:
    """Synthetic ``(<cell>.txt, pylock.<cell>.toml)`` texts for one package/version set (no network)."""
    compiled, packages = [], []
    for name, version in sorted(expected.items()):
        wheel = synthetic_wheel(name, version)
        compiled.append(f"{name}=={version} \\\n    --hash=sha256:{wheel['sha256']}")
        packages.append(
            f'[[packages]]\nname = "{name}"\nversion = "{version}"\n'
            f'wheels = [{{ url = "{wheel["url"]}", upload-time = 2026-01-01T00:00:00Z, '
            f'size = {wheel["size"]}, hashes = {{ sha256 = "{wheel["sha256"]}" }} }}]\n'
        )
    header = 'lock-version = "1.0"\ncreated-by = "test"\nrequires-python = ">=3.11"\n\n'
    return "\n".join(compiled) + "\n", header + "\n".join(packages)


def write_synthetic_frozen_inputs(gen, in_dir: Path, expected: dict[str, str]) -> dict:
    compiled, pylock = synthetic_frozen_texts(expected)
    in_dir.mkdir()
    inputs = gen.frozen_input_files(in_dir)
    for txt_path, pylock_path in inputs.values():
        txt_path.write_text(compiled, encoding="utf-8")
        pylock_path.write_text(pylock, encoding="utf-8")
    return inputs


@pytest.fixture
def c(tmp_path, monkeypatch):
    return setup_context(tmp_path, monkeypatch)


@pytest.fixture
def ubuntu(c, monkeypatch):
    report = linux_cell()
    monkeypatch.setattr(cell, "detect", lambda **kwargs: dict(report))
    return c


# ===================================================================== 1. Per-cell locks & packaging

def test_all_embedded_cell_locks_are_flat_under_resources_and_match_package_data():
    pkg_data = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["setuptools"]["package-data"]
    patterns = set(pkg_data["memory_os_v4"])
    assert {"resources/*.json", "resources/*.txt"} <= patterns
    assert (RESOURCES / "qualified_runtime_constraints_lexical_v1.txt").is_file()
    for (profile, cell_id), (cat, con, man) in qualified_runtime.CELL_LOCKS.items():
        for path in (cat, con, *((man,) if man is not None else ())):
            assert path.parent == RESOURCES, f"non-flat resource {path} would be missed by non-recursive package-data"
            assert path.suffix in {".json", ".txt"} and path.is_file() and not path.is_symlink()
        lock = qualified_runtime.cell_lock(cell_id, profile=profile)
        assert lock is not None and lock["cell_id"] == cell_id and lock["profile"] == profile
        assert len(lock["catalog_sha256"]) == 64 and len(lock["constraints_sha256"]) == 64
        if profile == "lexical":
            assert man is not None and len(lock["manifest_sha256"]) == 64


# SHA-256 of the frozen ``pylock.<cell>.toml`` inputs (tools/411/locks-figes-20261124/LISEZMOI.md).
FROZEN_PYLOCK_SHA256 = {
    "macos-arm64-py311": "99426580511687bafb85268f3917b0ba2b3c5e56ed304144ba1db8b88ce10fa6",
    "macos-arm64-py312": "8c86454108dc054678b17a0eec7f48db20abf2214dc63b1519a78c5cd7151a85",
    "macos-arm64-py313": "8258c4649414795df0e0132ea8d71808b3cd767f7ad5c7c213daa9561ebc96c2",
    "ubuntu2404-arm64-py311": "2d005f03c54bf251d67edad36939f592ed0331e27a1ce5cae2b4c0a08bde1d30",
    # GitHub Actions run 45879240439, job resolve-lock-x86_64 (SHA256SUMS of the artifact).
    "ubuntu2404-x86_64-py311": "a45cc341146d5c576fe6646d4b59a3f78ca85aed2415d738e15a8fa5fce02810",
}
FROZEN_RESOLVED_AT = {"ubuntu2404-x86_64-py311": "2026-11-26"}  # the other cells: 2026-11-24
FROZEN_WHEEL_COUNTS = {
    "macos-arm64-py311": 39, "macos-arm64-py312": 39, "macos-arm64-py313": 39, "ubuntu2404-arm64-py311": 40,
    "ubuntu2404-x86_64-py311": 40,
}
_MAC_MULTI = {"charset-normalizer", "cryptography", "regex"}
FROZEN_MULTI_WHEEL_PACKAGES = {
    "macos-arm64-py311": _MAC_MULTI, "macos-arm64-py312": _MAC_MULTI, "macos-arm64-py313": _MAC_MULTI,
    "ubuntu2404-arm64-py311": {"charset-normalizer", "cryptography"},
    "ubuntu2404-x86_64-py311": {"charset-normalizer", "cryptography"},
}


@pytest.mark.parametrize("cell_id", RETAINED_CELLS)
def test_each_retained_cell_lexical_lock_attests_catalog_constraints_manifest_and_tiktoken(cell_id):
    lock = qualified_runtime.cell_lock(cell_id, profile="lexical")
    assert lock is not None and lock["profile_id"] == f"lexical-{cell_id}-v1"
    verdict = cell.acceptance(CELL_REPORTS[cell_id](), profile="lexical")
    assert verdict["ok"] is True and verdict["lock"]["profile_id"] == f"lexical-{cell_id}-v1"
    cat = qualified_runtime.catalog(profile="lexical", cell_id=cell_id)
    assert cat["profile_id"] == f"lexical-{cell_id}-v1"
    expected = qualified_runtime.expected_versions(profile="lexical", extras=("ops", "brief"), cell_id=cell_id)
    assert len(expected) == 36 and expected["tiktoken"] == "0.12.0"
    assert not (set(expected) & qualified_runtime.ml_closure(extras=("ops", "brief")))
    lines = Path(lock["constraints"]).read_text(encoding="utf-8").splitlines()
    assert qualified_runtime.check_constraints(lines, profile="lexical", extras=("ops", "brief"), cell_id=cell_id) == {
        "ok": True, "mismatches": {}
    }
    manifest = json.loads(Path(lock["manifest"]).read_text(encoding="utf-8"))
    assert manifest["cell_id"] == cell_id and manifest["profile"] == "lexical"
    assert manifest["profile_id"] == cat["profile_id"]
    assert manifest["constraints_sha256"] == lock["constraints_sha256"]
    assert manifest["schema_version"] == 2 and manifest["lock_state"] == "frozen"
    assert manifest["resolved_at"] == FROZEN_RESOLVED_AT.get(cell_id, "2026-11-24")
    assert manifest["exclude_newer"] == "2026-11-24T00:00:00Z"
    assert manifest["pylock_sha256"] == FROZEN_PYLOCK_SHA256[cell_id]
    assert "urls_status" not in manifest and "uv pip compile" in manifest["source"]
    assert len(manifest["artifacts"]) == 36
    wheels = [(item, wheel) for item in manifest["artifacts"] for wheel in item["wheels"]]
    assert len(wheels) == FROZEN_WHEEL_COUNTS[cell_id]
    multi = {item["name"] for item in manifest["artifacts"] if len(item["wheels"]) > 1}
    assert multi == FROZEN_MULTI_WHEEL_PACKAGES[cell_id]
    for item, wheel in wheels:
        assert qualified_runtime.wheel_compatible_with_cell(wheel["filename"], cell_id) is True
        others = [other for other in RETAINED_CELLS if other != cell_id
                  and qualified_runtime.wheel_compatible_with_cell(wheel["filename"], other)]
        # A platform wheel of a Linux cell fits that cell only (never the other architecture).
        if not wheel["filename"].endswith("-none-any.whl") and cell_id.startswith("ubuntu2404-"):
            assert others == [], (wheel["filename"], others)
        assert set(wheel) == {"filename", "url", "sha256", "size"}
        assert qualified_runtime.valid_artifact_url(wheel["url"], name=item["name"], version=item["version"]) is True
        assert wheel["url"].endswith("/" + wheel["filename"]) and wheel["size"] > 0 and len(wheel["sha256"]) == 64
    assert all(item["wheels"] == sorted(item["wheels"], key=lambda w: w["filename"]) for item in manifest["artifacts"])
    catalog_doc = json.loads(Path(lock["catalog"]).read_text(encoding="utf-8"))
    assert catalog_doc.get("lock_state", "frozen") == "frozen"   # cell 1 keeps its historical catalog


def test_anchored_digests_refuse_tampered_manifest_hashes_and_byte_drift(tmp_path, monkeypatch):
    """Codex L5-r1 counter-example #1: replacing an artifact sha256 with '0'*64 MUST be refused."""
    cell_id = "ubuntu2404-arm64-py311"
    src_cat, src_con, src_man = qualified_runtime.CELL_LOCKS[("lexical", cell_id)]
    cat, con, man = tmp_path / src_cat.name, tmp_path / src_con.name, tmp_path / src_man.name
    shutil.copy2(src_cat, cat)
    shutil.copy2(src_con, con)
    shutil.copy2(src_man, man)
    monkeypatch.setattr(
        qualified_runtime, "CELL_LOCKS",
        {**qualified_runtime.CELL_LOCKS, ("lexical", cell_id): (cat, con, man)},
    )
    assert qualified_runtime.cell_lock(cell_id, profile="lexical") is not None

    # 1. Replace an artifact's sha256 with syntactically valid zeros: refused by anchored digest.
    mdoc = json.loads(man.read_text(encoding="utf-8"))
    mdoc["artifacts"][0]["wheels"][0]["sha256"] = "0" * 64
    man.write_text(json.dumps(mdoc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    assert qualified_runtime.cell_lock(cell_id, profile="lexical") is None
    with pytest.raises(ValueError):
        qualified_runtime.catalog(profile="lexical", cell_id=cell_id)
    assert qualified_runtime.inspect_current(profile="lexical", extras=("ops", "brief"), cell_id=cell_id)["state"] == "lock_unavailable"

    # 2. Restore manifest; append a harmless comment line to constraints -> refused by anchored digest.
    shutil.copy2(src_man, man)
    assert qualified_runtime.cell_lock(cell_id, profile="lexical") is not None
    con.write_text(con.read_text(encoding="utf-8") + "# tampered comment\n", encoding="utf-8")
    assert qualified_runtime.cell_lock(cell_id, profile="lexical") is None

    # 3. Restore constraints; alter whitespace in catalog JSON -> refused by anchored digest.
    shutil.copy2(src_con, con)
    cat.write_text(cat.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert qualified_runtime.cell_lock(cell_id, profile="lexical") is None

    # 4. Missing digest anchor entry -> refused even when files on disk are authentic.
    shutil.copy2(src_cat, cat)
    monkeypatch.setattr(
        qualified_runtime, "CELL_LOCK_DIGESTS",
        {k: v for k, v in qualified_runtime.CELL_LOCK_DIGESTS.items() if k != ("lexical", cell_id)},
    )
    assert qualified_runtime.cell_lock(cell_id, profile="lexical") is None


def test_exact_profile_id_refuses_substring_impostors_even_with_matching_digests(tmp_path, monkeypatch):
    """Codex L5-r1 counter-example #2: substring-matching impostor profile_id MUST be refused."""
    cases = [
        ("semantic", "macos-arm64-py311", "granite-macos-arm64-py312-macos-arm64-py311-imposteur"),
        ("lexical", "macos-arm64-py311", "lexical-macos-arm64-py312-macos-arm64-py311-imposteur"),
        ("lexical", "ubuntu2404-arm64-py311", "lexical-macos-arm64-py312-ubuntu2404-arm64-py311-imposteur"),
    ]
    for idx, (profile, cell_id, impostor) in enumerate(cases):
        sub = tmp_path / f"case_{idx}"
        sub.mkdir()
        src_cat, src_con, src_man = qualified_runtime.CELL_LOCKS[(profile, cell_id)]
        cat, con = sub / src_cat.name, sub / src_con.name
        man = None if src_man is None else sub / src_man.name
        doc = json.loads(src_cat.read_text(encoding="utf-8"))
        doc["profile_id"] = impostor
        cat.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        shutil.copy2(src_con, con)
        if man is not None and src_man is not None:
            mdoc = json.loads(src_man.read_text(encoding="utf-8"))
            mdoc["profile_id"] = impostor
            man.write_text(json.dumps(mdoc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        digests = (
            hashlib.sha256(cat.read_bytes()).hexdigest(),
            hashlib.sha256(con.read_bytes()).hexdigest(),
            None if man is None else hashlib.sha256(man.read_bytes()).hexdigest(),
        )
        monkeypatch.setattr(qualified_runtime, "CELL_LOCKS", {**qualified_runtime.CELL_LOCKS, (profile, cell_id): (cat, con, man)})
        monkeypatch.setattr(qualified_runtime, "CELL_LOCK_DIGESTS", {**qualified_runtime.CELL_LOCK_DIGESTS, (profile, cell_id): digests})
        assert qualified_runtime.cell_lock(cell_id, profile=profile) is None


def test_symlink_parent_dir_and_hardlinks_are_refused(tmp_path, monkeypatch):
    """Codex L5-r1 finding #2: symlink parent directory and hardlinks (st_nlink > 1) MUST be refused."""
    cell_id = "ubuntu2404-arm64-py311"
    src_cat, src_con, src_man = qualified_runtime.CELL_LOCKS[("lexical", cell_id)]
    real_dir = tmp_path / "real_dir"
    real_dir.mkdir()
    cat, con, man = real_dir / src_cat.name, real_dir / src_con.name, real_dir / src_man.name
    shutil.copy2(src_cat, cat)
    shutil.copy2(src_con, con)
    shutil.copy2(src_man, man)

    # Symlink parent directory pointing to authentic files with valid anchored digests -> refused.
    link_dir = tmp_path / "link_dir"
    link_dir.symlink_to(real_dir)
    monkeypatch.setattr(
        qualified_runtime, "CELL_LOCKS",
        {**qualified_runtime.CELL_LOCKS, ("lexical", cell_id): (link_dir / cat.name, link_dir / con.name, link_dir / man.name)},
    )
    assert qualified_runtime.cell_lock(cell_id, profile="lexical") is None

    # Real parent directory -> accepted.
    monkeypatch.setattr(
        qualified_runtime, "CELL_LOCKS",
        {**qualified_runtime.CELL_LOCKS, ("lexical", cell_id): (cat, con, man)},
    )
    assert qualified_runtime.cell_lock(cell_id, profile="lexical") is not None

    # Hardlink (st_nlink == 2) on each of catalog, constraints, manifest -> refused.
    for target in (cat, con, man):
        alias = tmp_path / f"hardlink_{target.name}"
        os.link(target, alias)
        try:
            assert target.stat().st_nlink == 2
            assert qualified_runtime.cell_lock(cell_id, profile="lexical") is None
        finally:
            alias.unlink()
    assert qualified_runtime.cell_lock(cell_id, profile="lexical") is not None

    # memory_os_constraints also refuses symlink parent and hardlinked constraints file.
    import memory_os_constraints as moc
    assert moc._is_plain_regular_file(con) is True
    assert moc._is_plain_regular_file(link_dir / con.name) is False
    alias = tmp_path / "con_hardlink.txt"
    os.link(con, alias)
    try:
        assert moc._is_plain_regular_file(con) is False
    finally:
        alias.unlink()


def test_manifest_url_schema_enforces_step3_frozen_urls_and_refuses_malformed_urls(tmp_path, monkeypatch):
    """Contract §6 + L5-r1 #4, step 3: every admitted wheel carries a strict https PyPI ``.whl`` URL."""
    gen = load_script("generate_cell_locks_411")
    cell_id = "ubuntu2404-arm64-py311"
    expected = qualified_runtime.expected_versions(profile="lexical", extras=("ops", "brief"), cell_id=cell_id)
    src_cat, src_con, src_man = qualified_runtime.CELL_LOCKS[("lexical", cell_id)]
    cat, con, man = tmp_path / src_cat.name, tmp_path / src_con.name, tmp_path / src_man.name
    shutil.copy2(src_cat, cat)
    shutil.copy2(src_con, con)
    shutil.copy2(src_man, man)

    def bind():
        digests = (
            hashlib.sha256(cat.read_bytes()).hexdigest(),
            hashlib.sha256(con.read_bytes()).hexdigest(),
            hashlib.sha256(man.read_bytes()).hexdigest(),
        )
        monkeypatch.setattr(qualified_runtime, "CELL_LOCKS", {**qualified_runtime.CELL_LOCKS, ("lexical", cell_id): (cat, con, man)})
        monkeypatch.setattr(qualified_runtime, "CELL_LOCK_DIGESTS", {**qualified_runtime.CELL_LOCK_DIGESTS, ("lexical", cell_id): digests})

    def refused(mdoc) -> bool:
        man.write_text(json.dumps(mdoc), encoding="utf-8")
        bind()
        return qualified_runtime.cell_lock(cell_id, profile="lexical") is None

    # 0. The untouched frozen manifest is accepted once re-bound.
    assert refused(json.loads(src_man.read_text(encoding="utf-8"))) is False

    # 1. Missing 'url' key -> refused even with synced digests.
    mdoc = json.loads(src_man.read_text(encoding="utf-8"))
    del mdoc["artifacts"][0]["wheels"][0]["url"]
    assert refused(mdoc)

    # 2. Null URL (the preliminary placeholder) -> refused.
    mdoc = json.loads(src_man.read_text(encoding="utf-8"))
    mdoc["artifacts"][0]["wheels"][0]["url"] = None
    assert refused(mdoc)

    # 3. The preliminary L5 manifest (schema 1, bare SHA-256 lists, url null) is no longer accepted.
    mdoc = json.loads(src_man.read_text(encoding="utf-8"))
    preliminary = {
        "artifacts": [{"name": item["name"], "version": item["version"], "url": None,
                       "sha256": sorted(w["sha256"] for w in item["wheels"])} for item in mdoc["artifacts"]],
        "cell_id": cell_id, "constraints_sha256": mdoc["constraints_sha256"], "lock_state": "preliminary",
        "profile": "lexical", "profile_id": mdoc["profile_id"], "schema_version": 1,
        "source": "preliminary", "urls_status": "to_fill_at_step_3",
    }
    assert refused(preliminary)
    assert refused({**preliminary, "lock_state": "frozen", "urls_status": "complete"})

    # 4. Malformed / non-PyPI / non-wheel URLs -> refused (incl. https:///x).
    for bad_url in (
        "https:///x",
        "https://files.pythonhosted.org/",
        "https://files.pythonhosted.org/packages/x",
        "https://files.pythonhosted.org/packages/annotated_types-0.7.0.tar.gz",
        "https://evil.example/packages/annotated_types-0.7.0-py3-none-any.whl",
        "http://files.pythonhosted.org/packages/annotated_types-0.7.0-py3-none-any.whl",
        "https://user@files.pythonhosted.org/packages/annotated_types-0.7.0-py3-none-any.whl",
        "https://files.pythonhosted.org:443/packages/annotated_types-0.7.0-py3-none-any.whl",
        "https://files.pythonhosted.org:x/packages/annotated_types-0.7.0-py3-none-any.whl",
        "https://files.pythonhosted.org/packages/annotated_types-0.7.0-py3-none-any.whl?x=1",
        "https://files.pythonhosted.org/packages/annotated_types-0.7.0-py3-none-any.whl?",
        "https://files.pythonhosted.org/packages/annotated_types-0.7.0-py3-none-any.whl#frag",
        "https://files.pythonhosted.org/packages/annotated_types-0.7.0-py3-none-any.whl#",
        "https://files.pythonhosted.org/packages/../annotated_types-0.7.0-py3-none-any.whl",
        "https://files.pythonhosted.org/packages/other_pkg-0.7.0-py3-none-any.whl",
        "https://files.pythonhosted.org/packages/annotated_types-9.9.9-py3-none-any.whl",
    ):
        assert qualified_runtime.valid_artifact_url(bad_url, name="annotated-types", version="0.7.0") is False
        mdoc = json.loads(src_man.read_text(encoding="utf-8"))
        assert mdoc["artifacts"][0]["name"] == "annotated-types"
        mdoc["artifacts"][0]["wheels"][0]["url"] = bad_url
        mdoc["artifacts"][0]["wheels"][0]["filename"] = bad_url.rsplit("/", 1)[-1]
        assert refused(mdoc), f"bad URL must be refused: {bad_url}"

    # 5. Manifest generated offline from a frozen PEP 751 lock -> accepted.
    assert qualified_runtime.valid_artifact_url(
        "https://files.pythonhosted.org/packages/ab/cd/typing_extensions-4.15.0-py3-none-any.whl",
        name="typing-extensions", version="4.15.0",
    ) is True
    assert qualified_runtime.valid_artifact_url(
        "https://files.pythonhosted.org/packages/ab/cd/numpy-2.4.2-1-cp311-cp311-manylinux_2_17_aarch64.whl",
        name="numpy", version="2.4.2",
    ) is True
    compiled, pylock = synthetic_frozen_texts(expected)
    ref_cat = json.loads((RESOURCES / "qualified_runtime_lexical_v1.json").read_text(encoding="utf-8"))
    ml_pkgs = gen._ml_closure_names(RESOURCES)
    f_cat, f_con, f_man = gen.build_cell_lock_files(
        cell_id, compiled, pylock.encode("utf-8"), reference_catalog=ref_cat, ml_packages=ml_pkgs,
    )
    assert f_man["schema_version"] == 2 and f_man["lock_state"] == "frozen" and f_cat["lock_state"] == "frozen"
    assert f_man["pylock_sha256"] == hashlib.sha256(pylock.encode("utf-8")).hexdigest()
    assert all(w["url"].startswith("https://files.pythonhosted.org/") for a in f_man["artifacts"] for w in a["wheels"])
    cat.write_text(json.dumps(f_cat, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    con.write_text(f_con, encoding="utf-8")
    man.write_text(json.dumps(f_man, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    bind()
    assert qualified_runtime.cell_lock(cell_id, profile="lexical") is not None


@pytest.mark.parametrize("tamper", [
    "neighbor-catalog",
    "neighbor-constraints",
    "neighbor-manifest",
    "catalog-wrong-cell-id",
    "catalog-wrong-tiktoken",
    "catalog-ml-injected",
    "constraints-marker-added",
    "constraints-extra-package",
    "constraints-missing-package",
    "constraints-version-drift",
    "manifest-wrong-constraints-sha",
    "manifest-wrong-cell-id",
    "manifest-wrong-profile-id",
    "manifest-bad-hex-hash",
    "manifest-uppercase-hash",
    "manifest-empty-hashes",
    "manifest-duplicate-hash",
    "manifest-missing-artifact",
    "manifest-extra-artifact",
    "manifest-version-mismatch",
    "manifest-url-null",
    "manifest-url-missing",
    "manifest-url-other-host",
    "manifest-no-wheel",
    "manifest-wheels-missing",
    "manifest-duplicate-package",
    "manifest-duplicate-wheel",
    "manifest-duplicate-url-other-package",
    "manifest-filename-mismatch",
    "manifest-filename-other-package",
    "manifest-wheels-unsorted",
    "manifest-size-zero",
    "manifest-size-bool",
    "manifest-size-string",
    "manifest-schema-1",
    "manifest-schema-3",
    "manifest-schema-true",
    "manifest-schema-string",
    "manifest-state-preliminary",
    "manifest-unknown-top-key",
    "manifest-unknown-artifact-key",
    "manifest-unknown-wheel-key",
    "manifest-missing-pylock-sha",
    "manifest-bad-pylock-sha",
    "manifest-bad-resolved-at",
    "manifest-bad-exclude-newer",
    "manifest-empty-source",
    "manifest-missing-for-lexical",
    "symlink-constraints",
])
def test_tampered_or_neighbor_cell_lock_is_refused(tmp_path, monkeypatch, tamper):
    cell_id = "ubuntu2404-arm64-py311"
    neighbor = "macos-arm64-py312"
    src_cat, src_con, src_man = qualified_runtime.CELL_LOCKS[("lexical", cell_id)]
    nbr_cat, nbr_con, nbr_man = qualified_runtime.CELL_LOCKS[("lexical", neighbor)]
    cat = tmp_path / src_cat.name
    con = tmp_path / src_con.name
    man = tmp_path / src_man.name
    shutil.copy2(src_cat, cat)
    shutil.copy2(src_con, con)
    shutil.copy2(src_man, man)

    entry = (cat, con, man)
    if tamper == "neighbor-catalog":
        shutil.copy2(nbr_cat, cat)
    elif tamper == "neighbor-constraints":
        shutil.copy2(nbr_con, con)
    elif tamper == "neighbor-manifest":
        shutil.copy2(nbr_man, man)
    elif tamper == "catalog-wrong-cell-id":
        doc = json.loads(cat.read_text())
        doc["cell_id"] = neighbor
        cat.write_text(json.dumps(doc))
    elif tamper == "catalog-wrong-tiktoken":
        doc = json.loads(cat.read_text())
        doc["extra_versions"]["brief"]["tiktoken"] = "0.13.0"
        cat.write_text(json.dumps(doc))
        con.write_text(con.read_text().replace("tiktoken==0.12.0", "tiktoken==0.13.0"))
        mdoc = json.loads(man.read_text())
        mdoc["constraints_sha256"] = hashlib.sha256(con.read_bytes()).hexdigest()
        for item in mdoc["artifacts"]:
            if item["name"] == "tiktoken":
                item["version"] = "0.13.0"
        man.write_text(json.dumps(mdoc))
    elif tamper == "catalog-ml-injected":
        doc = json.loads(cat.read_text())
        doc["versions"]["torch"] = "2.10.0"
        cat.write_text(json.dumps(doc))
        con.write_text(con.read_text() + "torch==2.10.0\n")
        mdoc = json.loads(man.read_text())
        mdoc["constraints_sha256"] = hashlib.sha256(con.read_bytes()).hexdigest()
        mdoc["artifacts"].append({"name": "torch", "version": "2.10.0", "wheels": [synthetic_wheel("torch", "2.10.0")]})
        man.write_text(json.dumps(mdoc))
    elif tamper == "constraints-marker-added":
        con.write_text(con.read_text().replace("numpy==2.4.2", 'numpy==2.4.2; sys_platform == "linux"'))
        mdoc = json.loads(man.read_text())
        mdoc["constraints_sha256"] = hashlib.sha256(con.read_bytes()).hexdigest()
        man.write_text(json.dumps(mdoc))
    elif tamper == "constraints-extra-package":
        con.write_text(con.read_text() + "extra-pkg==1.0.0\n")
        mdoc = json.loads(man.read_text())
        mdoc["constraints_sha256"] = hashlib.sha256(con.read_bytes()).hexdigest()
        man.write_text(json.dumps(mdoc))
    elif tamper == "constraints-missing-package":
        con.write_text("\n".join(line for line in con.read_text().splitlines() if not line.startswith("numpy==")) + "\n")
        mdoc = json.loads(man.read_text())
        mdoc["constraints_sha256"] = hashlib.sha256(con.read_bytes()).hexdigest()
        man.write_text(json.dumps(mdoc))
    elif tamper == "constraints-version-drift":
        con.write_text(con.read_text().replace("numpy==2.4.2", "numpy==2.4.3"))
        mdoc = json.loads(man.read_text())
        mdoc["constraints_sha256"] = hashlib.sha256(con.read_bytes()).hexdigest()
        man.write_text(json.dumps(mdoc))
    elif tamper == "manifest-wrong-constraints-sha":
        mdoc = json.loads(man.read_text())
        mdoc["constraints_sha256"] = "0" * 64
        man.write_text(json.dumps(mdoc))
    elif tamper == "manifest-wrong-cell-id":
        mdoc = json.loads(man.read_text())
        mdoc["cell_id"] = neighbor
        man.write_text(json.dumps(mdoc))
    elif tamper == "manifest-wrong-profile-id":
        mdoc = json.loads(man.read_text())
        mdoc["profile_id"] = f"lexical-{neighbor}-v1"
        man.write_text(json.dumps(mdoc))
    elif tamper == "manifest-bad-hex-hash":
        mdoc = json.loads(man.read_text())
        mdoc["artifacts"][0]["wheels"][0]["sha256"] = "not-a-sha256"
        man.write_text(json.dumps(mdoc))
    elif tamper == "manifest-uppercase-hash":
        mdoc = json.loads(man.read_text())
        mdoc["artifacts"][0]["wheels"][0]["sha256"] = "A" * 64
        man.write_text(json.dumps(mdoc))
    elif tamper == "manifest-empty-hashes":
        mdoc = json.loads(man.read_text())
        mdoc["artifacts"][0]["wheels"][0]["sha256"] = ""
        man.write_text(json.dumps(mdoc))
    elif tamper == "manifest-duplicate-hash":
        mdoc = json.loads(man.read_text())
        mdoc["artifacts"][1]["wheels"][0]["sha256"] = mdoc["artifacts"][0]["wheels"][0]["sha256"]
        man.write_text(json.dumps(mdoc))
    elif tamper == "manifest-missing-artifact":
        mdoc = json.loads(man.read_text())
        mdoc["artifacts"] = mdoc["artifacts"][1:]
        man.write_text(json.dumps(mdoc))
    elif tamper == "manifest-extra-artifact":
        mdoc = json.loads(man.read_text())
        mdoc["artifacts"].append({"name": "extra-pkg", "version": "1.0.0", "wheels": [synthetic_wheel("extra-pkg", "1.0.0")]})
        man.write_text(json.dumps(mdoc))
    elif tamper == "manifest-version-mismatch":
        mdoc = json.loads(man.read_text())
        mdoc["artifacts"][0]["version"] = "9.9.9"
        man.write_text(json.dumps(mdoc))
    elif tamper.startswith("manifest-") and tamper != "manifest-missing-for-lexical":
        mdoc = json.loads(man.read_text())
        first = mdoc["artifacts"][0]
        multi = next(item for item in mdoc["artifacts"] if len(item["wheels"]) > 1)
        if tamper == "manifest-url-null":
            first["wheels"][0]["url"] = None
        elif tamper == "manifest-url-missing":
            del first["wheels"][0]["url"]
        elif tamper == "manifest-url-other-host":
            first["wheels"][0]["url"] = first["wheels"][0]["url"].replace("files.pythonhosted.org", "pypi.org")
        elif tamper == "manifest-no-wheel":
            first["wheels"] = []
        elif tamper == "manifest-wheels-missing":
            del first["wheels"]
        elif tamper == "manifest-duplicate-package":
            mdoc["artifacts"].append(json.loads(json.dumps(first)))
        elif tamper == "manifest-duplicate-wheel":
            first["wheels"].append(dict(first["wheels"][0]))
        elif tamper == "manifest-duplicate-url-other-package":
            mdoc["artifacts"][1]["wheels"][0]["url"] = first["wheels"][0]["url"]
        elif tamper == "manifest-filename-mismatch":
            first["wheels"][0]["filename"] = "x" + first["wheels"][0]["filename"]
        elif tamper == "manifest-filename-other-package":
            other = mdoc["artifacts"][1]["wheels"][0]
            first["wheels"][0]["filename"] = other["filename"]
            first["wheels"][0]["url"] = other["url"].rsplit("/", 1)[0] + "/x/" + other["filename"]
        elif tamper == "manifest-wheels-unsorted":
            multi["wheels"].reverse()
        elif tamper == "manifest-size-zero":
            first["wheels"][0]["size"] = 0
        elif tamper == "manifest-size-bool":
            first["wheels"][0]["size"] = True
        elif tamper == "manifest-size-string":
            first["wheels"][0]["size"] = "13643"
        elif tamper == "manifest-schema-1":
            mdoc["schema_version"] = 1
        elif tamper == "manifest-schema-3":
            mdoc["schema_version"] = 3
        elif tamper == "manifest-schema-true":
            mdoc["schema_version"] = True
        elif tamper == "manifest-schema-string":
            mdoc["schema_version"] = "2"
        elif tamper == "manifest-state-preliminary":
            mdoc["lock_state"] = "preliminary"
        elif tamper == "manifest-unknown-top-key":
            mdoc["urls_status"] = "complete"
        elif tamper == "manifest-unknown-artifact-key":
            first["sdist"] = {"url": "https://files.pythonhosted.org/packages/ab/cd/annotated_types-0.7.0.tar.gz"}
        elif tamper == "manifest-unknown-wheel-key":
            first["wheels"][0]["path"] = "./local.whl"
        elif tamper == "manifest-missing-pylock-sha":
            del mdoc["pylock_sha256"]
        elif tamper == "manifest-bad-pylock-sha":
            mdoc["pylock_sha256"] = "Z" * 64
        elif tamper == "manifest-bad-resolved-at":
            mdoc["resolved_at"] = "24/11/2026"
        elif tamper == "manifest-bad-exclude-newer":
            mdoc["exclude_newer"] = "2026-11-24"
        elif tamper == "manifest-empty-source":
            mdoc["source"] = " "
        else:
            raise AssertionError(tamper)
        man.write_text(json.dumps(mdoc))
    elif tamper == "manifest-missing-for-lexical":
        entry = (cat, con, None)
    elif tamper == "symlink-constraints":
        target = tmp_path / "real-con.txt"
        con.rename(target)
        con.symlink_to(target)

    monkeypatch.setattr(qualified_runtime, "CELL_LOCKS", {**qualified_runtime.CELL_LOCKS, ("lexical", cell_id): entry})
    # Also test structural refusal even when anchored digests are synced to the tampered bytes.
    if entry[2] is not None and not con.is_symlink() and cat.is_file() and con.is_file() and man.is_file():
        synced = (
            hashlib.sha256(cat.read_bytes()).hexdigest(),
            hashlib.sha256(con.read_bytes()).hexdigest(),
            hashlib.sha256(man.read_bytes()).hexdigest(),
        )
        monkeypatch.setattr(qualified_runtime, "CELL_LOCK_DIGESTS", {**qualified_runtime.CELL_LOCK_DIGESTS, ("lexical", cell_id): synced})
    assert qualified_runtime.cell_lock(cell_id, profile="lexical") is None
    with pytest.raises(ValueError):
        qualified_runtime.catalog(profile="lexical", cell_id=cell_id)
    inspected = qualified_runtime.inspect_current(profile="lexical", extras=("ops", "brief"), cell_id=cell_id)
    assert inspected["ok"] is False and inspected["state"] == "lock_unavailable"


@pytest.mark.parametrize("cell_id", RETAINED_CELLS)
def test_qualified_runtime_and_profile_install_follow_the_detected_cell(monkeypatch, cell_id):
    report = CELL_REPORTS[cell_id]()
    monkeypatch.setattr(cell, "detect", lambda **kwargs: dict(report))
    cat = qualified_runtime.catalog(profile="lexical")
    assert cat["profile_id"] == f"lexical-{cell_id}-v1"
    expected = qualified_runtime.expected_versions(profile="lexical", extras=("ops", "brief"))
    inspected = qualified_runtime.inspect_current(
        profile="lexical", extras=("ops", "brief"), version_getter=lambda name: expected[name],
    )
    assert inspected["ok"] is True and inspected["cell_id"] == cell_id and inspected["profile_id"] == f"lexical-{cell_id}-v1"
    req, con_name = qs.profile_install("lexical")
    assert req == "memory-os-ai[ops,brief]"
    assert con_name == qualified_runtime.CELL_LOCKS[("lexical", cell_id)][1].name
    if cell_id != "macos-arm64-py311":
        with pytest.raises(ValueError):
            qualified_runtime.catalog(profile="semantic")
        sem = qualified_runtime.inspect_current(profile="semantic")
        assert sem["ok"] is False and sem["state"] == "lock_unavailable" and sem["cell_id"] == cell_id


def test_quickstart_prepare_names_the_cell_constraints_file_on_runtime_drift(ubuntu, monkeypatch):
    c = ubuntu
    monkeypatch.setattr(
        qualified_runtime, "inspect_current",
        lambda **kw: {"ok": False, "state": "drift", "cell_id": kw.get("cell_id"),
                      "mismatches": {"numpy": {"expected": "2.4.2", "actual": "2.4.3", "state": "drift"}}},
    )
    plan = prepare(c, profile="lexical", clients=("cursor",))
    assert plan["ok"] is False and "runtime_unqualified" in plan["blocker_codes"]
    assert any("qualified_runtime_constraints_lexical_ubuntu2404-arm64-py311_v1.txt" in b for b in plan["blockers"])


def test_check_constraints_still_refuses_markers_on_every_cell():
    for cell_id in RETAINED_CELLS:
        lines = qualified_runtime.constraints_path(profile="lexical", cell_id=cell_id).read_text().splitlines()
        marked = [line + '; sys_platform == "linux"' if line.startswith("numpy==") else line for line in lines]
        proof = qualified_runtime.check_constraints(marked, profile="lexical", extras=("ops", "brief"), cell_id=cell_id)
        assert proof["ok"] is False and "numpy" in proof["mismatches"]


# ===================================================================== 2. P4 "no extra packages" control

@pytest.mark.parametrize("cell_id", RETAINED_CELLS)
def test_no_extra_packages_gate_accepts_exact_lock_and_refuses_extras_and_ml(cell_id):
    expected = qualified_runtime.expected_versions(profile="lexical", extras=("ops", "brief"), cell_id=cell_id)
    clean = {**expected, "memory-os-ai": "4.1.1"}
    ok = qualified_runtime.check_no_extra_packages(clean, profile="lexical", extras=("ops", "brief"), cell_id=cell_id)
    assert ok["ok"] is True and ok["unexpected"] == {} and ok["ml_packages"] == [] and ok["cell_id"] == cell_id

    # Extra unrelated package: compare() still passes, check_no_extra_packages() fails.
    with_extra = {**clean, "mcp-types": "2.3.0"}
    assert qualified_runtime.compare(with_extra, profile="lexical", extras=("ops", "brief"), cell_id=cell_id)["ok"] is True
    bad = qualified_runtime.check_no_extra_packages(with_extra, profile="lexical", extras=("ops", "brief"), cell_id=cell_id)
    assert bad["ok"] is False and bad["unexpected"] == {"mcp-types": "2.3.0"} and bad["ml_packages"] == []

    # Extra ML package: flagged in both unexpected and ml_packages.
    with_ml = {**clean, "torch": "2.10.0"}
    ml_bad = qualified_runtime.check_no_extra_packages(with_ml, profile="lexical", extras=("ops", "brief"), cell_id=cell_id)
    assert ml_bad["ok"] is False and "torch" in ml_bad["unexpected"] and ml_bad["ml_packages"] == ["torch"]


def test_inspect_no_extra_packages_catches_duplicates_and_malformed_distributions():
    expected = qualified_runtime.expected_versions(profile="lexical", extras=("ops", "brief"), cell_id="ubuntu2404-arm64-py311")
    dists = [SimpleNamespace(metadata={"Name": n}, version=v) for n, v in {**expected, "memory-os-ai": "4.1.1"}.items()]
    ok = qualified_runtime.inspect_no_extra_packages(
        distributions=dists, profile="lexical", extras=("ops", "brief"), cell_id="ubuntu2404-arm64-py311",
    )
    assert ok["ok"] is True and ok["duplicates"] == [] and ok["malformed"] == []

    duped = dists + [SimpleNamespace(metadata={"Name": "Typing_Extensions"}, version="4.15.0")]
    dup_res = qualified_runtime.inspect_no_extra_packages(
        distributions=duped, profile="lexical", extras=("ops", "brief"), cell_id="ubuntu2404-arm64-py311",
    )
    assert dup_res["ok"] is False and dup_res["duplicates"] == ["typing-extensions"]

    malformed = dists + [SimpleNamespace(metadata={}, version="1.0")]
    mal_res = qualified_runtime.inspect_no_extra_packages(
        distributions=malformed, profile="lexical", extras=("ops", "brief"), cell_id="ubuntu2404-arm64-py311",
    )
    assert mal_res["ok"] is False and len(mal_res["malformed"]) == 1


# ===================================================================== 3. memory-os-constraints (stdlib only)

def test_memory_os_constraints_detection_agrees_with_the_product_on_every_case(monkeypatch):
    """Same facts (architecture included), same cell as ``ops/cell.py``, for every retained and refused case."""
    import memory_os_constraints
    from test_cell_411 import NOT_RETAINED, RETAINED

    assert memory_os_constraints._CELL_FACTS == cell._CELL_FACTS
    assert memory_os_constraints._ARCH_MACHINES == cell.ARCH_MACHINES
    assert {cell_id for (_, cell_id) in memory_os_constraints.CELL_FILES} == set(cell.RETAINED_CELLS)
    real = cell.detect
    for name, build in {**RETAINED, **NOT_RETAINED}.items():
        captured = {}

        def spy(**facts):
            captured.update(facts)
            return real(**facts)
        monkeypatch.setattr(cell, "detect", spy)
        expected = build()["cell_id"]
        found = memory_os_constraints.detect_cell_id(**{key: captured[key] for key in (
            "sys_platform", "machine", "python_version", "implementation", "os_release", "kernel_release", "environ")})
        assert found == expected, name


def test_memory_os_constraints_is_cell_aware_and_stdlib_only():
    code = """
import sys
sys.path.insert(0, sys.argv[1])
import memory_os_constraints as m
assert 'memory_os_v4' not in sys.modules
for cid in ('macos-arm64-py311', 'macos-arm64-py312', 'macos-arm64-py313', 'ubuntu2404-arm64-py311',
            'ubuntu2404-x86_64-py311'):
    p = m.path('lexical', cell_id=cid)
    assert p.endswith('.txt')
assert m.path('lexical', cell_id='ubuntu2404-x86_64-py311').endswith(
    'qualified_runtime_constraints_lexical_ubuntu2404-x86_64-py311_v1.txt')
assert m.path('semantic', cell_id='macos-arm64-py311').endswith('qualified_runtime_constraints_v1.txt')
for cid in ('macos-arm64-py312', 'macos-arm64-py313', 'ubuntu2404-arm64-py311', 'ubuntu2404-x86_64-py311'):
    try:
        m.path('semantic', cell_id=cid)
    except ValueError:
        pass
    else:
        raise AssertionError('semantic must be refused on ' + cid)
assert m.detect_cell_id(sys_platform='linux', machine='aarch64', python_version=(3, 11),
                        implementation='CPython', os_release='ID=ubuntu\\nVERSION_ID="24.04"\\n',
                        kernel_release='6.8.0-generic', environ={}) == 'ubuntu2404-arm64-py311'
assert m.detect_cell_id(sys_platform='linux', machine='aarch64', python_version=(3, 11),
                        implementation='CPython', os_release='ID=ubuntu\\nVERSION_ID="24.04"\\n',
                        kernel_release='5.15-microsoft-standard-WSL2', environ={}) is None
u2404 = dict(sys_platform='linux', implementation='CPython', os_release='ID=ubuntu\\nVERSION_ID="24.04"\\n',
             kernel_release='6.8.0-generic', environ={})
for machine in ('x86_64', 'amd64', 'AMD64'):
    assert m.detect_cell_id(machine=machine, python_version=(3, 11), **u2404) == 'ubuntu2404-x86_64-py311'
for machine, version in (('x86_64', (3, 12)), ('x86_64', (3, 13)), ('i686', (3, 11)), ('x86_64_v3', (3, 11)),
                         ('', (3, 11))):
    assert m.detect_cell_id(machine=machine, python_version=version, **u2404) is None
assert m.detect_cell_id(machine='x86_64', python_version=(3, 11),
                        **{**u2404, 'kernel_release': '5.15-microsoft-standard-WSL2'}) is None
assert m.detect_cell_id(machine='x86_64', python_version=(3, 11), **{**u2404, 'environ': {'WSL_DISTRO_NAME': 'U'}}) is None
assert m.detect_cell_id(machine='x86_64', python_version=(3, 11), **{**u2404, 'os_release': 'ID=debian\\nVERSION_ID=12\\n'}) is None
assert m.detect_cell_id(sys_platform='darwin', machine='x86_64', python_version=(3, 11), implementation='CPython') is None
assert m.main(['lexical', '--cell', 'ubuntu2404-x86_64-py311']) == 0
assert m.main(['semantic', '--cell', 'ubuntu2404-x86_64-py311']) == 1
assert m.main(['lexical', '--cell', 'ubuntu2404-arm64-py311']) == 0
assert m.main(['semantic', '--cell', 'ubuntu2404-arm64-py311']) == 1
assert m.main(['lexical', '--cell', 'debian-x86_64-py311']) == 1
assert m.main([]) == 2 and m.main(['hybrid']) == 2 and m.main(['lexical', 'extra']) == 2
assert 'memory_os_v4' not in sys.modules
print('OK')
"""
    env = {"PATH": "/usr/bin:/bin"}
    done = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code, str(ROOT / "v4/src")],
                          capture_output=True, text=True, env=env, timeout=30)
    assert done.returncode == 0 and done.stdout.strip().endswith("OK"), done.stderr


# ===================================================================== 4. Offline generator & L3 test pin

def test_lock_generator_parses_refuses_bad_input_and_builds_valid_bootstrap_test_pin(tmp_path):
    gen = load_script("generate_cell_locks_411")
    bootstrap = load_script("memory_os_bootstrap")

    # Rejects markers, missing hashes, bad hex hashes, duplicates.
    for bad in (
        'numpy==2.4.2; sys_platform == "linux" --hash=sha256:' + "a" * 64,
        "numpy==2.4.2",
        "numpy>=2.4.2 --hash=sha256:" + "a" * 64,
        "numpy==2.4.2 --hash=sha256:nothex",
        f"numpy==2.4.2 --hash=sha256:{'a'*64}\nnumpy==2.4.2 --hash=sha256:{'b'*64}",
    ):
        with pytest.raises(ValueError):
            gen.parse_uv_compiled_lock(bad)

    # Round-trip offline generation into a temporary resources copy using synthetic compiled locks.
    out_dir = tmp_path / "resources"
    shutil.copytree(RESOURCES, out_dir)
    expected = qualified_runtime.expected_versions(profile="lexical", extras=("ops", "brief"), cell_id="macos-arm64-py311")
    inputs = write_synthetic_frozen_inputs(gen, tmp_path / "inputs", expected)
    qr_copy = tmp_path / "qualified_runtime.py"
    con_copy = tmp_path / "memory_os_constraints.py"
    shutil.copy2(ROOT / "v4/src/memory_os_v4/ops/qualified_runtime.py", qr_copy)
    shutil.copy2(ROOT / "v4/src/memory_os_constraints.py", con_copy)
    written = gen.generate_all(
        inputs, out_dir,
        qualified_runtime_path=qr_copy, constraints_script_path=con_copy,
    )
    assert len(written["written"]) == 15
    assert "qualified_runtime.py:CELL_LOCK_DIGESTS" in written["written"]
    assert "memory_os_constraints.py:CELL_CONSTRAINTS_DIGESTS" in written["written"]
    checked = gen.generate_all(
        inputs, out_dir, check_only=True,
        qualified_runtime_path=qr_copy, constraints_script_path=con_copy,
    )
    assert checked["mismatches"] == []

    # Tampering a single digest character in qualified_runtime.py MUST be caught by --check.
    qr_text = qr_copy.read_text(encoding="utf-8")
    begin = qr_text.index(gen.DIGESTS_BEGIN)
    tampered_idx = qr_text.index("'", begin + len(gen.DIGESTS_BEGIN) + 120) + 1
    flipped = "0" if qr_text[tampered_idx] != "0" else "1"
    qr_copy.write_text(qr_text[:tampered_idx] + flipped + qr_text[tampered_idx + 1:], encoding="utf-8")
    drifted = gen.generate_all(
        inputs, out_dir, check_only=True,
        qualified_runtime_path=qr_copy, constraints_script_path=con_copy,
    )
    assert "qualified_runtime.py:CELL_LOCK_DIGESTS" in drifted["mismatches"]
    assert any(m.startswith("attestation:") for m in drifted["mismatches"])
    qr_copy.write_text(qr_text, encoding="utf-8")

    # Tampering a single digest character in memory_os_constraints.py MUST be caught by --check.
    con_text = con_copy.read_text(encoding="utf-8")
    cbegin = con_text.index(gen.CONSTRAINTS_DIGESTS_BEGIN)
    ctampered = con_text.index(": '", cbegin) + 3
    cflipped = "0" if con_text[ctampered] != "0" else "1"
    con_copy.write_text(con_text[:ctampered] + cflipped + con_text[ctampered + 1:], encoding="utf-8")
    cdrifted = gen.generate_all(
        inputs, out_dir, check_only=True,
        qualified_runtime_path=qr_copy, constraints_script_path=con_copy,
    )
    assert "memory_os_constraints.py:CELL_CONSTRAINTS_DIGESTS" in cdrifted["mismatches"]
    con_copy.write_text(con_text, encoding="utf-8")

    # Build a state='test' bootstrap pin from the REAL embedded L5 resources and validate via L3 load_pin/fill_pin.
    pin = gen.build_bootstrap_test_pin(RESOURCES)
    assert pin["state"] == "test" and set(pin["cells"]) == set(RETAINED_CELLS)
    loaded = bootstrap.load_pin(json.dumps(pin))
    assert loaded["ok"] is True and loaded["state"] == "test"
    filled_source = bootstrap.fill_pin((ROOT / "scripts/memory_os_bootstrap.py").read_text(encoding="utf-8"), pin)
    assert '"state": "test"' in filled_source
    # Repo bootstrap template itself stays untouched as state="template", and never forwards XDG_CONFIG_HOME.
    assert bootstrap.load_pin()["code"] == "release_pin_unfilled"
    assert "XDG_CONFIG_HOME" not in bootstrap.CHILD_ENV_KEEP


@pytest.mark.parametrize("target_file, profile, impostor, expected_failure", [
    ("qualified_runtime_lexical_v1.json", "lexical", "lexical-macos-arm64-py311-imposteur", "attestation:lexical:macos-arm64-py311"),
    ("qualified_runtime_v1.json", "semantic", "granite-macos-arm64-py311-imposteur", "attestation:semantic:macos-arm64-py311"),
])
def test_generator_and_check_refuse_impostor_historical_catalog_even_after_anchor_regen(
    tmp_path, target_file, profile, impostor, expected_failure,
):
    """Codex L5-r2 blocker #1: historical catalog with impostor profile_id + regenerated anchor MUST fail --check."""
    gen = load_script("generate_cell_locks_411")
    out_dir = tmp_path / "resources"
    shutil.copytree(RESOURCES, out_dir)
    expected = qualified_runtime.expected_versions(profile="lexical", extras=("ops", "brief"), cell_id="macos-arm64-py311")
    inputs = write_synthetic_frozen_inputs(gen, tmp_path / "inputs", expected)
    qr_copy = tmp_path / "qualified_runtime.py"
    con_copy = tmp_path / "memory_os_constraints.py"
    shutil.copy2(ROOT / "v4/src/memory_os_v4/ops/qualified_runtime.py", qr_copy)
    shutil.copy2(ROOT / "v4/src/memory_os_constraints.py", con_copy)
    gen.generate_all(
        inputs, out_dir,
        qualified_runtime_path=qr_copy, constraints_script_path=con_copy,
    )

    # Tamper historical catalog's profile_id to an impostor value.
    hist_path = out_dir / target_file
    doc = json.loads(hist_path.read_text(encoding="utf-8"))
    doc["profile_id"] = impostor
    hist_path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # 1. Attempting generation refuses BEFORE writing anything.
    with pytest.raises(ValueError, match="runtime attestation"):
        gen.generate_all(
            inputs, out_dir,
            qualified_runtime_path=qr_copy, constraints_script_path=con_copy,
        )

    # 2. Even if someone forcefully regenerates the anchors to match the tampered file, --check MUST fail.
    gen.sync_qualified_runtime_digests(qr_copy, out_dir, check_only=False)
    gen.sync_constraints_script_digests(con_copy, out_dir, check_only=False)
    checked = gen.generate_all(
        inputs, out_dir, check_only=True,
        qualified_runtime_path=qr_copy, constraints_script_path=con_copy,
    )
    assert expected_failure in checked["mismatches"]


def test_memory_os_constraints_verifies_anchored_sha256_and_refuses_tampered_file(tmp_path, monkeypatch, capsys):
    """Codex L5-r2 reserve #3: memory-os-constraints checks the served file's SHA-256 against its stdlib anchor."""
    import memory_os_constraints as moc

    assert set(moc.CELL_CONSTRAINTS_DIGESTS) == set(qualified_runtime.CELL_LOCK_DIGESTS)
    for key, (_, con_sha, _) in qualified_runtime.CELL_LOCK_DIGESTS.items():
        assert moc.CELL_CONSTRAINTS_DIGESTS[key] == con_sha

    cell_id = "ubuntu2404-arm64-py311"
    real_con = Path(moc.path("lexical", cell_id=cell_id))
    assert moc._verify_constraints_file(real_con, "lexical", cell_id) is True

    tampered = tmp_path / real_con.name
    tampered.write_text(real_con.read_text(encoding="utf-8") + "# tampered\n", encoding="utf-8")
    assert moc._verify_constraints_file(tampered, "lexical", cell_id) is False
    monkeypatch.setattr(moc, "path", lambda profile, *, cell_id=None: str(tampered))
    rc = moc.main(["lexical", "--cell", cell_id])
    captured = capsys.readouterr()
    assert rc == 1 and "digest mismatch" in captured.err and captured.out == ""


@pytest.mark.parametrize("client_key, var", [
    ("claude-code", "CLAUDE_CONFIG_DIR"),
    ("codex", "CODEX_HOME"),
    ("gemini-cli", "GEMINI_CLI_HOME"),
    ("grok", "GROK_HOME"),
])
@pytest.mark.parametrize("value_mode", ["non-empty", "empty"])
def test_bootstrap_detects_relocating_env_vars_without_forwarding_them(tmp_path, client_key, var, value_mode):
    """Codex L5-r2 reserve #4: bootstrap never forwards relocating env vars, yet reports client_env_override."""
    bs = load_script("memory_os_bootstrap")
    assert bs.DEFAULT_CLIENTS == qs.DEFAULT_CLIENTS
    assert bs.CLIENT_OVERRIDE_ENV == {c.key: c.override_env for c in clients.CLIENTS if c.override_env}
    assert var not in bs.CHILD_ENV_KEEP and var not in bs.UV_ENV_KEEP

    home = tmp_path / "home"
    home.mkdir()
    val = "" if value_mode == "empty" else str(tmp_path / "relocated")
    env = {"HOME": str(home), "PATH": os.environ.get("PATH", "/usr/bin:/bin"), var: val}
    assert var not in bs.child_environment(env)

    # Selected client -> preview refuses with client_env_override (consistent with quickstart).
    opts_sel = bs.parser().parse_args(["--profile", "lexical", "--clients", client_key])
    opts_sel.replay = ["--profile", "lexical", "--clients", client_key]
    opts_sel.host = None
    plan_sel = bs.build_plan(opts_sel, environ=env, run=lambda *a, **kw: SimpleNamespace(returncode=1, stdout=""))
    assert plan_sel["ok"] is False and "client_env_override" in plan_sel["blocker_codes"]
    assert any(var in b for b in plan_sel["blockers"])
    assert any(o["client"] == client_key and o["variable"] == var and o["selected"] is True
               for o in plan_sel["client_env_overrides"])

    # Unrelated client selected (--clients cursor) -> does NOT falsely block cursor, still reports notice.
    opts_other = bs.parser().parse_args(["--profile", "lexical", "--clients", "cursor"])
    opts_other.replay = ["--profile", "lexical", "--clients", "cursor"]
    opts_other.host = None
    plan_other = bs.build_plan(opts_other, environ=env, run=lambda *a, **kw: SimpleNamespace(returncode=1, stdout=""))
    assert "client_env_override" not in plan_other["blocker_codes"]
    assert any(var in n and "client_env_override" in n for n in plan_other["notices"])
    assert any(o["client"] == client_key and o["variable"] == var and o["selected"] is False
               for o in plan_other["client_env_overrides"])


# ===================================================================== 5. OS-specific client paths

@pytest.mark.parametrize("key, expected_rel", [
    ("claude-code", ".claude.json"),
    ("codex", ".codex/config.toml"),
    ("cursor", ".cursor/mcp.json"),
    ("antigravity", ".gemini/config/mcp_config.json"),
    ("gemini-cli", ".gemini/settings.json"),
    ("zed", ".config/zed/settings.json"),
    ("devin", ".config/devin/mcp_config.json"),
    ("grok", ".grok/config.toml"),
    ("vscode", ".copilot/mcp-config.json"),
])
def test_nine_established_linux_client_paths(tmp_path, key, expected_rel):
    client = next(item for item in clients.CLIENTS if item.key == key)
    assert client.supported_on("linux") is True
    assert client.config_path(tmp_path, platform="linux", environ={}) == tmp_path / expected_rel
    assert client.config_path(tmp_path, platform="darwin", environ={}) == tmp_path / client.path


@pytest.mark.parametrize("key", LINUX_UNSUPPORTED)
def test_vscode_windsurf_and_claude_desktop_are_refused_on_linux_without_guessing(ubuntu, key):
    c = ubuntu
    client = next(item for item in clients.CLIENTS if item.key == key)
    assert client.supported_on("linux") is False and client.supported_on("darwin") is True
    with pytest.raises(clients.ClientPathError) as err:
        client.config_path(c.home, platform="linux", environ={})
    assert err.value.code == "client_unsupported_on_os" and "Linux" in str(err.value)

    before = tree(c.root)
    plan = prepare(c, profile="lexical", clients=(key,))
    assert plan["ok"] is False and "client_unsupported_on_os" in plan["blocker_codes"]
    assert plan["clients"][0]["path"] is None and plan["clients"][0]["refusal_code"] == "client_unsupported_on_os"
    assert c.system.calls == [] or all(argv[0] != "pgrep" for argv in c.system.calls)  # never pgrep Claude on Linux
    with pytest.raises(qs.QuickstartError) as apply_err:
        qs.apply_install(plan, c.settings, home=c.home, run=c.system, download_approved=True)
    assert apply_err.value.code == "preflight_refused"
    assert tree(c.root) == before

    # Explicit uninstall selection for an unsupported Linux client is also refused cleanly.
    un_plan = uninstall.plan(c.settings, home=c.home, python=c.python, run=c.system, clients=(key,))
    assert un_plan["ok"] is False and "client_unsupported_on_os" in un_plan["blocker_codes"]

    # Doctor reports WARN (never FAIL, never crash).
    checks = {chk.area: chk for chk in doctor._clients(c.home)}
    assert checks[f"client:{key}"].status == "WARN" and "not supported" in checks[f"client:{key}"].detail


def test_macos_keeps_vscode_windsurf_and_claude_desktop_unchanged(tmp_path):
    for key in LINUX_UNSUPPORTED:
        client = next(item for item in clients.CLIENTS if item.key == key)
        assert client.config_path(tmp_path, platform="darwin", environ={}) == tmp_path / client.path


# ===================================================================== 6. Relocating env variables

@pytest.mark.parametrize("key, var", [
    ("claude-code", "CLAUDE_CONFIG_DIR"),
    ("codex", "CODEX_HOME"),
    ("gemini-cli", "GEMINI_CLI_HOME"),
    ("grok", "GROK_HOME"),
])
@pytest.mark.parametrize("plat", ["linux", "darwin"])
@pytest.mark.parametrize("value_mode", ["non-empty", "empty"])
def test_relocating_env_var_refuses_only_the_affected_client(tmp_path, key, var, plat, value_mode):
    client = next(item for item in clients.CLIENTS if item.key == key)
    other = next(item for item in clients.CLIENTS if item.key == "cursor")
    env = {var: "" if value_mode == "empty" else str(tmp_path / "relocated")}
    with pytest.raises(clients.ClientPathError) as err:
        client.config_path(tmp_path, platform=plat, environ=env)
    assert err.value.code == "client_env_override" and var in str(err.value)
    assert other.config_path(tmp_path, platform=plat, environ=env) == tmp_path / ".cursor/mcp.json"


@pytest.mark.parametrize("key, var", [
    ("claude-code", "CLAUDE_CONFIG_DIR"),
    ("codex", "CODEX_HOME"),
    ("gemini-cli", "GEMINI_CLI_HOME"),
    ("grok", "GROK_HOME"),
])
@pytest.mark.parametrize("value_mode", ["non-empty", "empty"])
def test_quickstart_and_uninstall_refuse_relocated_client_without_guessing(ubuntu, monkeypatch, key, var, value_mode):
    c = ubuntu
    monkeypatch.setenv(var, "" if value_mode == "empty" else str(c.root / "custom-dir"))
    before = tree(c.root)
    plan = prepare(c, profile="lexical", clients=(key,))
    assert plan["ok"] is False and "client_env_override" in plan["blocker_codes"]
    assert any(var in b for b in plan["blockers"])
    assert plan["clients"][0]["path"] is None
    assert tree(c.root) == before

    # Whole-registry uninstall marks the relocated client indeterminate (keeps service safe).
    un_all = uninstall.plan(c.settings, home=c.home, python=c.python, run=c.system)
    by_key = {item["client"]: item for item in un_all["clients"]}
    assert by_key[key]["action"] == "keep-relocated" and by_key[key]["dependency"] == "indeterminate"
    assert un_all["service"]["remaining_dependencies"][key] == "indeterminate"

    # Doctor reports WARN without crashing.
    checks = {chk.area: chk for chk in doctor._clients(c.home)}
    assert checks[f"client:{key}"].status == "WARN" and var in checks[f"client:{key}"].detail


# ===================================================================== 7. XDG_CONFIG_HOME (Zed only)

def test_xdg_config_home_applies_to_zed_on_linux_only_and_refuses_unsafe_values(ubuntu, monkeypatch):
    c = ubuntu
    zed = next(item for item in clients.CLIENTS if item.key == "zed")
    devin = next(item for item in clients.CLIENTS if item.key == "devin")

    # Default when unset/empty.
    assert zed.config_path(c.home, platform="linux", environ={}) == c.home / ".config/zed/settings.json"
    assert zed.config_path(c.home, platform="linux", environ={"XDG_CONFIG_HOME": ""}) == c.home / ".config/zed/settings.json"

    # Valid absolute path under HOME.
    custom = c.home / "my-xdg"
    assert zed.config_path(c.home, platform="linux", environ={"XDG_CONFIG_HOME": str(custom)}) == custom / "zed/settings.json"
    # Other clients ignore XDG_CONFIG_HOME even when set outside HOME.
    assert devin.config_path(c.home, platform="linux", environ={"XDG_CONFIG_HOME": "/etc/xdg"}) == c.home / ".config/devin/mcp_config.json"

    # Relative path refused.
    with pytest.raises(clients.ClientPathError) as rel_err:
        zed.config_path(c.home, platform="linux", environ={"XDG_CONFIG_HOME": "relative/xdg"})
    assert rel_err.value.code == "client_xdg_invalid"

    # Outside HOME refused.
    outside = c.root / "outside-home"
    outside.mkdir()
    with pytest.raises(clients.ClientPathError) as out_err:
        zed.config_path(c.home, platform="linux", environ={"XDG_CONFIG_HOME": str(outside)})
    assert out_err.value.code == "client_xdg_outside_home"

    # Parent traversal escaping HOME refused.
    with pytest.raises(clients.ClientPathError) as trav_err:
        zed.config_path(c.home, platform="linux", environ={"XDG_CONFIG_HOME": str(c.home) + "/sub/../../outside"})
    assert trav_err.value.code == "client_xdg_outside_home"

    # Symlink under HOME escaping outside HOME refused.
    link = c.home / "xdg-link"
    link.symlink_to(outside)
    with pytest.raises(clients.ClientPathError) as sym_err:
        zed.config_path(c.home, platform="linux", environ={"XDG_CONFIG_HOME": str(link)})
    assert sym_err.value.code == "client_xdg_outside_home"

    # End-to-end refusal in quickstart.prepare when XDG_CONFIG_HOME is outside HOME.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(outside))
    plan = prepare(c, profile="lexical", clients=("zed",))
    assert plan["ok"] is False and "client_xdg_outside_home" in plan["blocker_codes"]
    assert plan["clients"][0]["path"] is None

    # End-to-end install + undo in quickstart when XDG_CONFIG_HOME is valid under HOME.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(custom))
    ok_plan = prepare(c, profile="lexical", clients=("zed",))
    assert ok_plan["ok"] is True, ok_plan["blockers"]
    assert ok_plan["clients"][0]["path"] == str(custom / "zed/settings.json")


# ===================================================================== 8. Gemini system file & Grok/Claude warnings

def test_gemini_system_settings_file_emits_warning_in_quickstart_and_doctor(ubuntu, tmp_path, monkeypatch):
    c = ubuntu
    fake_etc = tmp_path / "etc-gemini-settings.json"
    monkeypatch.setattr(clients, "GEMINI_SYSTEM_SETTINGS", fake_etc)
    monkeypatch.delenv("GEMINI_CLI_SYSTEM_SETTINGS_PATH", raising=False)

    # Absent -> no system-file warning.
    plan_clean = prepare(c, profile="lexical", clients=("gemini-cli",))
    assert not any("takes precedence" in n for n in plan_clean["notices"])

    # Empty GEMINI_CLI_SYSTEM_SETTINGS_PATH still emits a warning (defined -> never silently ignored).
    monkeypatch.setenv("GEMINI_CLI_SYSTEM_SETTINGS_PATH", "")
    plan_empty_override = prepare(c, profile="lexical", clients=("gemini-cli",))
    assert any("GEMINI_CLI_SYSTEM_SETTINGS_PATH" in n for n in plan_empty_override["notices"])
    monkeypatch.delenv("GEMINI_CLI_SYSTEM_SETTINGS_PATH", raising=False)

    # Present -> warning emitted in quickstart and doctor.
    fake_etc.write_text("{}", encoding="utf-8")
    plan_warn = prepare(c, profile="lexical", clients=("gemini-cli",))
    assert any(str(fake_etc) in n and "takes precedence" in n for n in plan_warn["notices"])

    gemini_cfg = c.home / ".gemini/settings.json"
    gemini_cfg.parent.mkdir(parents=True, exist_ok=True)
    gemini_cfg.write_text("{}", encoding="utf-8")
    checks = {chk.area: chk for chk in doctor._clients(c.home)}
    assert checks["client:gemini-cli-system-settings"].status == "WARN"


def test_grok_and_claude_code_configured_together_emit_duplicate_warning(ubuntu, monkeypatch):
    c = ubuntu
    # Neither configured yet, only claude-code selected -> no Grok warning.
    plan_only_claude = prepare(c, profile="lexical", clients=("claude-code",), which=lambda name: "/usr/bin/claude")
    assert not any("[compat.claude]" in n for n in plan_only_claude["notices"])

    # Both selected together -> duplicate warning emitted.
    plan_both = prepare(c, profile="lexical", clients=("claude-code", "grok"), which=lambda name: "/usr/bin/claude")
    assert any("~/.claude.json" in n and "[compat.claude] mcps = false" in n for n in plan_both["notices"])

    # Only grok selected when ~/.claude.json already carries a memory-os-ai entry -> duplicate warning emitted.
    claude_path = c.home / ".claude.json"
    data = json.loads(claude_path.read_text(encoding="utf-8"))
    data["mcpServers"]["memory-os-ai"] = {"command": "/py", "args": ["-m", "memory_os_v4.ops.relay"]}
    claude_path.write_text(json.dumps(data), encoding="utf-8")
    plan_grok_after = prepare(c, profile="lexical", clients=("grok",))
    assert any("~/.claude.json" in n and "[compat.claude] mcps = false" in n for n in plan_grok_after["notices"])

    # Doctor also warns when both files contain a memory-os-ai entry.
    grok_path = c.home / ".grok/config.toml"
    grok_path.parent.mkdir(parents=True, exist_ok=True)
    grok_path.write_text(
        '[mcp_servers.memory-os-ai]\ncommand = "/py"\nargs = ["-m", "memory_os_v4.ops.relay"]\n',
        encoding="utf-8",
    )
    checks = {chk.area: chk for chk in doctor._clients(c.home)}
    assert checks["client:grok-claude-duplicate"].status == "WARN"
    assert "[compat.claude] mcps = false" in checks["client:grok-claude-duplicate"].detail


# ===================================================================== 9. L4 macOS-only leftovers

def test_auxiliary_launchd_jobs_manual_removal_is_excluded_on_linux(ubuntu):
    plan = uninstall.plan(ubuntu.settings, home=ubuntu.home, python=ubuntu.python, run=ubuntu.system)
    assert "auxiliary-launchd-jobs" not in {item["kind"] for item in plan["manual_removal_required"]}
    assert "auxiliary-launchd-jobs" not in plan["scope"]["not_inspected"]
    assert plan["scope"]["clients_inspected"] == list(LINUX_SUPPORTED)
    assert set(plan["scope"]["unsupported_on_os"]) == set(LINUX_UNSUPPORTED)


def test_auxiliary_launchd_jobs_kept_on_macos(c, monkeypatch):
    report = mac_cell()
    monkeypatch.setattr(cell, "detect", lambda **kwargs: dict(report))
    plan = uninstall.plan(c.settings, home=c.home, python=c.python, run=c.system)
    assert "auxiliary-launchd-jobs" in {item["kind"] for item in plan["manual_removal_required"]}
    assert "auxiliary-launchd-jobs" in plan["scope"]["not_inspected"]
    assert "unsupported_on_os" not in plan["scope"]


def test_duplicate_json_keys_in_catalog_or_manifest_are_refused(tmp_path, monkeypatch):
    cell_id = "ubuntu2404-arm64-py311"
    src_cat, src_con, src_man = qualified_runtime.CELL_LOCKS[("lexical", cell_id)]
    cat, con, man = tmp_path / src_cat.name, tmp_path / src_con.name, tmp_path / src_man.name
    shutil.copy2(src_cat, cat)
    shutil.copy2(src_con, con)
    shutil.copy2(src_man, man)

    def bind():
        digests = (
            hashlib.sha256(cat.read_bytes()).hexdigest(),
            hashlib.sha256(con.read_bytes()).hexdigest(),
            hashlib.sha256(man.read_bytes()).hexdigest(),
        )
        monkeypatch.setattr(qualified_runtime, "CELL_LOCKS",
                            {**qualified_runtime.CELL_LOCKS, ("lexical", cell_id): (cat, con, man)})
        monkeypatch.setattr(qualified_runtime, "CELL_LOCK_DIGESTS",
                            {**qualified_runtime.CELL_LOCK_DIGESTS, ("lexical", cell_id): digests})

    bind()
    assert qualified_runtime.cell_lock(cell_id, profile="lexical") is not None
    # Duplicate key in catalog JSON: refused even if digests match.
    raw_cat = cat.read_text(encoding="utf-8")
    cat.write_text(raw_cat.replace('"schema_version": 1,', '"schema_version": 99, "schema_version": 1,'), encoding="utf-8")
    bind()
    assert qualified_runtime.cell_lock(cell_id, profile="lexical") is None
    shutil.copy2(src_cat, cat)
    # Duplicate key in manifest JSON: refused even if digests match.
    raw_man = man.read_text(encoding="utf-8")
    assert '"schema_version": 2,' in raw_man
    man.write_text(raw_man.replace('"schema_version": 2,', '"schema_version": 99, "schema_version": 2,'), encoding="utf-8")
    bind()
    assert qualified_runtime.cell_lock(cell_id, profile="lexical") is None


def test_xdg_config_home_with_control_chars_is_refused(tmp_path):
    zed = next(item for item in clients.CLIENTS if item.key == "zed")
    for bad in (str(tmp_path / "a\nb"), str(tmp_path / "a\x00b")):
        with pytest.raises(clients.ClientPathError) as err:
            zed.config_path(tmp_path, platform="linux", environ={"XDG_CONFIG_HOME": bad})
        assert err.value.code == "client_xdg_invalid"


def test_setup_without_write_launchd_on_linux_omits_launchd_and_unsupported_paths(ubuntu, monkeypatch, capsys):
    c = ubuntu
    monkeypatch.setattr(cli.OpsSettings, "from_env", classmethod(lambda cls: c.settings))
    rc = cli.main(["setup"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "--write-launchd" not in out
    assert "launchd jobs are macOS-only" in out
    assert "Library/Application Support" not in out
    assert "$XDG_CONFIG_HOME/zed/settings.json" in out


def test_setup_write_launchd_is_refused_on_linux_without_writing_or_creating_token(ubuntu, tmp_path, monkeypatch, capsys):
    c = ubuntu
    monkeypatch.setattr(cli.OpsSettings, "from_env", classmethod(lambda cls: c.settings))
    out_dir = tmp_path / "launchd-out"
    rc = cli.main(["setup", "--write-launchd", str(out_dir)])
    captured = capsys.readouterr()
    assert rc == 2
    assert "macOS-only" in captured.err and "nothing written" in captured.err
    assert not out_dir.exists()
    assert not c.settings.token_file.exists()


def test_linux_uninstall_can_stop_service_when_only_linux_supported_clients_were_installed(ubuntu):
    c = ubuntu
    result = install(c, profile="lexical", clients=("cursor",))
    assert result["ok"] is True, result
    # Plant a stale macOS-path VS Code file under HOME: must NEVER block Linux uninstall because vscode is unsupported on Linux.
    plan = uninstall.plan(c.settings, home=c.home, python=c.python, run=c.system)
    assert plan["ok"] is True and plan["service"]["action"] == "stop"
    applied = uninstall.apply(plan, c.settings, home=c.home, run=c.system, sleep=lambda s: None)
    assert applied["ok"] is True and applied["service"]["outcome"] == "stopped-and-unit-moved"


# ===================================================================== 6. Codex L5-r3 reserves

PYPI = "https://files.pythonhosted.org/packages/ab/cd/"


@pytest.mark.parametrize("bad_url", [
    PYPI + "pkg-1.0-abc-py3-none-any.whl",                 # build tag must start with a digit
    PYPI + "pkg-1.0-_1-py3-none-any.whl",
    "https://files.pythonhosted.org/%2e%2e/pkg-1.0-py3-none-any.whl",   # encoded traversal
    "https://files.pythonhosted.org/packages/%2E%2E/pkg-1.0-py3-none-any.whl",
    "https://files.pythonhosted.org/packages%2Fpkg-1.0-py3-none-any.whl",
    PYPI + "pkg-1.0-py3-none-any%2Ewhl",
    "https://files.pythonhosted.org/packages\\pkg-1.0-py3-none-any.whl",
    PYPI + "..\\pkg-1.0-py3-none-any.whl",
    "https://files.pythonhosted.org/packages/./pkg-1.0-py3-none-any.whl",
    "https://files.pythonhosted.org/./pkg-1.0-py3-none-any.whl",
    "https://files.pythonhosted.org/packages/../pkg-1.0-py3-none-any.whl",
    PYPI + "pkg-1.0-py3+x-none-any.whl",                   # tag outside [A-Za-z0-9_.]
    PYPI + "pkg-1.0-py3-none-any~.whl",
    PYPI + "p$kg-1.0-py3-none-any.whl",
])
def test_artifact_url_refuses_bad_build_tag_encoded_traversal_backslash_and_dot_segments(bad_url):
    """Codex L5-r3 reserve #2: strict PEP 427 wheel name; no '%', no backslash, no '.'/'..' segment."""
    gen = load_script("generate_cell_locks_411")
    assert qualified_runtime.valid_artifact_url(bad_url) is False
    assert qualified_runtime.valid_artifact_url(bad_url, name="pkg", version="1.0") is False
    assert gen._valid_https_url(bad_url, name="pkg", version="1.0") is False  # same rule, not a copy


@pytest.mark.parametrize("good_url, name, version", [
    (PYPI + "pkg-1.0-py3-none-any.whl", "pkg", "1.0"),
    (PYPI + "pkg-1.0-1-py3-none-any.whl", "pkg", "1.0"),                  # valid build tag
    (PYPI + "pkg-1.0-1a_b.2-py3-none-any.whl", "pkg", "1.0"),
    (PYPI + "pkg-1.0-cp311-cp311-manylinux_2_17_aarch64.manylinux2014_aarch64.whl", "pkg", "1.0"),  # compound tag
    (PYPI + "pkg-1.0-py2.py3-none-any.whl", "pkg", "1.0"),
    (PYPI + "Typing.Extensions-4.15.0-py3-none-any.whl", "typing-extensions", "4.15.0"),
])
def test_artifact_url_accepts_valid_build_tag_and_compound_tags(good_url, name, version):
    gen = load_script("generate_cell_locks_411")
    assert qualified_runtime.valid_artifact_url(good_url, name=name, version=version) is True
    assert gen._valid_https_url(good_url, name=name, version=version) is True


CLIENTS_ARG_CORPUS = [None, "", "codex", " codex", "Codex", "all", "codex,,cursor", "codex,", "claude-code,codex"]


def test_bootstrap_and_quickstart_parse_clients_identically():
    """Codex L5-r3 reserve #3: one ``--clients`` rule (exact split, no strip, no case folding)."""
    bs = load_script("memory_os_bootstrap")
    expected = {
        None: qs.DEFAULT_CLIENTS, "": qs.DEFAULT_CLIENTS, "codex": ("codex",), " codex": (" codex",),
        "Codex": ("Codex",), "all": ("all",), "codex,,cursor": ("codex", "", "cursor"),
        "codex,": ("codex", ""), "claude-code,codex": ("claude-code", "codex"),
    }
    assert set(expected) == set(CLIENTS_ARG_CORPUS)
    for value in CLIENTS_ARG_CORPUS:
        assert bs.parse_clients_arg(value) == qs.parse_clients_arg(value) == expected[value], repr(value)
        # uninstall keeps its own default (None = every client we installed); same rule otherwise.
        assert qs.parse_clients_arg(value, None) == (expected[value] if value else None)
        # A client is selected for client_env_override only when its exact name is in the tuple.
        found = bs.detect_client_env_overrides({"CODEX_HOME": ""}, value)
        assert [(o["client"], o["selected"]) for o in found] == [("codex", "codex" in expected[value])]


@pytest.mark.parametrize("value", CLIENTS_ARG_CORPUS)
def test_cli_quickstart_and_uninstall_use_the_shared_clients_rule(monkeypatch, value):
    seen = {}

    class Stop(Exception):
        pass

    def fake_prepare(s, *, clients, **kwargs):
        seen["quickstart"] = clients
        raise Stop

    def fake_plan(s, *, clients, **kwargs):
        seen["uninstall"] = clients
        raise Stop

    monkeypatch.setattr(qs, "settings_for_install", lambda s, backend=None: (s, backend))
    monkeypatch.setattr(qs, "prepare", fake_prepare)
    monkeypatch.setattr(uninstall, "settings_from_service", lambda s, home: s)
    monkeypatch.setattr(uninstall, "plan", fake_plan)
    monkeypatch.setattr(cli, "_print_json", lambda payload: None)
    args = SimpleNamespace(clients=value, undo=None, backend=None, profile=None, replace_existing=None,
                           apply=False, report=False, remove_existing=None)
    assert cli.cmd_quickstart(args, SimpleNamespace()) == 1
    assert cli.cmd_uninstall(args, SimpleNamespace()) == 1
    assert seen == {"quickstart": qs.parse_clients_arg(value), "uninstall": qs.parse_clients_arg(value, None)}


# ===================================================================== 7. Step 3, part 2: frozen locks

@pytest.mark.parametrize("bad_url", [
    PYPI + "pkg-1.0-py3..py2-none-any.whl",                # '..' inside a wheel name element
    PYPI + "pkg-1.0-py3-none..x-any.whl",
    PYPI + "pkg-1.0-py3-none-any..x.whl",
    PYPI + "pkg-1.0-1..2-py3-none-any.whl",
    PYPI + "p..kg-1.0-py3-none-any.whl",
    PYPI + "pkg-1..0-py3-none-any.whl",                    # version: no '..'
    PYPI + "pkg-.1.0-py3-none-any.whl",                    # version: first character alphanumeric
    PYPI + "pkg-_1.0-py3-none-any.whl",
    PYPI + "pkg-+1.0-py3-none-any.whl",
    PYPI + "pkg-1.0~rc1-py3-none-any.whl",                 # version outside [A-Za-z0-9_.!+]
    PYPI + "pkg-1.0$-py3-none-any.whl",
    PYPI + "pkg-1.0@x-py3-none-any.whl",
    PYPI + "pkg-1.0;x-py3-none-any.whl",
])
def test_artifact_url_refuses_double_dot_in_wheel_name_and_bad_version_syntax(bad_url):
    """Codex E3-r1 point 1: no '..' inside any wheel name element; strict version syntax."""
    gen = load_script("generate_cell_locks_411")
    version = bad_url.rsplit("/", 1)[-1].split("-")[1]
    assert qualified_runtime.valid_artifact_url(bad_url) is False
    assert qualified_runtime.valid_artifact_url(bad_url, name="pkg", version=version) is False
    assert gen._valid_https_url(bad_url, name="pkg", version=version) is False


@pytest.mark.parametrize("good_url, name, version", [
    ("https://files.pythonhosted.org/..cache/pkg-1.0-py3-none-any.whl", "pkg", "1.0"),   # a plain directory name
    ("https://files.pythonhosted.org/packages/..x/pkg-1.0-py3-none-any.whl", "pkg", "1.0"),
    ("https://files.pythonhosted.org/packages/a..b/pkg-1.0-py3-none-any.whl", "pkg", "1.0"),
    (PYPI + "pkg-1!2.0+local.1_x-py3-none-any.whl", "pkg", "1!2.0+local.1_x"),
    (PYPI + "pkg-2026.2.28-cp311-cp311-macosx_11_0_arm64.whl", "pkg", "2026.2.28"),
    (PYPI + "pkg-0.0.22-py3-none-any.whl", "pkg", "0.0.22"),
])
def test_artifact_url_accepts_dotted_directory_names_and_valid_versions(good_url, name, version):
    """Codex E3-r1 point 1: the per-segment '.'/'..' rule is the whole traversal rule ('/..cache/' is fine)."""
    assert qualified_runtime.valid_artifact_url(good_url, name=name, version=version) is True
    for segment in ("/../", "/./"):
        assert qualified_runtime.valid_artifact_url(good_url.replace("/pkg-", segment + "pkg-"), name=name, version=version) is False


def _pylock(packages: str) -> str:
    return 'lock-version = "1.0"\ncreated-by = "test"\nrequires-python = ">=3.11"\n\n' + packages


def _pylock_package(name="pkg", version="1.0", wheels=None, extra="") -> str:
    if wheels is None:
        wheel = synthetic_wheel(name, version)
        wheels = f'[{{ url = "{wheel["url"]}", size = 1024, hashes = {{ sha256 = "{wheel["sha256"]}" }} }}]'
    return f'[[packages]]\nname = "{name}"\nversion = "{version}"\nwheels = {wheels}\n{extra}\n'


def test_pylock_parser_reads_admitted_wheels_and_refuses_everything_else():
    gen = load_script("generate_cell_locks_411")
    one, two = synthetic_wheel("pkg", "1.0"), synthetic_wheel("pkg", "1.0", "cp311-abi3-macosx_10_9_universal2")
    entry = lambda w, more="": f'{{ url = "{w["url"]}", size = {w["size"]}, hashes = {{ sha256 = "{w["sha256"]}" }}{more} }}'  # noqa: E731
    parsed = gen.parse_pylock(_pylock(
        _pylock_package(wheels=f'[{entry(one)}, {entry(two, more=", name = " + json.dumps(two["filename"]))}]')
        + _pylock_package("Other_Pkg", "2.0")
    ))
    assert [item["name"] for item in parsed] == ["other-pkg", "pkg"]
    assert parsed[1] == {"name": "pkg", "version": "1.0", "wheels": sorted([one, two], key=lambda w: w["filename"])}

    sha = one["sha256"]
    sdist = 'sdist = { url = "https://files.pythonhosted.org/packages/ab/cd/pkg-1.0.tar.gz", hashes = { sha256 = "' + sha + '" } }'
    refusals = {
        "sdist": _pylock(_pylock_package(extra=sdist)),
        "sdist-only": _pylock('[[packages]]\nname = "pkg"\nversion = "1.0"\n' + sdist + "\n"),
        "no-wheel": _pylock(_pylock_package(wheels="[]")),
        "wheels-absent": _pylock('[[packages]]\nname = "pkg"\nversion = "1.0"\n'),
        "marker": _pylock(_pylock_package(extra='marker = "sys_platform == \'linux\'"')),
        "vcs": _pylock(_pylock_package(extra='vcs = { type = "git", url = "https://example.invalid/x.git" }')),
        "directory": _pylock(_pylock_package(extra='directory = { path = "." }')),
        "bad-host": _pylock(_pylock_package(
            wheels="[" + entry({**one, "url": one["url"].replace("files.pythonhosted.org", "evil.example")}) + "]")),
        "http": _pylock(_pylock_package(wheels="[" + entry({**one, "url": one["url"].replace("https://", "http://")}) + "]")),
        "other-package-wheel": _pylock(_pylock_package(wheels="[" + entry(synthetic_wheel("other", "1.0")) + "]")),
        "other-version-wheel": _pylock(_pylock_package(wheels="[" + entry(synthetic_wheel("pkg", "1.1")) + "]")),
        "tar.gz-as-wheel": _pylock(_pylock_package(wheels="[" + entry({**one, "url": PYPI + "pkg-1.0.tar.gz"}) + "]")),
        "filename-mismatch": _pylock(_pylock_package(wheels="[" + entry(one, more=', name = "pkg-1.0-py2-none-any.whl"') + "]")),
        "sha-short": _pylock(_pylock_package(wheels="[" + entry({**one, "sha256": sha[:63]}) + "]")),
        "sha-uppercase": _pylock(_pylock_package(wheels="[" + entry({**one, "sha256": sha.upper()}) + "]")),
        "sha-not-hex": _pylock(_pylock_package(wheels="[" + entry({**one, "sha256": "g" * 64}) + "]")),
        "no-sha256": _pylock(_pylock_package(wheels='[{ url = "' + one["url"] + '", size = 1, hashes = { md5 = "' + "a" * 32 + '" } }]')),
        "extra-hash-algorithm": _pylock(_pylock_package(
            wheels='[{ url = "' + one["url"] + '", size = 1, hashes = { sha256 = "' + sha + '", md5 = "' + "a" * 32 + '" } }]')),
        "size-zero": _pylock(_pylock_package(wheels="[" + entry({**one, "size": 0}) + "]")),
        "size-absent": _pylock(_pylock_package(wheels='[{ url = "' + one["url"] + '", hashes = { sha256 = "' + sha + '" } }]')),
        "wheel-path": _pylock(_pylock_package(wheels="[" + entry(one, more=', path = "./pkg.whl"') + "]")),
        "duplicate-package": _pylock(_pylock_package() + _pylock_package(name="PKG")),
        "duplicate-wheel": _pylock(_pylock_package(wheels=f"[{entry(one)}, {entry(one)}]")),
        "duplicate-sha": _pylock(_pylock_package(wheels=f"[{entry(one)}, {entry({**two, 'sha256': sha})}]")),
        "loose-version": _pylock(_pylock_package(version=">=1.0")),
        "lock-version": _pylock(_pylock_package()).replace('lock-version = "1.0"', 'lock-version = "2.0"'),
        "unknown-top-key": _pylock(_pylock_package()).replace("created-by", 'environments = ["x"]\ncreated-by'),
        "no-packages": _pylock(""),
        "not-toml": "lock-version = ",
        "duplicate-toml-key": _pylock(_pylock_package(extra='version = "1.0"')),
    }
    for label, text in refusals.items():
        with pytest.raises(ValueError):
            gen.parse_pylock(text)
            pytest.fail(f"pylock must be refused: {label}")


def test_frozen_builder_refuses_any_disagreement_between_pylock_compiled_lock_and_constraints():
    gen = load_script("generate_cell_locks_411")
    cell_id = "macos-arm64-py312"
    expected = qualified_runtime.expected_versions(profile="lexical", extras=("ops", "brief"), cell_id=cell_id)
    ref_cat = json.loads((RESOURCES / "qualified_runtime_lexical_v1.json").read_text(encoding="utf-8"))
    ml_pkgs = gen._ml_closure_names(RESOURCES)
    compiled, pylock = synthetic_frozen_texts(expected)

    def build(compiled_text=compiled, pylock_text=pylock, **kwargs):
        return gen.build_cell_lock_files(
            cell_id, compiled_text, pylock_text.encode("utf-8"), reference_catalog=ref_cat, ml_packages=ml_pkgs, **kwargs,
        )

    catalog_doc, constraints_text, manifest = build()
    assert catalog_doc["lock_state"] == "frozen" and catalog_doc["recorded_at"] == "2026-11-24"
    assert constraints_text.encode("utf-8") == qualified_runtime.CELL_LOCKS[("lexical", cell_id)][1].read_bytes()
    assert set(manifest) == qualified_runtime._MANIFEST_KEYS
    assert manifest["schema_version"] == qualified_runtime.MANIFEST_SCHEMA_VERSION == gen.MANIFEST_SCHEMA_VERSION == 2
    assert "--python-version 3.12 --python-platform aarch64-apple-darwin --only-binary :all:" in manifest["source"]
    assert "--exclude-newer 2026-11-24T00:00:00Z" in manifest["source"]

    numpy_wheel = synthetic_wheel("numpy", expected["numpy"])
    extra = synthetic_wheel("extra-pkg", "1.0")
    cases = {
        # a package the constraints do not know
        "package-absent-from-constraints": (compiled, pylock + "\n" + _pylock_package("extra-pkg", "1.0")),
        # a package of the constraints without any locked wheel
        "package-missing-from-pylock": (compiled, synthetic_frozen_texts({k: v for k, v in expected.items() if k != "numpy"})[1]),
        # another version than the constraints
        "version-differs": (compiled, synthetic_frozen_texts({**expected, "numpy": "2.4.3"})[1]),
        # a wheel whose SHA-256 is not one of the hashes of the compiled lock
        "sha-unknown-to-compiled-lock": (compiled, pylock.replace(numpy_wheel["sha256"], "f" * 64)),
        "compiled-has-extra-package": (compiled + f"extra-pkg==1.0 --hash=sha256:{extra['sha256']}\n", pylock),
    }
    for label, (compiled_text, pylock_text) in cases.items():
        with pytest.raises(ValueError):
            build(compiled_text, pylock_text)
            pytest.fail(f"must be refused: {label}")
    with pytest.raises(ValueError, match="sdist"):
        build(pylock_text=pylock.replace(
            'name = "numpy"\n', 'name = "numpy"\nsdist = { url = "https://files.pythonhosted.org/packages/ab/cd/numpy-2.4.2.tar.gz" }\n'))
    with pytest.raises(ValueError, match="resolved_at"):
        build(resolved_at="24/11/2026")
    with pytest.raises(ValueError, match="exclude_newer"):
        build(exclude_newer="2026-11-24")
    # Cell 1: the preserved historical constraints must agree with the frozen lock.
    with pytest.raises(ValueError, match="preserved constraints"):
        gen.build_cell_lock_files(
            "macos-arm64-py311", compiled, pylock.encode("utf-8"), reference_catalog=ref_cat, ml_packages=ml_pkgs,
            existing_constraints_bytes=b"numpy==2.4.3\n",
        )


def test_frozen_generation_refuses_bad_pylock_before_writing_and_check_detects_drift(tmp_path):
    gen = load_script("generate_cell_locks_411")
    out_dir = tmp_path / "resources"
    shutil.copytree(RESOURCES, out_dir)
    expected = qualified_runtime.expected_versions(profile="lexical", extras=("ops", "brief"), cell_id="macos-arm64-py311")
    inputs = write_synthetic_frozen_inputs(gen, tmp_path / "inputs", expected)
    qr_copy = tmp_path / "qualified_runtime.py"
    con_copy = tmp_path / "memory_os_constraints.py"
    shutil.copy2(ROOT / "v4/src/memory_os_v4/ops/qualified_runtime.py", qr_copy)
    shutil.copy2(ROOT / "v4/src/memory_os_constraints.py", con_copy)
    snapshot = {path.name: path.read_bytes() for path in out_dir.iterdir()}
    snapshot_code = (qr_copy.read_bytes(), con_copy.read_bytes())

    def run(check_only=False):
        return gen.generate_all(
            inputs, out_dir, check_only=check_only, qualified_runtime_path=qr_copy, constraints_script_path=con_copy,
        )

    # A sdist in the LAST cell's pylock: nothing at all is written, for any cell.
    assert gen.RETAINED_CELLS[-1] == "ubuntu2404-x86_64-py311"
    last_pylock = inputs["ubuntu2404-x86_64-py311"][1]
    good = last_pylock.read_text(encoding="utf-8")
    last_pylock.write_text(good.replace(
        'name = "numpy"\n', 'name = "numpy"\nsdist = { url = "https://files.pythonhosted.org/packages/ab/cd/numpy-2.4.2.tar.gz" }\n',
    ), encoding="utf-8")
    with pytest.raises(ValueError, match="sdist"):
        run()
    assert {path.name: path.read_bytes() for path in out_dir.iterdir()} == snapshot
    assert (qr_copy.read_bytes(), con_copy.read_bytes()) == snapshot_code
    last_pylock.write_text(good, encoding="utf-8")

    written = run()
    assert len(written["written"]) == 15 and run(check_only=True)["mismatches"] == []
    # The historical cell-1 catalog and constraints, and the constraints of every cell, are byte-identical.
    for name, payload in snapshot.items():
        if "constraints" in name or name in {"qualified_runtime_lexical_v1.json", "qualified_runtime_v1.json"}:
            assert (out_dir / name).read_bytes() == payload, name
    assert con_copy.read_bytes() == snapshot_code[1]

    # --check sees a pylock that no longer matches the embedded manifest (its SHA-256 is recorded).
    last_pylock.write_text(good + "\n", encoding="utf-8")
    drift = run(check_only=True)["mismatches"]
    assert drift == ["qualified_runtime_manifest_lexical_ubuntu2404-x86_64-py311_v1.json"]
    last_pylock.write_text(good, encoding="utf-8")
    arm64_pylock = inputs["ubuntu2404-arm64-py311"][1]
    arm64_pylock.write_text(good + "\n", encoding="utf-8")
    assert run(check_only=True)["mismatches"] == ["qualified_runtime_manifest_lexical_ubuntu2404-arm64-py311_v1.json"]
    arm64_pylock.write_text(good, encoding="utf-8")
    # --check runs the runtime attestation on disk: a manifest edited by hand is caught twice.
    man_path = out_dir / "qualified_runtime_manifest_lexical_macos-arm64-py313_v1.json"
    mdoc = json.loads(man_path.read_text(encoding="utf-8"))
    mdoc["artifacts"][0]["wheels"][0]["url"] = None
    man_path.write_text(json.dumps(mdoc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    drift = run(check_only=True)["mismatches"]
    assert man_path.name in drift and "attestation:lexical:macos-arm64-py313" in drift
    # A missing cell input is refused.
    with pytest.raises(ValueError, match="missing frozen input"):
        gen.generate_all({k: v for k, v in inputs.items() if k != "macos-arm64-py313"}, out_dir)


def test_generator_cli_takes_the_frozen_input_dir_and_no_longer_knows_preliminary(tmp_path, capsys):
    gen = load_script("generate_cell_locks_411")
    out_dir = tmp_path / "resources"
    shutil.copytree(RESOURCES, out_dir)
    expected = qualified_runtime.expected_versions(profile="lexical", extras=("ops", "brief"), cell_id="macos-arm64-py311")
    write_synthetic_frozen_inputs(gen, tmp_path / "inputs", expected)
    qr_copy = tmp_path / "qualified_runtime.py"
    con_copy = tmp_path / "memory_os_constraints.py"
    shutil.copy2(ROOT / "v4/src/memory_os_v4/ops/qualified_runtime.py", qr_copy)
    shutil.copy2(ROOT / "v4/src/memory_os_constraints.py", con_copy)
    common = ["--input-dir", str(tmp_path / "inputs"), "--output-dir", str(out_dir),
              "--qualified-runtime", str(qr_copy), "--constraints-script", str(con_copy)]
    assert gen.main([*common, "--check"]) == 1 and "MISMATCH" in capsys.readouterr().err   # synthetic != embedded
    assert gen.main(common) == 0 and "WRITTEN" in capsys.readouterr().out
    assert gen.main([*common, "--check"]) == 0 and capsys.readouterr().out.startswith("OK")
    for removed in (["--lock-state", "preliminary"], ["--urls-json", "x.json"], ["--recorded-at", "2026-11-23"]):
        with pytest.raises(SystemExit):
            gen.main([*common, *removed])
        capsys.readouterr()
    assert not hasattr(gen, "DEFAULT_INPUT_FILES")


def test_bootstrap_cell_entry_lists_every_admitted_wheel_of_the_frozen_manifest():
    gen = load_script("generate_cell_locks_411")
    for cell_id in RETAINED_CELLS:
        _, con, man = qualified_runtime.CELL_LOCKS[("lexical", cell_id)]
        entry = gen.build_bootstrap_cell_entry(cell_id, constraints_path=con, manifest_path=man)
        assert len(entry["constraints"]) == 36 and len(entry["artifacts"]) == FROZEN_WHEEL_COUNTS[cell_id]
        assert all(item["url"].startswith("https://files.pythonhosted.org/packages/") for item in entry["artifacts"])
        synthetic = gen.build_bootstrap_cell_entry(cell_id, constraints_path=con, manifest_path=man, test_values=True)
        assert len(synthetic["artifacts"]) == 36 and all("example.invalid" in item["url"] for item in synthetic["artifacts"])
    _, con, man = qualified_runtime.CELL_LOCKS[("lexical", "macos-arm64-py312")]
    with pytest.raises(ValueError, match="frozen manifest"):
        gen.build_bootstrap_cell_entry("macos-arm64-py313", constraints_path=con, manifest_path=man)


def test_classifiers_declare_python_312_313_and_linux():
    """Contract §10 D2: 3.12/3.13 are declared (frozen locks, attested SQLite). 4.1.2 lot A: Linux is declared; it
    covers the retained Linux cells (4.1.3: Ubuntu 24.04 arm64 and x86_64), which the README and notes state."""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    project = tomllib.loads(text)["project"]
    classifiers = project["classifiers"]
    for minor in ("3.11", "3.12", "3.13"):
        assert f"Programming Language :: Python :: {minor}" in classifiers
    assert "Operating System :: MacOS :: MacOS X" in classifiers
    assert [item for item in classifiers if "Linux" in item or "POSIX" in item] == ["Operating System :: POSIX :: Linux"]
    assert "# \"Operating System" not in text     # no commented-out classifier left behind
    import memory_os_v4
    assert project["version"] == memory_os_v4.__version__ == "4.1.3"   # 4.1.3 (D2): no legacy v3 package


# ===================================================================== 8. Codex E3-gel-r1: wheels bound to the cell

MAC_CELLS = ("macos-arm64-py311", "macos-arm64-py312", "macos-arm64-py313")
UBUNTU_CELL = "ubuntu2404-arm64-py311"
X86_CELL = "ubuntu2404-x86_64-py311"


def embedded_manifest(cell_id: str) -> dict:
    return json.loads(qualified_runtime.CELL_LOCKS[("lexical", cell_id)][2].read_text(encoding="utf-8"))


def frozen_texts_from_manifest(manifest: dict) -> tuple[str, str]:
    """``(<cell>.txt, pylock.<cell>.toml)`` texts carrying exactly the wheels of one manifest."""
    compiled, packages = [], []
    for item in manifest["artifacts"]:
        hashes = " ".join(f"--hash=sha256:{wheel['sha256']}" for wheel in item["wheels"])
        compiled.append(f"{item['name']}=={item['version']} {hashes}")
        wheels = ", ".join(
            f'{{ url = "{w["url"]}", size = {w["size"]}, hashes = {{ sha256 = "{w["sha256"]}" }} }}' for w in item["wheels"]
        )
        packages.append(f'[[packages]]\nname = "{item["name"]}"\nversion = "{item["version"]}"\nwheels = [{wheels}]\n')
    return "\n".join(compiled) + "\n", _pylock("\n".join(packages))


@pytest.mark.parametrize("cell_id", RETAINED_CELLS)
def test_every_frozen_wheel_fits_its_cell_and_no_neighbor_lock_fits_another_cell(cell_id):
    names = [wheel["filename"] for item in embedded_manifest(cell_id)["artifacts"] for wheel in item["wheels"]]
    assert len(names) == FROZEN_WHEEL_COUNTS[cell_id]
    assert all(qualified_runtime.wheel_compatible_with_cell(name, cell_id) for name in names)
    for other in RETAINED_CELLS:
        if other != cell_id:
            assert not all(qualified_runtime.wheel_compatible_with_cell(name, other) for name in names), other


@pytest.mark.parametrize("cell_id", [c for c in RETAINED_CELLS if c != "macos-arm64-py311"])
def test_the_generator_rebuilds_each_embedded_lock_with_its_own_resolution_date(cell_id):
    """Every field but the pylock digest is rebuilt from the cell's own wheels; the date is a frozen fact per cell."""
    gen = load_script("generate_cell_locks_411")
    ref_cat = json.loads((RESOURCES / "qualified_runtime_lexical_v1.json").read_text(encoding="utf-8"))
    cat_path, con_path, _ = qualified_runtime.CELL_LOCKS[("lexical", cell_id)]
    embedded = embedded_manifest(cell_id)
    compiled, pylock = frozen_texts_from_manifest(embedded)
    catalog_doc, constraints_text, manifest = gen.build_cell_lock_files(
        cell_id, compiled, pylock.encode("utf-8"), reference_catalog=ref_cat, ml_packages=gen._ml_closure_names(RESOURCES))
    assert catalog_doc == json.loads(cat_path.read_text(encoding="utf-8"))
    assert constraints_text.encode("utf-8") == con_path.read_bytes()
    assert {k: v for k, v in manifest.items() if k != "pylock_sha256"} == \
        {k: v for k, v in embedded.items() if k != "pylock_sha256"}
    assert manifest["resolved_at"] == catalog_doc["recorded_at"] == FROZEN_RESOLVED_AT.get(cell_id, "2026-11-24")
    platform_target = gen.CELL_RESOLUTION[cell_id][1]
    assert f"--python-platform {platform_target} " in manifest["source"]
    assert platform_target == {"ubuntu2404-x86_64-py311": "x86_64-manylinux_2_28",
                               "ubuntu2404-arm64-py311": "aarch64-manylinux_2_28"}.get(cell_id, "aarch64-apple-darwin")
    # An explicit date applies to every cell, as before.
    _, _, forced = gen.build_cell_lock_files(cell_id, compiled, pylock.encode("utf-8"), reference_catalog=ref_cat,
                                             ml_packages=gen._ml_closure_names(RESOURCES), resolved_at="2026-12-01")
    assert forced["resolved_at"] == "2026-12-01"


def test_the_x86_64_cell_is_named_like_the_arm64_one_in_every_generator_table():
    gen = load_script("generate_cell_locks_411")
    x86, arm = "ubuntu2404-x86_64-py311", "ubuntu2404-arm64-py311"
    assert gen.RETAINED_CELLS == cell.RETAINED_CELLS
    assert gen.CELL_LABELS[x86] == "Ubuntu 24.04 x86_64 CPython 3.11"
    assert gen.CELL_RESOLUTION[x86] == ("3.11", "x86_64-manylinux_2_28")
    assert gen.CELL_RESOURCE_NAMES[x86] == tuple(name.replace(arm, x86) for name in gen.CELL_RESOURCE_NAMES[arm])
    assert gen.FROZEN_INPUT_FILES[x86] == (f"{x86}.txt", f"pylock.{x86}.toml")
    assert ("lexical", x86, *gen.CELL_RESOURCE_NAMES[x86]) in gen.ALL_LOCK_RESOURCE_NAMES
    assert tuple(path.name for path in qualified_runtime.CELL_LOCKS[("lexical", x86)]) == gen.CELL_RESOURCE_NAMES[x86]


@pytest.mark.parametrize("filename, admitted_by", [
    ("pkg-1.0-py3-none-any.whl", RETAINED_CELLS),
    ("pkg-1.0-py2.py3-none-any.whl", RETAINED_CELLS),
    ("pkg-1.0-1-py3-none-any.whl", RETAINED_CELLS),                                        # build tag
    ("pkg-1.0-py311-none-any.whl", RETAINED_CELLS),                                        # py3X up to the cell
    ("pkg-1.0-py312-none-any.whl", ("macos-arm64-py312", "macos-arm64-py313")),
    ("pkg-1.0-py313-none-any.whl", ("macos-arm64-py313",)),
    ("pkg-1.0-cp312-none-any.whl", ("macos-arm64-py312",)),
    ("pkg-1.0-cp311-cp311-macosx_11_0_arm64.whl", ("macos-arm64-py311",)),
    ("pkg-1.0-cp312-cp312-macosx_11_0_arm64.whl", ("macos-arm64-py312",)),               # cp312 not on py313
    ("pkg-1.0-cp313-cp313-macosx_10_13_universal2.whl", ("macos-arm64-py313",)),
    ("pkg-1.0-cp312-none-macosx_11_0_arm64.whl", ("macos-arm64-py312",)),
    ("pkg-1.0-cp311-none-macosx_11_0_arm64.whl", ("macos-arm64-py311",)),
    ("pkg-1.0-py3-none-macosx_11_0_arm64.whl", MAC_CELLS),
    ("pkg-1.0-cp38-abi3-macosx_10_9_universal2.whl", MAC_CELLS),
    ("pkg-1.0-cp311-abi3-macosx_10_9_universal2.whl", MAC_CELLS),
    ("pkg-1.0-cp312-abi3-macosx_11_0_arm64.whl", ("macos-arm64-py312", "macos-arm64-py313")),
    ("pkg-1.0-cp313-abi3-macosx_11_0_arm64.whl", ("macos-arm64-py313",)),
    ("pkg-1.0-cp312-cp312-macosx_12_0_arm64.macosx_11_0_arm64.whl", ("macos-arm64-py312",)),   # one triple fits
    ("pkg-1.0-cp311-cp311-manylinux_2_17_aarch64.manylinux2014_aarch64.whl", (UBUNTU_CELL,)),
    ("pkg-1.0-cp311-cp311-manylinux_2_27_aarch64.manylinux_2_28_aarch64.whl", (UBUNTU_CELL,)),
    ("pkg-1.0-cp311-cp311-manylinux_2_39_aarch64.whl", (UBUNTU_CELL,)),
    ("pkg-1.0-cp311-cp311-manylinux_2_40_aarch64.manylinux_2_39_aarch64.whl", (UBUNTU_CELL,)),
    ("pkg-1.0-cp38-abi3-manylinux_2_28_aarch64.whl", (UBUNTU_CELL,)),
    ("pkg-1.0-cp311-none-manylinux_2_17_aarch64.whl", (UBUNTU_CELL,)),
    ("pkg-1.0-cp311-cp311-manylinux_2_28_x86_64.whl", (X86_CELL,)),
    ("pkg-1.0-cp311-cp311-manylinux2014_x86_64.whl", (X86_CELL,)),
    ("pkg-1.0-cp311-cp311-manylinux_2_17_x86_64.manylinux2014_x86_64.whl", (X86_CELL,)),
    ("pkg-1.0-cp311-cp311-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl", (X86_CELL,)),
    ("pkg-1.0-cp311-cp311-manylinux_2_39_x86_64.whl", (X86_CELL,)),
    ("pkg-1.0-cp311-cp311-manylinux_2_40_x86_64.manylinux_2_39_x86_64.whl", (X86_CELL,)),
    ("pkg-1.0-cp38-abi3-manylinux_2_28_x86_64.whl", (X86_CELL,)),
    ("pkg-1.0-cp311-none-manylinux_2_17_x86_64.whl", (X86_CELL,)),
    ("pkg-1.0-py3-none-manylinux_2_17_x86_64.whl", (X86_CELL,)),
    ("pkg-1.0-cp311-cp311-manylinux_2_17_aarch64.manylinux_2_17_x86_64.whl", (UBUNTU_CELL, X86_CELL)),  # both triples
    # --- refused everywhere
    ("pkg-1.0-cp312-cp312-macosx_26_0_arm64.whl", ()),                                    # macOS above the 11.0 target
    ("pkg-1.0-cp312-cp312-macosx_12_0_arm64.whl", ()),
    ("pkg-1.0-cp311-cp311-macosx_26_0_arm64.whl", ()),
    ("pkg-1.0-cp313-cp313-macosx_12_0_universal2.whl", ()),
    ("pkg-1.0-cp38-abi3-macosx_15_0_arm64.whl", ()),
    ("pkg-1.0-cp311-cp311-any.whl", ()),                                                   # cpXY ABI needs a platform
    ("pkg-1.0-cp312-cp312-any.whl", ()),
    ("pkg-1.0-cp312-abi3-any.whl", ()),
    ("pkg-1.0-cp311-cp311-manylinux_2_40_aarch64.whl", ()),                               # glibc above 2.39
    ("pkg-1.0-cp311-cp311-manylinux_3_0_aarch64.whl", ()),
    ("pkg-1.0-cp311-cp311-manylinux_2_16_aarch64.whl", ()),
    ("pkg-1.0-cp311-cp311-musllinux_1_2_aarch64.whl", ()),
    ("pkg-1.0-cp311-cp311-manylinux_2_40_x86_64.whl", ()),                                # glibc above 2.39
    ("pkg-1.0-cp311-cp311-manylinux_3_0_x86_64.whl", ()),
    ("pkg-1.0-cp311-cp311-manylinux_2_16_x86_64.whl", ()),
    ("pkg-1.0-cp311-cp311-manylinux2010_x86_64.whl", ()),                                 # below manylinux2014
    ("pkg-1.0-cp311-cp311-manylinux1_x86_64.whl", ()),
    ("pkg-1.0-cp311-cp311-musllinux_1_2_x86_64.whl", ()),
    ("pkg-1.0-cp311-cp311-linux_x86_64.whl", ()),
    ("pkg-1.0-cp311-cp311-manylinux_2_28_i686.whl", ()),
    ("pkg-1.0-cp311-cp311-manylinux2014_i686.whl", ()),
    ("pkg-1.0-cp312-cp312-manylinux_2_28_x86_64.whl", ()),                                # cp312 on a py311 cell
    ("pkg-1.0-cp313-cp313-manylinux_2_28_x86_64.whl", ()),
    ("pkg-1.0-cp311-cp311-macosx_11_0_x86_64.whl", ()),
    ("pkg-1.0-cp311-cp311-macosx_10_9_intel.whl", ()),
    ("pkg-1.0-cp311-cp311-win_amd64.whl", ()),
    ("pkg-1.0-cp311-cp311-win_arm64.whl", ()),
    ("pkg-1.0-cp311-cp311-linux_aarch64.whl", ()),
    ("pkg-1.0-cp313-cp313t-macosx_11_0_arm64.whl", ()),                                   # free-threaded ABI
    ("pkg-1.0-cp313t-cp313t-macosx_11_0_arm64.whl", ()),
    ("pkg-1.0-cp312-cp311-macosx_11_0_arm64.whl", ()),
    ("pkg-1.0-py3-abi3-any.whl", ()),
    ("pkg-1.0-py2-none-any.whl", ()),
    ("pkg-1.0-py314-none-any.whl", ()),
    ("pkg-1.0-pp311-pypy311_pp73-macosx_11_0_arm64.whl", ()),
    ("pkg-1.0-cp211-cp211-any.whl", ()),
    ("pkg-1.0-py3-none.whl", ()),                                                          # not parsable
    ("pkg-1.0-abc-py3-none-any.whl", ()),
    ("pkg-not_a_version-py3-none-any.whl", ()),
    ("pkg-1.0.tar.gz", ()),
    ("", ()),
])
def test_wheel_tags_are_bound_to_the_cell(filename, admitted_by):
    """Codex E3-gel-r2: the admitted tags are those of ``packaging.tags`` for the frozen target of the cell."""
    for cell_id in RETAINED_CELLS:
        assert qualified_runtime.wheel_compatible_with_cell(filename, cell_id) is (cell_id in admitted_by), cell_id
    assert qualified_runtime.wheel_compatible_with_cell(filename, "debian-x86_64-py311") is False
    assert qualified_runtime.wheel_compatible_with_cell(None, "macos-arm64-py311") is False


def test_cell_wheel_tags_are_the_packaging_tags_of_an_explicit_frozen_target():
    from packaging import tags

    assert qualified_runtime.MACOS_VERSION == (11, 0) and qualified_runtime.LINUX_GLIBC_MAX == (2, 39)
    assert qualified_runtime.CELL_WHEEL_TARGETS == {
        "macos-arm64-py311": ("macos", "arm64", 11), "macos-arm64-py312": ("macos", "arm64", 12),
        "macos-arm64-py313": ("macos", "arm64", 13), UBUNTU_CELL: ("linux", "aarch64", 11),
        X86_CELL: ("linux", "x86_64", 11)}
    mac = list(tags.mac_platforms((11, 0), "arm64"))
    assert mac[:2] == ["macosx_11_0_arm64", "macosx_11_0_universal2"] and "macosx_10_9_universal2" in mac
    linux = [f"manylinux_2_{m}_aarch64" for m in range(39, 16, -1)] + ["manylinux2014_aarch64"]
    linux_x86 = [f"manylinux_2_{m}_x86_64" for m in range(39, 16, -1)] + ["manylinux2014_x86_64"]
    for cell_id, platforms in ((UBUNTU_CELL, linux), (X86_CELL, linux_x86)):
        seen = {tag.platform for tag in qualified_runtime.cell_wheel_tags(cell_id)}
        other = "x86_64" if cell_id == UBUNTU_CELL else "aarch64"
        assert seen == {*platforms, "any"} and not any(other in p or "musllinux" in p or p.startswith("linux_")
                                                       or p.startswith(("manylinux1_", "manylinux2010_")) for p in seen)
    for cell_id, minor, platforms in (("macos-arm64-py311", 11, mac), ("macos-arm64-py312", 12, mac),
                                      ("macos-arm64-py313", 13, mac), (UBUNTU_CELL, 11, linux),
                                      (X86_CELL, 11, linux_x86)):
        expected = frozenset((
            *tags.cpython_tags(python_version=(3, minor), abis=[f"cp3{minor}"], platforms=platforms),
            *tags.compatible_tags(python_version=(3, minor), interpreter=f"cp3{minor}", platforms=platforms),
        ))
        assert qualified_runtime.cell_wheel_tags(cell_id) == expected
        platforms_seen = {tag.platform for tag in expected}
        assert platforms_seen == {*platforms, "any"}
        foreign = "aarch64" if cell_id == X86_CELL else "x86_64"
        assert not any("musllinux" in p or p.startswith("linux_") or foreign in p for p in platforms_seen)
    assert set(qualified_runtime.CELL_WHEEL_TARGETS) == set(RETAINED_CELLS)


@pytest.mark.parametrize("source_cell, target_cell", [
    (UBUNTU_CELL, "macos-arm64-py312"),            # Codex counter-example: the Ubuntu lock given to a macOS cell
    ("macos-arm64-py311", "macos-arm64-py312"),    # the py311 lock given to py312
    ("macos-arm64-py312", "macos-arm64-py313"),    # cp312 wheels in the py313 cell
    ("macos-arm64-py311", UBUNTU_CELL),
    (UBUNTU_CELL, X86_CELL),                       # the arm64 lock given to the x86_64 cell
    (X86_CELL, UBUNTU_CELL),                       # and the reverse
    (X86_CELL, "macos-arm64-py311"),
    ("macos-arm64-py311", X86_CELL),
])
def test_lock_of_another_cell_is_refused_by_generator_check_and_runtime(tmp_path, monkeypatch, capsys, source_cell, target_cell):
    gen = load_script("generate_cell_locks_411")
    ref_cat = json.loads((RESOURCES / "qualified_runtime_lexical_v1.json").read_text(encoding="utf-8"))
    ml_pkgs = gen._ml_closure_names(RESOURCES)
    foreign_compiled, foreign_pylock = frozen_texts_from_manifest(embedded_manifest(source_cell))
    own_compiled, own_pylock = frozen_texts_from_manifest(embedded_manifest(target_cell))

    # The own lock of the cell rebuilds the embedded manifest artifacts exactly.
    _, _, own_manifest = gen.build_cell_lock_files(
        target_cell, own_compiled, own_pylock.encode("utf-8"), reference_catalog=ref_cat, ml_packages=ml_pkgs)
    assert own_manifest["artifacts"] == embedded_manifest(target_cell)["artifacts"]

    # 1. Generator: refused.
    with pytest.raises(ValueError, match="not compatible with the cell"):
        gen.build_cell_lock_files(
            target_cell, foreign_compiled, foreign_pylock.encode("utf-8"), reference_catalog=ref_cat, ml_packages=ml_pkgs)

    # 2. Generation and --check with the foreign lock as input of the cell: refused, nothing written.
    out_dir = tmp_path / "resources"
    shutil.copytree(RESOURCES, out_dir)
    in_dir = tmp_path / "inputs"
    in_dir.mkdir()
    inputs = gen.frozen_input_files(in_dir)
    for cell_id, (txt_path, pylock_path) in inputs.items():
        compiled, pylock = frozen_texts_from_manifest(embedded_manifest(source_cell if cell_id == target_cell else cell_id))
        txt_path.write_text(compiled, encoding="utf-8")
        pylock_path.write_text(pylock, encoding="utf-8")
    qr_copy = tmp_path / "qualified_runtime.py"
    con_copy = tmp_path / "memory_os_constraints.py"
    shutil.copy2(ROOT / "v4/src/memory_os_v4/ops/qualified_runtime.py", qr_copy)
    shutil.copy2(ROOT / "v4/src/memory_os_constraints.py", con_copy)
    snapshot = {path.name: path.read_bytes() for path in out_dir.iterdir()}
    common = ["--input-dir", str(in_dir), "--output-dir", str(out_dir),
              "--qualified-runtime", str(qr_copy), "--constraints-script", str(con_copy)]
    with pytest.raises(ValueError, match="not compatible with the cell"):
        gen.main(common)
    # --check: reported as a MISMATCH with exit code 1, never as a traceback.
    capsys.readouterr()
    assert gen.main([*common, "--check"]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err.startswith("MISMATCH: ")
    assert f"input:lexical:{target_cell} (" in captured.err and "not compatible with the cell" in captured.err
    assert {path.name: path.read_bytes() for path in out_dir.iterdir()} == snapshot

    # 3. A manifest written by hand with the foreign wheels, anchors regenerated to match:
    #    --check (runtime attestation on disk) and the runtime itself both refuse it.
    cat, con, man = (out_dir / path.name for path in qualified_runtime.CELL_LOCKS[("lexical", target_cell)])
    forged = {**embedded_manifest(target_cell), "artifacts": embedded_manifest(source_cell)["artifacts"]}
    man.write_text(json.dumps(forged, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    gen.sync_qualified_runtime_digests(qr_copy, out_dir, check_only=False)
    gen.sync_constraints_script_digests(con_copy, out_dir, check_only=False)
    anchored = gen._parse_generated_dict(qr_copy, gen.DIGESTS_BEGIN, gen.DIGESTS_END)
    assert f"attestation:lexical:{target_cell}" in gen.validate_all_triplets_on_disk(out_dir, expected_digests_map=anchored)
    assert qualified_runtime.validate_lock_files(
        target_cell, profile="lexical", catalog_path=cat, constraints_path=con, manifest_path=man,
        expected_digests=anchored[("lexical", target_cell)], resources_dir=out_dir,
    ) is None
    monkeypatch.setattr(qualified_runtime, "CELL_LOCKS", {**qualified_runtime.CELL_LOCKS, ("lexical", target_cell): (cat, con, man)})
    monkeypatch.setattr(qualified_runtime, "CELL_LOCK_DIGESTS", {**qualified_runtime.CELL_LOCK_DIGESTS, **anchored})
    assert qualified_runtime.cell_lock(target_cell, profile="lexical") is None
    assert qualified_runtime.inspect_current(profile="lexical", extras=("ops", "brief"), cell_id=target_cell)["state"] == "lock_unavailable"
    # Control: the authentic manifest, same anchoring path, is accepted.
    man.write_bytes(snapshot[man.name])
    gen.sync_qualified_runtime_digests(qr_copy, out_dir, check_only=False)
    monkeypatch.setattr(qualified_runtime, "CELL_LOCK_DIGESTS", {
        **qualified_runtime.CELL_LOCK_DIGESTS, **gen._parse_generated_dict(qr_copy, gen.DIGESTS_BEGIN, gen.DIGESTS_END)})
    assert qualified_runtime.cell_lock(target_cell, profile="lexical") is not None


@pytest.mark.parametrize("cell_id, tags", [
    (UBUNTU_CELL, "cp311-cp311-musllinux_1_2_aarch64"),
    (UBUNTU_CELL, "cp311-cp311-manylinux_2_28_x86_64"),
    (UBUNTU_CELL, "cp311-cp311-manylinux_2_40_aarch64"),
    (UBUNTU_CELL, "cp311-cp311-macosx_11_0_arm64"),
    (UBUNTU_CELL, "cp312-cp312-manylinux_2_28_aarch64"),
    (UBUNTU_CELL, "cp311-cp311-linux_aarch64"),
    (UBUNTU_CELL, "cp311-cp311-any"),
    (UBUNTU_CELL, "cp311-cp311-manylinux2014_x86_64"),
    (X86_CELL, "cp311-cp311-musllinux_1_2_x86_64"),
    (X86_CELL, "cp311-cp311-linux_x86_64"),
    (X86_CELL, "cp311-cp311-manylinux_2_28_aarch64"),
    (X86_CELL, "cp311-cp311-manylinux_2_17_aarch64.manylinux2014_aarch64"),
    (X86_CELL, "cp311-cp311-manylinux_2_40_x86_64"),
    (X86_CELL, "cp311-cp311-manylinux2010_x86_64"),
    (X86_CELL, "cp311-cp311-manylinux_2_28_i686"),
    (X86_CELL, "cp312-cp312-manylinux_2_28_x86_64"),
    (X86_CELL, "cp311-cp311-macosx_11_0_x86_64"),
    (X86_CELL, "cp311-cp311-win_amd64"),
    (X86_CELL, "cp311-cp311-any"),
    ("macos-arm64-py313", "cp312-cp312-macosx_11_0_arm64"),
    ("macos-arm64-py313", "cp313-cp313t-macosx_11_0_arm64"),
    ("macos-arm64-py313", "cp313-cp313-macosx_26_0_arm64"),
    ("macos-arm64-py313", "cp313-cp313-macosx_12_0_arm64"),
    ("macos-arm64-py312", "cp311-cp311-macosx_11_0_arm64"),
    ("macos-arm64-py312", "cp312-cp312-macosx_11_0_x86_64"),
    ("macos-arm64-py312", "cp312-cp312-macosx_26_0_arm64"),          # Codex E3-gel-r2: macOS version ignored
    ("macos-arm64-py312", "cp312-cp312-macosx_12_0_arm64"),
    ("macos-arm64-py312", "cp312-cp312-any"),                        # Codex E3-gel-r2: refused by installers
    ("macos-arm64-py311", "cp311-cp311-macosx_26_0_arm64"),
    ("macos-arm64-py311", "cp311-cp311-macosx_12_0_arm64"),
    ("macos-arm64-py311", "cp311-cp311-win_amd64"),
    ("macos-arm64-py311", "cp311-cp311-manylinux_2_28_aarch64"),
])
def test_single_incompatible_wheel_is_refused_by_generator_and_runtime(tmp_path, monkeypatch, cell_id, tags):
    gen = load_script("generate_cell_locks_411")
    manifest = embedded_manifest(cell_id)
    first = manifest["artifacts"][0]
    bad = synthetic_wheel(first["name"], first["version"], tags)
    first["wheels"] = [bad]
    compiled, pylock = frozen_texts_from_manifest(manifest)
    ref_cat = json.loads((RESOURCES / "qualified_runtime_lexical_v1.json").read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="not compatible with the cell"):
        gen.build_cell_lock_files(
            cell_id, compiled, pylock.encode("utf-8"), reference_catalog=ref_cat, ml_packages=gen._ml_closure_names(RESOURCES))

    src_cat, src_con, src_man = qualified_runtime.CELL_LOCKS[("lexical", cell_id)]
    cat, con, man = tmp_path / src_cat.name, tmp_path / src_con.name, tmp_path / src_man.name
    shutil.copy2(src_cat, cat)
    shutil.copy2(src_con, con)

    def locked(document) -> bool:
        man.write_text(json.dumps(document), encoding="utf-8")
        digests = tuple(hashlib.sha256(path.read_bytes()).hexdigest() for path in (cat, con, man))
        monkeypatch.setattr(qualified_runtime, "CELL_LOCKS", {**qualified_runtime.CELL_LOCKS, ("lexical", cell_id): (cat, con, man)})
        monkeypatch.setattr(qualified_runtime, "CELL_LOCK_DIGESTS", {**qualified_runtime.CELL_LOCK_DIGESTS, ("lexical", cell_id): digests})
        return qualified_runtime.cell_lock(cell_id, profile="lexical") is not None

    assert locked(manifest) is False
    first["wheels"] = [synthetic_wheel(first["name"], first["version"])]   # py3-none-any: the same edit, compatible
    assert locked(manifest) is True


@pytest.mark.parametrize("cell_id, tags", [
    ("macos-arm64-py312", "cp312-none-macosx_11_0_arm64"),          # Codex E3-gel-r2: valid, was refused
    ("macos-arm64-py312", "py312-none-any"),
    ("macos-arm64-py312", "cp38-abi3-macosx_10_9_universal2"),
    ("macos-arm64-py312", "cp312-cp312-macosx_10_13_universal2"),
    ("macos-arm64-py311", "cp311-none-macosx_11_0_arm64"),
    ("macos-arm64-py313", "cp313-cp313-macosx_11_0_arm64"),
    (UBUNTU_CELL, "cp311-cp311-manylinux_2_39_aarch64"),
    (UBUNTU_CELL, "cp311-cp311-manylinux_2_17_aarch64.manylinux2014_aarch64"),
    (UBUNTU_CELL, "cp38-abi3-manylinux_2_28_aarch64"),
    (X86_CELL, "cp311-cp311-manylinux_2_39_x86_64"),
    (X86_CELL, "cp311-cp311-manylinux_2_17_x86_64.manylinux2014_x86_64"),
    (X86_CELL, "cp38-abi3-manylinux_2_28_x86_64"),
    (X86_CELL, "py3-none-any"),
])
def test_single_compatible_wheel_is_accepted_by_generator_and_runtime(tmp_path, monkeypatch, cell_id, tags):
    gen = load_script("generate_cell_locks_411")
    manifest = embedded_manifest(cell_id)
    first = manifest["artifacts"][0]
    first["wheels"] = [synthetic_wheel(first["name"], first["version"], tags)]
    compiled, pylock = frozen_texts_from_manifest(manifest)
    ref_cat = json.loads((RESOURCES / "qualified_runtime_lexical_v1.json").read_text(encoding="utf-8"))
    _, _, built = gen.build_cell_lock_files(
        cell_id, compiled, pylock.encode("utf-8"), reference_catalog=ref_cat, ml_packages=gen._ml_closure_names(RESOURCES))
    assert built["artifacts"] == manifest["artifacts"]

    src_cat, src_con, src_man = qualified_runtime.CELL_LOCKS[("lexical", cell_id)]
    cat, con, man = tmp_path / src_cat.name, tmp_path / src_con.name, tmp_path / src_man.name
    shutil.copy2(src_cat, cat)
    shutil.copy2(src_con, con)
    man.write_text(json.dumps(manifest), encoding="utf-8")
    digests = tuple(hashlib.sha256(path.read_bytes()).hexdigest() for path in (cat, con, man))
    monkeypatch.setattr(qualified_runtime, "CELL_LOCKS", {**qualified_runtime.CELL_LOCKS, ("lexical", cell_id): (cat, con, man)})
    monkeypatch.setattr(qualified_runtime, "CELL_LOCK_DIGESTS", {**qualified_runtime.CELL_LOCK_DIGESTS, ("lexical", cell_id): digests})
    assert qualified_runtime.cell_lock(cell_id, profile="lexical") is not None


def test_check_reports_invalid_or_missing_inputs_as_mismatches_without_traceback(tmp_path, capsys):
    gen = load_script("generate_cell_locks_411")
    out_dir = tmp_path / "resources"
    shutil.copytree(RESOURCES, out_dir)
    in_dir = tmp_path / "inputs"
    in_dir.mkdir()
    inputs = gen.frozen_input_files(in_dir)
    for cell_id, (txt_path, pylock_path) in inputs.items():
        compiled, pylock = frozen_texts_from_manifest(embedded_manifest(cell_id))
        txt_path.write_text(compiled, encoding="utf-8")
        pylock_path.write_text(pylock, encoding="utf-8")
    qr_copy = tmp_path / "qualified_runtime.py"
    con_copy = tmp_path / "memory_os_constraints.py"
    shutil.copy2(ROOT / "v4/src/memory_os_v4/ops/qualified_runtime.py", qr_copy)
    shutil.copy2(ROOT / "v4/src/memory_os_constraints.py", con_copy)
    common = ["--input-dir", str(in_dir), "--output-dir", str(out_dir),
              "--qualified-runtime", str(qr_copy), "--constraints-script", str(con_copy), "--check"]
    # These inputs carry the embedded wheels but another pylock text: only the manifests differ (pylock_sha256).
    assert gen.main(common) == 1
    assert "input:" not in capsys.readouterr().err

    txt_path, pylock_path = inputs["macos-arm64-py313"]
    good = pylock_path.read_text(encoding="utf-8")
    snapshot = {path.name: path.read_bytes() for path in out_dir.iterdir()}
    for label, text in {
        "sdist": good.replace('name = "numpy"\n', 'name = "numpy"\nsdist = { url = "https://files.pythonhosted.org/packages/ab/cd/numpy-2.4.2.tar.gz" }\n'),
        "macos-26": good.replace("macosx_11_0_arm64", "macosx_26_0_arm64"),
        "not-toml": "lock-version = ",
        "missing-file": None,
    }.items():
        if text is None:
            pylock_path.unlink()
        else:
            assert text != good, label
            pylock_path.write_text(text, encoding="utf-8")
        assert gen.main(common) == 1, label
        captured = capsys.readouterr()
        assert captured.out == "" and captured.err.startswith("MISMATCH: "), label
        assert "input:lexical:macos-arm64-py313 (" in captured.err and "Traceback" not in captured.err, label
        assert "input:lexical:macos-arm64-py312" not in captured.err, label
    assert {path.name: path.read_bytes() for path in out_dir.iterdir()} == snapshot


# ===================================================================== 4.1.3 Lot 3: VS Code on Linux

def test_vscode_on_linux_uses_the_copilot_user_configuration_and_macos_is_unchanged(tmp_path):
    vscode = next(item for item in clients.CLIENTS if item.key == "vscode")
    linux = vscode.on("linux")
    assert linux.fmt == "copilot-json" and "COPILOT_HOME" in linux.override_env
    assert linux.config_path(tmp_path, platform="linux", environ={}) == tmp_path / ".copilot/mcp-config.json"
    mac = vscode.on("darwin")
    assert mac == vscode and mac.fmt == "vscode" and "COPILOT_HOME" not in mac.override_env
    assert mac.config_path(tmp_path, platform="darwin", environ={"COPILOT_HOME": "/elsewhere"}) == \
        tmp_path / "Library/Application Support/Code/User/mcp.json"            # macOS: unchanged, no new guard
    snippet = json.loads(clients.snippet(linux, "/py", {"MEMORY_V4_URL": "u"}))
    entry = snippet["mcpServers"][clients.SERVER]
    assert entry["type"] == "stdio" and entry["tools"] == ["*"] and entry["args"] == ["-m", "memory_os_v4.ops.relay"]
    assert "servers" in json.loads(clients.snippet(mac, "/py", {}))


@pytest.mark.parametrize("value", ["/elsewhere/.copilot", ""])
def test_copilot_home_refuses_vscode_on_linux_even_empty(tmp_path, value):
    linux = next(item for item in clients.CLIENTS if item.key == "vscode").on("linux")
    with pytest.raises(clients.ClientPathError) as err:
        linux.config_path(tmp_path, platform="linux", environ={"COPILOT_HOME": value})
    assert err.value.code == "client_env_override" and "COPILOT_HOME" in str(err.value)


def test_vscode_install_and_uninstall_on_linux_write_the_copilot_file(ubuntu):
    c = ubuntu
    target = c.home / ".copilot/mcp-config.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"mcpServers": {"other": {"type": "stdio", "command": "x"}}}, indent=2) + "\n")
    mac_file = c.home / "Library/Application Support/Code/User/mcp.json"
    mac_before = mac_file.read_bytes() if mac_file.exists() else None
    result = install(c, profile="lexical", clients=("vscode",))
    assert result["ok"] is True, json.dumps({k: result.get(k) for k in ("stage", "error", "code", "clients")}, default=str)[:3000]
    data = json.loads(target.read_text())
    assert data["mcpServers"]["other"] == {"type": "stdio", "command": "x"}          # foreign entry preserved
    ours = data["mcpServers"][clients.SERVER]
    assert ours["type"] == "stdio" and ours["tools"] == ["*"] and ours["env"]["MEMORY_V4_CLIENT_ID"] == "vscode"
    from memory_os_v4.ops.auth import read_token
    token = read_token(c.settings.token_file)
    assert token and token not in target.read_text()            # the token file path only, never its value
    assert (mac_file.read_bytes() if mac_file.exists() else None) == mac_before   # the macOS file is never touched
    plan = uninstall.plan(c.settings, home=c.home, python=c.python, run=c.system, clients=("vscode",))
    assert plan["ok"] is True, plan.get("blockers")
    assert [item["action"] for item in plan["clients"] if item["client"] == "vscode"] == ["remove"], plan["clients"]
    applied = uninstall.apply(plan, c.settings, home=c.home, run=c.system, sleep=lambda s: None)
    assert applied["ok"] is True
    after = json.loads(target.read_text())
    assert clients.SERVER not in after["mcpServers"] and after["mcpServers"]["other"] == {"type": "stdio", "command": "x"}


def test_vscode_on_linux_with_copilot_home_is_refused_by_quickstart(ubuntu, monkeypatch):
    c = ubuntu
    monkeypatch.setenv("COPILOT_HOME", str(c.home / "relocated"))
    before = tree(c.root)
    plan = prepare(c, profile="lexical", clients=("vscode",))
    assert plan["ok"] is False and "client_env_override" in plan["blocker_codes"]
    assert tree(c.root) == before


def test_only_tools_star_is_the_strict_relay_form():
    base = {"type": "stdio", "command": "/usr/bin/python3.11", "args": ["-m", "memory_os_v4.ops.relay"],
            "env": {"MEMORY_V4_URL": "u", "MEMORY_V4_TOKEN_FILE": "t"}}
    assert qs.relay_form({**base, "tools": ["*"]}) is True
    assert qs.relay_form({**base, "tools": ["memory_search"]}) is False   # a restriction we did not write
    assert qs.relay_form({**base, "tools": "*"}) is False

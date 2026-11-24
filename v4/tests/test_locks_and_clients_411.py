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
RETAINED_CELLS = ("macos-arm64-py311", "macos-arm64-py312", "macos-arm64-py313", "ubuntu2404-arm64-py311")
LINUX_SUPPORTED = ("claude-code", "codex", "cursor", "antigravity", "gemini-cli", "grok", "zed", "devin")
LINUX_UNSUPPORTED = ("vscode", "windsurf", "claude-desktop")


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
}


def load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"test_l5_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    assert manifest["lock_state"] == "preliminary"
    assert manifest["urls_status"] == "to_fill_at_step_3"
    assert len(manifest["artifacts"]) == 36
    assert all("url" in item and item["url"] is None for item in manifest["artifacts"])


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
    mdoc["artifacts"][0]["sha256"] = ["0" * 64]
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
    """Contract §6 + L5-r1 #4: 'url' field is mandatory; None while preliminary, https:// when frozen."""
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

    # 1. Missing 'url' key -> refused even with synced digests.
    mdoc = json.loads(src_man.read_text(encoding="utf-8"))
    del mdoc["artifacts"][0]["url"]
    man.write_text(json.dumps(mdoc), encoding="utf-8")
    bind()
    assert qualified_runtime.cell_lock(cell_id, profile="lexical") is None

    # 2. Preliminary with urls_status='to_fill_at_step_3' but non-null url -> refused.
    mdoc = json.loads(src_man.read_text(encoding="utf-8"))
    mdoc["artifacts"][0]["url"] = "https://files.pythonhosted.org/packages/example.whl"
    man.write_text(json.dumps(mdoc), encoding="utf-8")
    bind()
    assert qualified_runtime.cell_lock(cell_id, profile="lexical") is None

    # 3. Frozen manifest without real https URLs -> refused.
    mdoc = json.loads(src_man.read_text(encoding="utf-8"))
    mdoc["lock_state"] = "frozen"
    mdoc["urls_status"] = "complete"
    man.write_text(json.dumps(mdoc), encoding="utf-8")
    bind()
    assert qualified_runtime.cell_lock(cell_id, profile="lexical") is None

    # 4. Frozen manifest with malformed / non-PyPI / non-wheel URLs -> refused (incl. https:///x).
    for bad_url in (
        "https:///x",
        "https://files.pythonhosted.org/",
        "https://files.pythonhosted.org/packages/x",
        "https://files.pythonhosted.org/packages/annotated_types-0.7.0.tar.gz",
        "https://evil.example/packages/annotated_types-0.7.0-py3-none-any.whl",
        "http://files.pythonhosted.org/packages/annotated_types-0.7.0-py3-none-any.whl",
        "https://user@files.pythonhosted.org/packages/annotated_types-0.7.0-py3-none-any.whl",
        "https://files.pythonhosted.org:443/packages/annotated_types-0.7.0-py3-none-any.whl",
        "https://files.pythonhosted.org/packages/annotated_types-0.7.0-py3-none-any.whl?x=1",
        "https://files.pythonhosted.org/packages/annotated_types-0.7.0-py3-none-any.whl#frag",
        "https://files.pythonhosted.org/packages/../annotated_types-0.7.0-py3-none-any.whl",
        "https://files.pythonhosted.org/packages/other_pkg-0.7.0-py3-none-any.whl",
        "https://files.pythonhosted.org/packages/annotated_types-9.9.9-py3-none-any.whl",
    ):
        assert qualified_runtime.valid_artifact_url(bad_url, name="annotated-types", version="0.7.0") is False
        mdoc = json.loads(src_man.read_text(encoding="utf-8"))
        mdoc["lock_state"] = "frozen"
        mdoc["urls_status"] = "complete"
        for item in mdoc["artifacts"]:
            dist = item["name"].replace("-", "_")
            item["url"] = f"https://files.pythonhosted.org/packages/ab/cd/{dist}-{item['version']}-py3-none-any.whl"
        mdoc["artifacts"][0]["url"] = bad_url
        man.write_text(json.dumps(mdoc), encoding="utf-8")
        bind()
        assert qualified_runtime.cell_lock(cell_id, profile="lexical") is None, f"bad URL must be refused: {bad_url}"

    # 5. Frozen manifest generated with complete PEP 427 https://files.pythonhosted.org/*.whl URLs -> accepted.
    assert qualified_runtime.valid_artifact_url(
        "https://files.pythonhosted.org/packages/ab/cd/typing_extensions-4.15.0-py3-none-any.whl",
        name="typing-extensions", version="4.15.0",
    ) is True
    assert qualified_runtime.valid_artifact_url(
        "https://files.pythonhosted.org/packages/ab/cd/numpy-2.4.2-1-cp311-cp311-manylinux_2_17_aarch64.whl",
        name="numpy", version="2.4.2",
    ) is True
    compiled = "\n".join(
        f"{name}=={ver} \\\n    --hash=sha256:{hashlib.sha256(f'{name}:{ver}'.encode()).hexdigest()}"
        for name, ver in sorted(expected.items())
    ) + "\n"
    ref_cat = json.loads((RESOURCES / "qualified_runtime_lexical_v1.json").read_text(encoding="utf-8"))
    ml_pkgs = gen._ml_closure_names(RESOURCES)
    with pytest.raises(ValueError, match="artifact_urls"):
        gen.build_cell_lock_files(cell_id, compiled, reference_catalog=ref_cat, ml_packages=ml_pkgs, lock_state="frozen")
    urls = {
        name: f"https://files.pythonhosted.org/packages/ab/cd/{name.replace('-', '_')}-{ver}-py3-none-any.whl"
        for name, ver in expected.items()
    }
    f_cat, f_con, f_man = gen.build_cell_lock_files(
        cell_id, compiled, reference_catalog=ref_cat, ml_packages=ml_pkgs, lock_state="frozen", artifact_urls=urls,
    )
    assert f_man["urls_status"] == "complete" and all(a["url"].startswith("https://files.pythonhosted.org/") for a in f_man["artifacts"])
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
        mdoc["artifacts"].append({"name": "torch", "version": "2.10.0", "url": None, "sha256": ["a" * 64]})
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
        mdoc["artifacts"][0]["sha256"] = ["not-a-sha256"]
        man.write_text(json.dumps(mdoc))
    elif tamper == "manifest-uppercase-hash":
        mdoc = json.loads(man.read_text())
        mdoc["artifacts"][0]["sha256"] = ["A" * 64]
        man.write_text(json.dumps(mdoc))
    elif tamper == "manifest-empty-hashes":
        mdoc = json.loads(man.read_text())
        mdoc["artifacts"][0]["sha256"] = []
        man.write_text(json.dumps(mdoc))
    elif tamper == "manifest-duplicate-hash":
        mdoc = json.loads(man.read_text())
        h = mdoc["artifacts"][0]["sha256"][0]
        mdoc["artifacts"][0]["sha256"] = [h, h]
        man.write_text(json.dumps(mdoc))
    elif tamper == "manifest-missing-artifact":
        mdoc = json.loads(man.read_text())
        mdoc["artifacts"] = mdoc["artifacts"][1:]
        man.write_text(json.dumps(mdoc))
    elif tamper == "manifest-extra-artifact":
        mdoc = json.loads(man.read_text())
        mdoc["artifacts"].append({"name": "extra-pkg", "version": "1.0.0", "url": None, "sha256": ["b" * 64]})
        man.write_text(json.dumps(mdoc))
    elif tamper == "manifest-version-mismatch":
        mdoc = json.loads(man.read_text())
        mdoc["artifacts"][0]["version"] = "9.9.9"
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

def test_memory_os_constraints_is_cell_aware_and_stdlib_only():
    code = """
import sys
sys.path.insert(0, sys.argv[1])
import memory_os_constraints as m
assert 'memory_os_v4' not in sys.modules
for cid in ('macos-arm64-py311', 'macos-arm64-py312', 'macos-arm64-py313', 'ubuntu2404-arm64-py311'):
    p = m.path('lexical', cell_id=cid)
    assert p.endswith('.txt')
assert m.path('semantic', cell_id='macos-arm64-py311').endswith('qualified_runtime_constraints_v1.txt')
for cid in ('macos-arm64-py312', 'macos-arm64-py313', 'ubuntu2404-arm64-py311'):
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
    synthetic_compiled = "\n".join(
        f"{name}=={ver} \\\n    --hash=sha256:{hashlib.sha256(f'{name}:{ver}'.encode()).hexdigest()}"
        for name, ver in sorted(expected.items())
    ) + "\n"
    in_dir = tmp_path / "inputs"
    in_dir.mkdir()
    inputs = {}
    for cid, fname in gen.DEFAULT_INPUT_FILES.items():
        p = in_dir / fname
        p.write_text(synthetic_compiled, encoding="utf-8")
        inputs[cid] = p
    qr_copy = tmp_path / "qualified_runtime.py"
    con_copy = tmp_path / "memory_os_constraints.py"
    shutil.copy2(ROOT / "v4/src/memory_os_v4/ops/qualified_runtime.py", qr_copy)
    shutil.copy2(ROOT / "v4/src/memory_os_constraints.py", con_copy)
    written = gen.generate_all(
        inputs, out_dir, recorded_at="2026-11-23", lock_state="preliminary",
        qualified_runtime_path=qr_copy, constraints_script_path=con_copy,
    )
    assert len(written["written"]) == 12
    assert "qualified_runtime.py:CELL_LOCK_DIGESTS" in written["written"]
    assert "memory_os_constraints.py:CELL_CONSTRAINTS_DIGESTS" in written["written"]
    checked = gen.generate_all(
        inputs, out_dir, recorded_at="2026-11-23", lock_state="preliminary", check_only=True,
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
        inputs, out_dir, recorded_at="2026-11-23", lock_state="preliminary", check_only=True,
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
        inputs, out_dir, recorded_at="2026-11-23", lock_state="preliminary", check_only=True,
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
    synthetic_compiled = "\n".join(
        f"{name}=={ver} \\\n    --hash=sha256:{hashlib.sha256(f'{name}:{ver}'.encode()).hexdigest()}"
        for name, ver in sorted(expected.items())
    ) + "\n"
    in_dir = tmp_path / "inputs"
    in_dir.mkdir()
    inputs = {}
    for cid, fname in gen.DEFAULT_INPUT_FILES.items():
        p = in_dir / fname
        p.write_text(synthetic_compiled, encoding="utf-8")
        inputs[cid] = p
    qr_copy = tmp_path / "qualified_runtime.py"
    con_copy = tmp_path / "memory_os_constraints.py"
    shutil.copy2(ROOT / "v4/src/memory_os_v4/ops/qualified_runtime.py", qr_copy)
    shutil.copy2(ROOT / "v4/src/memory_os_constraints.py", con_copy)
    gen.generate_all(
        inputs, out_dir, recorded_at="2026-11-23", lock_state="preliminary",
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
            inputs, out_dir, recorded_at="2026-11-23", lock_state="preliminary",
            qualified_runtime_path=qr_copy, constraints_script_path=con_copy,
        )

    # 2. Even if someone forcefully regenerates the anchors to match the tampered file, --check MUST fail.
    gen.sync_qualified_runtime_digests(qr_copy, out_dir, check_only=False)
    gen.sync_constraints_script_digests(con_copy, out_dir, check_only=False)
    checked = gen.generate_all(
        inputs, out_dir, recorded_at="2026-11-23", lock_state="preliminary", check_only=True,
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
])
def test_eight_established_linux_client_paths(tmp_path, key, expected_rel):
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
    assert "4.1.2" in str(err.value)

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
    man.write_text(raw_man.replace('"schema_version": 1,', '"schema_version": 99, "schema_version": 1,'), encoding="utf-8")
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

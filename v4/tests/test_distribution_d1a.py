"""No real download, model or user config: D1a payload/consent regression tests."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tomllib
import zipfile

import httpx
import pytest

from memory_os_v4.build_descriptor import NAME, verify
from memory_os_v4.ops import cli, doctor, provision
from memory_os_v4 import readiness, __version__ as VERSION

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("memory_os_build", ROOT / "memory_os_build.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
SHA = "a" * 40


def test_release_versions_and_packaged_notes_are_consistent():
    """Check public release identities without pinning a future test to this version."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    standalone = tomllib.loads((ROOT / "v4/pyproject.toml").read_text())
    registry = json.loads((ROOT / "server.json").read_text())
    assert project["project"]["version"] == standalone["project"]["version"] == VERSION
    assert registry["version"] == VERSION
    package = registry["packages"][0]
    assert package["version"] == VERSION
    requirement = f"memory-os-ai[ops,brief]=={VERSION}"
    assert next(a["value"] for a in package["runtimeArguments"] if a.get("name") == "--from") == requirement
    launch = registry["_meta"]["io.modelcontextprotocol.registry/publisher-provided"]["launch"]
    assert launch["manual_pinned_command"] == f"uvx --from {requirement} memory-os-relay"
    release = f"docs/releases/v{VERSION}.md"
    assert release in project["tool"]["setuptools"]["data-files"]["share/doc/memory-os-ai"]
    assert (ROOT / release).is_file()


@pytest.fixture
def sealed(tmp_path, monkeypatch):
    root = tmp_path / "source"
    root.mkdir()
    for name in builder.INPUTS:
        (root / name).write_text("fixture " + name)
    (root / "pyproject.toml").write_text(f'[project]\nname = "memory-os-ai"\nversion = "{VERSION}"\n')
    for directory, package in (("v4/src", "memory_os_v4"),):   # 4.1.3: no legacy v3 package
        folder = root / directory / package
        folder.mkdir(parents=True)
        (folder / "__init__.py").write_text(f'__version__ = "{VERSION}"\n')
    monkeypatch.delenv("MEMORY_OS_BUILD_SOURCE_COMMIT", raising=False)
    git = ["git", "-C", str(root)]
    for args in (["init", "-q"], ["add", "."], ["-c", "user.name=Synthetic", "-c", "user.email=test@invalid", "commit", "-qm", "fixture"]):
        subprocess.run([*git, *args], check=True, capture_output=True)
    return root


def install(sealed, target):
    for directory, package in (("v4/src", "memory_os_v4"),):
        shutil.copytree(sealed / directory / package, target / package)
    record = builder.seal(sealed, VERSION)
    descriptor = builder.descriptor(target, record)
    (target / "memory_os_v4" / NAME).write_text(json.dumps(descriptor))
    return record


def test_sdist_git_free_identity_matches_payload_and_source_commit(sealed, tmp_path):
    original = builder.seal(sealed, VERSION)
    exported = tmp_path / "sdist"
    shutil.copytree(sealed, exported, ignore=shutil.ignore_patterns(".git"))
    (exported / builder.SEED).write_text(json.dumps(original))
    assert builder.seal(exported, VERSION) == original
    install(exported, tmp_path / "installed")
    check = verify(tmp_path / "installed", VERSION)
    assert check["ok"] and check["commit"] == original["source_commit"]
    module = exported / "v4/src/memory_os_v4/__init__.py"
    module.write_text(module.read_text() + "\n# tampered\n")
    with pytest.raises(RuntimeError, match="Modified"):
        builder.seal(exported, VERSION)


@pytest.mark.parametrize("damage", ["missing", "changed", "extra", "version", "descriptor", "root", "symlink", "directory-symlink",
                                    "legacy-v3-beside"])
def test_installed_descriptor_fails_closed(sealed, tmp_path, damage):
    root = tmp_path / "installed"
    install(sealed, root)
    path = root / "memory_os_v4" / NAME
    module = root / "memory_os_v4/__init__.py"
    if damage == "missing":
        path.unlink()
    elif damage == "changed":
        module.write_text("modified")
    elif damage == "extra":
        (root / "memory_os_v4/injected.py").write_text("x")
    elif damage == "version":
        assert not verify(root, "0.0.0")["ok"]
        return
    elif damage == "descriptor":
        path.write_text("[]")
    elif damage == "root":
        record = json.loads(path.read_text())
        record["payload_sha256"] = "b" * 64
        path.write_text(json.dumps(record))
    elif damage == "legacy-v3-beside":
        (root / "memory_os_ai").mkdir()         # 4.1.3: a stale v3 package next to the payload is refused
        (root / "memory_os_ai/__init__.py").write_text("")
    elif damage == "symlink":
        other = tmp_path / "external"
        module.rename(other)
        module.symlink_to(other)
    else:
        folder = root / "memory_os_v4"
        folder.rename(tmp_path / "other")
        folder.symlink_to(tmp_path / "other", target_is_directory=True)
    assert verify(root, VERSION)["code"] == "build_unqualified"


def test_declared_sha_cannot_lie_or_hide_untracked_runtime(sealed, monkeypatch):
    monkeypatch.setenv("MEMORY_OS_BUILD_SOURCE_COMMIT", SHA)
    with pytest.raises(RuntimeError, match="refused"):
        builder.seal(sealed, VERSION)
    monkeypatch.delenv("MEMORY_OS_BUILD_SOURCE_COMMIT", raising=False)
    (sealed / "v4/src/memory_os_v4/untracked.py").write_text("x")
    with pytest.raises(RuntimeError, match="refused"):
        builder.seal(sealed, VERSION)


def test_real_hooks_tiny_wheel_and_git_free_sdist_roundtrip(sealed, tmp_path, monkeypatch):
    # Actual setuptools packaging, a few KB only: no dependency installer/model.
    for name in ("setup.py", "memory_os_build.py"):
        shutil.copyfile(ROOT / name, sealed / name)
    (sealed / "pyproject.toml").write_text(f'''[project]
name = "memory-os-ai"
version = "{VERSION}"
[tool.setuptools.packages.find]
where = ["v4/src"]
include = ["memory_os_v4*"]
''')
    (sealed / "MANIFEST.in").write_text("include setup.py memory_os_build.py LICENSE LICENSE.GPL-3.0\n")
    subprocess.run(["git", "-C", str(sealed), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(sealed), "-c", "user.name=Synthetic", "-c", "user.email=test@invalid",
                    "commit", "-qm", "tiny build inputs"], check=True, capture_output=True)
    command = [sys.executable, "-B", "setup.py", "-q"]
    result = subprocess.run([*command, "sdist", "bdist_wheel"], cwd=sealed, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    wheel = next((sealed / "dist").glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        direct = json.loads(archive.read("memory_os_v4/" + NAME))
    extracted = tmp_path / "git-free-sdist"
    extracted.mkdir()
    with tarfile.open(next((sealed / "dist").glob("*.tar.gz"))) as archive:
        archive.extractall(extracted, filter="data")
    source = next(extracted.iterdir())
    assert (source / builder.SEED).is_file() and not (source / ".git").exists()
    result = subprocess.run([*command, "bdist_wheel"], cwd=source, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    with zipfile.ZipFile(next((source / "dist").glob("*.whl"))) as archive:
        rebuilt = json.loads(archive.read("memory_os_v4/" + NAME))
    assert rebuilt == direct


def test_stale_build_directory_content_is_refused(sealed, tmp_path):
    """4.1.3: a stale build/lib/memory_os_ai/ (from a 4.1.2 build) is outside the inventory yet would be packed
    by setuptools: the attested build_py refuses any build-directory content outside the shipped payload."""
    for name in ("setup.py", "memory_os_build.py"):
        shutil.copyfile(ROOT / name, sealed / name)
    (sealed / "pyproject.toml").write_text(f'''[project]
name = "memory-os-ai"
version = "{VERSION}"
[tool.setuptools.packages.find]
where = ["v4/src"]
include = ["memory_os_v4*"]
''')
    (sealed / "MANIFEST.in").write_text("include setup.py memory_os_build.py LICENSE LICENSE.GPL-3.0\n")
    subprocess.run(["git", "-C", str(sealed), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(sealed), "-c", "user.name=Synthetic", "-c", "user.email=test@invalid",
                    "commit", "-qm", "tiny build inputs"], check=True, capture_output=True)
    command = [sys.executable, "-B", "setup.py", "-q", "bdist_wheel"]
    clean = subprocess.run(command, cwd=sealed, capture_output=True, text=True, timeout=30)
    assert clean.returncode == 0, clean.stderr
    with zipfile.ZipFile(next((sealed / "dist").glob("*.whl"))) as archive:
        assert not any(n.startswith("memory_os_ai/") for n in archive.namelist())
    shutil.rmtree(sealed / "dist")
    stale = sealed / "build/lib/memory_os_ai"
    stale.mkdir(parents=True)
    (stale / "__init__.py").write_text('__version__ = "4.1.2"\n')
    dirty = subprocess.run(command, cwd=sealed, capture_output=True, text=True, timeout=30)
    assert dirty.returncode != 0 and "outside the shipped payload" in dirty.stderr, dirty.stderr[-600:]
    assert not list((sealed / "dist").glob("*.whl")) if (sealed / "dist").exists() else True


def test_pep517_clean_export_and_git_free_sdist_rebuild(sealed, tmp_path):
    # Use the actual PEP 517 frontend, not setup.py (which adds its own root).
    # Tiny synthetic packages only; existing build tools, no dependency download.
    python = os.environ.get("MEMORY_TEST_BUILD_PYTHON", sys.executable)
    if "MEMORY_TEST_BUILD_PYTHON" not in os.environ:
        equipped = subprocess.run([python, "-B", "-c", "import build, wheel"],
                                  capture_output=True, timeout=10)
        if equipped.returncode and Path("/opt/homebrew/bin/python3.11").is_file():
            python = "/opt/homebrew/bin/python3.11"
    for name in ("setup.py", "memory_os_build.py"):
        shutil.copyfile(ROOT / name, sealed / name)
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())["build-system"]
    (sealed / "pyproject.toml").write_text(f'''[build-system]
requires = {json.dumps(metadata["requires"])}
build-backend = {json.dumps(metadata["build-backend"])}
[project]
name = "memory-os-ai"
version = "{VERSION}"
dependencies = {json.dumps(tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['dependencies'])}
[project.optional-dependencies]
{chr(10).join(f'{json.dumps(k)} = {json.dumps(v)}' for k, v in tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['optional-dependencies'].items())}
[tool.setuptools.packages.find]
where = ["v4/src"]
include = ["memory_os_v4*"]
''')
    (sealed / "MANIFEST.in").write_text("include setup.py memory_os_build.py LICENSE LICENSE.GPL-3.0\n")
    git = ["git", "-C", str(sealed)]
    subprocess.run([*git, "add", "."], check=True, capture_output=True)
    subprocess.run([*git, "-c", "user.name=Synthetic", "-c", "user.email=test@invalid",
                    "commit", "-qm", "PEP 517 fixture"], check=True, capture_output=True)
    commit = subprocess.check_output([*git, "rev-parse", "HEAD"], text=True).strip()
    exported = tmp_path / "clean-export"
    exported.mkdir()
    archived = subprocess.check_output([*git, "archive", commit])
    with tarfile.open(fileobj=io.BytesIO(archived)) as archive:
        archive.extractall(exported, filter="data")
    assert not (exported / ".git").exists()
    safe_env = {k: v for k, v in os.environ.items()
                if k in ("PATH", "HOME", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT", "LANG", "LC_ALL")}
    env = {**safe_env, "PYTHONPATH": "", "PYTHONDONTWRITEBYTECODE": "1",
           "MEMORY_OS_BUILD_GIT_DIR": str(sealed / ".git"),
           "MEMORY_OS_BUILD_SOURCE_COMMIT": commit}
    command = [python, "-m", "build", "--no-isolation"]

    def build(source, kind, output, environment):
        result = subprocess.run([*command, kind, "--outdir", str(output)], cwd=source,
                                env=environment, capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr

    direct_dist, sdist_dist = tmp_path / "direct", tmp_path / "sdist-dist"
    build(exported, "--wheel", direct_dist, env)
    build(exported, "--sdist", sdist_dist, env)
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    with tarfile.open(next(sdist_dist.glob("*.tar.gz"))) as archive:
        archive.extractall(extracted, filter="data")
    source = next(extracted.iterdir())
    assert (source / "memory_os_build.py").read_bytes() == (ROOT / "memory_os_build.py").read_bytes()
    assert (source / builder.SEED).is_file() and not (source / ".git").exists()
    git_free_env = {k: v for k, v in env.items() if not k.startswith("MEMORY_OS_BUILD_")}
    rebuilt_dist = tmp_path / "rebuilt"
    build(source, "--wheel", rebuilt_dist, git_free_env)
    descriptors = []
    for folder in (direct_dist, rebuilt_dist):
        with zipfile.ZipFile(next(folder.glob("*.whl"))) as archive:
            descriptors.append(json.loads(archive.read("memory_os_v4/" + NAME)))
    assert descriptors[0] == descriptors[1]
    assert descriptors[0]["source_commit"] == commit
    checker = importlib.util.spec_from_file_location("extra_gate", ROOT / "scripts/check_extra_markers.py")
    gate = importlib.util.module_from_spec(checker)
    checker.loader.exec_module(gate)
    for folder in (direct_dist, rebuilt_dist):
        proof = gate.wheel_markers(next(folder.glob("*.whl")))
        # 4.1.3 (D2-b): the dead v3-only extras are gone; the v4 extras remain
        assert proof["ok"] and "v3" not in proof["extras"]
        assert not {"legacy-v3", "audio", "gpu", "doc-legacy", "cloud-all", "cloud-s3"} & set(proof["extras"])
        assert {"brief", "ops", "semantic", "documents"} <= set(proof["extras"])
        assert all(n > 0 for n in proof["marker_evaluations"].values())
        from email.parser import BytesParser
        from memory_os_v4.ops.qualified_runtime import check_requirements
        with zipfile.ZipFile(next(folder.glob('*.whl'))) as archive:
            metadata = BytesParser().parsebytes(archive.read(next(n for n in archive.namelist() if n.endswith('.dist-info/METADATA'))))
            requires = metadata.get_all('Requires-Dist')
            # 4.1.0: wheel METADATA carry bounds containing the qualified versions, per profile;
            # no ML package is active without the [semantic] extra (exact locks: constraints files).
            for profile in ('lexical', 'semantic'):
                assert check_requirements(requires, profile=profile) == {'ok': True, 'mismatches': {}}, profile
                assert check_requirements(requires, profile=profile, extras=('ops', 'brief'))['ok'], profile
            assert 'semantic' in metadata.get_all('Provides-Extra')
            from packaging.requirements import Requirement
            unconditional = {Requirement(text).name for text in requires if Requirement(text).marker is None}
            assert not {'torch', 'sentence-transformers', 'transformers', 'faiss-cpu'} & unconditional
            assert 'numpy' in unconditional


def test_no_extra_name_can_parse_as_pep440_version():
    from packaging import version
    checker = importlib.util.spec_from_file_location("extra_gate", ROOT / "scripts/check_extra_markers.py")
    gate = importlib.util.module_from_spec(checker)
    checker.loader.exec_module(gate)
    gate.check_names(tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["optional-dependencies"], version)
    with pytest.raises(ValueError, match="PEP 440"):
        gate.check_names(["v3"], version)


def test_identity_installed_env_consistency_and_no_git(sealed, tmp_path, monkeypatch):
    root = tmp_path / "installed"
    record = install(sealed, root)
    monkeypatch.setattr(readiness, "__file__", str(root / "memory_os_v4/readiness.py"))
    monkeypatch.delenv("MEMORY_V4_BUILD_COMMIT", raising=False)
    monkeypatch.setattr(readiness.subprocess, "check_output", lambda *a, **k: pytest.fail("No Git in installed runtime"))
    assert readiness.build_identity()["identity_source"] == "embedded_descriptor"
    monkeypatch.setenv("MEMORY_V4_BUILD_COMMIT", record["source_commit"])
    assert readiness.build_identity()["commit"] == record["source_commit"]
    monkeypatch.setenv("MEMORY_V4_BUILD_COMMIT", SHA)
    with pytest.raises(ValueError, match="contradicts"):
        readiness.build_identity()
    (root / "memory_os_v4" / NAME).unlink()
    assert readiness.build_identity()["identity_source"] == "unqualified"
    assert readiness.build_identity()["commit"] is None


@pytest.mark.asyncio
async def test_ready_rechecks_installed_payload_after_startup(sealed, tmp_path, monkeypatch):
    from memory_os_v4 import Config, MemoryV4, server
    root = tmp_path / "installed"
    install(sealed, root)
    monkeypatch.setattr(readiness, "__file__", str(root / "memory_os_v4/readiness.py"))
    monkeypatch.delenv("MEMORY_V4_BUILD_COMMIT", raising=False)
    token = "synthetic-d1-token-" + "x" * 32
    path = tmp_path / "token"
    path.write_text(token)
    path.chmod(0o600)
    monkeypatch.setenv("MEMORY_V4_TOKEN_FILE", str(path))
    with MemoryV4(config=Config(embed_model="hash", data_dir=str(tmp_path / "store"))) as engine:
        monkeypatch.setattr(server, "_engine", engine)
        app = server._build_http_app("127.0.0.1", 8765)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765",
                                     headers={"Authorization": "Bearer " + token}) as client:
            response = await client.get("/ready")
            assert response.status_code == 200 and response.json()["identity_source"] == "embedded_descriptor"
            (root / "memory_os_v4/__init__.py").write_text("tampered")
            response = await client.get("/ready")
            assert response.status_code == 503 and response.json()["reason"] == "build_unqualified"


def test_guard_is_required_before_any_download(tiny_resources, monkeypatch):
    _, _, paths = tiny_resources
    def refuse(*args):
        raise provision.ProvisionError("machine guard refused")
    monkeypatch.setattr(provision, "guard", refuse)
    monkeypatch.setattr(provision, "opener", lambda: pytest.fail("Guard must precede network"))
    with pytest.raises(provision.ProvisionError, match="guard refused"):
        provision.apply(provision.plan(**paths), download_approved=True)
    assert not paths["cache_dir"].exists()


def test_doctor_explains_embedded_identity(monkeypatch, tmp_path):
    from memory_os_v4.ops.settings import OpsSettings
    # Synthetic RELEASED deployment; a development checkout must not make the
    # doctor accept an unqualified prerelease in production.
    info = {"ok": True, "ready": True, "version": "4.0.6", "commit": SHA, "source_dirty": False,
            "commit_source": "embedded_descriptor", "identity_source": "embedded_descriptor"}
    monkeypatch.setattr(doctor, "http_get", lambda *a: (200, json.dumps(info).encode()))
    monkeypatch.setattr(doctor, "_get_without_token", lambda *a: 401)
    result = {check.area: check for check in doctor._server(OpsSettings.from_env())}["ready"]
    assert result.status == "PASS" and "no longer needed" in result.hint


def test_extras_are_explicit():
    """4.1.3 (D2-b): only the v4 extras are published; the v3-only ones (legacy-v3, audio, gpu, doc-legacy,
    cloud-*) installed dependencies without any code once the v3 package left the wheel."""
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert not any(name.startswith(("pymupdf", "pytesseract", "python-pptx", "python-docx", "Pillow", "requests")) for name in meta["dependencies"])
    # 4.1.0: the ML closure left the core for the [semantic] extra, with a bound containing the
    # qualified version; the exact lock ==5.2.3 is asserted on the semantic constraints file.
    from packaging.requirements import Requirement
    from memory_os_v4.ops import qualified_runtime
    assert not any(Requirement(text).name == "sentence-transformers" for text in meta["dependencies"])
    bounds = [Requirement(text) for text in meta["optional-dependencies"]["semantic"]
              if Requirement(text).name == "sentence-transformers"]
    assert len(bounds) == 1 and bounds[0].specifier.contains("5.2.3") and "==" not in str(bounds[0].specifier)
    constraints = (qualified_runtime.PROFILE.parent / "qualified_runtime_constraints_v1.txt").read_text().splitlines()
    assert "sentence-transformers==5.2.3" in constraints
    assert qualified_runtime.check_constraints(constraints, profile="semantic", extras=("ops", "brief"))["ok"]
    assert "pymupdf>=1.24" in meta["optional-dependencies"]["documents"]
    assert sorted(meta["optional-dependencies"]) == ["brief", "dev", "documents", "ops", "semantic"]


def test_legacy_v3_package_is_not_shipped():
    """4.1.3 (D2): the legacy ``memory_os_ai`` package and its ``memory-os-ai-v3`` command leave the wheel.

    The sources are kept, unshipped, under ``legacy/v3/`` until 4.2.0; v3 configurations are still RECOGNISED
    by ``quickstart``/``uninstall`` (string match only), so an old client entry can be found and removed."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert "memory-os-ai-v3" not in project["project"]["scripts"]
    assert not any(target.startswith("memory_os_ai.") for target in project["project"]["scripts"].values())
    find = project["tool"]["setuptools"]["packages"]["find"]
    assert find == {"where": ["v4/src"], "include": ["memory_os_v4*"]}
    assert builder.PACKAGES == ("memory_os_v4",)
    assert not (ROOT / "src/memory_os_ai").exists() and (ROOT / "legacy/v3/memory_os_ai/__init__.py").is_file()
    assert not any(line.split()[:2] == ["recursive-include", "legacy"] for line in
                   (ROOT / "MANIFEST.in").read_text().splitlines())
    assert importlib.util.find_spec("memory_os_ai") is None


@pytest.fixture
def tiny_resources(tmp_path, monkeypatch):
    content = b"synthetic resource\n"
    sha = hashlib.sha256(content).hexdigest()
    data = {"schema_version": 1, "model_id": "owner/model", "revision": SHA, "licence": "Apache-2.0",
            "files": [{"path": "config.json", "bytes": len(content), "sha256": sha,
                       "upstream_blob": hashlib.sha1(f"blob {len(content)}\0".encode() + content).hexdigest()}],
            "cl100k": {"url": "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken",
                       "bytes": len(content), "sha256": sha}}
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(data))
    monkeypatch.setattr(provision, "CATALOG", path)
    monkeypatch.setattr(provision, "guard", lambda *a: None)
    return content, data, {"cache_dir": tmp_path / "cache", "table_path": tmp_path / "table/local.tiktoken"}


class Response(io.BytesIO):
    def __init__(self, data, url, length=None):
        super().__init__(data)
        self.url = url
        self.headers = {"Content-Length": str(len(data) if length is None else length)}

    def geturl(self):
        return self.url


class Source:
    def __init__(self, content, record, mode="good"):
        self.content, self.record, self.mode = content, record, mode
        self.calls = []

    def open(self, url, timeout):
        self.calls.append(url)
        if "/api/models/" in url:
            obj = {"sha": self.record["revision"], "cardData": {"license": "apache-2.0"},
                   "siblings": [{"rfilename": f["path"], "size": f["bytes"], "blobId": f["upstream_blob"]}
                                for f in self.record["files"]]}
            if self.mode == "revision":
                obj["sha"] = "b" * 40
            return Response(json.dumps(obj).encode(), url)
        data = b"x" * len(self.content) if self.mode == "hash" else self.content
        return Response(data, "http://evil.invalid" if self.mode == "redirect" else url,
                        999 if self.mode == "length" else None)


def test_preview_is_read_only_without_network(tiny_resources, monkeypatch):
    _, _, paths = tiny_resources
    monkeypatch.setattr(provision, "opener", lambda: pytest.fail("Preview cannot open network"))
    result = provision.plan(**paths)
    assert not result["ready"] and result["download_bytes"] > 0
    assert not paths["cache_dir"].exists() and not paths["table_path"].parent.exists()
    with pytest.raises(provision.ProvisionError, match="consent"):
        provision.apply(result)
    assert not paths["cache_dir"].exists()


def test_provision_pinned_success_offline_reapply_and_no_profile_change(tiny_resources, monkeypatch):
    content, record, paths = tiny_resources
    source = Source(content, record)
    monkeypatch.setattr(provision, "opener", lambda: source)
    result = provision.apply(provision.plan(**paths), download_approved=True)
    assert result["ok"] and result["ready"] and result["downloaded_bytes"] == 2 * len(content)
    assert Path(result["env"]["MEMORY_V4_CL100K_PATH"]).read_bytes() == content
    assert len(source.calls) == 3  # metadata, one model file, table
    monkeypatch.setattr(provision, "opener", lambda: pytest.fail("Existing resources work offline"))
    repeated = provision.apply(provision.plan(**paths))
    assert repeated["ready"] and not repeated["modified"]
    ref = paths["cache_dir"] / "models--owner--model/refs/main"
    assert ref.read_bytes() == record['revision'].encode('ascii')
    ref.write_text("b" * 40)
    with pytest.raises(provision.ProvisionError, match="profile change"):
        provision.plan(**paths)


def test_provisioned_ref_resolves_with_real_hf_cache_reader_without_model(tiny_resources, monkeypatch):
    content, record, paths = tiny_resources
    record['files'][0]['path'] = 'modules.json'
    provision.CATALOG.write_text(json.dumps(record))
    monkeypatch.setattr(provision, 'opener', lambda: Source(content, record))
    result = provision.apply(provision.plan(**paths), download_approved=True)
    assert result['ok'], result.get('error')
    model = paths['cache_dir'] / 'models--owner--model'
    expected = model / 'snapshots' / record['revision'] / 'modules.json'
    assert (model / 'refs/main').read_bytes() == record['revision'].encode('ascii')
    code = '''import sys
from huggingface_hub import try_to_load_from_cache
for revision in (None, 'main'):
 found = try_to_load_from_cache('owner/model', 'modules.json', cache_dir=sys.argv[1], revision=revision)
 assert found == sys.argv[2], (revision, found)
assert 'torch' not in sys.modules and 'sentence_transformers' not in sys.modules
print('HF offline ref resolves exactly without model import')
'''
    run = subprocess.run([sys.executable, '-B', '-c', code, str(paths['cache_dir']), str(expected)],
                         env={**os.environ, 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'},
                         capture_output=True, text=True, timeout=10)
    assert run.returncode == 0, run.stdout + run.stderr


@pytest.mark.parametrize('suffix', [b'\n', b'\r\n', b' ', b'\x00'])
def test_noncanonical_existing_ref_refused_in_preview_and_apply_without_repair(tiny_resources, monkeypatch, suffix):
    content, record, paths = tiny_resources
    monkeypatch.setattr(provision, 'opener', lambda: Source(content, record))
    preview = provision.plan(**paths)
    assert provision.apply(preview, download_approved=True)['ok']
    good = provision.plan(**paths)
    ref = paths['cache_dir'] / 'models--owner--model/refs/main'
    ref.write_bytes(record['revision'].encode('ascii') + suffix)
    before = {str(p): p.read_bytes() for p in paths['cache_dir'].rglob('*') if p.is_file()}
    monkeypatch.setattr(provision, 'opener', lambda: pytest.fail('Ref refusal before network'))
    for action in (lambda: provision.plan(**paths), lambda: provision.apply(good)):
        with pytest.raises(provision.ProvisionError, match='refs/main bytes differ.*no silent profile change or repair'):
            action()
        assert {str(p): p.read_bytes() for p in paths['cache_dir'].rglob('*') if p.is_file()} == before


def test_ref_changed_during_download_is_never_repaired(tiny_resources, monkeypatch):
    content, record, paths = tiny_resources
    ref = paths['cache_dir'] / 'models--owner--model/refs/main'
    class RacingSource(Source):
        def open(self, url, timeout):
            if '/api/models/' in url:
                ref.parent.mkdir(parents=True)
                ref.write_bytes(record['revision'].encode('ascii') + b'\n')
            return super().open(url, timeout)
    monkeypatch.setattr(provision, 'opener', lambda: RacingSource(content, record))
    result = provision.apply(provision.plan(**paths), download_approved=True)
    assert not result['ok'] and 'refs/main bytes differ' in result['error']
    assert ref.read_bytes() == record['revision'].encode('ascii') + b'\n'
    assert Path(result['staging_dir']).exists()


@pytest.mark.parametrize('kind', ['symlink', 'directory'])
def test_nonregular_existing_ref_is_refused_without_touching_target(tiny_resources, monkeypatch, kind):
    _, record, paths = tiny_resources
    ref = paths['cache_dir'] / 'models--owner--model/refs/main'
    ref.parent.mkdir(parents=True)
    target = paths['cache_dir'] / 'third-party-ref'
    target.write_bytes(record['revision'].encode('ascii'))
    if kind == 'symlink':
        ref.symlink_to(target)
    else:
        ref.mkdir()
    monkeypatch.setattr(provision, 'opener', lambda: pytest.fail('No network for invalid ref'))
    with pytest.raises(provision.ProvisionError, match='not a regular ref'):
        provision.plan(**paths)
    assert target.read_bytes() == record['revision'].encode('ascii')
    assert ref.is_symlink() if kind == 'symlink' else ref.is_dir()


@pytest.mark.parametrize("mode", ["revision", "hash", "length", "redirect"])
def test_bad_download_preserves_evidence_without_activation(tiny_resources, monkeypatch, mode):
    content, record, paths = tiny_resources
    monkeypatch.setattr(provision, "opener", lambda: Source(content, record, mode))
    result = provision.apply(provision.plan(**paths), download_approved=True)
    assert not result["ok"] and result["code"] == "provision_failed"
    assert not (paths["cache_dir"] / "models--owner--model/refs/main").exists()
    assert not paths["table_path"].exists()
    assert (Path(result["staging_dir"]) / "PROVENANCE.json").exists()


def test_changed_preview_existing_table_and_lock_are_not_overwritten(tiny_resources, monkeypatch):
    content, record, paths = tiny_resources
    preview = provision.plan(**paths)
    preview["download_bytes"] += 1
    with pytest.raises(provision.ProvisionError, match="changed since"):
        provision.apply(preview, download_approved=True)
    paths["table_path"].parent.mkdir()
    paths["table_path"].write_bytes(b"foreign")
    with pytest.raises(provision.ProvisionError, match="never overwritten"):
        provision.plan(**paths)
    paths["table_path"].unlink()
    paths["cache_dir"].mkdir()
    existing = paths["cache_dir"] / ".memory-os-provision.lock"
    existing.write_text("foreign owner")
    monkeypatch.setattr(provision, "opener", lambda: Source(content, record))
    result = provision.apply(provision.plan(**paths), download_approved=True)
    assert not result["ok"] and existing.read_text() == "foreign owner"


@pytest.mark.parametrize("url", ["http://huggingface.co/x", "https://huggingface.co.evil/x", "https://user:secret@huggingface.co/x", "https://huggingface.co:8443/x"])
def test_redirect_allowlist_is_strict(url):
    assert not provision.allowed_url(url)


def test_installed_admin_preview_path_without_clone(tiny_resources, monkeypatch, capsys):
    _, _, paths = tiny_resources
    monkeypatch.setattr(provision, "opener", lambda: pytest.fail("No network"))
    result = cli.main(["provision", "--resources", "cl100k", "--cache-dir", str(paths["cache_dir"]),
                       "--table-path", str(paths["table_path"])])
    assert result == 0 and json.loads(capsys.readouterr().out)["mode"] == "preview"
    assert not paths["cache_dir"].exists()


def test_build_tolerates_generated_bytecode_but_not_foreign_content(tmp_path):
    """4.1.3 (Codex lot1 r2): build_py --compile may create __pycache__ for the shipped py-module."""
    record = {"version": VERSION, "source_commit": SHA, "source_files": {}}
    root = tmp_path / "build-lib"
    (root / "memory_os_v4").mkdir(parents=True)
    (root / "memory_os_constraints.py").write_text("")
    (root / "__pycache__").mkdir()
    (root / "__pycache__/memory_os_constraints.cpython-311.pyc").write_bytes(b"\0")
    builder.descriptor(root, record)                         # compiled cache of the shipped module: accepted
    (root / "__pycache__/memory_os_ai.cpython-311.pyc").write_bytes(b"\0")
    with pytest.raises(RuntimeError, match="outside the shipped payload"):
        builder.descriptor(root, record)                     # any other cache: refused
    (root / "__pycache__/memory_os_ai.cpython-311.pyc").unlink()
    (root / "memory_os_ai").mkdir()
    with pytest.raises(RuntimeError, match="outside the shipped payload"):
        builder.descriptor(root, record)                     # a stale v3 tree, even empty: refused at BUILD time


@pytest.mark.parametrize("leftover, refused", [("empty", False), ("orphan-cache", False), ("source", True),
                                               ("sourceless-pyc", True), ("symlink", True)])
def test_installed_legacy_leftovers_only_code_is_refused(sealed, tmp_path, leftover, refused):
    """4.1.3 (Codex lot1 r2): after an upgrade, an empty memory_os_ai or orphan __pycache__ (never imported without
    sources, PEP 3147) must not make /ready unavailable; importable v3 code still does."""
    root = tmp_path / "installed"
    install(sealed, root)
    legacy = root / "memory_os_ai"
    if leftover == "symlink":
        (tmp_path / "elsewhere").mkdir()
        legacy.symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    else:
        (legacy / "__pycache__").mkdir(parents=True)
        if leftover == "orphan-cache":
            (legacy / "__pycache__/engine.cpython-311.pyc").write_bytes(b"\0")
        elif leftover == "source":
            (legacy / "__init__.py").write_text("")
        elif leftover == "sourceless-pyc":
            (legacy / "engine.pyc").write_bytes(b"\0")
    check = verify(root, VERSION)
    assert (check.get("code") == "build_unqualified") is refused and check.get("ok", False) is (not refused)


def test_qualification_tool_environment_has_no_v3_switch_or_path():
    spec = importlib.util.spec_from_file_location("qualify_d1a", ROOT / "scripts/qualify_distribution_d1a.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    env = tool.qualification_environment({"PATH": "/bin", "MEMORY_TEST_HASH_V3": "1", "MEMORY_V4_DIR": "/x"})
    assert "MEMORY_TEST_HASH_V3" not in env and "MEMORY_V4_DIR" not in env
    assert env["PYTHONPATH"].split(":") == ["v4/src", "v4"]
    # nothing the root suite collects imports the legacy package any more
    for path in (ROOT / "tests").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "from memory_os_ai" not in text and "import memory_os_ai" not in text, path


@pytest.mark.parametrize("change", ["source-in-pycache", "dangling-link", "new-source"])
def test_cached_pass_does_not_hide_v3_code_appearing_later(sealed, tmp_path, change):
    """4.1.3 (Codex r1 WAL review, item 6): the cache stamp covers exactly what _importable_legacy inspects."""
    root = tmp_path / "installed"
    install(sealed, root)
    legacy = root / "memory_os_ai"
    if change == "source-in-pycache":
        (legacy / "__pycache__").mkdir(parents=True)
    elif change == "new-source":
        legacy.mkdir()
    assert verify(root, VERSION, max_age=60)["ok"]                 # cached success
    if change == "source-in-pycache":
        (legacy / "__pycache__" / "engine.py").write_text("")
    elif change == "dangling-link":
        legacy.symlink_to(tmp_path / "nowhere", target_is_directory=True)
    else:
        (legacy / "__init__.py").write_text("")
    assert verify(root, VERSION, max_age=60)["code"] == "build_unqualified"   # within the TTL

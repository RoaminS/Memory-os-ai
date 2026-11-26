"""Offline build identity, including wheel builds from a Git-free sdist.

No model imports. A declared SHA is checked against local Git contents rather
than accepted as an attestation. The sdist carries its checked source inventory.
"""
from __future__ import annotations

import hashlib
from fnmatch import fnmatch
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tomllib

from setuptools.command.build_py import build_py
from setuptools.command.sdist import sdist

SEED = "_memory_os_build_source.json"
DESCRIPTOR = "_build_identity.json"
PACKAGES = ("memory_os_v4",)  # 4.1.3: the legacy v3 package is no longer shipped
INPUTS = ("pyproject.toml", "setup.py", "memory_os_build.py", "MANIFEST.in", "README.md",
          "LICENSE", "LICENSE.GPL-3.0")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): digest(p) for package in PACKAGES
            for p in sorted((root / package).rglob("*")) if p.is_file()
            and "__pycache__" not in p.parts and p.suffix not in {".pyc", ".pyo"}
            and p.name != DESCRIPTOR}


def source_inventory(root: Path) -> dict[str, str]:
    result = {name: digest(root / name) for name in INPUTS}
    for directory in ("src", "v4/src"):
        result.update({f"{directory}/{name}": value for name, value in inventory(root / directory).items()})
    for line in (root / "MANIFEST.in").read_text().splitlines():
        words = line.split()
        if not words:
            continue
        if words[0] == "include":
            candidates = (p for pattern in words[1:] for p in root.glob(pattern))
        elif words[0] == "recursive-include":
            candidates = (p for pattern in words[2:] for p in (root / words[1]).rglob(pattern))
        else:
            continue
        for path in candidates:
            if path.is_file():
                result[str(path.relative_to(root))] = digest(path)
    data = tomllib.loads((root / "pyproject.toml").read_text())
    for patterns in data.get("tool", {}).get("setuptools", {}).get("data-files", {}).values():
        for pattern in patterns:
            for path in root.glob(pattern):
                if path.is_file():
                    result[str(path.relative_to(root))] = digest(path)
    return {name: value for name, value in sorted(result.items()) if selected(name, root)}


def selected(name: str, root: Path) -> bool:
    rules = [line.split() for line in (root / "MANIFEST.in").read_text().splitlines()]
    if any(words and words[0] == "exclude" and any(fnmatch(name, pattern) for pattern in words[1:]) for words in rules):
        return False
    if "__pycache__" in name.split("/") or name.endswith((".pyc", ".pyo")) or name.rsplit("/", 1)[-1] == DESCRIPTOR:
        return False
    if name in INPUTS or name.startswith("v4/src/memory_os_v4/"):
        return True
    for words in rules:
        if words and words[0] == "include" and any(fnmatch(name, p) for p in words[1:]):
            return True
        if words and words[0] == "recursive-include" and name.startswith(words[1].rstrip("/") + "/"):
            if any(fnmatch(name.rsplit("/", 1)[-1], p) for p in words[2:]):
                return True
    data = tomllib.loads((root / "pyproject.toml").read_text())
    return any(fnmatch(name, p) for patterns in data.get("tool", {}).get("setuptools", {}).get("data-files", {}).values()
               for p in patterns)


def seal(root: Path, version: str) -> dict:
    files = source_inventory(root)
    for name in ("v4/src/memory_os_v4/__init__.py",):
        match = re.search(r'__version__\s*=\s*[\'"]([^\'"]+)', (root / name).read_text())
        if not match or match[1] != version:
            raise RuntimeError("Package source version differs from build metadata")
    seed = root / SEED
    if seed.exists():
        record = json.loads(seed.read_text())
        if record.get("schema_version") != 1 or record.get("version") != version or record.get("source_files") != files:
            raise RuntimeError("Modified or incompatible sdist source inventory")
        if not re.fullmatch(r"[0-9a-f]{40}", str(record.get("source_commit", ""))):
            raise RuntimeError("Invalid sdist source commit")
        if os.environ.get("MEMORY_OS_BUILD_SOURCE_COMMIT", record["source_commit"]) != record["source_commit"]:
            raise RuntimeError("Declared build commit contradicts attested sdist")
        return record
    declared = os.environ.get("MEMORY_OS_BUILD_SOURCE_COMMIT")
    if declared and not re.fullmatch(r"[0-9a-f]{40}", declared):
        raise RuntimeError("MEMORY_OS_BUILD_SOURCE_COMMIT must be a complete lowercase Git SHA")
    candidates = [["git", "-C", str(root)]]
    for name in ("lexical-dev-git", "git-state"):
        git_dir = root / ".local-qualification" / name
        if git_dir.is_dir():
            candidates.append(["git", "--git-dir", str(git_dir), "--work-tree", str(root)])
    external = os.environ.get("MEMORY_OS_BUILD_GIT_DIR")
    if external:
        candidates.append(["git", "--git-dir", str(Path(external).resolve()), "--work-tree", str(root)])
    for git in candidates:
        rev = subprocess.run([*git, "rev-parse", declared or "HEAD"], cwd=root,
                             capture_output=True, text=True, timeout=10)
        commit = rev.stdout.strip()
        if rev.returncode or not re.fullmatch(r"[0-9a-f]{40}", commit):
            continue
        archive = subprocess.run([*git, "archive", commit],
                                 cwd=root, capture_output=True, timeout=10)
        if archive.returncode:
            continue
        with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as stream:
            expected = {m.name: hashlib.sha256(stream.extractfile(m).read()).hexdigest()
                        for m in stream if m.isfile() and selected(m.name, root)}
        if files == expected:
            return {"schema_version": 1, "version": version, "source_commit": commit, "source_files": files}
    raise RuntimeError("Build refused: source does not match an available sealed Git commit; "
                       "use a clean export with Git metadata or an unmodified attested sdist")


def descriptor(build_root: Path, record: dict) -> dict:
    files = inventory(build_root)
    expected = {name.removeprefix("v4/src/"): value
                for name, value in record["source_files"].items()
                if name.startswith("v4/src/memory_os_v4/")}
    if files != expected:
        raise RuntimeError("Installed payload differs from sealed source (or stale build directory)")
    def canonical(value):
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return {"schema_version": 1, "version": record["version"], "source_commit": record["source_commit"],
            "source_inventory_sha256": hashlib.sha256(canonical(record["source_files"])).hexdigest(),
            "payload_sha256": hashlib.sha256(canonical(files)).hexdigest(), "files": files}


class AttestedBuildPy(build_py):
    def run(self):
        if getattr(self, "editable_mode", False):
            return super().run()  # development checkout, no installed-release descriptor claim
        record = seal(Path.cwd(), self.distribution.get_version())
        super().run()
        root = Path(self.build_lib)
        target = root / "memory_os_v4" / DESCRIPTOR
        target.write_text(json.dumps(descriptor(root, record), sort_keys=True, indent=2) + "\n", encoding="utf-8")

    def get_outputs(self, include_bytecode=1):
        if getattr(self, "editable_mode", False):
            return super().get_outputs(include_bytecode)
        return [*super().get_outputs(include_bytecode), str(Path(self.build_lib) / "memory_os_v4" / DESCRIPTOR)]


class AttestedSdist(sdist):
    def make_release_tree(self, base_dir, files):
        record = seal(Path.cwd(), self.distribution.get_version())
        super().make_release_tree(base_dir, files)
        (Path(base_dir) / SEED).write_text(json.dumps(record, sort_keys=True, indent=2) + "\n", encoding="utf-8")

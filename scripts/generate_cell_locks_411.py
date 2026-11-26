#!/usr/bin/env python3
"""Offline generator/verifier for 4.1.1 per-cell lexical locks, frozen artifact manifests, and L3 test pins.

Reads, with zero network, the frozen resolution of each retained cell (contract §6, step 3):
``<cell>.txt`` (``uv pip compile --generate-hashes``, binary wheels only) and ``pylock.<cell>.toml``
(PEP 751, read with ``tomllib``), and writes flat packaged resources under
``v4/src/memory_os_v4/resources/`` (required because ``pyproject.toml`` package-data
``resources/*.json`` / ``resources/*.txt`` is non-recursive).

Every manifest is ``frozen`` (schema 2): each package lists the wheels admitted for the cell,
``{filename, url, sha256, size}``. The ``preliminary`` state of lot L5 is no longer produced.

    generate_cell_locks_411.py --input-dir ~/memory-os-local/tools/411/locks-figes-20261124 [--check]
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
RESOURCES = ROOT / "v4" / "src" / "memory_os_v4" / "resources"
QUALIFIED_RUNTIME_PY = ROOT / "v4" / "src" / "memory_os_v4" / "ops" / "qualified_runtime.py"
CONSTRAINTS_SCRIPT_PY = ROOT / "v4" / "src" / "memory_os_constraints.py"
BOOTSTRAP_SCRIPT = ROOT / "scripts" / "memory_os_bootstrap.py"

DIGESTS_BEGIN = "# BEGIN GENERATED CELL_LOCK_DIGESTS"
DIGESTS_END = "# END GENERATED CELL_LOCK_DIGESTS"
CONSTRAINTS_DIGESTS_BEGIN = "# BEGIN GENERATED CELL_CONSTRAINTS_DIGESTS"
CONSTRAINTS_DIGESTS_END = "# END GENERATED CELL_CONSTRAINTS_DIGESTS"

ALL_LOCK_RESOURCE_NAMES = (
    ("semantic", "macos-arm64-py311", "qualified_runtime_v1.json", "qualified_runtime_constraints_v1.txt", None),
    (
        "lexical",
        "macos-arm64-py311",
        "qualified_runtime_lexical_v1.json",
        "qualified_runtime_constraints_lexical_v1.txt",
        "qualified_runtime_manifest_lexical_macos-arm64-py311_v1.json",
    ),
    (
        "lexical",
        "macos-arm64-py312",
        "qualified_runtime_lexical_macos-arm64-py312_v1.json",
        "qualified_runtime_constraints_lexical_macos-arm64-py312_v1.txt",
        "qualified_runtime_manifest_lexical_macos-arm64-py312_v1.json",
    ),
    (
        "lexical",
        "macos-arm64-py313",
        "qualified_runtime_lexical_macos-arm64-py313_v1.json",
        "qualified_runtime_constraints_lexical_macos-arm64-py313_v1.txt",
        "qualified_runtime_manifest_lexical_macos-arm64-py313_v1.json",
    ),
    (
        "lexical",
        "ubuntu2404-arm64-py311",
        "qualified_runtime_lexical_ubuntu2404-arm64-py311_v1.json",
        "qualified_runtime_constraints_lexical_ubuntu2404-arm64-py311_v1.txt",
        "qualified_runtime_manifest_lexical_ubuntu2404-arm64-py311_v1.json",
    ),
    (
        "lexical",
        "ubuntu2404-x86_64-py311",
        "qualified_runtime_lexical_ubuntu2404-x86_64-py311_v1.json",
        "qualified_runtime_constraints_lexical_ubuntu2404-x86_64-py311_v1.txt",
        "qualified_runtime_manifest_lexical_ubuntu2404-x86_64-py311_v1.json",
    ),
)

RETAINED_CELLS = (
    "macos-arm64-py311",
    "macos-arm64-py312",
    "macos-arm64-py313",
    "ubuntu2404-arm64-py311",
    "ubuntu2404-x86_64-py311",
)
# Frozen input filenames per cell, inside ``--input-dir``: (requirements with hashes, PEP 751 lock).
FROZEN_INPUT_FILES = {cell_id: (f"{cell_id}.txt", f"pylock.{cell_id}.toml") for cell_id in RETAINED_CELLS}
# Resolution target of each cell: (``--python-version``, ``--python-platform``).
CELL_RESOLUTION = {
    "macos-arm64-py311": ("3.11", "aarch64-apple-darwin"),
    "macos-arm64-py312": ("3.12", "aarch64-apple-darwin"),
    "macos-arm64-py313": ("3.13", "aarch64-apple-darwin"),
    "ubuntu2404-arm64-py311": ("3.11", "aarch64-manylinux_2_28"),
    "ubuntu2404-x86_64-py311": ("3.11", "x86_64-manylinux_2_28"),
}
RESOLVER = "uv 0.13.9"
DEFAULT_RESOLVED_AT = "2026-11-24"
# Resolution date of a cell resolved later with the same frozen recipe (same uv, same --exclude-newer): the
# x86_64 lock comes from GitHub Actions run 45879240439 (2026-11-26). Used when no --resolved-at is given.
CELL_RESOLVED_AT = {"ubuntu2404-x86_64-py311": "2026-11-26"}
DEFAULT_EXCLUDE_NEWER = "2026-11-24T00:00:00Z"
MANIFEST_SCHEMA_VERSION = 2
_PYLOCK_TOP_KEYS = frozenset({"lock-version", "created-by", "requires-python", "packages"})
_PYLOCK_PACKAGE_KEYS = frozenset({"name", "version", "wheels"})
_PYLOCK_WHEEL_KEYS = frozenset({"name", "url", "upload-time", "size", "hashes"})
CELL_LABELS = {
    "macos-arm64-py311": "macOS arm64 CPython 3.11",
    "macos-arm64-py312": "macOS arm64 CPython 3.12",
    "macos-arm64-py313": "macOS arm64 CPython 3.13",
    "ubuntu2404-arm64-py311": "Ubuntu 24.04 arm64 CPython 3.11",
    "ubuntu2404-x86_64-py311": "Ubuntu 24.04 x86_64 CPython 3.11",
}
# Flat resource filenames under v4/src/memory_os_v4/resources/.
# Cell 1 keeps its historical catalog and constraints filenames unchanged.
CELL_RESOURCE_NAMES = {
    "macos-arm64-py311": (
        "qualified_runtime_lexical_v1.json",
        "qualified_runtime_constraints_lexical_v1.txt",
        "qualified_runtime_manifest_lexical_macos-arm64-py311_v1.json",
    ),
    "macos-arm64-py312": (
        "qualified_runtime_lexical_macos-arm64-py312_v1.json",
        "qualified_runtime_constraints_lexical_macos-arm64-py312_v1.txt",
        "qualified_runtime_manifest_lexical_macos-arm64-py312_v1.json",
    ),
    "macos-arm64-py313": (
        "qualified_runtime_lexical_macos-arm64-py313_v1.json",
        "qualified_runtime_constraints_lexical_macos-arm64-py313_v1.txt",
        "qualified_runtime_manifest_lexical_macos-arm64-py313_v1.json",
    ),
    "ubuntu2404-arm64-py311": (
        "qualified_runtime_lexical_ubuntu2404-arm64-py311_v1.json",
        "qualified_runtime_constraints_lexical_ubuntu2404-arm64-py311_v1.txt",
        "qualified_runtime_manifest_lexical_ubuntu2404-arm64-py311_v1.json",
    ),
    "ubuntu2404-x86_64-py311": (
        "qualified_runtime_lexical_ubuntu2404-x86_64-py311_v1.json",
        "qualified_runtime_constraints_lexical_ubuntu2404-x86_64-py311_v1.txt",
        "qualified_runtime_manifest_lexical_ubuntu2404-x86_64-py311_v1.json",
    ),
}
REQUIRED_TIKTOKEN_VERSION = "0.12.0"
_PIN_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9][A-Za-z0-9.!+_-]*)$")
_HASH_RE = re.compile(r"^--hash=sha256:([0-9a-f]{64})$")
_HEX64_RE = re.compile(r"[0-9a-f]{64}")
_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_UTC_SECOND_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")


def canonical_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_uv_compiled_lock(text: str) -> list[dict]:
    """Parse a ``uv pip compile --generate-hashes`` output strictly; zero markers, zero loose bounds.

    Returns a sorted list of ``{"name": <canonical>, "version": <str>, "sha256": [<64 hex>, ...]}``.
    """
    logical_lines: list[str] = []
    current = ""
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            if current:
                raise ValueError("unexpected blank or comment line inside line continuation")
            continue
        if stripped.endswith("\\"):
            part = stripped[:-1].strip()
            current = f"{current} {part}".strip() if current else part
        else:
            line = f"{current} {stripped}".strip() if current else stripped
            logical_lines.append(line)
            current = ""
    if current:
        raise ValueError("unterminated line continuation at end of compiled lock")

    entries: dict[str, dict] = {}
    for line in logical_lines:
        # Refuse markers, comments-after-spec, URLs, extras or loose operators.
        if ";" in line or "@" in line or "[" in line or "]" in line or "#" in line:
            raise ValueError(f"forbidden marker, extra, URL or inline comment in lock line: {line[:120]!r}")
        tokens = line.split()
        if len(tokens) < 2:
            raise ValueError(f"lock entry lacks --hash=sha256: {line[:120]!r}")
        pin = _PIN_RE.fullmatch(tokens[0])
        if not pin:
            raise ValueError(f"not an exact 'name==version' pin: {tokens[0]!r}")
        name = canonical_name(pin.group(1))
        version = pin.group(2)
        if name in entries:
            raise ValueError(f"duplicate package in compiled lock: {name}")
        hashes: list[str] = []
        for token in tokens[1:]:
            match = _HASH_RE.fullmatch(token)
            if not match:
                raise ValueError(f"invalid hash option for {name}: {token!r}")
            hashes.append(match.group(1))
        unique_hashes = sorted(set(hashes))
        if not unique_hashes:
            raise ValueError(f"no sha256 hash found for {name}")
        entries[name] = {"name": name, "version": version, "sha256": unique_hashes}

    if not entries:
        raise ValueError("compiled lock contains no packages")
    return [entries[name] for name in sorted(entries)]


def _ml_closure_names(resources_dir: Path) -> set[str]:
    semantic = json.loads((resources_dir / "qualified_runtime_v1.json").read_text(encoding="utf-8"))
    lexical = json.loads((resources_dir / "qualified_runtime_lexical_v1.json").read_text(encoding="utf-8"))
    sem = {canonical_name(k) for k in semantic["versions"]}
    for extra in semantic.get("extra_versions", {}).values():
        sem |= {canonical_name(k) for k in extra}
    lex = {canonical_name(k) for k in lexical["versions"]}
    for extra in lexical.get("extra_versions", {}).values():
        lex |= {canonical_name(k) for k in extra}
    return sem - lex


def _load_qualified_runtime():
    """Load the runtime's own ``qualified_runtime`` module so validation rules are never duplicated."""
    v4_src = str(ROOT / "v4" / "src")
    if v4_src not in sys.path:
        sys.path.insert(0, v4_src)
    from memory_os_v4.ops import qualified_runtime
    return qualified_runtime


def _valid_https_url(value: object, *, name: str | None = None, version: str | None = None) -> bool:
    return _load_qualified_runtime().valid_artifact_url(value, name=name, version=version)


def frozen_input_files(input_dir: Path) -> dict[str, tuple[Path, Path]]:
    """``{cell_id: (<cell>.txt, pylock.<cell>.toml)}`` inside one frozen input directory."""
    return {cell_id: (input_dir / txt, input_dir / lock) for cell_id, (txt, lock) in FROZEN_INPUT_FILES.items()}


def parse_pylock(text: str) -> list[dict]:
    """Parse one PEP 751 ``pylock.<cell>.toml`` strictly; binary wheels on the allowed host only.

    Returns a list sorted by name of ``{"name": <canonical>, "version", "wheels": [...]}``, each
    wheel being ``{"filename", "sha256", "size", "url"}``, sorted by ``filename``. Refused: a sdist
    (or any other source), a marker or any unknown key, a package without wheel, an invalid URL, a
    wheel ``name`` that is not the last URL segment, a SHA-256 that is not 64 lowercase hexadecimal
    characters, a non-positive size, and every duplicate (package, filename, URL or SHA-256).
    """
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"unparsable pylock: {exc}") from None
    unknown_top = sorted(set(doc) - _PYLOCK_TOP_KEYS)
    if unknown_top:
        raise ValueError(f"unsupported pylock keys: {', '.join(unknown_top)}")
    if doc.get("lock-version") != "1.0":
        raise ValueError(f"unsupported pylock lock-version: {doc.get('lock-version')!r}")
    packages = doc.get("packages")
    if not isinstance(packages, list) or not packages:
        raise ValueError("pylock contains no packages")
    entries: dict[str, dict] = {}
    seen_files: set[str] = set()
    seen_urls: set[str] = set()
    seen_hashes: set[str] = set()
    for package in packages:
        if not isinstance(package, dict):
            raise ValueError("pylock package is not a table")
        raw_name, version = package.get("name"), package.get("version")
        if not isinstance(raw_name, str) or not _PIN_RE.fullmatch(f"{raw_name}=={version}"):
            raise ValueError(f"pylock package lacks an exact name and version: {raw_name!r} {version!r}")
        name = canonical_name(raw_name)
        if "sdist" in package:
            raise ValueError(f"sdist forbidden in pylock (binary wheels only): {name}")
        unknown = sorted(set(package) - _PYLOCK_PACKAGE_KEYS)
        if unknown:
            raise ValueError(f"unsupported pylock package keys for {name}: {', '.join(unknown)}")
        if name in entries:
            raise ValueError(f"duplicate package in pylock: {name}")
        raw_wheels = package.get("wheels")
        if not isinstance(raw_wheels, list) or not raw_wheels:
            raise ValueError(f"pylock package without wheel: {name}")
        wheels = []
        for wheel in raw_wheels:
            if not isinstance(wheel, dict):
                raise ValueError(f"pylock wheel of {name} is not a table")
            unknown = sorted(set(wheel) - _PYLOCK_WHEEL_KEYS)
            if unknown:
                raise ValueError(f"unsupported pylock wheel keys for {name}: {', '.join(unknown)}")
            url = wheel.get("url")
            if not _valid_https_url(url, name=name, version=version):
                raise ValueError(f"invalid wheel artifact URL for {name}: {url!r}")
            filename = url.rsplit("/", 1)[-1]
            if wheel.get("name", filename) != filename:
                raise ValueError(f"wheel filename of {name} is not the last URL segment: {wheel.get('name')!r}")
            hashes = wheel.get("hashes")
            digest = hashes.get("sha256") if isinstance(hashes, dict) else None
            if not isinstance(digest, str) or not _HEX64_RE.fullmatch(digest) or set(hashes) != {"sha256"}:
                raise ValueError(f"wheel {filename} needs exactly one sha256 of 64 lowercase hexadecimal characters")
            size = wheel.get("size")
            if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
                raise ValueError(f"wheel {filename} needs a positive integer size")
            if filename in seen_files or url in seen_urls or digest in seen_hashes:
                raise ValueError(f"duplicate wheel filename, URL or sha256 in pylock: {filename}")
            seen_files.add(filename)
            seen_urls.add(url)
            seen_hashes.add(digest)
            wheels.append({"filename": filename, "sha256": digest, "size": size, "url": url})
        entries[name] = {"name": name, "version": version, "wheels": sorted(wheels, key=lambda w: w["filename"])}
    return [entries[name] for name in sorted(entries)]


def build_cell_lock_files(
    cell_id: str,
    compiled_text: str,
    pylock_bytes: bytes,
    *,
    reference_catalog: dict,
    ml_packages: set[str],
    resolved_at: str | None = None,
    exclude_newer: str = DEFAULT_EXCLUDE_NEWER,
    existing_constraints_bytes: bytes | None = None,
) -> tuple[dict, str, dict]:
    """Produce ``(catalog_dict, constraints_text, manifest_dict)`` for one retained lexical cell.

    ``compiled_text`` is ``<cell>.txt`` and ``pylock_bytes`` the bytes of ``pylock.<cell>.toml``.
    Both must describe the same packages and versions, and every admitted wheel SHA-256 must be one
    of the hashes of its package in ``<cell>.txt``; the constraints follow the same set exactly.
    Every wheel must also fit the cell (``qualified_runtime.wheel_compatible_with_cell``: the lock
    of another cell is refused). ``resolved_at`` defaults to the frozen date of the cell.
    """
    if cell_id not in RETAINED_CELLS:
        raise ValueError(f"not a retained cell: {cell_id}")
    if resolved_at is None:
        resolved_at = CELL_RESOLVED_AT.get(cell_id, DEFAULT_RESOLVED_AT)
    if not isinstance(resolved_at, str) or not _DATE_RE.fullmatch(resolved_at):
        raise ValueError(f"invalid resolved_at (YYYY-MM-DD): {resolved_at!r}")
    if not isinstance(exclude_newer, str) or not _UTC_SECOND_RE.fullmatch(exclude_newer):
        raise ValueError(f"invalid exclude_newer (YYYY-MM-DDTHH:MM:SSZ): {exclude_newer!r}")
    artifacts = parse_uv_compiled_lock(compiled_text)
    by_name = {item["name"]: item["version"] for item in artifacts}
    if by_name.get("tiktoken") != REQUIRED_TIKTOKEN_VERSION:
        raise ValueError(
            f"{cell_id}: tiktoken must be pinned to {REQUIRED_TIKTOKEN_VERSION} "
            f"(found {by_name.get('tiktoken')!r})"
        )
    leaked_ml = sorted(set(by_name) & {canonical_name(pkg) for pkg in ml_packages})
    if leaked_ml:
        raise ValueError(f"{cell_id}: ML packages forbidden in lexical lock: {', '.join(leaked_ml)}")

    try:
        locked = parse_pylock(pylock_bytes.decode("utf-8"))
    except UnicodeDecodeError:
        raise ValueError(f"{cell_id}: pylock is not UTF-8") from None
    except ValueError as exc:
        raise ValueError(f"{cell_id}: {exc}") from None
    locked_versions = {item["name"]: item["version"] for item in locked}
    if locked_versions != by_name:
        differing = sorted(
            name for name in set(locked_versions) | set(by_name) if locked_versions.get(name) != by_name.get(name)
        )
        raise ValueError(f"{cell_id}: pylock packages or versions disagree with the constraints: {', '.join(differing)}")
    admitted = {item["name"]: set(item["sha256"]) for item in artifacts}
    for item in locked:
        for wheel in item["wheels"]:
            if not _load_qualified_runtime().wheel_compatible_with_cell(wheel["filename"], cell_id):
                raise ValueError(f"{cell_id}: wheel {wheel['filename']} is not compatible with the cell")
            if wheel["sha256"] not in admitted[item["name"]]:
                raise ValueError(f"{cell_id}: wheel {wheel['filename']} sha256 is not a hash of the compiled lock")

    brief_keys = {canonical_name(k) for k in reference_catalog.get("extra_versions", {}).get("brief", {})}
    versions = {name: by_name[name] for name in sorted(by_name) if name not in brief_keys}
    brief_versions = {name: by_name[name] for name in sorted(by_name) if name in brief_keys}
    if set(brief_versions) != brief_keys:
        missing = sorted(brief_keys - set(brief_versions))
        raise ValueError(f"{cell_id}: missing brief extra packages: {', '.join(missing)}")

    profile_id = f"lexical-{cell_id}-v1"
    label = CELL_LABELS[cell_id]
    catalog_doc = {
        "basis": (
            f"Lexical runtime closure for {label} ({cell_id}), resolved via uv pip compile "
            "--generate-hashes (binary wheels only) from the 36-package lexical profile; "
            "no ML package, no model, no developer extras"
        ),
        "cell_id": cell_id,
        "closure_resource": "qualified_runtime_closure_v1.json",
        "encoding_runtime_keys": [],
        "extra_versions": {
            "brief": brief_versions,
            "ops": {},
        },
        "limits": (
            f"Exact lock of the lexical retrieval profile for {label} ({cell_id}). "
            f"Lock state: frozen (resolved {resolved_at}, exclude-newer {exclude_newer}). "
            "No encoder, no vector index: the ML closure is neither required nor inspected."
        ),
        "lock_state": "frozen",
        "profile_id": profile_id,
        "recorded_at": resolved_at,
        "schema_version": 1,
        "versions": versions,
    }

    if existing_constraints_bytes is not None:
        constraints_text = existing_constraints_bytes.decode("utf-8")
        # Verify the preserved constraints file agrees line-for-line with the compiled lock.
        existing_pins = {}
        for raw in constraints_text.splitlines():
            s = raw.strip()
            if not s or s.startswith("#"):
                continue
            m = _PIN_RE.fullmatch(s)
            if not m:
                raise ValueError(f"{cell_id}: existing constraints line is not an exact pin: {s!r}")
            existing_pins[canonical_name(m.group(1))] = m.group(2)
        if existing_pins != by_name:
            raise ValueError(f"{cell_id}: preserved constraints file disagrees with compiled lock")
        constraints_sha = _sha256_bytes(existing_constraints_bytes)
    else:
        header = (
            f"# Lexical profile, {label} ({cell_id}): core + ops + brief; "
            "no ML package, no model, no developer extras.\n"
        )
        constraints_text = header + "".join(f"{name}=={by_name[name]}\n" for name in sorted(by_name))
        constraints_sha = _sha256_bytes(constraints_text.encode("utf-8"))

    python_version, python_platform = CELL_RESOLUTION[cell_id]
    manifest_doc = {
        "artifacts": locked,
        "cell_id": cell_id,
        "constraints_sha256": constraints_sha,
        "exclude_newer": exclude_newer,
        "lock_state": "frozen",
        "profile": "lexical",
        "profile_id": profile_id,
        "pylock_sha256": _sha256_bytes(pylock_bytes),
        "resolved_at": resolved_at,
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "source": (
            f"{RESOLVER}: uv pip compile entree_lexical.txt --python-version {python_version} "
            f"--python-platform {python_platform} --only-binary :all: --no-header "
            f"--exclude-newer {exclude_newer} --format pylock.toml -o pylock.{cell_id}.toml "
            f"(resolved {resolved_at}; binary wheels only; every wheel admitted for the cell is listed)"
        ),
    }
    return catalog_doc, constraints_text, manifest_doc


def _load_bootstrap_module():
    spec = importlib.util.spec_from_file_location("memory_os_bootstrap_l5", BOOTSTRAP_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {BOOTSTRAP_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_bootstrap_cell_entry(
    cell_id: str,
    *,
    constraints_path: Path,
    manifest_path: Path,
    profile: str = "lexical",
    test_values: bool = False,
) -> dict:
    """Produce one ``cells.<cell_id>.<profile>`` block for ``scripts/memory_os_bootstrap.py``.

    By default every wheel admitted by the frozen manifest is listed with its real ``filename``,
    ``url``, ``sha256`` and ``size``. With ``test_values=True`` (offline tests) one synthetic
    ``example.invalid`` URL, wheel filename and test size per package is attached to the first
    admitted SHA-256 of ``manifest_path``.
    """
    if cell_id not in RETAINED_CELLS:
        raise ValueError(f"not a retained cell: {cell_id}")

    constraints_bytes = constraints_path.read_bytes()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION or manifest.get("lock_state") != "frozen"
            or manifest.get("cell_id") != cell_id or manifest.get("profile") != profile):
        raise ValueError(f"manifest {manifest_path} is not the frozen manifest of {cell_id}/{profile}")
    if manifest.get("constraints_sha256") != _sha256_bytes(constraints_bytes):
        raise ValueError(f"manifest {manifest_path} constraints_sha256 disagrees with {constraints_path}")

    constraints = []
    for raw in constraints_bytes.decode("utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if not _PIN_RE.fullmatch(line):
            raise ValueError(f"invalid constraint line in {constraints_path}: {line!r}")
        constraints.append(line)
    constraints.sort()

    bootstrap = _load_bootstrap_module()
    artifacts = []
    for item in manifest["artifacts"]:
        name = canonical_name(item["name"])
        version = item["version"]
        if test_values:
            filename = f"{name.replace('-', '_')}-{version}-py3-none-any.whl"
            wheels = [{
                "filename": filename,
                "url": f"https://example.invalid/test-only/{cell_id}/{filename}",
                "sha256": item["wheels"][0]["sha256"],
                "size": 1024,
            }]
        else:
            wheels = item["wheels"]
        for wheel in wheels:
            artifacts.append({
                "name": name,
                "version": version,
                "filename": wheel["filename"],
                "url": wheel["url"],
                "sha256": wheel["sha256"],
                "size": wheel["size"],
            })

    entry = {"constraints": constraints, "artifacts": artifacts}
    entry["lock_sha256"] = bootstrap.lock_fingerprint(cell_id, profile, entry)
    problem = bootstrap._lock_problem(cell_id, profile, entry)
    if problem:
        raise ValueError(f"generated bootstrap entry invalid: {problem}")
    return entry


def build_bootstrap_test_pin(
    resources_dir: Path = RESOURCES,
    *,
    version: str = "4.1.1",
    wheel_sha256: str = "a" * 64,
    wheel_content_sha256: str = "b" * 64,
    wheel_size: int = 262144,
    cells: tuple[str, ...] = RETAINED_CELLS,
) -> dict:
    """Produce a complete ``state="test"`` bootstrap pin covering ``cells.<cell>.lexical`` for L5 tests."""
    bootstrap = _load_bootstrap_module()
    cell_entries: dict[str, dict] = {}
    for cell_id in cells:
        _, constraints_name, manifest_name = CELL_RESOURCE_NAMES[cell_id]
        cell_entries[cell_id] = {
            "lexical": build_bootstrap_cell_entry(
                cell_id,
                constraints_path=resources_dir / constraints_name,
                manifest_path=resources_dir / manifest_name,
                profile="lexical",
                test_values=True,
            )
        }
    pin = {
        "schema_version": 1,
        "state": "test",
        "package": "memory-os-ai",
        "version": version,
        "wheel": {
            "filename": f"memory_os_ai-{version}-py3-none-any.whl",
            "sha256": wheel_sha256,
            "size": wheel_size,
            "content_sha256": wheel_content_sha256,
            "source": f"https://example.invalid/test-only/memory_os_ai-{version}-py3-none-any.whl",
        },
        "cells": cell_entries,
    }
    checked = bootstrap.load_pin(json.dumps(pin))
    if not checked["ok"]:
        raise ValueError(f"generated test pin invalid: {checked['message']}")
    return pin


def compute_cell_lock_digests(resources_dir: Path) -> dict[tuple[str, str], tuple[str, str, str | None]]:
    """Compute SHA-256 digests of ``(catalog, constraints, manifest | None)`` for all 6 locks."""
    digests: dict[tuple[str, str], tuple[str, str, str | None]] = {}
    for profile, cell_id, cat_name, con_name, man_name in ALL_LOCK_RESOURCE_NAMES:
        cat_path = resources_dir / cat_name
        con_path = resources_dir / con_name
        man_path = None if man_name is None else resources_dir / man_name
        for p in (cat_path, con_path, *((man_path,) if man_path is not None else ())):
            if p.is_symlink() or not p.is_file():
                raise ValueError(f"missing or symlinked lock resource: {p}")
        digests[(profile, cell_id)] = (
            _sha256_bytes(cat_path.read_bytes()),
            _sha256_bytes(con_path.read_bytes()),
            None if man_path is None else _sha256_bytes(man_path.read_bytes()),
        )
    return digests


def render_cell_lock_digests_block(resources_dir: Path) -> str:
    """Render the deterministic Python constant block anchored in ``qualified_runtime.py``."""
    digests = compute_cell_lock_digests(resources_dir)
    lines = [
        DIGESTS_BEGIN,
        "CELL_LOCK_DIGESTS: dict[tuple[str, str], tuple[str, str, str | None]] = {",
    ]
    for profile, cell_id, _, _, _ in ALL_LOCK_RESOURCE_NAMES:
        cat_sha, con_sha, man_sha = digests[(profile, cell_id)]
        lines.append(f"    ({profile!r}, {cell_id!r}): (")
        lines.append(f"        {cat_sha!r},")
        lines.append(f"        {con_sha!r},")
        lines.append(f"        {man_sha!r},")
        lines.append("    ),")
    lines.append("}")
    lines.append(DIGESTS_END)
    return "\n".join(lines)


def render_constraints_digests_block(resources_dir: Path) -> str:
    """Render the stdlib-only ``CELL_CONSTRAINTS_DIGESTS`` constant block in ``memory_os_constraints.py``."""
    digests = compute_cell_lock_digests(resources_dir)
    lines = [
        CONSTRAINTS_DIGESTS_BEGIN,
        "CELL_CONSTRAINTS_DIGESTS: dict[tuple[str, str], str] = {",
    ]
    for profile, cell_id, _, _, _ in ALL_LOCK_RESOURCE_NAMES:
        _, con_sha, _ = digests[(profile, cell_id)]
        lines.append(f"    ({profile!r}, {cell_id!r}): {con_sha!r},")
    lines.append("}")
    lines.append(CONSTRAINTS_DIGESTS_END)
    return "\n".join(lines)


def _sync_generated_block(
    target_path: Path,
    begin_marker: str,
    end_marker: str,
    expected_block: str,
    *,
    check_only: bool = False,
) -> bool:
    text = target_path.read_text(encoding="utf-8")
    pattern = re.compile(re.escape(begin_marker) + r".*?" + re.escape(end_marker), re.DOTALL)
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise ValueError(f"expected exactly one {begin_marker}..{end_marker} block in {target_path}")
    current_block = matches[0].group(0)
    if check_only:
        return current_block == expected_block
    updated = text[: matches[0].start()] + expected_block + text[matches[0].end() :]
    if updated != text:
        target_path.write_text(updated, encoding="utf-8")
    return True


def _parse_generated_dict(target_path: Path, begin_marker: str, end_marker: str) -> dict | None:
    """Safely parse the dict literal inside a generated constant block using ``ast.literal_eval``."""
    text = target_path.read_text(encoding="utf-8")
    pattern = re.compile(re.escape(begin_marker) + r"(.*?)" + re.escape(end_marker), re.DOTALL)
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        return None
    try:
        tree = ast.parse(matches[0].group(1))
    except SyntaxError:
        return None
    if len(tree.body) != 1:
        return None
    stmt = tree.body[0]
    value_node = stmt.value if isinstance(stmt, (ast.Assign, ast.AnnAssign)) else None
    if value_node is None:
        return None
    try:
        parsed = ast.literal_eval(value_node)
    except (ValueError, SyntaxError):
        return None
    return parsed if isinstance(parsed, dict) else None


def sync_qualified_runtime_digests(
    qualified_runtime_path: Path,
    resources_dir: Path,
    *,
    check_only: bool = False,
) -> bool:
    """Write or verify ``CELL_LOCK_DIGESTS`` in ``qualified_runtime.py``; returns True when matching/written."""
    expected_block = render_cell_lock_digests_block(resources_dir)
    return _sync_generated_block(
        qualified_runtime_path, DIGESTS_BEGIN, DIGESTS_END, expected_block, check_only=check_only,
    )


def sync_constraints_script_digests(
    constraints_script_path: Path,
    resources_dir: Path,
    *,
    check_only: bool = False,
) -> bool:
    """Write or verify ``CELL_CONSTRAINTS_DIGESTS`` in ``memory_os_constraints.py``."""
    expected_block = render_constraints_digests_block(resources_dir)
    return _sync_generated_block(
        constraints_script_path,
        CONSTRAINTS_DIGESTS_BEGIN,
        CONSTRAINTS_DIGESTS_END,
        expected_block,
        check_only=check_only,
    )


def validate_all_triplets_on_disk(
    resources_dir: Path,
    *,
    expected_digests_map: dict | None = None,
    expected_constraints_map: dict | None = None,
) -> list[str]:
    """Validate ALL 6 lock triplets (historical files included) using the runtime's own attestation."""
    qr = _load_qualified_runtime()
    base = resources_dir.absolute()
    failures: list[str] = []
    for profile, cell_id, cat_name, con_name, man_name in ALL_LOCK_RESOURCE_NAMES:
        cat_path = base / cat_name
        con_path = base / con_name
        man_path = None if man_name is None else base / man_name
        expected_digests = None if expected_digests_map is None else expected_digests_map.get((profile, cell_id))
        if expected_digests_map is not None and expected_digests is None:
            failures.append(f"attestation:{profile}:{cell_id}")
            continue
        attested = qr.validate_lock_files(
            cell_id,
            profile=profile,
            catalog_path=cat_path,
            constraints_path=con_path,
            manifest_path=man_path,
            expected_digests=expected_digests,
            resources_dir=base,
        )
        if attested is None:
            failures.append(f"attestation:{profile}:{cell_id}")
            continue
        if expected_constraints_map is not None:
            if expected_constraints_map.get((profile, cell_id)) != attested["constraints_sha256"]:
                failures.append(f"constraints_digest:{profile}:{cell_id}")
    return failures


def generate_all(
    input_files: dict[str, tuple[Path, Path]],
    output_dir: Path,
    *,
    resolved_at: str | None = None,
    exclude_newer: str = DEFAULT_EXCLUDE_NEWER,
    check_only: bool = False,
    qualified_runtime_path: Path | None = None,
    constraints_script_path: Path | None = None,
) -> dict[str, list[str]]:
    """Generate (or verify with ``check_only=True``) flat per-cell lexical lock resources and anchored digests.

    ``input_files`` maps each retained cell to ``(<cell>.txt, pylock.<cell>.toml)``. Validates all
    6 lock triplets (including historical semantic and Cell-1 lexical files) with
    ``qualified_runtime`` before writing anything, after writing, and during ``--check``.
    """
    qr = _load_qualified_runtime()
    base_dir = output_dir.absolute()
    ref_catalog_path = base_dir / "qualified_runtime_lexical_v1.json"
    reference_catalog = json.loads(ref_catalog_path.read_text(encoding="utf-8"))
    ml_packages = _ml_closure_names(base_dir)

    written: list[str] = []
    mismatches: list[str] = []
    planned_writes: list[tuple[Path, bytes]] = []
    candidate_triplets: list[tuple[str, str, bytes, bytes, bytes | None]] = []

    # Validate the historical semantic triplet first (must always satisfy runtime invariants).
    sem_cat_path = base_dir / "qualified_runtime_v1.json"
    sem_con_path = base_dir / "qualified_runtime_constraints_v1.txt"
    sem_ok = qr.validate_lock_files(
        "macos-arm64-py311",
        profile="semantic",
        catalog_path=sem_cat_path,
        constraints_path=sem_con_path,
        manifest_path=None,
        resources_dir=base_dir,
    )
    if sem_ok is None:
        if check_only:
            mismatches.append("attestation:semantic:macos-arm64-py311")
        else:
            raise ValueError("historical semantic lock (macos-arm64-py311) failed runtime attestation; nothing written")

    for cell_id in RETAINED_CELLS:
        if cell_id not in input_files:
            raise ValueError(f"missing frozen input lock for {cell_id}")
        compiled_path, pylock_path = input_files[cell_id]
        cat_name, con_name, man_name = CELL_RESOURCE_NAMES[cell_id]
        cat_path, con_path, man_path = base_dir / cat_name, base_dir / con_name, base_dir / man_name

        # Cell 1 preserves the historical catalog and constraints files byte-for-byte.
        preserve_historical = cell_id == "macos-arm64-py311"
        existing_con = con_path.read_bytes() if preserve_historical else None
        try:
            catalog_doc, constraints_text, manifest_doc = build_cell_lock_files(
                cell_id,
                compiled_path.read_text(encoding="utf-8"),
                pylock_path.read_bytes(),
                reference_catalog=reference_catalog,
                ml_packages=ml_packages,
                resolved_at=resolved_at,
                exclude_newer=exclude_newer,
                existing_constraints_bytes=existing_con,
            )
        except (OSError, ValueError) as exc:
            if not check_only:
                raise
            # --check reports an invalid or unreadable input as a mismatch, never as a traceback.
            mismatches.append(f"input:lexical:{cell_id} ({exc})")
            continue
        man_bytes = (json.dumps(manifest_doc, indent=2, sort_keys=True) + "\n").encode("utf-8")
        if preserve_historical:
            cat_bytes = qr._read_plain_file(cat_path)
            con_bytes = qr._read_plain_file(con_path)
            if cat_bytes is None or con_bytes is None:
                if check_only:
                    mismatches.append(f"attestation:lexical:{cell_id}")
                    continue
                raise ValueError(f"historical lexical lock files for {cell_id} are missing or unsafe; nothing written")
        else:
            cat_bytes = (json.dumps(catalog_doc, indent=2, sort_keys=True) + "\n").encode("utf-8")
            con_bytes = constraints_text.encode("utf-8")
            planned_writes.append((cat_path, cat_bytes))
            planned_writes.append((con_path, con_bytes))
        planned_writes.append((man_path, man_bytes))
        candidate_triplets.append(("lexical", cell_id, cat_bytes, con_bytes, man_bytes))

    # Validate all 5 lexical triplets (including historical Cell-1 catalog/constraints) BEFORE any write.
    if not check_only:
        for profile, cell_id, cat_bytes, con_bytes, man_bytes in candidate_triplets:
            attested = qr.validate_lock_bytes(
                cell_id,
                profile=profile,
                catalog_bytes=cat_bytes,
                constraints_bytes=con_bytes,
                manifest_bytes=man_bytes,
                resources_dir=base_dir,
            )
            if attested is None:
                raise ValueError(
                    f"{profile}/{cell_id} lock triplet failed runtime attestation (including historical files); "
                    "nothing written"
                )
        for path, payload in planned_writes:
            path.write_bytes(payload)
            written.append(path.name)
        if qualified_runtime_path is not None:
            sync_qualified_runtime_digests(qualified_runtime_path, base_dir, check_only=False)
            written.append(f"{qualified_runtime_path.name}:CELL_LOCK_DIGESTS")
        if constraints_script_path is not None:
            sync_constraints_script_digests(constraints_script_path, base_dir, check_only=False)
            written.append(f"{constraints_script_path.name}:CELL_CONSTRAINTS_DIGESTS")
        # Final post-write attestation on disk across all 6 triplets with the anchored digests.
        anchored_locks = (
            _parse_generated_dict(qualified_runtime_path, DIGESTS_BEGIN, DIGESTS_END)
            if qualified_runtime_path is not None
            else None
        )
        anchored_con = (
            _parse_generated_dict(constraints_script_path, CONSTRAINTS_DIGESTS_BEGIN, CONSTRAINTS_DIGESTS_END)
            if constraints_script_path is not None
            else None
        )
        post_failures = validate_all_triplets_on_disk(
            base_dir, expected_digests_map=anchored_locks, expected_constraints_map=anchored_con,
        )
        if post_failures:
            raise ValueError("post-write runtime attestation failed: " + ", ".join(post_failures))
        return {"written": written, "mismatches": mismatches}

    # --check mode: verify generated payloads, anchored digests in both modules, AND all 6 triplets via runtime.
    for path, payload in planned_writes:
        if not path.is_file() or path.read_bytes() != payload:
            mismatches.append(path.name)

    anchored_locks = None
    if qualified_runtime_path is not None:
        if not sync_qualified_runtime_digests(qualified_runtime_path, base_dir, check_only=True):
            mismatches.append(f"{qualified_runtime_path.name}:CELL_LOCK_DIGESTS")
        anchored_locks = _parse_generated_dict(qualified_runtime_path, DIGESTS_BEGIN, DIGESTS_END)
        if anchored_locks is None:
            mismatches.append(f"{qualified_runtime_path.name}:CELL_LOCK_DIGESTS_UNPARSABLE")

    anchored_con = None
    if constraints_script_path is not None:
        if not sync_constraints_script_digests(constraints_script_path, base_dir, check_only=True):
            mismatches.append(f"{constraints_script_path.name}:CELL_CONSTRAINTS_DIGESTS")
        anchored_con = _parse_generated_dict(constraints_script_path, CONSTRAINTS_DIGESTS_BEGIN, CONSTRAINTS_DIGESTS_END)
        if anchored_con is None:
            mismatches.append(f"{constraints_script_path.name}:CELL_CONSTRAINTS_DIGESTS_UNPARSABLE")

    for failure in validate_all_triplets_on_disk(
        base_dir, expected_digests_map=anchored_locks, expected_constraints_map=anchored_con,
    ):
        if failure not in mismatches:
            mismatches.append(failure)
    return {"written": written, "mismatches": mismatches}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--input-dir", type=Path,
        help="Frozen input directory holding <cell>.txt and pylock.<cell>.toml for the five retained cells",
    )
    parser.add_argument("--output-dir", type=Path, default=RESOURCES)
    parser.add_argument(
        "--qualified-runtime", type=Path, default=QUALIFIED_RUNTIME_PY,
        help="Path to qualified_runtime.py whose CELL_LOCK_DIGESTS constant is generated/verified",
    )
    parser.add_argument(
        "--constraints-script", type=Path, default=CONSTRAINTS_SCRIPT_PY,
        help="Path to memory_os_constraints.py whose CELL_CONSTRAINTS_DIGESTS constant is generated/verified",
    )
    parser.add_argument("--resolved-at", default=None,
                        help="Resolution date (YYYY-MM-DD) of every cell; default: the frozen date of each cell")
    parser.add_argument("--exclude-newer", default=DEFAULT_EXCLUDE_NEWER, help="uv --exclude-newer of the resolution")
    parser.add_argument("--check", action="store_true", help="Verify existing files, anchored digests and runtime invariants without writing")
    parser.add_argument(
        "--emit-test-pin", type=Path, default=None,
        help="Write a state='test' bootstrap pin JSON for the five cells (never 'release')",
    )
    args = parser.parse_args(argv)

    if args.input_dir is not None:
        result = generate_all(
            frozen_input_files(args.input_dir),
            args.output_dir,
            resolved_at=args.resolved_at,
            exclude_newer=args.exclude_newer,
            check_only=args.check,
            qualified_runtime_path=args.qualified_runtime,
            constraints_script_path=args.constraints_script,
        )
        if args.check:
            if result["mismatches"]:
                print("MISMATCH: " + ", ".join(result["mismatches"]), file=sys.stderr)
                return 1
            print(f"OK: all {len(ALL_LOCK_RESOURCE_NAMES)} cell locks, manifests, anchored digests and runtime invariants match")
        else:
            print("WRITTEN: " + ", ".join(result["written"]))

    if args.emit_test_pin is not None:
        pin = build_bootstrap_test_pin(args.output_dir)
        args.emit_test_pin.write_text(json.dumps(pin, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"TEST_PIN_WRITTEN: {args.emit_test_pin}")

    if args.input_dir is None and args.emit_test_pin is None:
        parser.error("provide --input-dir and/or --emit-test-pin")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

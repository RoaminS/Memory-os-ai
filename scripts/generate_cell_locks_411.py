#!/usr/bin/env python3
"""Offline generator/verifier for 4.1.1 per-cell lexical locks, artifact manifests, and L3 test pins.

Reads pre-resolved ``uv pip compile --generate-hashes`` files (binary wheels only, zero network)
and writes flat packaged resources under ``v4/src/memory_os_v4/resources/`` (required because
``pyproject.toml`` package-data ``resources/*.json`` / ``resources/*.txt`` is non-recursive).

At step 3 of the 4.1.1 contract, re-run this script against the re-frozen lock files (with
``--lock-state frozen`` and real wheel metadata when producing the release bootstrap pin).
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
)

RETAINED_CELLS = (
    "macos-arm64-py311",
    "macos-arm64-py312",
    "macos-arm64-py313",
    "ubuntu2404-arm64-py311",
)
# Mapping from preliminary lock filenames (~/memory-os-local/tools/411/locks-preliminaires/) to cells.
DEFAULT_INPUT_FILES = {
    "macos-arm64-py311": "cellule_1-controle.txt",
    "macos-arm64-py312": "cellule_2a.txt",
    "macos-arm64-py313": "cellule_2b.txt",
    "ubuntu2404-arm64-py311": "cellule_3.txt",
}
CELL_LABELS = {
    "macos-arm64-py311": "macOS arm64 CPython 3.11",
    "macos-arm64-py312": "macOS arm64 CPython 3.12",
    "macos-arm64-py313": "macOS arm64 CPython 3.13",
    "ubuntu2404-arm64-py311": "Ubuntu 24.04 arm64 CPython 3.11",
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
}
REQUIRED_TIKTOKEN_VERSION = "0.12.0"
_PIN_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9][A-Za-z0-9.!+_-]*)$")
_HASH_RE = re.compile(r"^--hash=sha256:([0-9a-f]{64})$")


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


def build_cell_lock_files(
    cell_id: str,
    compiled_text: str,
    *,
    reference_catalog: dict,
    ml_packages: set[str],
    recorded_at: str = "2026-11-23",
    lock_state: str = "preliminary",
    existing_constraints_bytes: bytes | None = None,
    artifact_urls: dict[str, str] | None = None,
) -> tuple[dict, str, dict]:
    """Produce ``(catalog_dict, constraints_text, manifest_dict)`` for one retained lexical cell.

    ``artifact_urls`` maps canonical package names to their verified ``https://`` wheel URLs
    (contract §6). When ``lock_state == 'preliminary'`` and ``artifact_urls`` is omitted, each
    artifact carries ``"url": None`` and the manifest carries ``"urls_status": "to_fill_at_step_3"``.
    When ``lock_state == 'frozen'``, ``artifact_urls`` is mandatory for every artifact.
    """
    if cell_id not in RETAINED_CELLS:
        raise ValueError(f"not a retained cell: {cell_id}")
    if lock_state not in ("preliminary", "frozen"):
        raise ValueError(f"invalid lock_state: {lock_state!r}")
    if lock_state == "frozen" and artifact_urls is None:
        raise ValueError(f"{cell_id}: frozen lock manifests require artifact_urls (step 3 PyPI metadata)")
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

    canon_urls: dict[str, str] | None = None
    if artifact_urls is not None:
        canon_urls = {}
        for raw_name, url in artifact_urls.items():
            key = canonical_name(raw_name)
            expected_ver = by_name.get(key)
            if not _valid_https_url(url, name=key, version=expected_ver):
                raise ValueError(f"{cell_id}: invalid wheel artifact URL for {raw_name}: {url!r}")
            canon_urls[key] = url
        missing_urls = sorted(set(by_name) - set(canon_urls))
        if missing_urls:
            raise ValueError(f"{cell_id}: missing artifact URLs for: {', '.join(missing_urls)}")

    manifest_artifacts = []
    for item in artifacts:
        url = None if canon_urls is None else canon_urls[item["name"]]
        manifest_artifacts.append({
            "name": item["name"],
            "sha256": item["sha256"],
            "url": url,
            "version": item["version"],
        })
    urls_status = "complete" if canon_urls is not None else "to_fill_at_step_3"

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
            f"Lock state: {lock_state} (re-frozen at step 3 before build). "
            "No encoder, no vector index: the ML closure is neither required nor inspected."
        ),
        "lock_state": lock_state,
        "profile_id": profile_id,
        "recorded_at": recorded_at,
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

    source_note = (
        f"uv pip compile --generate-hashes ({lock_state}, binary wheels only; "
        "artifact URLs filled from PyPI metadata)"
        if urls_status == "complete"
        else f"uv pip compile --generate-hashes ({lock_state}, binary wheels only; "
        "artifact URLs to fill at step 3 from PyPI metadata under the locks GO)"
    )
    manifest_doc = {
        "artifacts": manifest_artifacts,
        "cell_id": cell_id,
        "constraints_sha256": constraints_sha,
        "lock_state": lock_state,
        "profile": "lexical",
        "profile_id": profile_id,
        "schema_version": 1,
        "source": source_note,
        "urls_status": urls_status,
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
    artifacts_metadata: dict[str, dict] | None = None,
    test_values: bool = False,
) -> dict:
    """Produce one ``cells.<cell_id>.<profile>`` block for ``scripts/memory_os_bootstrap.py``.

    When ``test_values=True`` (L5 offline testing before step-3 wheel downloads), synthetic
    ``example.invalid`` URLs, wheel filenames and positive test sizes are attached to the real
    pinned SHA-256 digests from ``manifest_path``. When ``artifacts_metadata`` is supplied (step 3),
    each entry provides ``{"filename", "url", "size", "sha256"}`` and its ``sha256`` must appear
    in the admitted digests of ``manifest_path``.
    """
    if cell_id not in RETAINED_CELLS:
        raise ValueError(f"not a retained cell: {cell_id}")
    if not test_values and artifacts_metadata is None:
        raise ValueError("either artifacts_metadata (step 3) or test_values=True (tests only) is required")
    if test_values and artifacts_metadata is not None:
        raise ValueError("pass either artifacts_metadata or test_values=True, not both")

    constraints_bytes = constraints_path.read_bytes()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1 or manifest.get("cell_id") != cell_id or manifest.get("profile") != profile:
        raise ValueError(f"manifest {manifest_path} does not match {cell_id}/{profile}")
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
        admitted = set(item["sha256"])
        if test_values:
            dist_norm = name.replace("-", "_")
            filename = f"{dist_norm}-{version}-py3-none-any.whl"
            url = f"https://example.invalid/test-only/{cell_id}/{filename}"
            digest = item["sha256"][0]
            size = 1024
        else:
            meta = artifacts_metadata.get(name)
            if not isinstance(meta, dict):
                raise ValueError(f"{cell_id}: missing wheel metadata for {name}")
            digest = meta.get("sha256")
            if digest not in admitted:
                raise ValueError(f"{cell_id}: wheel sha256 for {name} is not in manifest admitted digests")
            filename = meta["filename"]
            url = meta["url"]
            if item.get("url") is not None and url != item["url"]:
                raise ValueError(f"{cell_id}: wheel url for {name} disagrees with manifest url")
            size = meta["size"]
        artifacts.append({
            "name": name,
            "version": version,
            "filename": filename,
            "url": url,
            "sha256": digest,
            "size": size,
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
    """Compute SHA-256 digests of ``(catalog, constraints, manifest | None)`` for all 5 locks."""
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
    """Validate ALL 5 lock triplets (historical files included) using the runtime's own attestation."""
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
    input_files: dict[str, Path],
    output_dir: Path,
    *,
    recorded_at: str = "2026-11-23",
    lock_state: str = "preliminary",
    check_only: bool = False,
    artifact_urls_by_cell: dict[str, dict[str, str]] | None = None,
    qualified_runtime_path: Path | None = None,
    constraints_script_path: Path | None = None,
) -> dict[str, list[str]]:
    """Generate (or verify with ``check_only=True``) flat per-cell lexical lock resources and anchored digests.

    Validates all 5 lock triplets (including historical semantic and Cell-1 lexical files) with
    ``qualified_runtime`` before writing anything and again during ``--check``.
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
            raise ValueError(f"missing input compiled lock for {cell_id}")
        compiled_text = input_files[cell_id].read_text(encoding="utf-8")
        cat_name, con_name, man_name = CELL_RESOURCE_NAMES[cell_id]
        cat_path, con_path, man_path = base_dir / cat_name, base_dir / con_name, base_dir / man_name

        # Cell 1 preserves the historical catalog and constraints files byte-for-byte.
        preserve_historical = cell_id == "macos-arm64-py311"
        existing_con = con_path.read_bytes() if preserve_historical else None
        cell_urls = None if artifact_urls_by_cell is None else artifact_urls_by_cell.get(cell_id)
        catalog_doc, constraints_text, manifest_doc = build_cell_lock_files(
            cell_id,
            compiled_text,
            reference_catalog=reference_catalog,
            ml_packages=ml_packages,
            recorded_at=recorded_at,
            lock_state=lock_state,
            existing_constraints_bytes=existing_con,
            artifact_urls=cell_urls,
        )
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

    # Validate all 4 lexical triplets (including historical Cell-1 catalog/constraints) BEFORE any write.
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
        # Final post-write attestation on disk across all 5 triplets with the anchored digests.
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

    # --check mode: verify generated payloads, anchored digests in both modules, AND all 5 triplets via runtime.
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


def _load_urls_json(path: Path) -> dict[str, dict[str, str]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"invalid urls JSON in {path}: expected object")
    if set(raw) & set(RETAINED_CELLS):
        return {cell_id: dict(mapping) for cell_id, mapping in raw.items()}
    return {cell_id: dict(raw) for cell_id in RETAINED_CELLS}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-dir", type=Path,
        help="Directory holding cellule_1-controle.txt, cellule_2a.txt, cellule_2b.txt, cellule_3.txt",
    )
    parser.add_argument(
        "--cell", action="append", default=[], metavar="CELL=PATH",
        help="Explicit cell_id=compiled_lock_path mapping (overrides --input-dir)",
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
    parser.add_argument(
        "--urls-json", type=Path, default=None,
        help="Optional JSON mapping {cell_id: {package: https_url}} or {package: https_url} (required for --lock-state frozen)",
    )
    parser.add_argument("--recorded-at", default="2026-11-23")
    parser.add_argument("--lock-state", choices=("preliminary", "frozen"), default="preliminary")
    parser.add_argument("--check", action="store_true", help="Verify existing files, anchored digests and runtime invariants without writing")
    parser.add_argument(
        "--emit-test-pin", type=Path, default=None,
        help="Write a state='test' bootstrap pin JSON for the four cells (never 'release')",
    )
    args = parser.parse_args(argv)

    input_files: dict[str, Path] = {}
    if args.input_dir is not None:
        for cell_id, filename in DEFAULT_INPUT_FILES.items():
            input_files[cell_id] = args.input_dir / filename
    for spec in args.cell:
        if "=" not in spec:
            parser.error(f"invalid --cell argument (expected CELL=PATH): {spec}")
        cell_id, raw_path = spec.split("=", 1)
        input_files[cell_id] = Path(raw_path)

    artifact_urls_by_cell = _load_urls_json(args.urls_json) if args.urls_json is not None else None

    if input_files:
        result = generate_all(
            input_files,
            args.output_dir,
            recorded_at=args.recorded_at,
            lock_state=args.lock_state,
            check_only=args.check,
            artifact_urls_by_cell=artifact_urls_by_cell,
            qualified_runtime_path=args.qualified_runtime,
            constraints_script_path=args.constraints_script,
        )
        if args.check:
            if result["mismatches"]:
                print("MISMATCH: " + ", ".join(result["mismatches"]), file=sys.stderr)
                return 1
            print("OK: all 5 cell locks, manifests, anchored digests and runtime invariants match")
        else:
            print("WRITTEN: " + ", ".join(result["written"]))

    if args.emit_test_pin is not None:
        pin = build_bootstrap_test_pin(args.output_dir)
        args.emit_test_pin.write_text(json.dumps(pin, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"TEST_PIN_WRITTEN: {args.emit_test_pin}")

    if not input_files and args.emit_test_pin is None:
        parser.error("provide --input-dir/--cell and/or --emit-test-pin")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

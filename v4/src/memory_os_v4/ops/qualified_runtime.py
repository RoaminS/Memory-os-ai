"""Offline installed/deployed version attestation; never imports torch or a model.

One exact catalog, constraints file and (for lexical cells) artifact manifest per retained
platform cell (4.1.1, contract §6, lot L5). ``semantic`` stays limited to ``macos-arm64-py311``
and remains the default profile of every function so existing callers are unchanged.

Package metadata carry compatible bounds (``check_requirements``); the exact ``==`` locks live in
the per-cell constraints files (``check_constraints``, refusing every marker) and in the installed-
version comparison (``compare`` / ``inspect_current``). The separate P4 control
``check_no_extra_packages`` / ``inspect_no_extra_packages`` enforces that no extra distribution
outside the attested lock is installed.
"""
from __future__ import annotations

import functools
import hashlib
import hmac
from importlib import metadata
import json
import os
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit

REFERENCE_CELL = 'macos-arm64-py311'
REQUIRED_TIKTOKEN_VERSION = '0.12.0'
ALLOWED_ARTIFACT_HOSTS = frozenset({'files.pythonhosted.org'})
_ML_FLOOR_CANON = frozenset({'torch', 'sentence-transformers', 'transformers', 'faiss-cpu'})
_HEX64 = re.compile(r'^[0-9a-f]{64}$')
_CANON = re.compile(r'[-_.]+')

PROFILE = Path(__file__).resolve().parents[1] / 'resources/qualified_runtime_v1.json'
_RES = PROFILE.parent
CATALOGS = {'semantic': PROFILE, 'lexical': _RES / 'qualified_runtime_lexical_v1.json'}
CONSTRAINTS = {'semantic': _RES / 'qualified_runtime_constraints_v1.txt',
               'lexical': _RES / 'qualified_runtime_constraints_lexical_v1.txt'}

# Embedded flat locks per (profile, platform cell): (catalog, constraints, manifest | None).
# Non-recursive package-data in pyproject.toml (resources/*.json, resources/*.txt) requires flat
# filenames directly under resources/. Semantic stays unique on macos-arm64-py311.
CELL_LOCKS: dict[tuple[str, str], tuple[Path, Path, Path | None]] = {
    ('semantic', 'macos-arm64-py311'): (
        _RES / 'qualified_runtime_v1.json',
        _RES / 'qualified_runtime_constraints_v1.txt',
        None,
    ),
    ('lexical', 'macos-arm64-py311'): (
        _RES / 'qualified_runtime_lexical_v1.json',
        _RES / 'qualified_runtime_constraints_lexical_v1.txt',
        _RES / 'qualified_runtime_manifest_lexical_macos-arm64-py311_v1.json',
    ),
    ('lexical', 'macos-arm64-py312'): (
        _RES / 'qualified_runtime_lexical_macos-arm64-py312_v1.json',
        _RES / 'qualified_runtime_constraints_lexical_macos-arm64-py312_v1.txt',
        _RES / 'qualified_runtime_manifest_lexical_macos-arm64-py312_v1.json',
    ),
    ('lexical', 'macos-arm64-py313'): (
        _RES / 'qualified_runtime_lexical_macos-arm64-py313_v1.json',
        _RES / 'qualified_runtime_constraints_lexical_macos-arm64-py313_v1.txt',
        _RES / 'qualified_runtime_manifest_lexical_macos-arm64-py313_v1.json',
    ),
    ('lexical', 'ubuntu2404-arm64-py311'): (
        _RES / 'qualified_runtime_lexical_ubuntu2404-arm64-py311_v1.json',
        _RES / 'qualified_runtime_constraints_lexical_ubuntu2404-arm64-py311_v1.txt',
        _RES / 'qualified_runtime_manifest_lexical_ubuntu2404-arm64-py311_v1.json',
    ),
}


# Exact expected profile_id per (profile, platform cell); matched by strict equality, never substring.
EXPECTED_PROFILE_IDS: dict[tuple[str, str], str] = {
    ('semantic', 'macos-arm64-py311'): 'granite-macos-arm64-py311-v1',
    ('lexical', 'macos-arm64-py311'): 'lexical-macos-arm64-py311-v1',
    ('lexical', 'macos-arm64-py312'): 'lexical-macos-arm64-py312-v1',
    ('lexical', 'macos-arm64-py313'): 'lexical-macos-arm64-py313-v1',
    ('lexical', 'ubuntu2404-arm64-py311'): 'lexical-ubuntu2404-arm64-py311-v1',
}

# Anchored SHA-256 digests of (catalog, constraints, manifest | None) per (profile, platform cell).
# Generated and verified by scripts/generate_cell_locks_411.py (--check).
# BEGIN GENERATED CELL_LOCK_DIGESTS
CELL_LOCK_DIGESTS: dict[tuple[str, str], tuple[str, str, str | None]] = {
    ('semantic', 'macos-arm64-py311'): (
        '20a9f65087a81b1d2af839b0baa01d4b0194029cfe4c5512947b1e211cc52444',
        'eeffd1c60060a2c4a2279f48e07818095914f6de34f5638c4bbeaa524055220d',
        None,
    ),
    ('lexical', 'macos-arm64-py311'): (
        '4d3f236b06c825655bb0da90c5624d91bb48e065bca352df69f8a24abef77925',
        '17967671d2f143083c56d241fb6ad1c0ce5cb8a8ecd5b92ad706257e21860321',
        'e8a8d795174db157110138791c4558ece00d27ca066f9320abcda10bbffe59d7',
    ),
    ('lexical', 'macos-arm64-py312'): (
        'ec4625ccbe897985cef57ac490b0ffa1e365d1f69608a40cef5959c434589632',
        '769ba2f9a4020549a976a06406e2a387772c50d33531de966a27110eea8db5cc',
        'ae0a3a9e5b7a2faeba0478320586a0e6c337ea0ea7ae16ba1a1fbbe06d775e17',
    ),
    ('lexical', 'macos-arm64-py313'): (
        '062eeb2d84d9f90833a186e243820189e82a5f2b227590b0c3a9e3f41aa333d7',
        'f7068c427ab1de814fa891afa8875e092d82a842e6532b28f532ee257fd9d8eb',
        'a0d6e20f8a3d9895114ec72e4da2976940349af74eaf92d56c2bd59a43d39f6a',
    ),
    ('lexical', 'ubuntu2404-arm64-py311'): (
        'b9670a31c57aa7849096c12d4fe311e3b3c1f67908d8ee11d190918bc3cb407b',
        'ce22752cb9ee6ac527c12aa87264db8f0b8c2437ebcae9983f7e20f21efe194d',
        '637c902934a9bb83c297740b34694a2f7716a4ba79afd739d4226265b9a01e3a',
    ),
}
# END GENERATED CELL_LOCK_DIGESTS


def _canon(name: str) -> str:
    return _CANON.sub('-', name).lower()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_plain_file(path: Path) -> bytes | None:
    """Read a regular single-link file with zero symbolic links on the path or its parents."""
    if not isinstance(path, Path) or not path.is_absolute() or '..' in path.parts:
        return None
    for part in (path, *path.parents):
        try:
            if part.is_symlink():
                return None
        except OSError:
            return None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        fh = os.fdopen(fd, 'rb')
    except BaseException:
        os.close(fd)
        raise
    try:
        with fh:
            info = os.fstat(fh.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                return None
            return fh.read()
    except OSError:
        return None


def _detect_cell_id() -> str | None:
    from . import cell
    return cell.detect().get('cell_id')


def _resolve_cell_id(profile: str, cell_id: str | None) -> str:
    if profile not in CATALOGS:
        raise ValueError('Unknown retrieval profile for the qualified runtime: ' + str(profile))
    if cell_id is not None:
        return cell_id
    detected = _detect_cell_id()
    if detected is None:
        raise ValueError(f'No retained platform cell detected for profile {profile}')
    return detected


def _strict_json_loads(text: str):
    """JSON parser rejecting duplicate keys at every object level."""
    def no_dups(pairs):
        keys = [k for k, _ in pairs]
        if len(keys) != len(set(keys)):
            raise ValueError('duplicate JSON key')
        return dict(pairs)
    return json.loads(text, object_pairs_hook=no_dups)


def _reference_ml_closure_canon(resources_dir: Path | None = None) -> set[str]:
    """Canonical names of packages present in the semantic reference catalog but absent from lexical."""
    if resources_dir is None:
        sem_path, lex_path = CATALOGS['semantic'], CATALOGS['lexical']
    else:
        sem_path = resources_dir / CATALOGS['semantic'].name
        lex_path = resources_dir / CATALOGS['lexical'].name
    sem_raw = _read_plain_file(sem_path)
    lex_raw = _read_plain_file(lex_path)
    if sem_raw is None or lex_raw is None:
        return set(_ML_FLOOR_CANON)
    try:
        sem = _strict_json_loads(sem_raw.decode('utf-8'))
        lex = _strict_json_loads(lex_raw.decode('utf-8'))
        sem_names = {_canon(k) for k in sem.get('versions', {})}
        for group in sem.get('extra_versions', {}).values():
            sem_names |= {_canon(k) for k in group}
        lex_names = {_canon(k) for k in lex.get('versions', {})}
        for group in lex.get('extra_versions', {}).values():
            lex_names |= {_canon(k) for k in group}
    except (UnicodeDecodeError, ValueError, TypeError, AttributeError):
        return set(_ML_FLOOR_CANON)
    return (sem_names - lex_names) | _ML_FLOOR_CANON


def _extract_catalog_versions(record: dict) -> dict[str, str] | None:
    """Canonical {name: version} across base versions and all extra_versions; None if malformed."""
    if not isinstance(record, dict) or record.get('schema_version') != 1:
        return None
    versions = record.get('versions')
    if not isinstance(versions, dict) or not versions:
        return None
    merged: dict[str, str] = {}
    for name, version in versions.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(version, str) or not version.strip():
            return None
        key = _canon(name)
        if key in merged:
            return None
        merged[key] = version
    extras = record.get('extra_versions', {})
    if not isinstance(extras, dict):
        return None
    for group in extras.values():
        if not isinstance(group, dict):
            return None
        for name, version in group.items():
            if not isinstance(name, str) or not name.strip() or not isinstance(version, str) or not version.strip():
                return None
            key = _canon(name)
            if key in merged and merged[key] != version:
                return None
            merged[key] = version
    return merged


def _constraints_match(lines: list[str], wanted_canon: dict[str, str]) -> dict:
    """Strict ``==version`` validation with zero markers, extras, URLs, duplicates or extra lines."""
    from packaging.requirements import InvalidRequirement, Requirement
    from packaging.utils import canonicalize_name

    found: dict[str, list[str]] = {}
    mismatches: dict[str, dict] = {}
    for text in lines:
        text = text.strip()
        if not text or text.startswith('#'):
            continue
        try:
            item = Requirement(text)
        except InvalidRequirement:
            mismatches[text] = {'expected': None, 'actual': 'unparsable constraint'}
            continue
        # A marker, an extra or a URL makes the line something else than an unconditional exact pin.
        exact = str(item.specifier) if not (item.marker or item.extras or item.url) else text
        found.setdefault(canonicalize_name(item.name), []).append(exact)
    for key, version in wanted_canon.items():
        if found.get(key) != ['==' + version]:
            mismatches[key] = {'expected': '==' + version, 'actual': found.get(key)}
    for key in sorted(set(found) - set(wanted_canon)):
        mismatches[key] = {'expected': None, 'actual': found[key]}
    return {'ok': not mismatches, 'mismatches': mismatches}


_WHEEL_TAG = re.compile(r'[A-Za-z0-9_.]+')  # distribution, python tag, abi tag, platform tag
_WHEEL_BUILD_TAG = re.compile(r'[0-9][A-Za-z0-9_.]*')  # optional; starts with a digit
_WHEEL_VERSION = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.!+]*')  # no dash; first character alphanumeric
_ISO_DATE = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}')
_ISO_UTC_SECOND = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z')

# Frozen artifact manifest (4.1.1 step 3): the only manifest schema the runtime accepts. The
# ``preliminary`` schema 1 (bare SHA-256 lists, ``url: null``) of lot L5 is no longer embedded and
# is refused like every other unknown schema.
MANIFEST_SCHEMA_VERSION = 2
_MANIFEST_KEYS = frozenset({
    'artifacts', 'cell_id', 'constraints_sha256', 'exclude_newer', 'lock_state', 'profile',
    'profile_id', 'pylock_sha256', 'resolved_at', 'schema_version', 'source',
})
_ARTIFACT_KEYS = frozenset({'name', 'version', 'wheels'})
_WHEEL_KEYS = frozenset({'filename', 'sha256', 'size', 'url'})


def valid_artifact_url(value: object, *, name: str | None = None, version: str | None = None) -> bool:
    """Strict wheel artifact URL check (contract §6): https, allowed PyPI host, PEP 427 ``.whl`` path."""
    if not isinstance(value, str) or not value:
        return False
    if any(ord(ch) <= 32 or ord(ch) == 127 for ch in value):
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    if parsed.scheme != 'https':
        return False
    if not parsed.hostname or parsed.hostname.lower() not in ALLOWED_ARTIFACT_HOSTS:
        return False
    if parsed.username is not None or parsed.password is not None or port is not None:
        return False
    if parsed.query or parsed.fragment or '?' in value or '#' in value:
        return False
    path = parsed.path
    if not path or not path.startswith('/') or '//' in path or path.endswith('/'):
        return False
    # No percent-encoding (encoded traversal), no backslash, no ``.``/``..`` path segment. The
    # segment test is the whole traversal rule: a name such as ``/..cache/`` is a plain directory.
    if '%' in path or '\\' in path or any(segment in ('.', '..') for segment in path.split('/')):
        return False
    filename = path.rsplit('/', 1)[-1]
    if not filename.endswith('.whl') or len(filename) <= 4:
        return False
    # PEP 427: {distribution}-{version}(-{build tag})?-{python tag}-{abi tag}-{platform tag}.whl
    parts = filename[:-4].split('-')
    if len(parts) not in (5, 6) or any(not p or '..' in p for p in parts):
        return False
    if not _WHEEL_TAG.fullmatch(parts[0]) or not all(_WHEEL_TAG.fullmatch(tag) for tag in parts[-3:]):
        return False
    if not _WHEEL_VERSION.fullmatch(parts[1]):
        return False
    if len(parts) == 6 and not _WHEEL_BUILD_TAG.fullmatch(parts[2]):
        return False
    if name is not None and _canon(parts[0]) != _canon(name):
        return False
    if version is not None and parts[1] != version:
        return False
    return True


# Explicit, frozen installation target of each retained cell: (system, CPython minor). The admitted
# wheel tags are computed with ``packaging.tags`` (the logic pip and uv share) for THIS target, never
# for the host that happens to run the check:
# - macOS cells: macOS 11.0 on arm64 (``MACOS_TARGET``), i.e. ``macosx_11_0_arm64`` and the
#   ``universal2`` tags up to 11.0; a wheel requiring a later macOS is refused;
# - Ubuntu 24.04 arm64: glibc up to 2.39 (``LINUX_GLIBC_MAX``), i.e. ``manylinux_2_17`` ..
#   ``manylinux_2_39`` on aarch64 plus the ``manylinux2014_aarch64`` alias; no ``linux_aarch64``,
#   no ``musllinux``.
CELL_WHEEL_TARGETS: dict[str, tuple[str, int]] = {
    'macos-arm64-py311': ('macos', 11),
    'macos-arm64-py312': ('macos', 12),
    'macos-arm64-py313': ('macos', 13),
    'ubuntu2404-arm64-py311': ('linux', 11),
}
MACOS_TARGET = ((11, 0), 'arm64')
LINUX_GLIBC_MAX = (2, 39)  # Ubuntu 24.04
_LINUX_GLIBC_MIN_MINOR = 17  # manylinux2014: the first glibc with aarch64 manylinux wheels


@functools.lru_cache(maxsize=None)
def cell_wheel_tags(cell_id: str) -> frozenset:
    """The ``packaging.tags.Tag`` set a wheel may carry to be installable on the retained ``cell_id``."""
    from packaging import tags

    system, minor = CELL_WHEEL_TARGETS[cell_id]
    if system == 'macos':
        platforms = list(tags.mac_platforms(*MACOS_TARGET))
    else:
        glibc_major, glibc_minor = LINUX_GLIBC_MAX
        platforms = [f'manylinux_{glibc_major}_{m}_aarch64'
                     for m in range(glibc_minor, _LINUX_GLIBC_MIN_MINOR - 1, -1)] + ['manylinux2014_aarch64']
    interpreter = f'cp3{minor}'
    return frozenset((
        *tags.cpython_tags(python_version=(3, minor), abis=[interpreter], platforms=platforms),
        *tags.compatible_tags(python_version=(3, minor), interpreter=interpreter, platforms=platforms),
    ))


def wheel_compatible_with_cell(filename: object, cell_id: object) -> bool:
    """True when the wheel ``filename`` can be installed on the retained cell ``cell_id``.

    One rule for the runtime attestation, the lock generator and its ``--check``: the tags of the
    file name (``packaging.utils.parse_wheel_filename``, compound tags expanded) must intersect
    ``cell_wheel_tags(cell_id)``. A name that cannot be parsed, or an unknown cell, is refused.
    """
    from packaging.utils import InvalidWheelFilename, parse_wheel_filename
    from packaging.version import InvalidVersion

    if not isinstance(cell_id, str) or cell_id not in CELL_WHEEL_TARGETS or not isinstance(filename, str):
        return False
    try:
        wheel_tags = parse_wheel_filename(filename)[3]
    except (InvalidWheelFilename, InvalidVersion, ValueError):
        return False
    return not wheel_tags.isdisjoint(cell_wheel_tags(cell_id))


def _manifest_valid(
    manifest: dict,
    *,
    profile: str,
    cell_id: str,
    profile_id: str,
    constraints_sha: str,
    expected_canon: dict[str, str],
) -> bool:
    """Frozen manifest only: every package lists its admitted wheels ``{filename, url, sha256, size}``.

    Refused: an unknown schema or key, a state other than ``frozen``, a package without wheel, a
    null or invalid URL, a ``filename`` that is not the last URL segment (or not the wheel of this
    package and version), a wheel whose tags do not fit the cell (``wheel_compatible_with_cell``),
    a malformed SHA-256 or size, any duplicate (package, filename, URL or
    SHA-256), wheels not sorted by ``filename``, and a package/version set other than the catalog's.
    """
    if not isinstance(manifest, dict) or set(manifest) != _MANIFEST_KEYS:
        return False
    schema = manifest['schema_version']
    if isinstance(schema, bool) or schema != MANIFEST_SCHEMA_VERSION:
        return False
    if (manifest['cell_id'] != cell_id or manifest['profile'] != profile
            or manifest['profile_id'] != profile_id
            or manifest['constraints_sha256'] != constraints_sha):
        return False
    if manifest['lock_state'] != 'frozen':
        return False
    pylock_sha, resolved_at, exclude_newer, source = (
        manifest['pylock_sha256'], manifest['resolved_at'], manifest['exclude_newer'], manifest['source'])
    if not isinstance(pylock_sha, str) or _HEX64.fullmatch(pylock_sha) is None:
        return False
    if not isinstance(resolved_at, str) or _ISO_DATE.fullmatch(resolved_at) is None:
        return False
    if not isinstance(exclude_newer, str) or _ISO_UTC_SECOND.fullmatch(exclude_newer) is None:
        return False
    if not isinstance(source, str) or not source.strip():
        return False
    artifacts = manifest['artifacts']
    if not isinstance(artifacts, list) or not artifacts:
        return False
    seen: dict[str, str] = {}
    seen_files: set[str] = set()
    seen_urls: set[str] = set()
    seen_hashes: set[str] = set()
    for item in artifacts:
        if not isinstance(item, dict) or set(item) != _ARTIFACT_KEYS:
            return False
        name, version, wheels = item['name'], item['version'], item['wheels']
        if not isinstance(name, str) or not name.strip() or not isinstance(version, str) or not version.strip():
            return False
        key = _canon(name)
        if key in seen:
            return False
        seen[key] = version
        if not isinstance(wheels, list) or not wheels:
            return False
        previous = None
        for wheel in wheels:
            if not isinstance(wheel, dict) or set(wheel) != _WHEEL_KEYS:
                return False
            filename, url, digest, size = wheel['filename'], wheel['url'], wheel['sha256'], wheel['size']
            if not valid_artifact_url(url, name=name, version=version):
                return False
            if not isinstance(filename, str) or filename != url.rsplit('/', 1)[-1]:
                return False
            if not wheel_compatible_with_cell(filename, cell_id):
                return False
            if not isinstance(digest, str) or _HEX64.fullmatch(digest) is None:
                return False
            if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
                return False
            if previous is not None and filename <= previous:
                return False
            previous = filename
            if filename in seen_files or url in seen_urls or digest in seen_hashes:
                return False
            seen_files.add(filename)
            seen_urls.add(url)
            seen_hashes.add(digest)
    return seen == expected_canon


def _attest_triplet_bytes(
    cell_id: str,
    *,
    profile: str,
    catalog_bytes: bytes,
    constraints_bytes: bytes,
    manifest_bytes: bytes | None,
    catalog_label: str,
    constraints_label: str,
    manifest_label: str | None = None,
    expected_digests: tuple[str, str, str | None] | None = None,
    require_digests: bool = True,
    ml_closure_canon: set[str] | None = None,
) -> tuple[dict, dict] | None:
    """Single source of truth for lock attestation (runtime AND generator ``--check``)."""
    expected_profile_id = EXPECTED_PROFILE_IDS.get((profile, cell_id))
    if not isinstance(expected_profile_id, str) or not expected_profile_id:
        return None
    if profile == 'lexical' and manifest_bytes is None:
        return None
    catalog_sha = _sha256_bytes(catalog_bytes)
    constraints_sha = _sha256_bytes(constraints_bytes)
    manifest_sha = _sha256_bytes(manifest_bytes) if manifest_bytes is not None else None
    if require_digests:
        if not isinstance(expected_digests, tuple) or len(expected_digests) != 3:
            return None
        exp_cat_sha, exp_con_sha, exp_man_sha = expected_digests
        if profile == 'lexical' and exp_man_sha is None:
            return None
        if (manifest_bytes is None) != (exp_man_sha is None):
            return None
        if not isinstance(exp_cat_sha, str) or _HEX64.fullmatch(exp_cat_sha) is None:
            return None
        if not isinstance(exp_con_sha, str) or _HEX64.fullmatch(exp_con_sha) is None:
            return None
        if exp_man_sha is not None and (not isinstance(exp_man_sha, str) or _HEX64.fullmatch(exp_man_sha) is None):
            return None
        if (not hmac.compare_digest(catalog_sha, exp_cat_sha)
                or not hmac.compare_digest(constraints_sha, exp_con_sha)):
            return None
        if exp_man_sha is not None and (manifest_sha is None or not hmac.compare_digest(manifest_sha, exp_man_sha)):
            return None
    try:
        record = _strict_json_loads(catalog_bytes.decode('utf-8'))
        profile_id = record.get('profile_id') if isinstance(record, dict) else None
        if profile_id != expected_profile_id:
            return None
        if 'cell_id' in record and record['cell_id'] != cell_id:
            return None
        expected_canon = _extract_catalog_versions(record)
        if expected_canon is None:
            return None
        if profile == 'lexical':
            if expected_canon.get('tiktoken') != REQUIRED_TIKTOKEN_VERSION:
                return None
            forbidden_ml = _reference_ml_closure_canon() if ml_closure_canon is None else ml_closure_canon
            if set(expected_canon) & forbidden_ml:
                return None
        constraints_text = constraints_bytes.decode('utf-8')
        if not _constraints_match(constraints_text.splitlines(), expected_canon)['ok']:
            return None
        if manifest_bytes is not None:
            manifest_doc = _strict_json_loads(manifest_bytes.decode('utf-8'))
            if not _manifest_valid(
                manifest_doc,
                profile=profile,
                cell_id=cell_id,
                profile_id=expected_profile_id,
                constraints_sha=constraints_sha,
                expected_canon=expected_canon,
            ):
                return None
    except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError):
        return None
    result = {
        'catalog': catalog_label,
        'constraints': constraints_label,
        'cell_id': cell_id,
        'profile': profile,
        'profile_id': profile_id,
        'catalog_sha256': catalog_sha,
        'constraints_sha256': constraints_sha,
    }
    if manifest_label is not None and manifest_sha is not None:
        result['manifest'] = manifest_label
        result['manifest_sha256'] = manifest_sha
    return result, record


def validate_lock_bytes(
    cell_id: str,
    *,
    profile: str,
    catalog_bytes: bytes,
    constraints_bytes: bytes,
    manifest_bytes: bytes | None = None,
    expected_digests: tuple[str, str, str | None] | None = None,
    resources_dir: Path | None = None,
) -> dict | None:
    """Validate candidate or on-disk lock bytes using the exact runtime attestation rules."""
    ml_canon = _reference_ml_closure_canon(resources_dir) if profile == 'lexical' else None
    loaded = _attest_triplet_bytes(
        cell_id,
        profile=profile,
        catalog_bytes=catalog_bytes,
        constraints_bytes=constraints_bytes,
        manifest_bytes=manifest_bytes,
        catalog_label='<memory:catalog>',
        constraints_label='<memory:constraints>',
        manifest_label=None if manifest_bytes is None else '<memory:manifest>',
        expected_digests=expected_digests,
        require_digests=expected_digests is not None,
        ml_closure_canon=ml_canon,
    )
    return None if loaded is None else loaded[0]


def validate_lock_files(
    cell_id: str,
    *,
    profile: str,
    catalog_path: Path,
    constraints_path: Path,
    manifest_path: Path | None = None,
    expected_digests: tuple[str, str, str | None] | None = None,
    resources_dir: Path | None = None,
) -> dict | None:
    """Validate on-disk lock files (plain single-link files, no symlink parents) with runtime rules."""
    if profile == 'lexical' and manifest_path is None:
        return None
    catalog_bytes = _read_plain_file(catalog_path)
    constraints_bytes = _read_plain_file(constraints_path)
    manifest_bytes = _read_plain_file(manifest_path) if manifest_path is not None else None
    if catalog_bytes is None or constraints_bytes is None or (manifest_path is not None and manifest_bytes is None):
        return None
    ml_canon = _reference_ml_closure_canon(resources_dir or catalog_path.parent) if profile == 'lexical' else None
    loaded = _attest_triplet_bytes(
        cell_id,
        profile=profile,
        catalog_bytes=catalog_bytes,
        constraints_bytes=constraints_bytes,
        manifest_bytes=manifest_bytes,
        catalog_label=str(catalog_path),
        constraints_label=str(constraints_path),
        manifest_label=None if manifest_path is None else str(manifest_path),
        expected_digests=expected_digests,
        require_digests=expected_digests is not None,
        ml_closure_canon=ml_canon,
    )
    return None if loaded is None else loaded[0]


def _load_attested_lock(cell_id: str, *, profile: str = 'semantic') -> tuple[dict, dict] | None:
    """Single-read attestation of ``(catalog, constraints, manifest)``; returns ``(lock_info, catalog_doc)``."""
    entry = CELL_LOCKS.get((profile, cell_id))
    expected_digests = CELL_LOCK_DIGESTS.get((profile, cell_id))
    if not isinstance(entry, tuple) or len(entry) != 3:
        return None
    catalog_path, constraints_path, manifest_path = entry
    if profile == 'lexical' and manifest_path is None:
        return None
    catalog_bytes = _read_plain_file(catalog_path)
    constraints_bytes = _read_plain_file(constraints_path)
    manifest_bytes = _read_plain_file(manifest_path) if manifest_path is not None else None
    if catalog_bytes is None or constraints_bytes is None or (manifest_path is not None and manifest_bytes is None):
        return None
    return _attest_triplet_bytes(
        cell_id,
        profile=profile,
        catalog_bytes=catalog_bytes,
        constraints_bytes=constraints_bytes,
        manifest_bytes=manifest_bytes,
        catalog_label=str(catalog_path),
        constraints_label=str(constraints_path),
        manifest_label=None if manifest_path is None else str(manifest_path),
        expected_digests=expected_digests,
        require_digests=True,
    )


def cell_lock(cell_id: str, *, profile: str = 'semantic') -> dict | None:
    """The attested embedded lock of ``cell_id`` for ``profile``, or None. Read-only.

    A lock counts only if EVERY piece is a regular single-link file (no symlink on the path or its
    parents, ``st_nlink == 1``), matches the SHA-256 digests anchored in ``CELL_LOCK_DIGESTS``,
    parses cleanly (no duplicate JSON keys) and agrees end-to-end:
    - catalog JSON matches the exact ``EXPECTED_PROFILE_IDS[(profile, cell_id)]`` (never substring)
      and optional ``cell_id``; for ``lexical``, ``tiktoken==0.12.0`` is enforced and no ML package
      may appear;
    - constraints file contains exact marker-free ``==`` pins matching the catalog 1-to-1;
    - artifact manifest (mandatory for ``lexical``) matches ``cell_id``, ``profile``, ``profile_id``,
      ``constraints_sha256`` and the frozen schema (``schema_version`` 2, ``lock_state`` ``frozen``),
      and covers the exact package/version set, each package listing its admitted wheels
      (``filename``, ``https://files.pythonhosted.org`` ``url``, ``sha256``, ``size``). The
      ``preliminary`` schema of lot L5 is refused.
    The lock of a neighbouring cell is never inherited.
    """
    loaded = _load_attested_lock(cell_id, profile=profile)
    return None if loaded is None else loaded[0]


def constraints_path(*, profile: str = 'semantic', cell_id: str | None = None) -> Path:
    """Path to the attested constraints file for ``(profile, cell_id)`` (detected cell when None)."""
    target = _resolve_cell_id(profile, cell_id)
    lock = cell_lock(target, profile=profile)
    if lock is None:
        raise ValueError(f'No attested qualified runtime lock for profile {profile} on cell {target}')
    return Path(lock['constraints'])


def catalog(*, profile: str = 'semantic', cell_id: str | None = None) -> dict:
    """Attested catalog for ``(profile, cell_id)`` (detected host cell when ``cell_id`` is None)."""
    target = _resolve_cell_id(profile, cell_id)
    loaded = _load_attested_lock(target, profile=profile)
    if loaded is None:
        raise ValueError(f'No attested qualified runtime lock for profile {profile} on cell {target}')
    return loaded[1]


def expected_versions(*, profile: str = 'semantic', extras=(), cell_id: str | None = None) -> dict:
    record = catalog(profile=profile, cell_id=cell_id)
    versions = dict(record['versions'])
    for extra in extras:
        if extra not in record.get('extra_versions', {}):
            raise ValueError('Unknown qualified extra: ' + extra)
        versions.update(record['extra_versions'][extra])
    return versions


def ml_closure(*, extras=()) -> set:
    """Packages locked by the semantic catalog only: required by no lexical install."""
    return set(expected_versions(profile='semantic', extras=extras, cell_id=REFERENCE_CELL)) - set(
        expected_versions(profile='lexical', extras=extras, cell_id=REFERENCE_CELL))


def compare(
    actual: dict,
    *,
    encoding_only: bool = False,
    extras=(),
    profile: str = 'semantic',
    cell_id: str | None = None,
) -> dict:
    """Exact comparison of installed versions with the attested lock of ``(profile, cell_id)``.

    A gap is ``missing`` (package not installed) or ``drift`` (another version). The global
    ``state`` is ``qualified``, ``missing_dependency`` as soon as one package is missing (drifts
    stay listed), else ``drift``. Packages outside the catalog are neither required nor inspected
    here; use ``check_no_extra_packages`` for the P4 no-extra-package gate.
    """
    target = _resolve_cell_id(profile, cell_id)
    record = catalog(profile=profile, cell_id=target)
    expected = expected_versions(profile=profile, extras=extras, cell_id=target)
    if encoding_only:
        expected = {k: expected[k] for k in record['encoding_runtime_keys']}
    mismatches = {key: {'expected': value, 'actual': actual.get(key),
                        'state': 'missing' if actual.get(key) is None else 'drift'}
                  for key, value in expected.items() if actual.get(key) != value}
    if not mismatches:
        state = 'qualified'
    elif any(entry['state'] == 'missing' for entry in mismatches.values()):
        state = 'missing_dependency'
    else:
        state = 'drift'
    return {'ok': not mismatches, 'state': state, 'cell_id': target, 'profile_id': record['profile_id'],
            'expected': expected, 'actual': {key: actual.get(key) for key in expected},
            'mismatches': mismatches, 'attestation': 'package versions only; no numerical equivalence claim'}


def inspect_current(
    *,
    version_getter=None,
    extras=(),
    profile: str = 'semantic',
    cell_id: str | None = None,
) -> dict:
    """Inspect the running interpreter against the attested lock of its cell (or explicit ``cell_id``)."""
    if profile not in CATALOGS:
        raise ValueError('Unknown retrieval profile for the qualified runtime: ' + str(profile))
    target = cell_id if cell_id is not None else _detect_cell_id()
    if target is None or cell_lock(target, profile=profile) is None:
        return {
            'ok': False,
            'state': 'lock_unavailable',
            'cell_id': target,
            'profile_id': None,
            'expected': {},
            'actual': {},
            'mismatches': {'_cell_lock': {'expected': f'{profile}/{target or "unknown"}',
                                          'actual': None, 'state': 'missing'}},
            'attestation': 'cell lock missing or failed integrity attestation',
        }
    get = version_getter or metadata.version
    actual = {}
    for key in expected_versions(profile=profile, extras=extras, cell_id=target):
        try:
            actual[key] = get(key)
        except metadata.PackageNotFoundError:
            actual[key] = None
    return compare(actual, extras=extras, profile=profile, cell_id=target)


def check_no_extra_packages(
    installed: dict[str, str],
    *,
    profile: str = 'semantic',
    extras=(),
    cell_id: str | None = None,
    allowed_extra: tuple[str, ...] = ('memory-os-ai',),
) -> dict:
    """Distinct P4 gate: verify that NO package outside the attested cell lock is installed.

    ``allowed_extra`` defaults to ``('memory-os-ai',)`` (the product distribution itself).
    Does not replace ``compare`` (which inspects missing/drifted catalog packages).
    """
    from packaging.utils import canonicalize_name

    target = _resolve_cell_id(profile, cell_id)
    record = catalog(profile=profile, cell_id=target)
    expected = expected_versions(profile=profile, extras=extras, cell_id=target)
    expected_canon = {canonicalize_name(name) for name in expected}
    allowed_canon = {canonicalize_name(name) for name in allowed_extra}
    permitted = expected_canon | allowed_canon

    seen_canon: dict[str, str] = {}
    duplicates: list[str] = []
    unexpected: dict[str, str] = {}
    for raw_name, version in installed.items():
        key = canonicalize_name(raw_name)
        if key in seen_canon:
            duplicates.append(key)
        seen_canon[key] = version
        if key not in permitted:
            unexpected[key] = version
    ml_names = {canonicalize_name(name) for name in ml_closure(extras=extras)} if profile == 'lexical' else set()
    ml_found = sorted(set(unexpected) & ml_names)
    return {
        'ok': not unexpected and not duplicates,
        'profile': profile,
        'cell_id': target,
        'profile_id': record['profile_id'],
        'allowed_extra': sorted(allowed_canon),
        'unexpected': dict(sorted(unexpected.items())),
        'ml_packages': ml_found,
        'duplicates': sorted(set(duplicates)),
        'installed_count': len(installed),
    }


def inspect_no_extra_packages(
    *,
    distributions=None,
    profile: str = 'semantic',
    extras=(),
    cell_id: str | None = None,
    allowed_extra: tuple[str, ...] = ('memory-os-ai',),
) -> dict:
    """Enumerate installed distributions (default ``importlib.metadata.distributions()``) for P4."""
    from packaging.utils import canonicalize_name

    dists = list(metadata.distributions() if distributions is None else distributions)
    installed: dict[str, str] = {}
    duplicates: list[str] = []
    malformed: list[str] = []
    for dist in dists:
        try:
            raw_name = dist.metadata['Name'] if dist.metadata is not None else None
            version = dist.version
        except Exception:
            malformed.append(repr(dist))
            continue
        if not isinstance(raw_name, str) or not raw_name.strip() or not isinstance(version, str) or not version.strip():
            malformed.append(repr(dist))
            continue
        key = canonicalize_name(raw_name)
        if key in installed:
            duplicates.append(key)
        installed[key] = version
    report = check_no_extra_packages(
        installed, profile=profile, extras=extras, cell_id=cell_id, allowed_extra=allowed_extra,
    )
    all_dups = sorted(set(report['duplicates']) | set(duplicates))
    report['duplicates'] = all_dups
    report['malformed'] = malformed
    report['ok'] = report['ok'] and not all_dups and not malformed
    return report


def check_requirements(
    requirements: list[str],
    *,
    profile: str = 'semantic',
    extras=(),
    cell_id: str | None = None,
) -> dict:
    """Validate package METADATA (bounds) independently of the executing environment.

    Every package of the catalog of ``(profile, cell_id)`` needs an active requirement whose version
    set CONTAINS the qualified version. The ``semantic`` extra is implicitly active for the semantic
    profile; in the lexical profile no package of the ML closure may be an active requirement.
    Exact pins are not checked here: see ``check_constraints``.
    """
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    active_extras = ('', *extras, *(('semantic',) if profile == 'semantic' else ()))
    active = {}
    for text in requirements:
        item = Requirement(text)
        if item.marker is None or any(item.marker.evaluate({'extra': extra}) for extra in active_extras):
            active.setdefault(canonicalize_name(item.name), []).append(item.specifier)
    mismatches = {}
    for name, version in expected_versions(profile=profile, extras=extras, cell_id=cell_id).items():
        specifiers = active.get(canonicalize_name(name))
        if not specifiers:
            mismatches[name] = {'expected': version, 'actual': None, 'reason': 'no active requirement'}
        elif not all(specifier.contains(version, prereleases=True) for specifier in specifiers):
            mismatches[name] = {'expected': version, 'actual': sorted(str(s) for s in specifiers),
                                'reason': 'qualified version outside the declared bounds'}
    if profile == 'lexical':
        for name in sorted(ml_closure(extras=extras)):
            specifiers = active.get(canonicalize_name(name))
            if specifiers is not None:
                mismatches[name] = {'expected': None, 'actual': sorted(str(s) for s in specifiers),
                                    'reason': 'ML package required outside the semantic extra'}
    return {'ok': not mismatches, 'mismatches': mismatches}


def check_constraints(
    lines: list[str],
    *,
    profile: str = 'semantic',
    extras=(),
    cell_id: str | None = None,
) -> dict:
    """Validate a constraints file: the exact lock of one ``(profile, cell_id)``, nothing else.

    Exactly ``==version`` for every package of the catalog of ``(profile, cell_id)`` and no line
    outside that catalog (every environment marker, extra or URL is refused).
    """
    from packaging.utils import canonicalize_name

    wanted_map = {
        canonicalize_name(name): (name, version)
        for name, version in expected_versions(profile=profile, extras=extras, cell_id=cell_id).items()
    }
    raw = _constraints_match(lines, {key: version for key, (_, version) in wanted_map.items()})
    # Preserve original catalog key spelling in mismatches for callers.
    mismatches = {
        wanted_map[key][0] if key in wanted_map else key: detail
        for key, detail in raw['mismatches'].items()
    }
    return {'ok': not mismatches, 'mismatches': mismatches}

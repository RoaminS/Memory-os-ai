"""Offline installed/deployed version attestation; never imports torch or a model.

One exact catalog per retrieval profile (docs/LEXICAL_PROFILE_4_1_0.md §8-§9). ``semantic`` is
the historical catalog and the default of every function, so existing callers are unchanged.
Package metadata carry compatible bounds (``check_requirements``); the exact ``==`` locks live in
the per-profile constraints files (``check_constraints``) and in the installed-version comparison.
"""
from __future__ import annotations

from importlib import metadata
import json
from pathlib import Path

PROFILE = Path(__file__).resolve().parents[1] / 'resources/qualified_runtime_v1.json'
CATALOGS = {'semantic': PROFILE, 'lexical': PROFILE.parent / 'qualified_runtime_lexical_v1.json'}
CONSTRAINTS = {'semantic': PROFILE.parent / 'qualified_runtime_constraints_v1.txt',
               'lexical': PROFILE.parent / 'qualified_runtime_constraints_lexical_v1.txt'}
# Embedded locks per (profile, platform cell): catalog and constraints. A retained cell without an
# entry here, or whose files are absent, is refused (ops/cell.py); the other lexical locks come with L5.
CELL_LOCKS = {(profile, 'macos-arm64-py311'): (CATALOGS[profile], CONSTRAINTS[profile])
              for profile in ('semantic', 'lexical')}


def cell_lock(cell_id: str, *, profile: str = 'semantic') -> dict | None:
    """The embedded lock of ``cell_id`` for ``profile``, or None. Read-only.

    A lock counts only if both files exist and the catalog is valid and names this very cell in
    its ``profile_id``: the lock of a neighbouring cell is never inherited.
    """
    # TODO(L5): this only proves that the constraints file EXISTS, not that it agrees with the
    # catalog (check_constraints), and inspect_current()/catalog() still read the global catalog of
    # the profile. Adding CELL_LOCKS entries is not enough: L5 must select the lock of the detected
    # cell and attest that very lock (catalog + constraints + installed versions) end to end.
    paths = CELL_LOCKS.get((profile, cell_id))
    if paths is None or not all(path.is_file() for path in paths):
        return None
    try:
        record = json.loads(paths[0].read_text())
        profile_id = record['profile_id']
        valid = record['schema_version'] == 1 and bool(record['versions']) and f'-{cell_id}-' in profile_id
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not valid:
        return None
    return {'catalog': str(paths[0]), 'constraints': str(paths[1]), 'profile_id': profile_id}


def catalog(*, profile: str = 'semantic') -> dict:
    if profile not in CATALOGS:
        raise ValueError('Unknown retrieval profile for the qualified runtime: ' + str(profile))
    record = json.loads(CATALOGS[profile].read_text())
    if record['schema_version'] != 1 or not record['versions']:
        raise ValueError('Invalid qualified runtime catalog')
    return record


def expected_versions(*, profile: str = 'semantic', extras=()) -> dict:
    record = catalog(profile=profile)
    versions = dict(record['versions'])
    for extra in extras:
        if extra not in record.get('extra_versions', {}):
            raise ValueError('Unknown qualified extra: ' + extra)
        versions.update(record['extra_versions'][extra])
    return versions


def ml_closure(*, extras=()) -> set:
    """Packages locked by the semantic catalog only: required by no lexical install."""
    return set(expected_versions(profile='semantic', extras=extras)) - set(
        expected_versions(profile='lexical', extras=extras))


def compare(actual: dict, *, encoding_only: bool = False, extras=(), profile: str = 'semantic') -> dict:
    """Exact comparison of installed versions with the lock of ``profile``.

    A gap is ``missing`` (package not installed) or ``drift`` (another version). The global
    ``state`` is ``qualified``, ``missing_dependency`` as soon as one package is missing (drifts
    stay listed), else ``drift``. Packages outside the catalog are neither required nor inspected.
    """
    record = catalog(profile=profile)
    expected = expected_versions(profile=profile, extras=extras)
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
    return {'ok': not mismatches, 'state': state, 'profile_id': record['profile_id'],
            'expected': expected, 'actual': {key: actual.get(key) for key in expected},
            'mismatches': mismatches, 'attestation': 'package versions only; no numerical equivalence claim'}


def inspect_current(*, version_getter=None, extras=(), profile: str = 'semantic') -> dict:
    get = version_getter or metadata.version
    actual = {}
    for key in expected_versions(profile=profile, extras=extras):
        try:
            actual[key] = get(key)
        except metadata.PackageNotFoundError:
            actual[key] = None
    return compare(actual, extras=extras, profile=profile)


def check_requirements(requirements: list[str], *, profile: str = 'semantic', extras=()) -> dict:
    """Validate package METADATA (bounds) independently of the executing environment.

    Every package of the catalog of ``profile`` needs an active requirement whose version set
    CONTAINS the qualified version. The ``semantic`` extra is implicitly active for the semantic
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
    for name, version in expected_versions(profile=profile, extras=extras).items():
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


def check_constraints(lines: list[str], *, profile: str = 'semantic', extras=()) -> dict:
    """Validate a constraints file: the exact lock of one profile, nothing else.

    Exactly ``==version`` for every package of the catalog of ``profile`` and no line outside
    that catalog: the semantic constraints do not validate the lexical profile, nor conversely.
    """
    from packaging.requirements import InvalidRequirement, Requirement
    from packaging.utils import canonicalize_name
    found, mismatches = {}, {}
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
    wanted = {canonicalize_name(name): (name, version)
              for name, version in expected_versions(profile=profile, extras=extras).items()}
    for key, (name, version) in wanted.items():
        if sorted(set(found.get(key, []))) != ['==' + version]:
            mismatches[name] = {'expected': '==' + version, 'actual': found.get(key)}
    for key in sorted(set(found) - set(wanted)):
        mismatches[key] = {'expected': None, 'actual': found[key]}
    return {'ok': not mismatches, 'mismatches': mismatches}

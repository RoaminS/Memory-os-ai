"""Offline installed/deployed version attestation; never imports torch or a model."""
from __future__ import annotations

from importlib import metadata
import json
from pathlib import Path

PROFILE = Path(__file__).resolve().parents[1] / 'resources/qualified_runtime_v1.json'


def catalog() -> dict:
    record = json.loads(PROFILE.read_text())
    if record['schema_version'] != 1 or not record['versions']:
        raise ValueError('Invalid qualified runtime catalog')
    return record


def expected_versions(*, extras=()) -> dict:
    record = catalog()
    versions = dict(record['versions'])
    for extra in extras:
        if extra not in record.get('extra_versions', {}):
            raise ValueError('Unknown qualified extra: ' + extra)
        versions.update(record['extra_versions'][extra])
    return versions


def compare(actual: dict, *, encoding_only: bool = False, extras=()) -> dict:
    record = catalog()
    expected = expected_versions(extras=extras)
    if encoding_only:
        expected = {k: expected[k] for k in record['encoding_runtime_keys']}
    mismatches = {key: {'expected': value, 'actual': actual.get(key)}
                  for key, value in expected.items() if actual.get(key) != value}
    return {'ok': not mismatches, 'profile_id': record['profile_id'],
            'expected': expected, 'actual': {key: actual.get(key) for key in expected},
            'mismatches': mismatches, 'attestation': 'package versions only; no numerical equivalence claim'}


def inspect_current(*, version_getter=None, extras=()) -> dict:
    get = version_getter or metadata.version
    actual = {}
    for key in expected_versions(extras=extras):
        try:
            actual[key] = get(key)
        except metadata.PackageNotFoundError:
            actual[key] = None
    return compare(actual, extras=extras)


def check_requirements(requirements: list[str], *, extras=()) -> dict:
    """Validate core wheel/project pins independently of the executing environment."""
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
    core = {}
    for text in requirements:
        item = Requirement(text)
        if item.marker is None or any(item.marker.evaluate({'extra': extra}) for extra in ('', *extras)):
            core.setdefault(canonicalize_name(item.name), []).append(str(item.specifier))
    wanted = expected_versions(extras=extras)
    mismatches = {name: {'expected': '==' + version, 'actual': core.get(canonicalize_name(name))}
                  for name, version in wanted.items()
                  if sorted(set(core.get(canonicalize_name(name), []))) != ['==' + version]}
    return {'ok': not mismatches, 'mismatches': mismatches}

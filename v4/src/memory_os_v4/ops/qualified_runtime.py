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


def compare(actual: dict, *, encoding_only: bool = False) -> dict:
    record = catalog()
    expected = record['versions']
    if encoding_only:
        expected = {k: expected[k] for k in record['encoding_runtime_keys']}
    mismatches = {key: {'expected': value, 'actual': actual.get(key)}
                  for key, value in expected.items() if actual.get(key) != value}
    return {'ok': not mismatches, 'profile_id': record['profile_id'],
            'expected': expected, 'actual': {key: actual.get(key) for key in expected},
            'mismatches': mismatches, 'attestation': 'package versions only; no numerical equivalence claim'}


def inspect_current(*, version_getter=None) -> dict:
    get = version_getter or metadata.version
    actual = {}
    for key in catalog()['versions']:
        try:
            actual[key] = get(key)
        except metadata.PackageNotFoundError:
            actual[key] = None
    return compare(actual)


def check_requirements(requirements: list[str]) -> dict:
    """Validate core wheel/project pins independently of the executing environment."""
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
    core = {}
    for text in requirements:
        item = Requirement(text)
        if item.marker is None:
            core.setdefault(canonicalize_name(item.name), []).append(str(item.specifier))
    wanted = catalog()['versions']
    mismatches = {name: {'expected': '==' + version, 'actual': core.get(canonicalize_name(name))}
                  for name, version in wanted.items()
                  if core.get(canonicalize_name(name)) != ['==' + version]}
    return {'ok': not mismatches, 'mismatches': mismatches}

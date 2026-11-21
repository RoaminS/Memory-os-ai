#!/usr/bin/env python3
"""Offline gate for a pip --dry-run --ignore-installed --report resolution.

No network, installs, imports of the model or writes outside the requested
proof path. A resolution gate is not an installed-runtime or security proof.
"""
from __future__ import annotations

import argparse
from email.parser import Parser
import hashlib
import json
from pathlib import Path
import sys
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'v4/src'))
from memory_os_v4.ops.qualified_runtime import check_requirements, expected_versions, ml_closure  # noqa: E402
from packaging.utils import canonicalize_name  # noqa: E402


def check(report, version, *, extras=('ops', 'brief'), wheel=None, profile='semantic'):
    """Compare a resolution with the catalog of ``profile`` (``semantic``: the historical default).

    A lexical resolution must not contain any package of the ML closure (``ml_packages``).
    """
    expected = {**expected_versions(profile=profile, extras=extras), 'memory-os-ai': version}
    found, duplicate, project_entry = {}, [], None
    for item in report.get('install', []):
        data = item['metadata']
        name = canonicalize_name(data['name'])
        if name in found:
            duplicate.append(name)
        found[name] = data['version']
        if name == 'memory-os-ai':
            project_entry = item
    drift = {n: {'expected': v, 'actual': found.get(n)} for n, v in expected.items() if found.get(n) != v}
    extra = sorted(set(found) - set(expected))
    closure = {canonicalize_name(name): name for name in ml_closure(extras=extras)} if profile == 'lexical' else {}
    ml_packages = sorted(name for key, name in closure.items() if key in found)
    env = report.get('environment', {})
    scope_ok = (env.get('sys_platform'), env.get('platform_machine'), env.get('python_version')) == ('darwin', 'arm64', '3.11')
    wheel_ok = True
    if wheel:
        with zipfile.ZipFile(wheel) as archive:
            entries = [n for n in archive.namelist() if n.endswith('.dist-info/METADATA')]
            if len(entries) != 1:
                raise ValueError('Ambiguous wheel metadata')
            metadata = Parser().parsestr(archive.read(entries[0]).decode())
        hashes = (project_entry or {}).get('download_info', {}).get('archive_info', {}).get('hashes', {})
        wheel_ok = (metadata['Version'] == version and canonicalize_name(metadata['Name']) == 'memory-os-ai'
                    and hashes.get('sha256') == hashlib.sha256(wheel.read_bytes()).hexdigest()
                    and check_requirements(metadata.get_all('Requires-Dist') or [], profile=profile,
                                           extras=extras)['ok'])
    return {'ok': scope_ok and not drift and not extra and not duplicate and not ml_packages and wheel_ok,
            'profile': profile, 'scope_ok': scope_ok, 'expected_packages': len(expected),
            'resolved_packages': len(found), 'mismatches': drift, 'unexpected_packages': extra,
            'ml_packages': ml_packages, 'duplicates': duplicate, 'wheel_ok': wheel_ok,
            'model_calls': 0, 'downloads_by_checker': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--wheel', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--profile', choices=('lexical', 'semantic'), default='semantic')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Fresh proof file required; keep refused evidence')
    try:
        version = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['version']
        result = check(json.loads(args.report.read_text()), version, wheel=args.wheel, profile=args.profile)
        result['pip_report_sha256'] = hashlib.sha256(args.report.read_bytes()).hexdigest()
        result['wheel_sha256'] = hashlib.sha256(args.wheel.read_bytes()).hexdigest()
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        result = {'ok': False, 'error': type(exc).__name__}
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps(result, sort_keys=True))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

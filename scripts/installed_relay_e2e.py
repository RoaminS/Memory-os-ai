#!/usr/bin/env python3
"""Test each installed artifact against a synthetic TCP server (Claude, outside sandbox).

No installation, network downloads, production service control or publication.
Produces evidence only; record it with verify_release_manifest.py afterwards.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def matrix(manifest):
    """(kind, artifact suffix, profile, environment key). 4.1.0: exactly three cases (QUALIFY_4_1_0 G14)."""
    if manifest['version'] == '4.1.0':
        return [('wheel', '.whl', 'semantic', 'wheel_environment'), ('sdist', '.tar.gz', 'semantic', 'sdist_environment'),
                ('wheel', '.whl', 'lexical', 'lexical_environment')]
    return [('wheel', '.whl', None, 'wheel_environment'), ('sdist', '.tar.gz', None, 'sdist_environment')]


def main() -> int:
    if not __debug__:
        raise RuntimeError('Qualification requires assertions enabled; do not use Python -O')
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--environments', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--check-only', action='store_true', help='Check artifact/environment inventory without TCP')
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    from verify_release_manifest import validate_local
    validate_local(manifest, args.manifest.parent, require_source=not args.check_only)
    environments = json.loads(args.environments.read_text())
    case = Path(__file__).with_name('installed_relay_case.py')
    runner_hashes = {'runner': digest(Path(__file__)), 'case': digest(case)}
    assert runner_hashes == manifest['installed_relay_scripts_sha256'], 'test scripts differ from qualification'
    repo = Path(__file__).resolve().parents[1]
    cases = matrix(manifest)
    if manifest['version'] == '4.1.0':
        assert (environments.get('ok') is True and environments.get('source_commit') == manifest['source_commit']
                and environments.get('run_tag') == manifest['run_tag']
                and environments.get('artifacts') == manifest['artifacts']), 'environments of another candidate/run'
    for kind, suffix, profile, key in cases:
        artifact = next(a for a in manifest['artifacts'] if a['name'].endswith(suffix))
        path = args.manifest.parent.resolve() / 'artifacts' / artifact['name']
        assert digest(path) == artifact['sha256'], 'artifact changed'
        python = Path(environments[key]) / 'bin/python'
        assert python.is_file() and repo not in python.absolute().parents, 'environment missing/inside checkout'
    if args.check_only:
        print('Inventory OK; TCP not executed; no qualifying proof produced.')
        return 0
    report = {'source_commit': manifest['source_commit'], 'scripts_sha256': runner_hashes, 'cases': [], 'ok': False}
    if manifest['version'] == '4.1.0':
        report['run_tag'] = manifest['run_tag']
    env = {**os.environ, 'PYTHONPATH': '', 'PYTHONNOUSERSITE': '1', 'PYTHONDONTWRITEBYTECODE': '1',
           'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'TOKENIZERS_PARALLELISM': 'false', 'PYTHONOPTIMIZE': '0'}
    with tempfile.TemporaryDirectory(prefix='installed-relay-evidence-') as directory:
        workspace = Path(directory)
        executable_case = workspace / 'case.py'
        executable_case.write_bytes(case.read_bytes())
        for kind, suffix, profile, key in cases:
            artifact = next(a for a in manifest['artifacts'] if a['name'].endswith(suffix))
            path = args.manifest.parent.resolve() / 'artifacts' / artifact['name']
            label = kind if profile is None else kind + '-' + profile
            output = workspace / (label + '.json')
            with (workspace / (label + '.log')).open('w') as log:
                try:
                    result = subprocess.run([str(Path(environments[key]) / 'bin/python'),
                                             str(executable_case), '--kind', kind, '--artifact', str(path),
                                             '--sha256', artifact['sha256'], '--source-commit', manifest['source_commit'],
                                             '--version', manifest['version'],
                                             *(['--profile', profile] if profile else []),
                                             '--output', str(output)], cwd=workspace, env=env,
                                            stdout=log, stderr=log, timeout=180)
                    record = json.loads(output.read_text()) if output.exists() else {'ok': False, 'error': 'case produced no report'}
                    record['exit_code'] = result.returncode
                except subprocess.TimeoutExpired:
                    record = {'ok': False, 'error': 'case exceeded 180s', 'exit_code': -1}
            record.update(kind=kind, artifact_sha256=artifact['sha256'])
            if profile:
                record['profile'] = profile
            report['cases'].append(record)
            print(label + ': ' + ('PASS' if record.get('ok') and record['exit_code'] == 0 else 'FAIL'), flush=True)
    report['ok'] = len(report['cases']) == len(cases) and all(c.get('ok') and c['exit_code'] == 0 for c in report['cases'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print('Evidence saved; no publication. Guard remains unchanged until evidence is recorded.')
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

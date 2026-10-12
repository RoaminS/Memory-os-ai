"""Assemble immutable qualified artifacts/evidence. Never build/install/control a service."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

from release_406_checks import SOURCE, WHEEL, SDIST, digest, validate_local

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--artifacts', type=Path, required=True)
    p.add_argument('--full', type=Path, required=True)
    p.add_argument('--artifact-proof', type=Path, required=True)
    p.add_argument('--resolution', type=Path, required=True)
    p.add_argument('--sqlite-trial', type=Path, required=True)
    p.add_argument('--jsonl-trial', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    output = a.output.absolute()
    if output.exists() or not output.resolve().is_relative_to(ROOT / '.local-qualification'):
        p.error('Fresh folder under clone .local-qualification required')
    output.mkdir(mode=0o700)
    (output / 'artifacts').mkdir(mode=0o700)
    (output / 'evidence').mkdir(mode=0o700)
    manifest = {'version': '4.0.6', 'source_commit': SOURCE, 'release_ready': False, 'open_blockers': [],
                'checks': {'production_copy': 'pending', 'installed_relays': 'pending'},
                'artifacts': [], 'distribution_evidence': {},
                'copy_scripts_sha256': {}, 'installed_relay_scripts_sha256': {}}
    for name, sha in [('memory_os_ai-4.0.6-py3-none-any.whl', WHEEL), ('memory_os_ai-4.0.6.tar.gz', SDIST)]:
        original = a.artifacts / name
        if digest(original) != sha:
            raise ValueError('Original qualified artifact differs: ' + name)
        dest = output / 'artifacts' / name
        shutil.copyfile(original, dest)
        if digest(dest) != sha:
            raise ValueError('Artifact copy differs')
        manifest['artifacts'].append({'name': name, 'sha256': sha, 'bytes': dest.stat().st_size})
    for name, source in [('full', a.full), ('artifacts', a.artifact_proof), ('resolution', a.resolution)]:
        dest = output / 'evidence' / (name + '.json')
        shutil.copyfile(source, dest)
        manifest['distribution_evidence'][name] = {'path': str(dest.relative_to(output)), 'sha256': digest(dest)}
    manifest['qualification_source_sha256'] = json.loads(a.full.read_text())['source_sha256']
    for backend, directory in [('sqlite', a.sqlite_trial), ('jsonl', a.jsonl_trial)]:
        files = {'ready': 'ready.out', 'probe': 'probe.out', 'doctor_failures': 'doctor_fail.json',
                 'configs_before': 'real_configs_before.json', 'configs_after': 'real_configs_after.json'}
        record = {k: json.loads((directory / v).read_text()) for k, v in files.items()}
        record['original_evidence_sha256'] = {v: digest(directory / v) for v in files.values()}
        dest = output / 'evidence' / ('trial_' + backend + '.json')
        dest.write_text(json.dumps(record, indent=2) + '\n')
        manifest['distribution_evidence']['trial_' + backend] = {'path': str(dest.relative_to(output)), 'sha256': digest(dest)}
    twine = output / 'evidence/twine-check-strict.log'
    shutil.copyfile(a.artifacts / 'twine-check-strict.log', twine)
    manifest['twine_evidence'] = {'path': str(twine.relative_to(output)), 'sha256': digest(twine)}
    for name, leaf in [('runner', 'qualify_sqlite_release_copy.py'), ('worker', 'sqlite_release_worker.py'),
                       ('doctor', 'qualify_release_copy.py'), ('profile', 'qualify_sqlite_activation.py')]:
        manifest['copy_scripts_sha256'][name] = digest(ROOT / 'scripts' / leaf)
    manifest['copy_scripts_sha256']['guard'] = digest(ROOT / 'v4/src/memory_os_v4/ops/provision.py')
    manifest['installed_relay_scripts_sha256'] = {'runner': digest(ROOT / 'scripts/installed_relay_e2e.py'),
                                                 'case': digest(ROOT / 'scripts/installed_relay_case.py')}
    scripts = ['verify_release_manifest.py', 'release_406_checks.py', 'make_release_406.py',
               'qualify_sqlite_release_copy.py', 'sqlite_release_worker.py', 'qualify_distribution_d1a.py',
               'installed_relay_case.py', 'installed_relay_e2e.py', 'qualify_release_copy.py',
               'qualify_sqlite_activation.py', 'qualify_405_test_isolation.py', 'source_release_checks.py',
               'release_copy_worker.py', 'test_home_guard.py']
    manifest['qualification_tooling_sha256'] = {name: digest(ROOT / 'scripts' / name) for name in scripts}
    manifest['qualification_tooling_commit'] = subprocess.check_output(
        ['git', '--git-dir=' + str(ROOT / '.local-qualification/lexical-dev-git'), 'rev-parse', 'HEAD'], text=True).strip()
    validate_local(manifest, output, require_copy=False)
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'assembled': True, 'source_commit': SOURCE, 'release_ready': False,
                      'pending': ['SQLite production-copy qualification', 'installed wheel/sdist relays']}))
    return 0


if __name__ == '__main__':
    sys.exit(main())

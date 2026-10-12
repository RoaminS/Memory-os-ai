"""Sequential 4.0.5->4.0.6 SQLite copy drill. No service control or live engine."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import plistlib
import shutil
import sys
import time

from qualify_distribution_d1a import command
from qualify_release_copy import private_doctor
from qualify_sqlite_activation import profile_environment
from release_copy_worker import QUERIES
from release_406_checks import SOURCE, INSTANCE, digest, validate_copy, validate_local

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'v4/src'))
from memory_os_v4.ops.provision import guard, min_free_bytes  # noqa: E402


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--environments', type=Path, required=True)
    p.add_argument('--baseline-python', type=Path, required=True)
    p.add_argument('--source-dir', type=Path, required=True)
    p.add_argument('--production-plist', type=Path, required=True)
    p.add_argument('--output-root', type=Path, required=True)
    a = p.parse_args()
    manifest = json.loads(a.manifest.read_text())
    validate_local(manifest, a.manifest.parent, require_copy=False)
    root = a.output_root.absolute()
    if (root.exists() or root.is_symlink() or not root.resolve().is_relative_to(ROOT / '.local-qualification')
            or root.resolve() == (ROOT / '.local-qualification').resolve()):
        raise ValueError('Fresh private output below clone .local-qualification required')
    if a.source_dir.resolve().is_relative_to(root.resolve()) or root.resolve().is_relative_to(a.source_dir.resolve()):
        raise ValueError('Copy/source paths must be disjoint')
    guard(root.parent)
    root.mkdir(mode=0o700)
    tools = {'runner': Path(__file__), 'worker': Path(__file__).with_name('sqlite_release_worker.py'),
             'guard': ROOT / 'v4/src/memory_os_v4/ops/provision.py',
             'doctor': Path(__file__).with_name('qualify_release_copy.py'),
             'profile': Path(__file__).with_name('qualify_sqlite_activation.py')}
    report = {'ok': False, 'version': '4.0.6', 'baseline_version': '4.0.5', 'source_commit': SOURCE,
              'artifacts': manifest['artifacts'], 'source_sql_writes': False, 'production_modified': False,
              'min_free_bytes': min_free_bytes(), 'workers': {},
              'scripts_sha256': {k: digest(v) for k, v in tools.items()}}
    started = time.monotonic()
    try:
        if report['scripts_sha256'] != manifest['copy_scripts_sha256']:
            raise ValueError('Copy tools differ from sealed manifest')
        os.setpriority(os.PRIO_PROCESS, 0, 15)
        declared = plistlib.loads(a.production_plist.read_bytes()).get('EnvironmentVariables', {})
        if (Path(declared.get('MEMORY_V4_DIR', '')).resolve() != a.source_dir.resolve()
                or declared.get('MEMORY_V4_STORE_BACKEND') != 'sqlite_s1'
                or declared.get('MEMORY_V4_WORK_STATE') != '1'):
            raise ValueError('Source path/backend/tasks must match the production plist')
        report['production_plist_sha256'] = digest(a.production_plist)
        report['source_dir'] = str(a.source_dir.resolve())
        profile = profile_environment(a.production_plist)
        paths = json.loads(a.environments.read_text())
        interpreters = {'baseline': a.baseline_python.absolute(),
                        **{k: Path(paths[k + '_environment']).absolute() / 'bin/python' for k in ('wheel', 'sdist')}}
        for executable in interpreters.values():
            if not executable.is_file() or ROOT in executable.parents:
                raise ValueError('Installed environments must be outside checkout')
        (root / 'home').mkdir(mode=0o700)
        plan = {'format': 'sqlite-release-406-v1', 'source_commit': SOURCE, 'queries': list(QUERIES),
                'at_time': time.time(), 'expected_instance_id': INSTANCE,
                'criterion': {'startup_seconds_max': 120, 'total_seconds_max': 900,
                              'corpus_encoded_texts': 0, 'exact_rankings': True, 'authority_identical': True}}
        plan_path = root / 'plan.json'
        plan_path.write_text(json.dumps(plan, indent=2) + '\n')
        (root / 'copy-owner.json').write_text('{"owner":"sqlite-release-406-v1"}\n')
        worker = root / 'worker.py'
        shutil.copyfile(Path(__file__).with_name('sqlite_release_worker.py'), worker)
        shutil.copyfile(tools['guard'], root / 'qualification_guard.py')
        env = {k: os.environ[k] for k in ('PATH', 'LANG') if k in os.environ}
        env.update(profile, HOME=str(root / 'home'), PYTHONPATH='', PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1',
                   HF_HOME=os.environ.get('HF_HOME', str(Path.home() / '.cache/huggingface')),
                   HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', PYTHONOPTIMIZE='0',
                   MEMORY_V4_MIN_FREE_BYTES=str(min_free_bytes()), MEMORY_V4_STORE_BACKEND='sqlite_s1',
                   MEMORY_V4_WORK_STATE='1', MEMORY_V4_WRITE_MODE='read_only',
                   OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', VECLIB_MAXIMUM_THREADS='1')
        def monitor(path):
            guard(path)
            if time.monotonic() - started > 900:
                raise RuntimeError('Copy total time budget exceeded')
        command([str(interpreters['baseline']), '-B', str(worker), '--plan', str(plan_path),
                 '--kind', 'snapshot', '--snapshot-source', str(a.source_dir.absolute())],
                root, env, root / 'snapshot.log', monitor)
        logical = json.loads((root / 'snapshot.json').read_text())
        report['snapshot'] = logical
        if logical['store_instance_id'] != INSTANCE or logical['schema_version'] != 7:
            raise ValueError('Source identity/schema mismatch')
        cut_sha = digest(root / 'snapshot.sqlite3')
        for name in ('baseline', 'wheel', 'sdist'):
            target = root / (name + '-store')
            target.mkdir(mode=0o700)
            shutil.copyfile(root / 'snapshot.sqlite3', target / 'store.sqlite3')
            (target / 'store.sqlite3').chmod(0o600)
        for name in ('baseline', 'wheel', 'sdist', 'wheel-warm', 'sdist-warm'):
            executable = interpreters[name.removesuffix('-warm')]
            env['MEMORY_V4_BUILD_COMMIT'] = SOURCE
            if name == 'baseline':
                env.pop('MEMORY_V4_BUILD_COMMIT', None)
            command([str(executable), '-B', str(worker), '--plan', str(plan_path), '--kind', name],
                    root, env, root / (name + '.log'), monitor)
            report['workers'][name] = json.loads((root / (name + '.json')).read_text())
        if digest(root / 'snapshot.sqlite3') != cut_sha:
            raise ValueError('Sealed source cut changed')
        if digest(a.production_plist) != report['production_plist_sha256']:
            raise ValueError('Production definition changed during qualification')
        report['source_cut_sha256'] = cut_sha
        report.update(authority_identical=True, search_identical_to_baseline=True,
                      rankings_identical_between_artifacts_and_reopen=True, warm_encoded_texts=0)
        # Reuse the established private --warm HTTP/doctor drill, with this
        # harness's effective guard. No production port/token/service used.
        import qualify_release_copy as old
        old_guard = old.resources
        try:
            old.resources = monitor
            report['doctor'] = private_doctor(interpreters['wheel'], root / 'wheel-store', root,
                                              env, SOURCE, version='4.0.6')
        finally:
            old.resources = old_guard
        report['seconds'] = time.monotonic() - started
        report['ok'] = True
        validate_copy(report, manifest)
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        report.update(ok=False, error=type(exc).__name__ + ': ' + str(exc))
    finally:
        report['seconds'] = time.monotonic() - started
        (root / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: report.get(k) for k in ('ok', 'error', 'source_sql_writes', 'seconds')}))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

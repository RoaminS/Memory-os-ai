#!/usr/bin/env python3
"""Claude-only guarded COPY drill; no service control or production activation.

--snapshot-only creates a stable JSONL copy, without opening a live engine.
Default performs the full installed-runtime drill. Failed evidence is retained.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import signal
import subprocess
import time

from qualify_release_copy import inventory, snapshot
from source_release_checks import resources
from search_qualification import SEARCH_CRITERION

ROOT = Path(__file__).resolve().parents[1]
CONFIG_ENV_KEYS = set(re.findall(r'["\'](MEMORY_V4_[A-Z0-9_]+)["\']',
                                (ROOT / 'v4/src/memory_os_v4/config.py').read_text()))
QUERIES = ('memory-os-ai', 'SQLite', 'Romain', 'Claude', 'Codex', 'MPS', 'C4',
           'préférences utilisateur', 'production et sauvegarde', 'shared memory protocol')
CRITERION = {'migration_seconds_max': 120, 'total_seconds_max': 900,
             'frozen_rankings_identical': True, 'search_non_vacuity': SEARCH_CRITERION, 'warm_corpus_encoded_texts': 0,
             'normal_migration_identity_preserved': True, 'private_restore_identity_new': True,
             'rollback_after_new_writes_verified': True, 'private_reopen_required': True}
# Data/key/token paths never come from an operational environment into a copy.
DROP = {'MEMORY_V4_DIR', 'MEMORY_V4_URL', 'MEMORY_V4_TOKEN_FILE', 'MEMORY_V4_BACKUP_DIR',
        'MEMORY_V4_BACKUP_KEY', 'MEMORY_V4_OPS_DIR', 'MEMORY_V4_SERVICE_LABEL',
        'MEMORY_V4_RELAY_JOURNAL_DIR', 'MEMORY_V4_PROJECT_REGISTRY', 'MEMORY_V4_BUILD_COMMIT',
        'MEMORY_V4_WRITE_MODE', 'MEMORY_V4_STORE_BACKEND', 'MEMORY_V4_WORK_STATE', 'MEMORY_V4_PROJECT'}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def profile_environment(plist):
    settings = plistlib.loads(plist.read_bytes()).get('EnvironmentVariables', {})
    if settings.get('MEMORY_V4_PROJECT_REGISTRY'):
        raise ValueError('Registry must remain inactive; no implicit alias migration')
    allowed = CONFIG_ENV_KEYS | {'MEMORY_V4_CL100K_PATH', 'HF_HOME', 'HUGGINGFACE_HUB_CACHE', 'SENTENCE_TRANSFORMERS_HOME'}
    profile = {k: v for k, v in settings.items() if k in allowed and k not in DROP}
    if any(not isinstance(v, str) for v in profile.values()):
        raise ValueError('Plist environment must contain strings')
    for key, default in [('MEMORY_V4_DEVICE', 'auto'), ('MEMORY_V4_EMBED_DTYPE', 'native'),
                         ('MEMORY_V4_EMBED_BATCH_SIZE', '32'), ('MEMORY_V4_EMBED_MAX_SEQ_LENGTH', '512'),
                         ('MEMORY_V4_BM25_PROFILE', 'scoped_literal'), ('MEMORY_V4_LEXICAL_GUARD_K', '3'),
                         ('MEMORY_V4_SQLITE_EMBEDDING_NAMESPACE', 'v1')]:
        profile.setdefault(key, default)
    return profile


def run_worker(command, output, env, guard, timeout):
    started = time.monotonic()
    with (output / 'worker.log').open('x') as log:
        proc = subprocess.Popen(command, cwd=output, env=env, stdout=log,
                                stderr=subprocess.STDOUT, start_new_session=True)
        try:
            while proc.poll() is None:
                guard()
                if time.monotonic() - started > timeout:
                    raise RuntimeError('Frozen dry-run wall-time budget exceeded')
                time.sleep(.5)
            if proc.returncode:
                raise RuntimeError('Copy worker failed; inspect retained worker.json/log')
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--snapshot-only', action='store_true')
    parser.add_argument('--runtime-python', type=Path)
    parser.add_argument('--release-manifest', type=Path)
    parser.add_argument('--production-plist', type=Path)
    args = parser.parse_args()
    output = args.output.absolute()
    if (output.exists() or output.is_symlink() or not output.resolve().is_relative_to(ROOT / '.local-qualification')
            or output.resolve() == (ROOT / '.local-qualification').resolve()):
        parser.error('Fresh private directory below clone .local-qualification required')
    for part in (output.parent, *output.parent.parents, args.source, *args.source.parents):
        if part.is_symlink():
            parser.error('Symlink source/output path refused')
    output.mkdir(parents=True, mode=0o700)
    report = {'ok': False, 'production_activation': False, 'production_modified': None,
              'snapshot_only': args.snapshot_only, 'api_calls': 0, 'criterion': CRITERION}
    source_before = None
    started = time.monotonic()
    try:
        if os.getpriority(os.PRIO_PROCESS, 0) < 15:
            raise ValueError('nice 15 required')
        report['resources_before'] = resources(output)
        source_before = inventory(args.source)
        footprint = sum((args.source / name).stat().st_size for name in source_before)
        if shutil.disk_usage(output).free < 5_000_000_000 + 10 * footprint:
            raise ValueError('Need 5 GB reserve plus ten source footprints for retained copies/cache/drill')
        snapshot(args.source, output / 'snapshot')
        (output / 'snapshot/writer.lock').write_bytes(b'')  # Owned copy only, never source lock.
        (output / 'copy-owner.json').write_text(json.dumps({'owner': 'sqlite-activation-copy-v1'}) + '\n')
        report['source_files_sha256'] = source_before
        if args.snapshot_only:
            report['ok'] = True
        else:
            if not all((args.runtime_python, args.release_manifest, args.production_plist)):
                raise ValueError('Installed runtime, qualified release manifest and production plist required')
            from verify_release_manifest import validate_local, validate_relay
            manifest = json.loads(args.release_manifest.read_text())
            directory = args.release_manifest.resolve().parent
            validate_local(manifest, directory)
            validate_relay(json.loads((directory / 'installed-relay-proof.json').read_text()), manifest)
            if manifest['version'] != '4.0.5' or manifest['release_ready'] is not True:
                raise ValueError('Fully qualified 4.0.5 release required')
            artifact = next(a for a in manifest['artifacts'] if a['name'].endswith('.whl'))
            profile = profile_environment(args.production_plist)
            plan = {'format': 'sqlite-activation-copy-v1', 'production_activation': False,
                    'queries': QUERIES, 'at_time': time.time(), 'criterion': CRITERION,
                    'profile_environment': profile, 'source_files_sha256': source_before,
                    'release_manifest_sha256': sha(args.release_manifest),
                    'release_source_commit': manifest['source_commit'],
                    'wheel': str(directory / 'artifacts' / artifact['name']), 'wheel_sha256': artifact['sha256'],
                    'runner_sha256': sha(Path(__file__)),
                    'migration_adapter_sha256': sha(Path(__file__).with_name('migrate_sqlite_activation.py')),
                    'worker_sha256': sha(Path(__file__).with_name('sqlite_activation_worker.py')),
                    'search_gate_sha256': sha(Path(__file__).with_name('search_qualification.py'))}
            plan_path = output / 'plan.json'
            plan_path.write_text(json.dumps(plan, indent=2) + '\n')  # Frozen BEFORE worker/inference.
            report['plan_sha256'] = sha(plan_path)
            env = {k: v for k, v in os.environ.items()
                   if not k.startswith(('MEMORY_', 'OPENAI_', 'ANTHROPIC_', 'HF_', 'HUGGING_FACE_'))
                   and k not in {'PYTHONPATH', 'PYTHONSTARTUP', 'PYTEST_ADDOPTS', 'PYTHONOPTIMIZE'}
                   and 'PROXY' not in k.upper()}
            env.update(profile, MEMORY_V4_BUILD_COMMIT=manifest['source_commit'],
                       MEMORY_V4_MAINTENANCE_MODE='disabled', PYTHONPATH='', PYTHONNOUSERSITE='1',
                       HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', PYTHONDONTWRITEBYTECODE='1',
                       OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', VECLIB_MAXIMUM_THREADS='1',
                       TOKENIZERS_PARALLELISM='false')
            worker = output / 'worker.py'
            shutil.copyfile(Path(__file__).with_name('sqlite_activation_worker.py'), worker)
            shutil.copyfile(Path(__file__).with_name('migrate_sqlite_activation.py'),
                            output / 'migrate_sqlite_activation.py')
            shutil.copyfile(Path(__file__).with_name('search_qualification.py'),
                            output / 'search_qualification.py')
            # Preserve the venv executable spelling: resolving its symlink runs
            # the base interpreter and loses the qualified installation.
            run_worker([str(args.runtime_python.absolute()), '-B', str(worker), '--plan', str(plan_path.resolve())],
                       output, env, lambda: resources(output), CRITERION['total_seconds_max'])
            result = json.loads((output / 'worker.json').read_text())
            report['worker'] = result
            report['ok'] = result.get('ok') is True and result.get('service_drill_qualified') is True
    except Exception as exc:
        report.update(ok=False, error=f'{type(exc).__name__}: {exc}')
        if (output / 'worker.json').is_file():
            report['worker'] = json.loads((output / 'worker.json').read_text())
    finally:
        if source_before is not None:
            try:
                report['production_modified'] = inventory(args.source) != source_before
                if report['production_modified']:
                    report.update(ok=False, error='Source changed during drill; no retry')
            except Exception as exc:
                report.update(ok=False, production_modified=None, error=f'Source recheck failed: {type(exc).__name__}')
        report['seconds'] = time.monotonic() - started
        with (output / 'report.json').open('x') as fh:
            json.dump(report, fh, indent=2)
            fh.write('\n')
    print(json.dumps({k: report.get(k) for k in ('ok', 'seconds', 'error', 'production_modified')}))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""Claude-only sequential Hash lifecycle, 1k/10k; real machine guard, no promotion."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

from qualify_405_test_isolation import resource_sample

ROOT = Path(__file__).resolve().parents[1]


def attest(git_dir, commit):
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise RuntimeError('Full source commit required')
    base = ['git', '--git-dir=' + str(git_dir.resolve()), '--work-tree=' + str(ROOT)]
    check = subprocess.run([*base, 'cat-file', '-t', commit], cwd=ROOT, capture_output=True, text=True)
    if check.returncode or check.stdout.strip() != 'commit':
        raise RuntimeError('Commit unavailable in local metadata')
    paths = ['v4/src', 'v4/tests', 'scripts', 'conftest.py', 'pyproject.toml']
    diff = subprocess.run([*base, 'diff', '--quiet', commit, '--', *paths], cwd=ROOT)
    others = subprocess.run([*base, 'ls-files', '--others', '--exclude-standard', '--', *paths],
                            cwd=ROOT, capture_output=True, text=True)
    if diff.returncode or others.returncode or others.stdout.strip():
        raise RuntimeError('Source differs from sealed commit; no measurement')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--git-dir', type=Path, default=ROOT / '.local-qualification/lexical-dev-git')
    parser.add_argument('--items', type=int, choices=(1000, 10000), default=10000)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / '.local-qualification'):
        parser.error('Fresh output inside clone .local-qualification required')
    output.mkdir(parents=True, exist_ok=False)
    result = {'ok': False, 'production_modified': False, 'neural_inference': False,
              'api_calls': 0, 's4_activation_qualified': False, 'items': args.items}
    start = time.monotonic()
    try:
        result['resources_before'] = resource_sample(True)
        if os.getpriority(os.PRIO_PROCESS, 0) < 15:
            raise RuntimeError('nice 15 required')
        attest(args.git_dir, args.source_commit)
        os.environ.update(MEMORY_V4_BUILD_COMMIT=args.source_commit, PYTHONDONTWRITEBYTECODE='1',
                          HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', OMP_NUM_THREADS='1',
                          OPENBLAS_NUM_THREADS='1', VECLIB_MAXIMUM_THREADS='1')
        from memory_os_v4 import Config, MemoryV4
        from memory_os_v4.ops.auth import write_secret_file
        from memory_os_v4.ops.settings import OpsSettings
        from memory_os_v4.ops import sqlite_backup as s4
        settings = OpsSettings(url='http://127.0.0.1:9/mcp/', token_file=output / 'unused-token',
                               store_dir=output / 'synthetic-source', backup_dir=output / 'backups',
                               backup_key=output / 'backup.key', ops_dir=output / 'ops', service_label=None)
        budget = s4.Budget(settings)
        import secrets
        write_secret_file(settings.backup_key, secrets.token_bytes(32))
        with MemoryV4(config=Config(data_dir=str(settings.store_dir), embed_model='hash', hash_dim=16,
                                  store_backend='sqlite_s1')) as engine:
            seed_start = time.monotonic()
            for offset in range(0, args.items, 100):
                budget.check()
                engine.add_episodes_bulk([{'item_id': f'item-{i:05d}', 'text': f'Synthetic unique evidence number {i:05d}',
                                          'project': 's4-synthetic'} for i in range(offset, min(offset + 100, args.items))])
            result['seed_seconds'] = time.monotonic() - seed_start
            before = time.monotonic()
            archive = s4.backup(settings, apply=True)
            result['backup_seconds'] = time.monotonic() - before
            if not archive['ok']:
                raise RuntimeError('Backup failed: ' + archive.get('error', 'unknown'))
            result['backup'] = archive
            for i in range(5):
                engine.forget(f'item-{i:05d}', project='s4-synthetic')
            result['drill'] = s4.restore_drill(settings, apply=True, start_server=True, reconcile=True)
            if not result['drill']['ok']:
                raise RuntimeError('Drill failed: ' + result['drill'].get('error', 'unknown'))
            check = result['drill']['server_check']
            result['ok'] = (check['status_total'] == args.items - 5 and check['corpus_reencodings'] == 0
                            and result['drill']['duration_s'] <= 300
                            and len(result['drill']['forgets_replayed']) == 5)
        result['resources_after'] = resource_sample(True)
    except Exception as exc:
        result.update(ok=False, error=f'{type(exc).__name__}: {exc}')
    result.update(seconds=round(time.monotonic() - start, 3), source_commit=args.source_commit,
                  implementation_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (output / 'report.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: result.get(k) for k in ('ok', 'items', 'seconds', 'error')}))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

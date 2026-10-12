#!/usr/bin/env python3
"""Generate INACTIVE SQLite activation/ops plists; never deploy or run launchctl."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import plistlib


def render(server, backup, health, data_dir, runtime_python, *, work_state):
    env = dict(server.get('EnvironmentVariables', {}))
    for original in (backup, health):
        for key, value in original.get('EnvironmentVariables', {}).items():
            if key in {'MEMORY_V4_BACKUP_DIR', 'MEMORY_V4_BACKUP_KEY', 'MEMORY_V4_OPS_DIR',
                       'MEMORY_V4_TOKEN_FILE', 'MEMORY_V4_URL'}:
                if key in env and env[key] != value:
                    raise ValueError('Conflicting operational paths; resolve explicitly: ' + key)
                env.setdefault(key, value)
    commit = env.get('MEMORY_V4_BUILD_COMMIT', '')
    import re
    if not re.fullmatch('[0-9a-f]{40}', commit):
        raise ValueError('Qualified build commit required')
    if env.get('MEMORY_V4_PROJECT_REGISTRY'):
        raise ValueError('Registry must remain inactive')
    if env.get('MEMORY_V4_DEVICE', 'auto') not in {'auto', 'mps'}:
        raise ValueError('Unchanged auto/MPS required')
    for key, value in [('MEMORY_V4_EMBED_DTYPE', 'native'), ('MEMORY_V4_EMBED_BATCH_SIZE', '32'),
                       ('MEMORY_V4_EMBED_MAX_SEQ_LENGTH', '512'), ('MEMORY_V4_BM25_PROFILE', 'scoped_literal'),
                       ('MEMORY_V4_LEXICAL_GUARD_K', '3')]:
        if env.get(key, value) != value:
            raise ValueError('Unchanged qualified policy required: ' + key)
    if not env.get('MEMORY_V4_CL100K_PATH'):
        raise ValueError('Qualified offline tokenizer table required')
    data_dir = Path(data_dir).expanduser().absolute()
    if (data_dir.exists() or '..' in data_dir.parts or any(p.is_symlink() for p in data_dir.parents)
            or data_dir == Path.home() / '.memory-os-v4' or data_dir.is_relative_to(Path.home() / '.memory-os-v4')
            or data_dir.is_relative_to(Path.home() / 'Memory-os-ai') or data_dir.is_symlink()):
        raise ValueError('New separate SQLite directory required')
    env.update(MEMORY_V4_STORE_BACKEND='sqlite_s1', MEMORY_V4_DIR=str(data_dir),
               MEMORY_V4_WORK_STATE='1' if work_state else '0', MEMORY_V4_WRITE_MODE='read_only',
               MEMORY_V4_SQLITE_EMBEDDING_NAMESPACE=env.get('MEMORY_V4_SQLITE_EMBEDDING_NAMESPACE', 'v1'),
               HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
    env.pop('MEMORY_V4_SUPERSEDE_COS', None)
    env.pop('MEMORY_V4_DEDUP_COS', None)
    # Preserve key/token FILE paths and all production profile knobs, never their contents.
    result = {}
    for original, command in ((server, [str(runtime_python), '-m', 'memory_os_v4.server', '--transport',
                                         'http', '--host', '127.0.0.1', '--port', '8765', '--warm']),
            (backup, [str(runtime_python), '-m', 'memory_os_v4.ops.cli', 'backup', '--backend', 'sqlite', '--online', '--apply']),
            (health, [str(runtime_python), '-m', 'memory_os_v4.ops.cli', 'health'])):
        target = copy.deepcopy(original)
        target['ProgramArguments'] = command
        target['EnvironmentVariables'] = dict(env)
        target['EnvironmentVariables'].pop('MEMORY_V4_SERVICE_LABEL', None)
        if original is server:
            target['StandardErrorPath'] = str(data_dir / 'server.log')
            target['StandardOutPath'] = str(data_dir / 'server.out.log')
        else:
            target['Nice'] = 15
        result[target['Label']] = target
    drill = copy.deepcopy(result[backup['Label']])
    drill.update(Label=server['Label'] + '.restore-drill',
        ProgramArguments=[str(runtime_python), '-m', 'memory_os_v4.ops.cli', 'restore-drill',
                          '--backend', 'sqlite', '--apply', '--reconcile-forgets'],
        StartCalendarInterval={'Weekday': 1, 'Hour': 3, 'Minute': 30}, Nice=15)
    drill.pop('KeepAlive', None)
    drill.pop('RunAtLoad', None)
    drill['StandardErrorPath'] = str(Path(env.get('MEMORY_V4_OPS_DIR', '~/.local/state/memory-os')).expanduser()
                                    / 'logs/restore-drill.err')
    result[drill['Label']] = drill
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server-plist', type=Path, required=True)
    parser.add_argument('--backup-plist', type=Path, required=True)
    parser.add_argument('--health-plist', type=Path, required=True)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--runtime-python', type=Path, required=True)
    parser.add_argument('--work-state', choices=('0', '1'), required=True,
                        help='1 requires a separate L7b activation GO; templates only')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1] / '.local-qualification'
    output = args.output.absolute()
    if output.exists() or output.is_symlink() or not output.resolve().is_relative_to(root):
        parser.error('Fresh directory below clone .local-qualification required')
    paths = (args.server_plist, args.backup_plist, args.health_plist)
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    jobs = render(*(plistlib.loads(p.read_bytes()) for p in paths), args.data_dir,
                  args.runtime_python.absolute(), work_state=args.work_state == '1')
    output.mkdir(mode=0o700, parents=True)
    for label, job in jobs.items():
        # labels come from user-owned configuration, never use them as paths unchecked.
        if not label or Path(label).name != label or '/' in label or label in {'.', '..'}:
            raise ValueError('Unsafe launchd label')
        target = output / (label + '.plist')
        target.write_bytes(plistlib.dumps(job))
        target.chmod(0o600)
    if before != {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}:
        raise ValueError('Inputs changed during generation; no deployment')
    (output / 'report.json').write_text(json.dumps({'ok': True, 'production_activation': False,
        'input_sha256': before, 'output_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                               for p in output.glob('*.plist')}}, indent=2) + '\n')
    print('Inactive templates generated; no launchctl, token/key creation or production change.')


if __name__ == '__main__':
    main()

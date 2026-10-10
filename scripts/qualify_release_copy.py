"""Guarded installed 4.0.3 qualification on stable read-only JSONL snapshot.

No live-store engine, service control, consolidation, download, publication or
credentials read. A changing snapshot stops, never retries automatically.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import signal
import socket
import subprocess
import time
import urllib.request

from source_release_checks import resources

FILES = ('items.jsonl', 'blocks.jsonl', 'requests.jsonl', 'meta.json', 'embeddings.npy',
         'ids.json', 'chunk_map.json', 'bm25.json', 'entities.json')
AUTHORITATIVE = ('items.jsonl', 'blocks.jsonl', 'requests.jsonl')
DERIVED = {'meta.json', 'embeddings.npy', 'ids.json', 'chunk_map.json', 'bm25.json', 'entities.json'}


def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def inventory(source):
    if (source / 'store.sqlite3').exists():
        raise ValueError('This release drill is JSONL only; SQLite remains opt-in')
    result = {}
    for p in sorted(source.iterdir()):
        if p.name in {'writer.lock', 'server.log'} or p.name.startswith('server.log.'):
            continue
        name = p.name
        if p.is_symlink():
            raise ValueError('Symlink source refused')
        if p.exists():
            if not p.is_file():
                raise ValueError('Regular snapshot files required')
            result[name] = digest(p)
    if 'items.jsonl' not in result or 'meta.json' not in result:
        raise ValueError('Complete JSONL store required')
    return result


def snapshot(source, target):
    source, target = source.resolve(), target.absolute()
    if target.exists() or target.is_symlink() or target.is_relative_to(source) or source.is_relative_to(target):
        raise ValueError('Fresh, disjoint private copy required')
    before = inventory(source)
    if sum((source / name).stat().st_size for name in before) > 256_000_000:
        raise ValueError('Snapshot exceeds 256 MB cap; no automatic partial backup')
    target.mkdir(mode=0o700)
    for name in before:
        shutil.copyfile(source / name, target / name)
    if before != inventory(source) or before != inventory(target):
        raise RuntimeError('Source changed during copy; preserve evidence, stop without retry')
    return before


def stop(child):
    if child.poll() is None:
        os.killpg(child.pid, signal.SIGTERM)
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=5)


def run(command, cwd, env, log, timeout):
    resources(cwd)
    start = time.monotonic()
    with log.open('x') as f:
        child = subprocess.Popen(['/usr/bin/nice', '-n', '15', *command], cwd=cwd, env=env,
                                 stdout=f, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            while child.poll() is None:
                resources(cwd)
                if time.monotonic() - start > timeout:
                    raise RuntimeError('Copy qualification time cap exceeded')
                time.sleep(.5)
            if child.returncode:
                raise RuntimeError('Copy worker failed: inspect private log')
        finally:
            stop(child)


def compare(reports):
    baseline = reports['baseline']
    candidates = [reports[k] for k in ('wheel', 'sdist', 'wheel-warm', 'sdist-warm')]
    if any(r['total'] != baseline['total'] or not r['source_records_unchanged'] for r in candidates):
        raise ValueError('Source records/count changed')
    profiles = [r['encoding_profile'] for r in candidates]
    if any(p != profiles[0] for p in profiles[1:]):
        raise ValueError('Installed artifact/warm encoding profile differs')
    p = profiles[0]
    if (p['device'] != 'mps' or p['parameter_devices'] != ['mps'] or p['dtype'] != 'native'
            or p['batch_size'] != 32 or p['max_seq_length'] != 512 or not p['parameter_dtypes']
            or not p.get('model_revision') or not p.get('runtime')):
        raise ValueError('Candidate production encoder not attested')
    for key in ('device', 'parameter_dtypes', 'parameter_devices', 'dtype', 'batch_size', 'max_seq_length', 'runtime'):
        if p[key] != baseline['encoding_profile'][key]:
            raise ValueError('Installed baseline/candidate production compute profile differs: ' + key)
    if any(reports[k]['startup_encoded_texts'] != 0 for k in ('wheel-warm', 'sdist-warm')):
        raise ValueError('Warm reopen reencoded corpus')
    reference = reports['wheel']['search']
    if any({q: r['ids'] for q, r in candidate['search'].items()} != {q: r['ids'] for q, r in reference.items()}
           for candidate in candidates[1:]):
        raise ValueError('Wheel/sdist/warm search rankings differ')
    changed = [q for q in reference if reference[q]['ids'] != baseline['search'][q]['ids']]
    return {'search_identical_to_402': not changed, 'changed_queries': changed,
            'rankings_identical_between_artifacts_and_reopen': True,
            'encoding_profile': p, 'warm_encoded_texts': 0,
            'explanation': None if not changed else
            'Legacy vectors were unverified; 4.0.3 rebuilt all vectors with attested native/MPS/batch32. This does NOT prove quality equivalence. Reviewer explanation/GO required; publication guard stays closed.'}


def private_doctor(python, store, root, env, commit):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    if port in (8765, 8799):
        raise ValueError('Live port refused')
    token_file = root / 'private-http.token'
    token_file.write_text(os.urandom(32).hex())
    token_file.chmod(0o600)
    private = {**env, 'MEMORY_V4_DIR': str(store), 'MEMORY_V4_TOKEN_FILE': str(token_file),
               'MEMORY_V4_URL': f'http://127.0.0.1:{port}/mcp/', 'MEMORY_V4_BUILD_COMMIT': commit,
               'MEMORY_V4_OPS_DIR': str(root / 'ops'), 'MEMORY_V4_BACKUP_DIR': str(root / 'backups'),
               'MEMORY_V4_BACKUP_KEY': str(root / 'backup.key')}
    private.pop('MEMORY_V4_SERVICE_LABEL', None)
    code = ('import json; from pathlib import Path; from memory_os_v4.ops.doctor import run; '
            'from dataclasses import asdict; '
            'checks=run(home=Path("private-doctor-home")); '
            'print(json.dumps([asdict(c) for c in checks])); '
            'raise SystemExit(int(any(c.status=="FAIL" for c in checks)))')
    with (root / 'doctor-server.log').open('x') as log:
        child = subprocess.Popen(['/usr/bin/nice', '-n', '15', str(python), '-m', 'memory_os_v4.server',
                                  '--transport', 'http', '--host', '127.0.0.1', '--port', str(port), '--warm'],
                                 cwd=root, env=private, stdout=log, stderr=log, start_new_session=True)
        try:
            deadline = time.monotonic() + 300
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            token = token_file.read_text()
            while child.poll() is None:
                resources(root)
                if time.monotonic() > deadline:
                    raise RuntimeError('Private doctor server startup timed out')
                try:
                    req = urllib.request.Request(f'http://127.0.0.1:{port}/ready', headers={'Authorization': 'Bearer ' + token})
                    with opener.open(req, timeout=1) as response:
                        ready = json.loads(response.read())
                    if ready.get('ready') and ready.get('version') == '4.0.3' and ready.get('commit') == commit:
                        break
                except (OSError, ValueError):
                    pass
                time.sleep(.2)
            else:
                raise RuntimeError('Private server exited')
            run([str(python), '-c', code], root, private, root / 'doctor.json', 30)
            return {'ok': True, 'port': port, 'doctor_sha256': digest(root / 'doctor.json'), 'ready': ready,
                    'scope': 'private copied store/server; missing private backups/launchd/clients are WARN, not production doctor'}
        finally:
            stop(child)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--environments', type=Path, required=True)
    parser.add_argument('--output-root', type=Path, required=True)
    parser.add_argument('--source-dir', type=Path, default=Path.home() / '.memory-os-v4')
    parser.add_argument('--baseline-python', type=Path, default=Path.home() / '.memory-os-runtime/venv-4.0.2/bin/python')
    parser.add_argument('--production-plist', type=Path, default=Path.home() / 'Library/LaunchAgents/com.romain.memory-os-v4.plist')
    a = parser.parse_args(argv)
    clone = Path(__file__).resolve().parents[1]
    root = a.output_root.resolve()
    if not root.is_relative_to(clone / '.local-qualification') or root.exists():
        raise ValueError('Fresh output strictly inside private clone required')
    manifest = json.loads(a.manifest.read_text())
    if manifest['version'] != '4.0.3':
        raise ValueError('Candidate version must be 4.0.3')
    from verify_release_manifest import validate_local
    validate_local(manifest, a.manifest.parent, require_source=False, require_copy=False)
    envs = json.loads(a.environments.read_text())
    plist = plistlib.loads(a.production_plist.read_bytes())
    # Inherit compute/retrieval settings, never the live token/store/log/service.
    settings = {k: v for k, v in plist.get('EnvironmentVariables', {}).items()
                if k.startswith(('MEMORY_V4_', 'PYTORCH_')) and k not in
                {'MEMORY_V4_DIR', 'MEMORY_V4_TOKEN_FILE', 'MEMORY_V4_BUILD_COMMIT', 'MEMORY_V4_PROJECT', 'MEMORY_V4_URL', 'MEMORY_V4_SERVICE_LABEL'}}
    env = {k: v for k, v in os.environ.items() if not k.startswith(('MEMORY_', 'OPENAI_', 'ANTHROPIC_', 'PYTORCH_'))}
    env.update(settings, PYTHONPATH='', PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1',
               HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1', PYTHONHASHSEED='0',
               OMP_NUM_THREADS='1', TOKENIZERS_PARALLELISM='false', PYTORCH_ENABLE_MPS_FALLBACK='0')
    resources(clone)
    root.mkdir(mode=0o700)
    (root / 'copy-owner.json').write_text(json.dumps({'source_commit': manifest['source_commit'], 'production_change': False}))
    report = {'ok': False, 'version': '4.0.3', 'source_commit': manifest['source_commit'],
              'artifacts': manifest['artifacts'], 'production_modified': False, 'reports': {},
              'scripts_sha256': {n: digest(Path(__file__).with_name(f)) for n, f in
                                [('runner', 'qualify_release_copy.py'), ('worker', 'release_copy_worker.py')]}}
    try:
        if report['scripts_sha256'] != manifest['copy_scripts_sha256']:
            raise ValueError('Copy scripts changed since artifact qualification')
        original = snapshot(a.source_dir, root / 'snapshot')
        report['snapshot_sha256'] = original
        report['baseline_profile_was_absent'] = 'embed_compute_profile' not in json.loads((root / 'snapshot/meta.json').read_text())
        shutil.copyfile(Path(__file__).with_name('release_copy_worker.py'), root / 'worker.py')
        anchor = time.time()  # identical time for all private searches; no annotation
        for kind, version, python, store_kind in [
            ('baseline', '4.0.2', a.baseline_python, 'baseline'),
            ('wheel', '4.0.3', Path(envs['wheel_environment']) / 'bin/python', 'wheel'),
            ('wheel-warm', '4.0.3', Path(envs['wheel_environment']) / 'bin/python', 'wheel'),
            ('sdist', '4.0.3', Path(envs['sdist_environment']) / 'bin/python', 'sdist'),
            ('sdist-warm', '4.0.3', Path(envs['sdist_environment']) / 'bin/python', 'sdist')]:
            store = root / (store_kind + '-store')
            if not store.exists():
                shutil.copytree(root / 'snapshot', store)
            command = [str(python), str(root / 'worker.py'), '--store', str(store), '--owner-root', str(root),
                       '--output', str(root / (kind + '.json')), '--version', version,
                       '--kind', 'baseline' if kind == 'baseline' else 'warm' if kind.endswith('-warm') else 'candidate',
                       '--at-time', str(anchor)]
            run(command, root, {**env, 'MEMORY_V4_DIR': str(store)}, root / (kind + '.log'), 1200)
            report['reports'][kind] = json.loads((root / (kind + '.json')).read_text())
            for name in set(original) - DERIVED:
                if digest(store / name) != original[name]:
                    raise RuntimeError('Authoritative log changed on copy: ' + name)
            old_meta = json.loads((root / 'snapshot/meta.json').read_text())
            new_meta = json.loads((store / 'meta.json').read_text())
            allowed = {'version', 'dim', 'embed_model', 'embed_compute_profile', 'bm25_profile'}
            if {k: v for k, v in old_meta.items() if k not in allowed} != {k: v for k, v in new_meta.items() if k not in allowed}:
                raise RuntimeError('Non-cache metadata changed on copy')
        report.update(compare(report['reports']))
        report['doctor'] = private_doctor(Path(envs['wheel_environment']) / 'bin/python', root / 'wheel-store', root, env, manifest['source_commit'])
        if inventory(root / 'snapshot') != original:
            raise RuntimeError('Read-only master snapshot changed')
        report['ok'] = report['doctor']['ok']
        report['search_review_required'] = not report['search_identical_to_402']
        if report['search_review_required']:
            report['warning'] = 'Changed legacy search rankings require explicit review; guard remains closed'
    except Exception as exc:
        report['error'] = str(exc)
    (root / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'ok': report['ok'], 'report': str(root / 'report.json'), 'production_modified': False}))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

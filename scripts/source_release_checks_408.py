#!/usr/bin/env python3
"""Claude outside sandbox: source TCP + one cached MiniLM test, guarded and sequential.

Synthetic stores only. Never downloads, touches a live service, or publishes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resources(root):
    text = subprocess.check_output(['/usr/sbin/sysctl', '-n', 'vm.swapusage'], text=True, timeout=3)
    match = re.search(r'used\s*=\s*([0-9.]+)([KMG])', text)
    if not match:
        raise RuntimeError('Swap measurement unavailable; no test may start')
    swap = float(match[1]) * {'K': 1024, 'M': 1024 ** 2, 'G': 1024 ** 3}[match[2]]
    free = shutil.disk_usage(root).free
    if swap > 8_500_000_000 or free < __import__('memory_os_v4.ops.provision', fromlist=['min_free_bytes']).min_free_bytes():
        raise RuntimeError(f'Resource guard stopped: swap={int(swap)}, free={free}')
    return {'swap_bytes': int(swap), 'free_bytes': free}


def junit_counts(path):
    tree = ET.parse(path).getroot()
    suites = [tree] if tree.tag == 'testsuite' else list(tree.iter('testsuite'))
    counts = {name: sum(int(s.get(name, '0')) for s in suites) for name in ('tests', 'failures', 'errors', 'skipped')}
    return {'passed': counts['tests'] - counts['failures'] - counts['errors'] - counts['skipped'],
            'failed': counts['failures'], 'errors': counts['errors'], 'skipped': counts['skipped']}


def main():
    if not __debug__:
        raise RuntimeError('Assertions required; do not use Python -O')
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    directory = args.manifest.resolve().parent
    manifest = json.loads(args.manifest.read_text())
    if manifest['version'] != '4.0.8':
        raise ValueError('This source qualification targets 4.0.8')
    files = manifest['qualification_source_sha256']
    if any(digest(root / name) != sha for name, sha in files.items()):
        raise ValueError('Source files differ from qualified artifacts')
    report = {'ok': False, 'version': manifest['version'], 'source_commit': manifest['source_commit'],
              'source_sha256': files, 'checks': {}, 'samples': [], 'evidence_sha256': {}}
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONOPTIMIZE': '0',
           'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'OMP_NUM_THREADS': '1',
           'OPENBLAS_NUM_THREADS': '1', 'VECLIB_MAXIMUM_THREADS': '1',
           'PYTHONPATH': str(root / 'src') + ':' + str(root / 'v4/src') + ':' + str(root / 'v4'),
           'MEMORY_TEST_HASH_V3': '0', 'KMP_DUPLICATE_LIB_OK': 'TRUE',
           'MEMORY_WORKSPACE': str(root), 'MEMORY_V4_BUILD_COMMIT': manifest['source_commit']}
    suites = [('source_tcp', ['v4/tests/test_server_http_e2e.py', 'v4/tests/test_ops_e2e.py'], 10),
              ('cached_semantic', ['v4/tests/test_core_slow_integration.py'], 1)]
    if manifest['version'] == '4.0.8':
        suites.append(('v3_cached', ['tests'], manifest['source_expected_v3_tests']))
    if manifest['version'] == '4.0.8':
        suites[0] = ('source_tcp', [*suites[0][1], 'v4/tests/test_l3_tcp.py'], 32)
        table = os.environ.get('MEMORY_V4_CL100K_PATH', '')
        reserve = os.environ.get('MEMORY_V4_MIN_FREE_BYTES', '5000000000')
        env = {k: v for k, v in env.items() if not k.startswith('MEMORY_V4_')}
        env.update(MEMORY_V4_MIN_FREE_BYTES=reserve, MEMORY_V4_CL100K_PATH=table, MEMORY_V4_BUILD_COMMIT=manifest['source_commit'],
                   MEMORY_V4_DIR=str(directory / 'unused-store'), MEMORY_V4_OPS_DIR=str(directory / 'ops'))
        env.pop('PYTEST_ADDOPTS', None)
    if args.output.exists():
        raise ValueError('Fresh proof required; preserve earlier evidence')
    from qualify_405_test_isolation import operator_home, snapshots, unreadable
    before = snapshots(operator_home())
    report['client_configs_before'] = before
    try:
        if unreadable(before):
            raise RuntimeError('Unreadable client config')
        for name, paths, count in suites:
            xml = directory / (name + '.xml')
            log_path = directory / (name + '.log')
            report['samples'].append(resources(root))  # before model import or network bind
            command = ['/usr/bin/nice', '-n', '15', sys.executable, '-m', 'pytest', '-c', 'pyproject.toml',
                       '-q', '-p', 'no:cacheprovider', '--basetemp=' + str(directory / (name + '-tests')),
                       '--junitxml=' + str(xml), *paths]
            with log_path.open('x') as log:
                process = subprocess.Popen(command, cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT,
                                           start_new_session=True)
                try:
                    started = time.monotonic()
                    while process.poll() is None:
                        report['samples'].append(resources(root))
                        if time.monotonic() - started > 300:
                            raise RuntimeError(name + ' exceeded 300 s')
                        time.sleep(2)
                finally:
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGTERM)
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            os.killpg(process.pid, signal.SIGKILL)
                            process.wait(timeout=5)
            if name == 'source_tcp':
                from qualify_405_test_isolation import tcp_evidence
                report['tcp_evidence'] = tcp_evidence(list(ET.parse(xml).getroot().iter('testcase')))
                if not report['tcp_evidence']['ok']:
                    raise RuntimeError('TCP exact-name manifest mismatch')
            result = junit_counts(xml)
            report['checks'][name] = result
            report['evidence_sha256'].update({xml.name: digest(xml), log_path.name: digest(log_path)})
            if process.returncode or result != {'passed': count, 'failed': 0, 'errors': 0, 'skipped': 0}:
                raise RuntimeError(name + ' failed/incomplete; see log')
            print(f'{name}: {count} passed, 0 skipped', flush=True)
        # Detect any concurrent source edit as well as an initial mismatch.
        if any(digest(root / name) != sha for name, sha in files.items()):
            raise RuntimeError('Source changed during checks')
        after = snapshots(operator_home())
        report['client_configs_after'] = after
        report['client_configs_unchanged'] = before == after and not unreadable(after)
        if not report['client_configs_unchanged']:
            raise RuntimeError('Client MCP config changed')
        report['ok'] = True
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, ET.ParseError) as exc:
        report['error'] = str(exc)
        print('REFUS: ' + str(exc), flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

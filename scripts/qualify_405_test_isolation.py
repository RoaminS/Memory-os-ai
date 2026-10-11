#!/usr/bin/env python3
"""Fresh, sequential hash-only suite evidence and read-only client-config checks."""
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

from test_home_guard import operator_home

ROOT = Path(__file__).resolve().parents[1]
TCP = ['v4/tests/test_server_http_e2e.py', 'v4/tests/test_ops_e2e.py', 'v4/tests/test_l3_tcp.py']


def snapshots(home):
    # Fingerprints only: never serialize config contents or credentials.
    from memory_os_v4.ops.clients import CLIENTS
    paths = [home / client.path for client in CLIENTS]
    paths += [home / '.codex/mcp.json', home / 'AGENTS.md', ROOT / '.mcp.json',
              ROOT / '.vscode/mcp.json', ROOT / 'CLAUDE.md', ROOT / 'AGENTS.md']
    result = {}
    for path in paths:
        try:
            stat = path.stat()
            result[str(path)] = {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                                 'mtime_ns': stat.st_mtime_ns, 'inode': stat.st_ino,
                                 'size': stat.st_size}
        except FileNotFoundError:
            result[str(path)] = None
    return result


def resource_sample(require_swap):
    free = shutil.disk_usage(ROOT).free
    swap = None
    if sys.platform == 'darwin':
        probe = subprocess.run(['/usr/sbin/sysctl', '-n', 'vm.swapusage'], capture_output=True,
                               text=True, timeout=5)
        match = re.search(r'used\s*=\s*([0-9.]+)([KMG])', probe.stdout)
        if probe.returncode == 0 and match:
            swap = float(match[1]) * {'K': 1024, 'M': 1024 ** 2, 'G': 1024 ** 3}[match[2]]
    if free < 5_000_000_000 or (swap is not None and swap > 8_500_000_000):
        raise RuntimeError(f'Machine guard stopped: disk={free}, swap={swap}')
    if require_swap and swap is None:
        raise RuntimeError('Swap unavailable; full outside-sandbox qualification refused')
    return {'free_bytes': free, 'swap_bytes': swap}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--suite', choices=('unit', 'full'), default='unit')
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / '.local-qualification'):
        parser.error('Fresh output inside .local-qualification required')
    output.mkdir(parents=True, exist_ok=False)
    report = {'ok': False, 'suite': args.suite, 'commands': [], 'samples': [],
              'neural_inference': False, 'tcp_qualified': False, 'release_qualified': False}
    before = snapshots(operator_home())
    report['configs_before'] = before
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'MEMORY_TEST_HASH_V3': '1',
           'MEMORY_V4_EMBED_MODEL': 'hash',
           'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'OMP_NUM_THREADS': '1',
           'OPENBLAS_NUM_THREADS': '1', 'VECLIB_MAXIMUM_THREADS': '1',
           'PYTHONPATH': os.pathsep.join(str(ROOT / p) for p in ('src', 'v4/src', 'v4'))}
    env.pop('PYTEST_ADDOPTS', None)
    cases = [('v3', ['tests']), ('v4', ['v4/tests', '-m', 'not slow'])]
    if args.suite == 'unit':
        cases[1][1].extend('--ignore=' + p for p in TCP)
    try:
        for name, paths in cases:
            command = [sys.executable, '-B', '-m', 'pytest', '-c', str(ROOT / 'pyproject.toml'),
                       '-q', '-p', 'no:cacheprovider', '-o', 'faulthandler_timeout=60',
                       '--basetemp=' + str(output / (name + '-tmp')),
                       '--junitxml=' + str(output / (name + '.xml')), *paths]
            report['samples'].append(resource_sample(args.suite == 'full'))
            start = time.monotonic()
            with (output / (name + '.log')).open('x') as log:
                process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log,
                                           stderr=subprocess.STDOUT, start_new_session=True)
                try:
                    while process.poll() is None:
                        report['samples'].append(resource_sample(args.suite == 'full'))
                        limit = 900 if args.suite == 'full' else 300
                        if time.monotonic() - start > limit:
                            raise RuntimeError(name + f' exceeded {limit} s')
                        time.sleep(1)
                finally:
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGTERM)
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            os.killpg(process.pid, signal.SIGKILL)
                            process.wait(timeout=5)
            report['commands'].append({'name': name, 'argv': command,
                                       'returncode': process.returncode, 'seconds': time.monotonic() - start})
            if process.returncode:
                raise RuntimeError(name + ' failed; evidence preserved')
            suites = list(ET.parse(output / (name + '.xml')).getroot().iter('testsuite'))
            counts = {k: sum(int(node.get(k, '0')) for node in suites)
                      for k in ('tests', 'failures', 'errors', 'skipped')}
            if name == 'v4':
                cases_xml = list(ET.parse(output / (name + '.xml')).getroot().iter('testcase'))
                counts['tcp_cases'] = sum(c.get('classname', '').split('.')[-1] in
                                         {'test_server_http_e2e', 'test_ops_e2e', 'test_l3_tcp'}
                                         for c in cases_xml)
                if args.suite == 'full' and counts['tcp_cases'] != 24:
                    raise RuntimeError('The 24 physical TCP cases are required')
            report['commands'][-1]['counts'] = counts
            minimum = 585 if name == 'v3' else (1606 if args.suite == 'full' else 1582)
            if counts['tests'] < minimum or any(counts[k] for k in ('failures', 'errors', 'skipped')):
                raise RuntimeError(name + ' incomplete; evidence preserved')
        report['ok'] = True
        report['tcp_qualified'] = args.suite == 'full'
    except (OSError, RuntimeError, subprocess.SubprocessError, ET.ParseError) as exc:
        report['error'] = str(exc)
    except KeyboardInterrupt:
        report['error'] = 'Interrupted; incomplete evidence preserved'
    finally:
        after = snapshots(operator_home())
        report['configs_after'] = after
        report['changed_configs'] = [p for p in before if before[p] != after[p]]
        report['client_configs_unchanged'] = not report['changed_configs']
        if report['changed_configs']:
            report['ok'] = False
            report['error'] = 'Client config changed; attribution must be investigated before release'
        paths = [ROOT / 'conftest.py', ROOT / 'MANIFEST.in',
                 *sorted((ROOT / 'scripts').glob('*.py')), *sorted((ROOT / 'tests').rglob('*.py')),
                 *sorted((ROOT / 'v4/tests').rglob('*.py')), *sorted((ROOT / 'src').rglob('*.py')),
                 *sorted((ROOT / 'v4/src').rglob('*.py'))]
        report['source_sha256'] = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                   for p in paths}
        (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: report.get(k) for k in ('ok', 'client_configs_unchanged', 'tcp_qualified', 'error')}))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

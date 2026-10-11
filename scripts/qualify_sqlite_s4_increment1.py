#!/usr/bin/env python3
"""Sequential Hash-only S4 unit evidence; optional one real B09 TCP case.

Real machine guard for physical TCP qualification; tiny synthetic units bounded
by their fixtures do not require a sandbox-unavailable swap probe. No models/downloads,
store migration, production service control or publication.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

from qualify_405_test_isolation import resource_sample, snapshots, operator_home

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--tcp', action='store_true', help='Claude outside sandbox: include physical B09')
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / '.local-qualification'):
        parser.error('Fresh output in .local-qualification required')
    output.mkdir(parents=True, exist_ok=False)
    report = {'ok': False, 'inference': False, 'production_change': False, 'tests_started': False,
              'tcp_qualified': False, 's4_activation_qualified': False, 'samples': []}
    before = None
    try:
        if args.tcp:
            report['samples'].append(resource_sample(True))
        report['resource_policy'] = 'physical_tcp_guard' if args.tcp else 'synthetic_unit_caps'
        before = snapshots(operator_home())
        paths = ['v4/tests/s4', 'v4/tests/test_relay_preflight_405.py']
        if args.tcp:
            paths += ['v4/tests/test_l3_tcp.py::test_S4_B09_read_only_window_keeps_reads_and_never_sends_mutation',
                      'v4/tests/test_l3_tcp.py::test_S4_B10_real_private_sqlite_restore_reconciles_forgets_and_reopens']
        command = [sys.executable, '-B', '-m', 'pytest', '-c', str(ROOT / 'pyproject.toml'), '-q',
                   '-p', 'no:cacheprovider', '--basetemp=' + str(output / 'pytest-tmp'),
                   '--junitxml=' + str(output / 'tests.xml'), *paths]
        env = {**os.environ, 'PYTHONPATH': os.pathsep.join(str(ROOT / p) for p in ('src', 'v4/src', 'v4')),
               'PYTHONDONTWRITEBYTECODE': '1', 'MEMORY_V4_EMBED_MODEL': 'hash',
               'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'OMP_NUM_THREADS': '1',
               'OPENBLAS_NUM_THREADS': '1', 'VECLIB_MAXIMUM_THREADS': '1'}
        env.pop('PYTEST_ADDOPTS', None)
        start = time.monotonic()
        with (output / 'pytest.log').open('x') as log:
            process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            report.update(tests_started=True, command=command)
            try:
                while process.poll() is None:
                    if args.tcp:
                        report['samples'].append(resource_sample(True))
                    if time.monotonic() - start > 300:
                        raise RuntimeError('S4 lightweight tests exceeded 300 s')
                    time.sleep(1)
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=5)
        report['returncode'] = process.returncode
        report['seconds'] = time.monotonic() - start
        suites = list(ET.parse(output / 'tests.xml').getroot().iter('testsuite'))
        counts = {k: sum(int(s.get(k, 0)) for s in suites) for k in ('tests', 'failures', 'errors', 'skipped')}
        report['counts'] = counts
        if process.returncode or counts['tests'] < (86 if args.tcp else 84) or any(counts[k] for k in ('failures', 'errors', 'skipped')):
            raise RuntimeError('S4 suite incomplete/failed; evidence preserved')
        report.update(ok=True, tcp_qualified=args.tcp)
    except (OSError, RuntimeError, subprocess.SubprocessError, ET.ParseError) as exc:
        report['error'] = str(exc)
    finally:
        if before is not None:
            after = snapshots(operator_home())
            report['client_configs_unchanged'] = before == after
            if before != after:
                report.update(ok=False, error='Client config changed')
        report['source_sha256'] = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                   for base in ('v4/src', 'v4/tests', 'scripts')
                                   for p in sorted((ROOT / base).rglob('*.py'))}
        (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: report.get(k) for k in ('ok', 'tests_started', 'tcp_qualified', 'error')}))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

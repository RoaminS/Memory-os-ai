#!/usr/bin/env python3
"""Claude-only network resolution gate BEFORE clean install, with disk/swap guards.

Explicit invocation resolves public PyPI wheel metadata; never installs or
loads a model. Private HOME/TMP, no assistant credentials or API calls. Failed
evidence is retained. A passing resolution does not qualify installed usage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tomllib

from check_runtime_resolution import check
from qualify_distribution_d1a import command
from memory_os_v4.ops.provision import guard, min_free_bytes

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheel', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    wheel, output = args.wheel.resolve(), args.output.resolve()
    if output.exists() or not output.is_relative_to(ROOT / '.local-qualification'):
        parser.error('Fresh output below clone .local-qualification required')
    minimum = min_free_bytes()
    guard(output.parent)
    output.mkdir(parents=True, mode=0o700)
    for name in ('home', 'tmp'):
        (output / name).mkdir(mode=0o700)
    env = {name: os.environ[name] for name in ('PATH', 'LANG') if name in os.environ}
    env.update(HOME=str(output / 'home'), TMPDIR=str(output / 'tmp'),
               XDG_CONFIG_HOME=str(output / 'home/.config'),
               XDG_CACHE_HOME=str(output / 'home/.cache'),
               PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1', PIP_CONFIG_FILE=os.devnull)
    result = {'ok': False, 'model_calls': 0, 'api_calls': 0, 'installed': False,
              'min_free_bytes': minimum}
    try:
        os.setpriority(os.PRIO_PROCESS, 0, 15)
        result['command'] = command([sys.executable, '-B', '-m', 'pip', 'install', '--dry-run',
            '--ignore-installed', '--only-binary=:all:', '--disable-pip-version-check', '--no-cache-dir',
            '--index-url', 'https://pypi.org/simple', '--report', str(output / 'pip-report.json'),
            str(wheel) + '[ops,brief]'], output, env, output / 'pip-resolution.log', guard)
        version = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['version']
        result.update(check(json.loads((output / 'pip-report.json').read_text()), version, wheel=wheel))
        result['wheel_sha256'] = hashlib.sha256(wheel.read_bytes()).hexdigest()
        result['pip_report_sha256'] = hashlib.sha256((output / 'pip-report.json').read_bytes()).hexdigest()
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        result['error'] = type(exc).__name__ + ': ' + str(exc)
    (output / 'report.json').write_text(json.dumps(result, sort_keys=True, indent=2) + '\n')
    print(json.dumps(result, sort_keys=True))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

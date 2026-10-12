#!/usr/bin/env python3
"""Read installed qualified METADATA only; seal the candidate's active closure offline.

Run with the qualified runtime's Python. No imports of MCP/torch/models, no
network and no installation. Output must be a new file in the operator's clone.
"""
from __future__ import annotations

import argparse
import hashlib
from importlib import metadata
import json
from pathlib import Path
import tomllib

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def closure(roots, environment, distribution=metadata.distribution):
    pending = [(Requirement(text), ('',)) for text in roots]
    records, seen = {}, set()
    while pending:
        req, parent_extras = pending.pop()
        if req.marker and not any(req.marker.evaluate({**environment, 'extra': e}) for e in parent_extras):
            continue
        name = canonicalize_name(req.name)
        if req.url or name == 'memory-os-ai':
            raise ValueError('Direct URLs/self references are not a qualified closure root')
        dist = distribution(name)
        if not req.specifier.contains(dist.version, prereleases=True):
            raise ValueError(f'Qualified metadata does not satisfy {req}')
        raw = dist.read_text('METADATA')
        records[name] = {'version': dist.version, 'requires': list(dist.requires or []),
                         'metadata_sha256': hashlib.sha256(raw.encode()).hexdigest()}
        extras = tuple(sorted({'', *req.extras}))
        key = (name, extras)
        if key not in seen:
            seen.add(key)
            pending.extend((Requirement(text), extras) for text in dist.requires or [])
    return dict(sorted(records.items()))


def seal(project, environment, distribution=metadata.distribution):
    core = closure(project['dependencies'], environment, distribution)
    groups = {}
    all_records = dict(core)
    for extra in ('ops', 'brief'):
        records = closure(project['dependencies'] + project['optional-dependencies'][extra], environment, distribution)
        groups[extra] = {n: d['version'] for n, d in records.items() if n not in core}
        all_records.update(records)
    return {'schema_version': 1, 'scope': 'macOS arm64 CPython 3.11; core + ops + brief only',
            'marker_environment': environment, 'versions': {n: d['version'] for n, d in core.items()},
            'extra_versions': groups, 'distribution_metadata': dict(sorted(all_records.items()))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    env = default_environment()
    if (env['sys_platform'], env['platform_machine'], env['python_version']) != ('darwin', 'arm64', '3.11'):
        parser.error('Qualified macOS arm64 Python 3.11 required')
    project = tomllib.loads(args.project.read_text())['project']
    record = seal(project, env)
    record['basis'] = 'Installed qualified 4.0.5 runtime METADATA; closure of candidate requirements, not pip freeze'
    record['source_package_version'] = metadata.version('memory-os-ai')
    if record['source_package_version'] != '4.0.5':
        parser.error('Expected qualified source runtime 4.0.5')
    with args.output.open('x') as stream:
        stream.write(json.dumps(record, sort_keys=True, indent=2) + '\n')
    print(json.dumps({'core': len(record['versions']), 'extra_versions': record['extra_versions']}))


if __name__ == '__main__':
    main()

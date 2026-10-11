#!/usr/bin/env python3
"""Offline gate: extra names must not parse as versions; all wheel markers must evaluate."""
from __future__ import annotations

import argparse
from email.parser import BytesParser
import importlib
import json
from pathlib import Path
import tomllib
import zipfile


def check_names(names, version_module):
    for name in names:
        try:
            version_module.Version(name)
        except version_module.InvalidVersion:
            continue
        raise ValueError(f"Extra name parses as a PEP 440 version: {name}; use a descriptive name")


def wheel_markers(path):
    with zipfile.ZipFile(path) as archive:
        members = [n for n in archive.namelist() if n.endswith('.dist-info/METADATA')]
        if len(members) != 1:
            raise ValueError('Expected exactly one wheel METADATA')
        metadata = BytesParser().parsebytes(archive.read(members[0]))
    extras = metadata.get_all('Provides-Extra', [])
    requirements = metadata.get_all('Requires-Dist', [])
    proofs = {}
    for implementation in ('packaging', 'pip._vendor.packaging'):
        versions = importlib.import_module(implementation + '.version')
        parse = importlib.import_module(implementation + '.requirements').Requirement
        check_names(extras, versions)
        evaluations = 0
        for text in requirements:
            requirement = parse(text)
            if requirement.marker:
                for extra in ['', *extras]:
                    value = requirement.marker.evaluate({'extra': extra})
                    if not isinstance(value, bool):
                        raise ValueError('Non-boolean marker evaluation')
                    evaluations += 1
        proofs[implementation] = evaluations
    return {'ok': True, 'wheel': path.name, 'extras': extras, 'marker_evaluations': proofs}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pyproject', type=Path)
    p.add_argument('--wheel', type=Path, action='append', default=[])
    args = p.parse_args(argv)
    if not args.pyproject and not args.wheel:
        p.error('Select --pyproject and/or --wheel')
    if args.pyproject:
        meta = tomllib.loads(args.pyproject.read_text())
        check_names(meta['project'].get('optional-dependencies', {}), importlib.import_module('packaging.version'))
    for wheel in args.wheel:
        print(json.dumps(wheel_markers(wheel), sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

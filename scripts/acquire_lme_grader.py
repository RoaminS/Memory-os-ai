#!/usr/bin/env python3
"""Acquire only the small official grading source, never a dataset/model/API.

Run by Claude outside the sandbox. Fresh private directory; failed evidence kept.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'v4'))
from bench.e2e.answer_campaign import atomic_json  # noqa: E402
from bench.e2e.data import sha256  # noqa: E402
from bench.e2e.experiments import private_path  # noqa: E402
from bench.e2e.guard import Limits, ensure_priority, sample  # noqa: E402
from bench.e2e.lme_grading import source_templates  # noqa: E402

URL = 'https://raw.githubusercontent.com/xiaowu0162/LongMemEval/main/src/evaluation/evaluate_qa.py'


def acquire(directory, opener=urllib.request.urlopen, check=None):
    _, directory = private_path(directory)
    check = check or (lambda: sample(ROOT))
    check()
    directory.mkdir(mode=0o700, exist_ok=False)
    path = directory / 'evaluate_qa.py'
    try:
        with opener(URL, timeout=20) as response:
            if response.geturl() != URL:
                raise ValueError('Unexpected official-source redirect')
            declared = response.headers.get('Content-Length')
            if declared is not None and not 0 < int(declared) <= 20000:
                raise ValueError('Official source physical size exceeds 20 KB bound')
            size = 0
            with path.open('xb') as stream:
                while block := response.read(2048):
                    check()
                    size += len(block)
                    if size > 20000:
                        raise ValueError('Official source exceeds 20 KB stream bound')
                    stream.write(block)
            if not size or declared is not None and int(declared) != size:
                raise ValueError('Official source truncated or empty')
        templates = source_templates(path)
        provenance = {'url': URL, 'sha256': sha256(path), 'bytes': size,
                      'retrieved_at': datetime.now(timezone.utc).isoformat(), 'licence': 'MIT, Di Wu 2024',
                      'upstream_git_revision': None, 'pinning': 'downloaded source SHA plus literal templates sealed BEFORE inference',
                      'script_sha256': sha256(Path(__file__)), 'model_calls': 0, 'api_calls': 0,
                      'dataset_downloads': 0, 'model_downloads': 0, 'template_names': sorted(templates),
                      'execution': 'AST literals only; upstream imports/API client NEVER executed'}
        atomic_json(directory / 'PROVENANCE.json', provenance)
        return provenance
    except BaseException as exc:
        atomic_json(directory / 'FAILED.json', {'reason': str(exc), 'preserved': True, 'model_calls': 0})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    ensure_priority(Limits())
    print(json.dumps(acquire(args.out)))


if __name__ == '__main__':
    main()

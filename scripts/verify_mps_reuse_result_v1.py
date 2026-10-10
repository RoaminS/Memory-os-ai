#!/usr/bin/env python3
"""Attest the completed failed equivalence gate; never re-score predictions."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def load(path):
    return json.loads(path.read_text())


def verify(evidence, summary):
    expected = load(summary)
    for name, digest in expected['files'].items():
        if hashlib.sha256((evidence / name).read_bytes()).hexdigest() != digest:
            raise ValueError('Frozen evidence changed: ' + name)
    report = load(evidence / 'report.json')
    plan = load(evidence / 'plan.json')
    receipt = load(evidence / 'receipt.json')
    if (report != expected['report'] or plan['source_commit'] != expected['source_commit'] or
            receipt['plan_sha256'] != expected['plan_sha256']):
        raise ValueError('Summary/source/receipt mismatch')
    if (not report['complete'] or report['criterion_met'] or report['quality']['equivalent'] or
            not report['performance_met'] or expected['verdict'] != 'equivalence_not_demonstrated'):
        raise ValueError('This attestation must preserve the original failed equivalence verdict')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('evidence_dir', type=Path)
    parser.add_argument('--summary', type=Path,
        default=Path(__file__).resolve().parents[1] / 'v4/bench/results/mps_beam_reuse_v1_summary.json')
    args = parser.parse_args()
    report = verify(args.evidence_dir, args.summary)
    print(json.dumps({'verified': True, 'complete': report['complete'],
        'performance_met': report['performance_met'], 'equivalent': False, 'criterion_met': False,
        'posthoc_rescoring': False, 'production_activation_performed': False,
        'production_state_inspected': False}, indent=2))


if __name__ == '__main__':
    main()

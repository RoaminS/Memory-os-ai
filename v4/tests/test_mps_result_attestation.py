"""A frozen failure must survive reporting; no model or score computation."""
import importlib.util
import json
import hashlib
from pathlib import Path

import pytest

path = Path(__file__).resolve().parents[2] / 'scripts/verify_mps_reuse_result_v1.py'
spec = importlib.util.spec_from_file_location('mps_result_attestation', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fixture(root):
    report = {'complete': True, 'performance_met': True, 'criterion_met': False,
              'quality': {'equivalent': False}}
    contents = {'report.json': report, 'plan.json': {'source_commit': 'frozen'},
                'receipt.json': {'plan_sha256': 'hash'}}
    for name, value in contents.items():
        (root / name).write_text(json.dumps(value))
    summary = root / 'summary.json'
    summary.write_text(json.dumps({'report': report, 'source_commit': 'frozen',
        'plan_sha256': 'hash', 'verdict': 'equivalence_not_demonstrated',
        'files': {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in contents}}))
    return summary


def test_completed_performance_success_still_attests_equivalence_failure(tmp_path):
    summary = fixture(tmp_path)
    assert module.verify(tmp_path, summary)['criterion_met'] is False


def test_edited_report_is_rejected_without_rescoring(tmp_path):
    summary = fixture(tmp_path)
    (tmp_path / 'report.json').write_text('{}')
    with pytest.raises(ValueError, match='Frozen evidence changed'):
        module.verify(tmp_path, summary)


def test_accidentally_regenerated_success_cannot_pass_failure_attestation(tmp_path):
    summary = fixture(tmp_path)
    data = json.loads(summary.read_text())
    data['report']['criterion_met'] = True
    p = tmp_path / 'report.json'
    p.write_text(json.dumps(data['report']))
    data['files']['report.json'] = hashlib.sha256(p.read_bytes()).hexdigest()
    summary.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='original failed equivalence'):
        module.verify(tmp_path, summary)

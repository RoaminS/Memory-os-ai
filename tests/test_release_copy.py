"""Copy safety and conservative release gate; no production access/ML/TCP."""
import importlib.util
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
spec = importlib.util.spec_from_file_location('copy_drill', Path(__file__).parents[1] / 'scripts/qualify_release_copy.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_snapshot_never_modifies_source_or_copies_symlinks(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'items.jsonl').write_text('{"id":"synthetic"}\n')
    (source / 'meta.json').write_text('{}')
    (source / 'server.log').write_text('never copied')
    before = module.inventory(source)
    target = tmp_path / 'copy'
    assert module.snapshot(source, target) == before
    assert module.inventory(source) == before
    assert not (target / 'server.log').exists()
    with pytest.raises(ValueError):
        module.snapshot(source, source / 'child')
    with pytest.raises(ValueError):
        module.snapshot(source, target)
    (source / 'blocks.jsonl').symlink_to(source / 'items.jsonl')
    with pytest.raises(ValueError, match='Symlink'):
        module.inventory(source)


def test_changing_source_stops_before_opening_an_engine(tmp_path, monkeypatch):
    source, target = tmp_path / 'source', tmp_path / 'copy'
    source.mkdir()
    (source / 'items.jsonl').write_text('{}\n')
    (source / 'meta.json').write_text('{}')
    original = module.shutil.copyfile
    def changed(src, dst):
        original(src, dst)
        if Path(src).name == 'meta.json':
            (source / 'items.jsonl').write_text('{"changed":true}\n')
    monkeypatch.setattr(module.shutil, 'copyfile', changed)
    with pytest.raises(RuntimeError, match='Source changed'):
        module.snapshot(source, target)


def test_l3_enrolment_is_only_permitted_additive_metadata():
    old = {'version': '4.0.3', 'operator_marker': 'must-stay'}
    new = {**old, 'version': '4.0.4', 'store_instance_id': 'a' * 32}
    module.validate_meta(old, new, enrolment=True)
    with pytest.raises(ValueError):
        module.validate_meta(old, new)
    for bad in ({**new, 'store_instance_id': 'invalid'}, {**new, 'operator_marker': 'changed'}):
        with pytest.raises(ValueError):
            module.validate_meta(old, bad, enrolment=True)
    with pytest.raises(ValueError):
        module.validate_meta(new, {**new, 'store_instance_id': 'b' * 32}, enrolment=True)


def test_l3_identity_stable_on_warm_copy():
    reports = {k: record() for k in ('baseline', 'wheel', 'sdist', 'wheel-warm', 'sdist-warm')}
    for kind in ('wheel', 'sdist'):
        reports[kind]['store_instance_id'] = reports[kind + '-warm']['store_instance_id'] = 'a' * 32
    assert module.compare(reports, baseline_version='4.0.3')['enrolment_created_on_private_copies']
    reports['wheel-warm']['store_instance_id'] = 'b' * 32
    with pytest.raises(ValueError, match='L3 identity'):
        module.compare(reports, baseline_version='4.0.3')


def record():
    return {'total': 1, 'source_records_unchanged': True, 'startup_encoded_texts': 0,
            'encoding_profile': {'device': 'mps', 'parameter_devices': ['mps'], 'parameter_dtypes': ['torch.float32'],
                                 'dtype': 'native', 'batch_size': 32, 'max_seq_length': 512,
                                 'model_revision': 'fixed', 'runtime': {'torch': 'fixed'}},
            'search': {'query': {'ids': ['fact']}}}


def test_search_change_is_explicit_and_does_not_qualify_silently():
    reports = {k: record() for k in ('baseline', 'wheel', 'sdist', 'wheel-warm', 'sdist-warm')}
    assert module.compare(reports)['search_identical_to_402']
    reports['baseline']['search']['query']['ids'] = ['other']
    outcome = module.compare(reports)
    assert not outcome['search_identical_to_402'] and outcome['changed_queries'] == ['query']
    reports['sdist-warm']['startup_encoded_texts'] = 1
    with pytest.raises(ValueError, match='reencoded'):
        module.compare(reports)


def test_copy_proof_cannot_lift_guard_without_complete_native_attestation():
    import verify_release_manifest as gate
    manifest = {'source_commit': 'a' * 40, 'artifacts': [], 'copy_scripts_sha256': {'runner': 'b' * 64, 'worker': 'c' * 64}}
    proof = {'ok': True, 'version': '4.0.3', 'source_commit': manifest['source_commit'], 'artifacts': [],
             'production_modified': False, 'search_identical_to_402': True,
             'rankings_identical_between_artifacts_and_reopen': True, 'warm_encoded_texts': 0,
             'doctor': {'ok': True}, 'encoding_profile': record()['encoding_profile'],
             'scripts_sha256': manifest['copy_scripts_sha256']}
    gate.validate_copy(proof, manifest)
    for key, value in [('production_modified', True), ('search_identical_to_402', False), ('warm_encoded_texts', 1)]:
        with pytest.raises(ValueError):
            gate.validate_copy({**proof, key: value}, manifest)
    for key in ('parameter_dtypes', 'runtime', 'model_revision'):
        with pytest.raises(ValueError):
            gate.validate_copy({**proof, 'encoding_profile': {**proof['encoding_profile'], key: None}}, manifest)


def test_review_must_bind_changed_rankings_to_original_copy_report():
    import verify_release_manifest as gate
    manifest = {'source_commit': 'a' * 40, 'artifacts': [], 'copy_scripts_sha256': {'runner': 'b' * 64, 'worker': 'c' * 64}}
    proof = {'ok': True, 'version': '4.0.3', 'source_commit': manifest['source_commit'], 'artifacts': [],
             'production_modified': False, 'search_identical_to_402': False, 'changed_queries': ['query'],
             'explanation': 'legacy vectors rebuilt', 'original_report_sha256': 'd' * 64,
             'rankings_identical_between_artifacts_and_reopen': True, 'warm_encoded_texts': 0,
             'doctor': {'ok': True}, 'encoding_profile': record()['encoding_profile'],
             'scripts_sha256': manifest['copy_scripts_sha256']}
    with pytest.raises(ValueError, match='revue Claude'):
        gate.validate_copy(proof, manifest)
    review = {'report_sha256': 'd' * 64, 'reviewer': 'Claude', 'accepted': True,
              'rationale': 'Explicit review of each changed query, legacy provenance and rebuilt native vectors. No claim of quality equivalence.'}
    gate.validate_copy({**proof, 'search_change_review': review}, manifest)
    with pytest.raises(ValueError):
        gate.validate_copy({**proof, 'search_change_review': {**review, 'report_sha256': 'e' * 64}}, manifest)

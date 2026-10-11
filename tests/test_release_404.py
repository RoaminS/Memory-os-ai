"""New release gates fail closed; synthetic evidence only, no model/TCP."""
import copy
import importlib.util
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location('release404', Path(__file__).parents[1] / 'scripts/verify_release_manifest.py')
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def test_404_rejects_old_ten_tcp_source_proof():
    manifest = {'version': '4.0.4', 'source_commit': 'a' * 40,
                'qualification_source_sha256': {'test.py': 'b' * 64}, 'source_expected_v3_tests': 566}
    proof = {'ok': True, **{k: manifest[k] for k in ('version', 'source_commit')},
             'source_sha256': manifest['qualification_source_sha256'],
             'checks': {name: {'passed': count, 'failed': 0, 'errors': 0, 'skipped': 0}
                        for name, count in [('source_tcp', 10), ('cached_semantic', 1), ('v3_cached', 566)]}}
    with pytest.raises(ValueError, match='source_tcp'):
        gate.validate_source(proof, manifest)
    proof['checks']['source_tcp']['passed'] = 20
    gate.validate_source(proof, manifest)


def test_404_copy_requires_current_403_baseline():
    manifest = {'version': '4.0.4', 'source_commit': 'a' * 40, 'artifacts': [], 'copy_scripts_sha256': {}}
    proof = {'ok': True, **{k: manifest[k] for k in ('version', 'source_commit', 'artifacts')},
             'production_modified': False, 'rankings_identical_between_artifacts_and_reopen': True,
             'warm_encoded_texts': 0, 'doctor': {'ok': True, 'ready': {'capabilities': {'store': {'instance_id': 'e' * 32}}}}, 'scripts_sha256': {},
             'store_instance_ids': {'wheel': 'e' * 32, 'sdist': 'f' * 32},
             'baseline_version': '4.0.2', 'search_identical_to_baseline': True,
             'encoding_profile': {'device': 'mps', 'dtype': 'native', 'batch_size': 32, 'max_seq_length': 512,
                                  'parameter_devices': ['mps'], 'parameter_dtypes': ['torch.bfloat16'],
                                  'model_revision': 'fixed', 'runtime': {'torch': 'fixed'}}}
    with pytest.raises(ValueError, match='baseline'):
        gate.validate_copy(proof, manifest)
    proof['baseline_version'] = '4.0.3'
    gate.validate_copy(proof, manifest)


@pytest.mark.parametrize('missing', ['brief', 'local_status_during_outage'])
def test_404_installed_relay_requires_exact_brief_and_outage_status(missing):
    manifest = {'version': '4.0.4', 'source_commit': 'a' * 40,
                'installed_relay_scripts_sha256': {'runner': 'b' * 64, 'case': 'c' * 64},
                'artifacts': [{'name': 'memory_os_ai-4.0.4' + suffix, 'sha256': 'd' * 64}
                              for suffix in ('-py3-none-any.whl', '.tar.gz')]}
    proof = {'ok': True, 'source_commit': manifest['source_commit'],
             'scripts_sha256': manifest['installed_relay_scripts_sha256'],
             'cases': [{'kind': kind, 'ok': True, 'exit_code': 0, 'facts': 2, 'version': '4.0.4',
                        'source_commit': manifest['source_commit'], 'artifact_sha256': 'd' * 64,
                        **{name: True for name in gate.CASE_CHECKS},
                        'local_status_during_outage': True, 'brief': {'ok': True, 'bytes': 5000, 'tokens': 1200}}
                       for kind in ('wheel', 'sdist')]}
    gate.validate_relay(proof, manifest)
    broken = copy.deepcopy(proof)
    del broken['cases'][1][missing]
    with pytest.raises(ValueError, match='brief exact'):
        gate.validate_relay(broken, manifest)


def test_405_requires_all_27_tcp_and_semantic_config_isolation():
    manifest = {'version': '4.0.5', 'source_commit': 'a' * 40,
                'qualification_source_sha256': {'test.py': 'b' * 64}, 'source_expected_v3_tests': 587}
    proof = {'ok': True, **{k: manifest[k] for k in ('version', 'source_commit')},
             'source_sha256': manifest['qualification_source_sha256'], 'client_configs_unchanged': True,
             'checks': {name: {'passed': count, 'failed': 0, 'errors': 0, 'skipped': 0}
                        for name, count in [('source_tcp', 27), ('cached_semantic', 1), ('v3_cached', 587)]}}
    gate.validate_source(proof, manifest)
    for count in (20, 26, 28):
        bad = copy.deepcopy(proof)
        bad['checks']['source_tcp']['passed'] = count
        with pytest.raises(ValueError, match='source_tcp'):
            gate.validate_source(bad, manifest)
    with pytest.raises(ValueError, match='isolation'):
        gate.validate_source({**proof, 'client_configs_unchanged': False}, manifest)


def test_405_copy_requires_404_baseline_write_mode_and_inactive_optins():
    manifest = {'version': '4.0.5', 'source_commit': 'a' * 40, 'artifacts': [], 'copy_scripts_sha256': {}}
    cap = {'store': {'instance_id': 'e' * 32, 'backend': 'jsonl'}, 'policies': {'write_mode': 'read_write'},
           'work_state': False, 'block_cas': False, 'projects': {'registry_enabled': False}}
    proof = {'ok': True, **{k: manifest[k] for k in ('version', 'source_commit', 'artifacts')},
             'production_modified': False, 'rankings_identical_between_artifacts_and_reopen': True,
             'warm_encoded_texts': 0, 'doctor': {'ok': True, 'ready': {'capabilities': cap}}, 'scripts_sha256': {},
             'store_instance_ids': {'wheel': 'e' * 32, 'sdist': 'f' * 32},
             'baseline_version': '4.0.4', 'search_identical_to_baseline': True,
             'encoding_profile': {'device': 'mps', 'dtype': 'native', 'batch_size': 32, 'max_seq_length': 512,
                                  'parameter_devices': ['mps'], 'parameter_dtypes': ['torch.bfloat16'],
                                  'model_revision': 'fixed', 'runtime': {'torch': 'fixed'}}}
    gate.validate_copy(proof, manifest)
    with pytest.raises(ValueError, match='baseline'):
        gate.validate_copy({**proof, 'baseline_version': '4.0.3'}, manifest)
    for key in ('work_state', 'block_cas'):
        bad = copy.deepcopy(proof)
        bad['doctor']['ready']['capabilities'][key] = True
        with pytest.raises(ValueError, match='politiques'):
            gate.validate_copy(bad, manifest)
    bad = copy.deepcopy(proof)
    del bad['doctor']['ready']['capabilities']['policies']['write_mode']
    with pytest.raises(ValueError, match='politiques'):
        gate.validate_copy(bad, manifest)

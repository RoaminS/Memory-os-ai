"""4.0.6 deployment evidence; independent of the immutable wheel's source commit."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

SOURCE = '50edb029d57a20dfc339c5a6ec7d1d62c1f2b5a1'
WHEEL = '2cf484d882c824e98776e3fc5d9e83f5b62f06e6d4d34c3b1216fe089ed0e201'
SDIST = '5384a1c535f2561a22827a08f3bfaacd25327db275372b6cca15668a5da51c0d'
INSTANCE = '286dfcb0357940468aedb9617a2ccd9c'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def evidence(manifest, directory, name):
    entry = manifest['distribution_evidence'][name]
    path = Path(entry['path'])
    require(not path.is_absolute() and '..' not in path.parts, 'unsafe evidence path')
    path = directory / path
    require(digest(path) == entry['sha256'], 'evidence changed: ' + name)
    return json.loads(path.read_text())


def validate_local(manifest, directory, *, require_copy=True):
    require(manifest['source_commit'] == SOURCE and manifest['version'] == '4.0.6', 'wrong qualified source')
    require(not manifest.get('open_blockers'), 'open blockers')
    root = Path(__file__).resolve().parents[1]
    for name, sha in manifest['qualification_tooling_sha256'].items():
        require(Path(name).name == name and digest(root / 'scripts' / name) == sha, 'qualification tooling changed')
    wanted = {'memory_os_ai-4.0.6-py3-none-any.whl': WHEEL, 'memory_os_ai-4.0.6.tar.gz': SDIST}
    require(len(manifest['artifacts']) == 2, 'artifact count')
    require({a['name']: a['sha256'] for a in manifest['artifacts']} == wanted, 'not the qualified artifacts')
    for a in manifest['artifacts']:
        p = directory / 'artifacts' / a['name']
        require(digest(p) == a['sha256'] and p.stat().st_size == a['bytes'], 'artifact changed')
    full = evidence(manifest, directory, 'full')
    require(full.get('ok') is True and full.get('tcp_qualified') is True
            and full.get('client_configs_unchanged') is True, 'full suite incomplete')
    require(full['source_sha256'] == manifest['qualification_source_sha256'], 'source inventory mismatch')
    commands = {c['name']: c for c in full['commands']}
    for name, n in [('v3', 647), ('v4', 1883)]:
        c = commands[name]
        require(c['returncode'] == 0 and c['counts']['tests'] == n
                and all(c['counts'][k] == 0 for k in ('errors', 'failures', 'skipped')), 'suite count/outcome mismatch')
    tcp = commands['v4']['tcp_evidence']
    from qualify_405_test_isolation import EXPECTED_TCP
    require(tcp == {'ok': True, 'expected_cases': len(EXPECTED_TCP), 'observed_cases': len(EXPECTED_TCP),
                    'missing': [], 'unexpected': [], 'unsuccessful': []}, 'physical TCP manifest incomplete')
    artifacts = evidence(manifest, directory, 'artifacts')
    require(artifacts.get('ok') is True and artifacts.get('artifact_checked') is True
            and artifacts.get('source_commit') == SOURCE and artifacts['identity']['commit'] == SOURCE
            and artifacts['artifact_sha256']['direct/memory_os_ai-4.0.6-py3-none-any.whl'] == WHEEL
            and artifacts['artifact_sha256']['via-sdist/memory_os_ai-4.0.6.tar.gz'] == SDIST,
            'artifact qualification mismatch')
    require(all(n in artifacts['steps'] for n in ('install-0', 'install-1', 'smoke-0', 'smoke-1',
                'extra_markers', 'quickstart-preview-0', 'quickstart-preview-1')), 'installed evidence incomplete')
    resolution = evidence(manifest, directory, 'resolution')
    require(resolution.get('ok') is True and resolution.get('wheel_sha256') == WHEEL
            and resolution.get('installed') is False, 'resolution mismatch')
    for backend in ('sqlite', 'jsonl'):
        trial = evidence(manifest, directory, 'trial_' + backend)
        ready, probe = trial['ready'], trial['probe']
        require(ready.get('ok') is True and ready['version'] == '4.0.6' and ready['commit'] == SOURCE
                and ready['backend'] == ('sqlite_s1' if backend == 'sqlite' else 'jsonl')
                and probe.get('ok') is True and probe['backend_expected'] == backend
                and ready['work_state'] is (backend == 'sqlite') and ready['retrieval_ready'] is True
                and trial['doctor_failures'] == [] and trial['configs_before'] == trial['configs_after']
                and trial['configs_before']['ok'] is True, 'clean trial incomplete: ' + backend)
    twine = directory / manifest['twine_evidence']['path']
    require(digest(twine) == manifest['twine_evidence']['sha256'] and 'FAILED' not in twine.read_text()
            and twine.read_text().count('PASSED') == 2, 'strict twine evidence mismatch')
    if require_copy:
        p = directory / 'copy-proof.json'
        require(manifest['checks'].get('production_copy') == 'passed'
                and digest(p) == manifest.get('copy_proof_sha256'), 'SQLite copy qualification pending')
        validate_copy(json.loads(p.read_text()), manifest)


def validate_copy(proof, manifest):
    require(proof.get('ok') is True and proof.get('version') == '4.0.6'
            and proof.get('baseline_version') == '4.0.5' and proof.get('source_commit') == SOURCE
            and proof.get('artifacts') == manifest['artifacts']
            and proof.get('source_sql_writes') is False and proof.get('production_modified') is False
            and proof.get('scripts_sha256') == manifest['copy_scripts_sha256'], 'SQLite copy proof mismatch')
    require(proof['snapshot']['store_instance_id'] == INSTANCE and proof['snapshot']['schema_version'] == 7,
            'wrong SQLite cut/identity/schema')
    require(proof.get('search_identical_to_baseline') is True
            and proof.get('rankings_identical_between_artifacts_and_reopen') is True
            and proof.get('authority_identical') is True and proof.get('warm_encoded_texts') == 0,
            'SQLite authority/cache/rankings changed')
    require(set(proof['workers']) == {'baseline', 'wheel', 'sdist', 'wheel-warm', 'sdist-warm'}, 'missing workers')
    reference = proof['workers']['baseline']
    from release_copy_worker import QUERIES
    require(set(reference['search']) == set(QUERIES), 'frozen probe set changed')
    for name, row in proof['workers'].items():
        require(row['ok'] is True and row['version'] == ('4.0.5' if name == 'baseline' else '4.0.6')
                and row['store_instance_id'] == INSTANCE and row['authority'] == reference['authority']
                and row['encoding_profile'] == reference['encoding_profile']
                and row['total'] == reference['total']
                and row['corpus_encoded_texts'] == 0 and row['startup_seconds'] <= 120,
                'worker mismatch: ' + name)
        require({q: h['ids'] for q, h in row['search'].items()} ==
                {q: h['ids'] for q, h in reference['search'].items()}, 'probe mismatch: ' + name)
    profile = reference['encoding_profile']
    require(profile['device'] == 'mps' and profile['dtype'] == 'native' and profile['batch_size'] == 32
            and profile['max_seq_length'] == 512 and profile['parameter_devices'] == ['mps']
            and profile.get('model_revision') and profile.get('runtime') and profile.get('parameter_dtypes'),
            'wrong production profile')
    require(proof['seconds'] <= 900, 'copy wall-time cap exceeded')
    doctor = proof['doctor']
    require(doctor.get('ok') is True, 'private doctor failed')
    ready, caps = doctor['ready'], doctor['ready']['capabilities']
    require(ready['version'] == '4.0.6' and ready['commit'] == SOURCE
            and ready.get('identity_source') == 'embedded_descriptor'
            and caps['store']['backend'] == 'sqlite_s1' and caps['store']['instance_id'] == INSTANCE
            and caps['work_state'] is True and caps['retrieval_ready'] is True
            and caps['block_cas'] is True and caps['runtime']['ok'] is True and caps['encoding']['loaded'] is True
            and ready['encoding_profile'] == profile
            and caps['projects']['registry_enabled'] is False
            and caps['policies']['write_mode'] == 'read_only'
            and caps['policies']['bm25_profile'] == 'scoped_literal' and caps['policies']['lexical_guard_k'] == 3
            and caps['brief']['tokenizer']['ready'] is True, 'private deployed policy mismatch')

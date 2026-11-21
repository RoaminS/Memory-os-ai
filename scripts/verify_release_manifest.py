"""Fail closed before upload; record matching installed-relay evidence without publishing."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

REQUIRED_CHECKS = {'a6_review', 'adversarial_probe', 'v3', 'v4', 'lint', 'twine_strict',
                   'wheel_install', 'sdist_install', 'wheel_smoke', 'sdist_smoke'}
EXPECTED = {'memory_os_ai-4.0.1-py3-none-any.whl', 'memory_os_ai-4.0.1.tar.gz'}
CASE_CHECKS = ('installed', 'ready', 'roundtrip', 'failure_reported', 'reconnected', 'no_replay', 'clean_shutdown')


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def expected_artifacts(version):
    if not isinstance(version, str) or not re.fullmatch(r'4\.\d+\.\d+', version):
        raise ValueError('version stable v4 invalide')
    return {f'memory_os_ai-{version}-py3-none-any.whl', f'memory_os_ai-{version}.tar.gz'}


def validate_source(proof, manifest):
    if (proof.get('ok') is not True or proof.get('version') != manifest['version']
            or proof.get('source_commit') != manifest['source_commit']
            or not manifest.get('qualification_source_sha256')
            or proof.get('source_sha256') != manifest['qualification_source_sha256']):
        raise ValueError('preuve source TCP/modèle absente ou différente')
    if manifest['version'] == '4.0.5' and proof.get('client_configs_unchanged') is not True:
        raise ValueError('preuve isolation des configurations MCP manquante')
    tcp_count = {'4.0.4': 20, '4.0.5': 27}.get(manifest['version'], 10)
    for name, count in [('source_tcp', tcp_count), ('cached_semantic', 1)]:
        result = proof.get('checks', {}).get(name, {})
        if result != {'passed': count, 'failed': 0, 'errors': 0, 'skipped': 0}:
            raise ValueError('suite source incomplète/ignorée/en échec : ' + name)
    if manifest['version'] in {'4.0.3', '4.0.4', '4.0.5'}:
        count = manifest.get('source_expected_v3_tests')
        if (type(count) is not int or count < 1 or proof.get('checks', {}).get('v3_cached') !=
                {'passed': count, 'failed': 0, 'errors': 0, 'skipped': 0}):
            raise ValueError('suite v3 réelle cached encore en attente/en échec')


def validate_copy(proof, manifest):
    if manifest.get('version') == '4.0.6':
        from release_406_checks import validate_copy as validate_sqlite
        return validate_sqlite(proof, manifest)
    version = manifest.get('version', '4.0.3')
    if (proof.get('ok') is not True or proof.get('version') != version
            or proof.get('source_commit') != manifest['source_commit']
            or proof.get('artifacts') != manifest['artifacts']
            or proof.get('production_modified') is not False
            or proof.get('rankings_identical_between_artifacts_and_reopen') is not True
            or proof.get('warm_encoded_texts') != 0 or proof.get('doctor', {}).get('ok') is not True
            or proof.get('scripts_sha256') != manifest.get('copy_scripts_sha256')):
        raise ValueError('preuve copie/profil/recherche/doctor absente, modifiée ou en échec')
    if version in {'4.0.4', '4.0.5'} and proof.get('baseline_version') != {'4.0.4': '4.0.3', '4.0.5': '4.0.4'}[version]:
        raise ValueError('baseline copie installée de la version précédente requise')
    if version in {'4.0.4', '4.0.5'}:
        identities = proof.get('store_instance_ids', {})
        if (set(identities) != {'wheel', 'sdist'}
                or any(not isinstance(v, str) or not re.fullmatch(r'[0-9a-f]{32}', v) for v in identities.values())
                or proof['doctor'].get('ready', {}).get('capabilities', {}).get('store', {}).get('instance_id') != identities['wheel']):
            raise ValueError('identités durables des copies non qualifiées')
    if version == '4.0.5':
        cap = proof['doctor'].get('ready', {}).get('capabilities', {})
        if (cap.get('policies', {}).get('write_mode') not in {'read_only', 'read_write'}
                or cap.get('store', {}).get('backend') != 'jsonl'
                or cap.get('work_state') is not False or cap.get('block_cas') is not False
                or cap.get('projects', {}).get('registry_enabled') is not False):
            raise ValueError('politiques 4.0.5 ou capacités inactives non qualifiées')
    identical = proof.get('search_identical_to_baseline') if version in {'4.0.4', '4.0.5'} else proof.get('search_identical_to_402')
    if identical is not True:
        review = proof.get('search_change_review', {})
        if (review.get('reviewer') != 'Claude' or review.get('accepted') is not True
                or not isinstance(review.get('rationale'), str) or len(review['rationale'].strip()) < 80
                or review.get('report_sha256') != proof.get('original_report_sha256')
                or not proof.get('changed_queries') or not proof.get('explanation')):
            raise ValueError('écarts de recherche : explication et revue Claude nécessaires, aucun accord automatique')
    profile = proof.get('encoding_profile', {})
    if (profile.get('device') != 'mps' or profile.get('dtype') != 'native'
            or profile.get('batch_size') != 32 or profile.get('max_seq_length') != 512
            or profile.get('parameter_devices') != ['mps'] or not profile.get('parameter_dtypes')
            or not profile.get('model_revision') or not profile.get('runtime')):
        raise ValueError('profil natif MPS non attesté')


def validate_brief(proof, manifest):
    from verify_404_increment3b import validate_reports
    validate_reports(proof['source'], proof['brief'])
    expected = manifest['qualification_source_sha256']
    for name, prefixes in [('source', ('v4/src/', 'v4/tests/')), ('brief', ('v4/src/',))]:
        report = proof[name]
        paths = {p for p in expected if p.startswith(prefixes) and p.endswith('.py')}
        if not paths or set(report['source_sha256']) != paths:
            raise ValueError('inventaire source L1a incomplet')
        for path, digest in report['source_sha256'].items():
            if expected.get(path) != digest:
                raise ValueError('preuve L1a provenant d’une autre source')


def validate_local(manifest, directory, *, require_source=True, require_copy=True, require_brief=True):
    if manifest.get('version') == '4.0.9':
        from release_409_checks import validate_local as validate_rc
        return validate_rc(manifest, directory, require_copy=require_copy, require_source=require_source)
    if manifest.get('version') == '4.0.8':
        from release_408_checks import validate_local as validate_rc
        return validate_rc(manifest, directory, require_copy=require_copy, require_source=require_source)
    if manifest.get('version', '').startswith('4.0.7'):
        from release_407_checks import validate_local as validate_rc
        return validate_rc(manifest, directory, require_copy=require_copy, require_source=require_source)
    if manifest.get('version') == '4.0.6':
        from release_406_checks import validate_local as validate_distribution
        return validate_distribution(manifest, directory, require_copy=require_copy)
    expected = expected_artifacts(manifest.get('version'))
    if not re.fullmatch(r'[0-9a-f]{40}', manifest.get('source_commit', '')):
        raise ValueError('identité version/source invalide')
    if manifest.get('open_blockers'):
        raise ValueError('blocages ouverts')
    checks = manifest.get('checks', {})
    if not all(checks.get(name) == 'passed' for name in REQUIRED_CHECKS):
        raise ValueError('preuves locales incomplètes ou en échec')
    artifacts = manifest.get('artifacts', [])
    if len(artifacts) != 2 or {a['name'] for a in artifacts} != expected:
        raise ValueError('inventaire wheel/sdist incorrect')
    for artifact in artifacts:
        path = directory / 'artifacts' / artifact['name']
        if not path.is_file() or sha256(path) != artifact['sha256']:
            raise ValueError('artefact absent/modifié : ' + artifact['name'])
    if manifest['version'] in {'4.0.2', '4.0.3', '4.0.4', '4.0.5'} and require_source:
        required = ('source_tcp', 'cached_semantic', 'v3_cached') if manifest['version'] in {'4.0.3', '4.0.4', '4.0.5'} else ('source_tcp', 'cached_semantic')
        if not all(checks.get(name) == 'passed' for name in required):
            raise ValueError('TCP source et test MiniLM cached encore en attente')
        path = directory / 'source-proof.json'
        if not path.is_file() or sha256(path) != manifest.get('source_proof_sha256'):
            raise ValueError('preuve source absente/modifiée')
        validate_source(json.loads(path.read_text()), manifest)
    if manifest['version'] in {'4.0.3', '4.0.4', '4.0.5'} and require_copy:
        path = directory / 'copy-proof.json'
        if (checks.get('production_copy') != 'passed' or not path.is_file()
                or sha256(path) != manifest.get('copy_proof_sha256')):
            raise ValueError('qualification sur copie/profil natif encore en attente')
        validate_copy(json.loads(path.read_text()), manifest)
    if manifest['version'] in {'4.0.4', '4.0.5'} and require_brief:
        path = directory / 'brief-proof.json'
        if (checks.get('brief_exact') != 'passed' or not path.is_file()
                or sha256(path) != manifest.get('brief_proof_sha256')):
            raise ValueError('qualification L1a TCP et 400 mesures exactes encore en attente')
        validate_brief(json.loads(path.read_text()), manifest)


def validate_relay(proof, manifest):
    if proof.get('ok') is not True or proof.get('source_commit') != manifest['source_commit']:
        raise ValueError('preuve relais absente, en échec ou provenant d’une autre source')
    expected_scripts = manifest.get('installed_relay_scripts_sha256', {})
    if set(expected_scripts) != {'runner', 'case'} or proof.get('scripts_sha256') != expected_scripts:
        raise ValueError('scripts de preuve différents')
    cases = proof.get('cases', [])
    if len(cases) != 2 or {case.get('kind') for case in cases} != {'wheel', 'sdist'}:
        raise ValueError('les deux installations doivent être testées')
    for case in cases:
        suffix = '.whl' if case['kind'] == 'wheel' else '.tar.gz'
        artifact = next(a for a in manifest['artifacts'] if a['name'].endswith(suffix))
        if (case.get('ok') is not True or case.get('exit_code') != 0 or case.get('facts') != 2
                or case.get('version') != manifest['version'] or case.get('source_commit') != manifest['source_commit']
                or case.get('artifact_sha256') != artifact['sha256']
                or not all(case.get(check) is True for check in CASE_CHECKS)):
            raise ValueError('cas installé incomplet/en échec ou autre artefact : ' + case['kind'])
        if manifest['version'] in {'4.0.4', '4.0.5', '4.0.6', '4.0.7rc1', '4.0.7'}:
            brief = case.get('brief', {})
            if (case.get('local_status_during_outage') is not True or not brief.get('ok')
                    or not 0 < brief.get('bytes', 0) <= 6000 or not 0 < brief.get('tokens', 0) <= 1500):
                raise ValueError('preuve brief exact/statut local du relais installé absente')


def main(argv=None) -> int:
    if not __debug__:
        raise RuntimeError('Assertions required; do not use Python -O')
    parser = argparse.ArgumentParser()
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--record-relay-proof', type=Path,
                        help='Enregistre les preuves concordantes et lève la garde seulement si tous les contrôles passent')
    parser.add_argument('--record-source-proof', type=Path,
                        help='Enregistre TCP source et MiniLM cached concordants, sans lever la garde relais')
    parser.add_argument('--record-copy-proof', type=Path)
    parser.add_argument('--record-full-proof', type=Path)
    parser.add_argument('--record-gate-proof', type=Path)
    parser.add_argument('--copy-review', type=Path, help='Revue Claude liée au SHA du rapport original si les classements changent')
    parser.add_argument('--record-brief-source', type=Path)
    parser.add_argument('--record-brief-exact', type=Path)
    args = parser.parse_args(argv)
    try:
        manifest = json.loads(args.manifest.read_text())
        if manifest.get('version') == '4.0.9':
            from release_409_checks import main as verify_rc
            return verify_rc(args)
        if manifest.get('version') == '4.0.8':
            from release_408_checks import main as verify_rc
            return verify_rc(args)
        if manifest.get('version', '').startswith('4.0.7'):
            from release_407_checks import main as verify_rc
            return verify_rc(args)
        if args.record_brief_source or args.record_brief_exact:
            if (not args.record_brief_source or not args.record_brief_exact or manifest['version'] not in {'4.0.4', '4.0.5'}
                    or args.record_source_proof or args.record_copy_proof or args.record_relay_proof):
                raise ValueError('enregistrer les deux preuves L1a séparément')
            validate_local(manifest, args.manifest.parent, require_source=False, require_copy=False, require_brief=False)
            proof = {'source': json.loads(args.record_brief_source.read_text()),
                     'brief': json.loads(args.record_brief_exact.read_text())}
            validate_brief(proof, manifest)
            destination = args.manifest.parent / 'brief-proof.json'
            destination.write_text(json.dumps(proof, ensure_ascii=False, indent=2) + '\n')
            manifest['checks']['brief_exact'] = 'passed'
            manifest.update(brief_proof_sha256=sha256(destination), release_ready=False)
            args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
            print('Preuves L1a enregistrées ; aucune publication/bascule.')
            return 0
        if args.copy_review and not args.record_copy_proof:
            raise ValueError('--copy-review nécessite --record-copy-proof')
        if args.record_copy_proof:
            if args.record_source_proof or args.record_relay_proof or manifest['version'] not in {'4.0.3', '4.0.4', '4.0.5', '4.0.6'}:
                raise ValueError('enregistrer la preuve copie 4.0.3/4.0.4 séparément')
            validate_local(manifest, args.manifest.parent, require_copy=False)
            proof = json.loads(args.record_copy_proof.read_text())
            if args.copy_review:
                review = json.loads(args.copy_review.read_text())
                if review.get('report_sha256') != sha256(args.record_copy_proof):
                    raise ValueError('revue provenant d’un autre rapport copie')
                proof.update(search_change_review=review, original_report_sha256=sha256(args.record_copy_proof))
            validate_copy(proof, manifest)
            destination = args.manifest.parent / 'copy-proof.json'
            destination.write_text(json.dumps(proof, ensure_ascii=False, indent=2) + '\n')
            manifest['checks']['production_copy'] = 'passed'
            manifest.update(copy_proof_sha256=sha256(destination), release_ready=False)
            args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
            print('Preuve copie enregistrée ; garde relais fermée. Aucune publication/bascule.')
            return 0
        if args.record_source_proof:
            if args.record_relay_proof:
                raise ValueError('enregistrer source et relais séparément')
            validate_local(manifest, args.manifest.parent, require_source=False, require_copy=False, require_brief=False)
            proof = json.loads(args.record_source_proof.read_text())
            validate_source(proof, manifest)
            destination = args.manifest.parent / 'source-proof.json'
            destination.write_bytes(args.record_source_proof.read_bytes())
            manifest['checks'].update(source_tcp='passed', cached_semantic='passed')
            if manifest['version'] in {'4.0.3', '4.0.4', '4.0.5'}:
                manifest['checks']['v3_cached'] = 'passed'
            manifest.update(source_proof_sha256=sha256(destination), release_ready=False)
            args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
            print('Preuve source enregistrée ; garde relais toujours fermée. Aucune publication.')
            return 0
        validate_local(manifest, args.manifest.parent)
        proof_path = args.record_relay_proof or args.manifest.parent / 'installed-relay-proof.json'
        if not args.record_relay_proof and manifest.get('release_ready') is not True:
            raise ValueError('release_ready != true ; relais installé encore en attente')
        proof = json.loads(proof_path.read_text())
        validate_relay(proof, manifest)
        if args.record_relay_proof:
            destination = args.manifest.parent / 'installed-relay-proof.json'
            destination.write_bytes(proof_path.read_bytes())
            manifest.update(release_ready=True, relay_proof_sha256=sha256(destination))
            args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
        elif sha256(proof_path) != manifest.get('relay_proof_sha256'):
            raise ValueError('preuve relais modifiée depuis son acceptation')
    except (ValueError, OSError, KeyError, TypeError, StopIteration, AssertionError) as exc:
        print('REFUS : ' + str(exc))
        return 1
    print('Preuves et empreintes concordantes ; aucune publication effectuée. Romain conserve le GO PyPI.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

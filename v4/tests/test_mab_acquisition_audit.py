"""Synthetic fixtures only: no remote data, encoder, CLI inference or API."""
import hashlib
import io
import json

import pytest

from bench.e2e import mab_acquisition as acquisition
from bench.e2e import mab_audit as audit_module
from bench.e2e.data import sha256


class Response(io.BytesIO):
    headers = {}


@pytest.mark.parametrize('failure', [None, 'oversized', 'changed_sha', 'not_mit',
                                    'changed_revision', 'corrupt_payload', 'truncated_payload', 'row_count', 'missing_converter'])
def test_acquisition_checks_metadata_before_transfer_preserves_failures(tmp_path, monkeypatch, failure):
    clone = tmp_path / 'clone'
    module_path = clone / 'v4/bench/e2e/mab_acquisition.py'
    module_path.parent.mkdir(parents=True)
    module_path.write_text('synthetic acquisition source')
    monkeypatch.setattr(acquisition, '__file__', str(module_path))
    payload = b'no-model-synthetic-parquet'
    expected = hashlib.sha256(payload).hexdigest()
    monkeypatch.setattr(acquisition, 'FILE_SHA', expected)
    revision = acquisition.REVISION
    info = {'sha': revision if failure != 'changed_revision' else 'f' * 40,
            'cardData': {'license': 'mit' if failure != 'not_mit' else 'unknown'}}
    card = b'---\nlicense: mit\n---\nSynthetic schema card\n'
    tree = [{'path': acquisition.REMOTE,
             'size': len(payload) if failure != 'oversized' else acquisition.CAP + 1,
             'lfs': {'oid': expected if failure != 'changed_sha' else '0' * 64}}]
    calls = []
    def opened(request, **kwargs):
        url = request.full_url
        calls.append(url)
        if '/revision/' in url:
            data = json.dumps(info).encode()
        elif url.endswith('README.md'):
            data = card
        elif '/tree/' in url:
            data = json.dumps(tree).encode()
        else:
            data = b'x' * len(payload) if failure == 'corrupt_payload' else payload
            if failure == 'truncated_payload':
                data = data[:-1]
        return Response(data)
    monkeypatch.setattr(acquisition.urllib.request, 'urlopen', opened)
    monkeypatch.setattr(acquisition, 'ensure_priority', lambda _: None)
    monkeypatch.setattr(acquisition, 'sample', lambda _: None)
    def environment():
        if failure == 'missing_converter':
            raise ValueError('Local converter unavailable')
        return {'pyarrow': 'synthetic', 'python': 'synthetic'}
    monkeypatch.setattr(acquisition, 'converter_environment', environment)
    def convert(parquet, target, check):
        target.write_bytes(b'{}\n')
        return 110 if failure != 'row_count' else 109, 3
    monkeypatch.setattr(acquisition, 'convert', convert)
    output = clone / '.local-qualification/datasets/mab'
    monkeypatch.setattr('sys.argv', ['acquire', '--romain-download-go', acquisition.GO_QUOTE,
                                    '--output', str(output)])
    if failure:
        with pytest.raises(ValueError):
            acquisition.main()
        assert not (output / 'PROVENANCE.json').exists()
        stopped = json.loads((output / 'FAILED.json').read_text())
        assert stopped['inference_calls'] == 0 and stopped['restart_in_place'] is False
        if failure in {'oversized', 'changed_sha', 'not_mit', 'changed_revision', 'missing_converter'}:
            assert not any(url.endswith('.parquet') for url in calls)
        else:
            assert any(output.glob('*.partial'))
    else:
        acquisition.main()
        p = json.loads((output / 'PROVENANCE.json').read_text())
        assert p['revision'] == revision and p['parquet_sha256'] == expected
        assert p['download_bytes'] == len(payload)
        assert p['romain_download_go'] == {'date': '2026-10-10', 'quote': acquisition.GO_QUOTE,
                                          'scope': 'download + independence audit ONLY; 0 inference'}
        assert p['acquisition_script_sha256'] == sha256(module_path)
        assert all('/main/' not in url for url in calls)
    before = list(calls)
    with pytest.raises(SystemExit):
        acquisition.main()
    assert calls == before  # no automatic restart, even before first transferred byte


def make_row(index, source='eventqa'):
    return {'context': ' '.join(f'unique-{index}-word-{word}' for word in range(140)),
            'questions': [f'Question in scenario {index}, case {q}?' for q in range(4)],
            'answers': [[f'GOLD-SECRET-{index}-{q}'] for q in range(4)],
            'metadata': {'source': source, 'qa_pair_ids': [f'case-{index}-{q}' for q in range(4)],
                         'keypoints': ['SCORER-SECRET'], 'haystack_sessions': [],
                         'previous_events': [], 'question_dates': [], 'demo': 'SCORER-SECRET'}}


def write_rows(path, values):
    path.write_text(''.join(json.dumps(r) + '\n' for r in values))
    return path


def reviewed(rows):
    return {hashlib.sha256(r['context'].encode()).hexdigest(): {
        'canonical_work_id': f'original-work-{i}', 'original_source_url': f'https://example.org/work/{i}',
        'original_licence': 'CC0-1.0', 'previously_observed': False} for i, r in enumerate(rows)}


def test_95_word_excerpt_invariant_to_context_headers_and_surrounding_text():
    text = ' '.join(f'word-{i}' for i in range(95))
    plain = set(audit_module.text_signatures(text))
    shifted = set(audit_module.text_signatures('new header plus extra words ' + text + ' afterword'))
    assert {key for key in plain & shifted if key[0] == 'excerpt'}
    assert set(audit_module.text_signatures(text.upper())) == plain


def test_generic_source_and_unique_qa_ids_do_not_manufacture_independent_roots(tmp_path):
    values = [make_row(i) for i in range(110)]
    data = write_rows(tmp_path / 'mab.jsonl', values)
    before = data.read_bytes()
    result = audit_module.audit(data, tmp_path / 'audit.sqlite', [], lambda: None)
    assert result['contexts'] == 110 and result['questions'] == 440
    assert result['usable_reference_questions'] == 440
    assert result['verdict'] == 'NO-GO' and result['fresh_attested_groups'] == 0
    assert result['conservative_components'] == 1
    assert result['source_identity_unresolved_contexts'] == 110
    assert 'GOLD-SECRET' not in json.dumps(result) and 'SCORER-SECRET' not in json.dumps(result)
    assert data.read_bytes() == before and result['inference_calls'] == 0


def test_attested_roots_support_eligibility_but_never_authorize_inference(tmp_path):
    values = [make_row(i) for i in range(110)]
    data = write_rows(tmp_path / 'mab', values)
    result = audit_module.audit(data, tmp_path / 'db', [], lambda: None, reviewed(values))
    assert result['verdict'] == 'GO' and result['fresh_attested_groups'] == 110
    assert result['fresh_unique_usable_questions'] == 440
    assert result['campaign_authorized'] is False and result['requires_sealed_plan_and_protocol']
    assert result['inference_calls'] == 0


def test_observed_excerpt_excludes_whole_work_even_without_duplicate_full_context(tmp_path):
    values = [make_row(i) for i in range(110)]
    review = reviewed(values)
    review[sha256_text(values[1]['context'])]['canonical_work_id'] = 'original-work-0'
    previous = make_row(999)
    # Context copied with a header: must exclude rows 0 AND 1 by original-work root.
    previous['context'] = 'different header ' + values[0]['context']
    observed = write_rows(tmp_path / 'seen', [previous])
    result = audit_module.audit(write_rows(tmp_path / 'data', values), tmp_path / 'db',
                                [{'name': 'public-source', 'path': observed, 'format': 'mab'}],
                                lambda: None, review)
    assert result['fresh_attested_groups'] == 108
    assert result['fresh_unique_usable_questions'] == 432
    assert result['excluded_components'] == 1
    assert any(r['row'] == 0 and r['kind'] == 'excerpt' for r in result['overlaps'])


def sha256_text(text):
    return hashlib.sha256(text.encode()).hexdigest()


def test_known_observed_public_work_and_repeated_questions_reduce_eligible_count(tmp_path):
    values = [make_row(i) for i in range(110)]
    for r in values:
        r['questions'][0] = 'Identical duplicated query'
    review = reviewed(values)
    review[sha256_text(values[0]['context'])]['previously_observed'] = True
    result = audit_module.audit(write_rows(tmp_path / 'data', values), tmp_path / 'db', [],
                                lambda: None, review)
    assert result['conservative_components'] == 1 and result['fresh_attested_groups'] == 0
    assert result['verdict'] == 'NO-GO'


@pytest.mark.parametrize('mutation', ['bad_reference', 'alignment', 'unknown_metadata'])
def test_annotation_validation_without_gold_fabrication(mutation):
    r = make_row(0)
    if mutation == 'bad_reference':
        r['answers'][0] = []
        assert audit_module.inspect_row(r, 0)['usable_reference_questions'] == 3
    elif mutation == 'alignment':
        r['answers'].pop()
        with pytest.raises(ValueError, match='alignment'):
            audit_module.inspect_row(r, 0)
    else:
        r['metadata']['unsupported'] = ['secret']
        assert audit_module.inspect_row(r, 0)['usable_reference_questions'] == 0


def test_observed_reader_matches_missing_beam_annotations_and_both_lme_variants(tmp_path):
    beam = write_rows(tmp_path / 'beam', [{'probing_questions': repr({'knowledge_update': [{'question': 'Probe?'}]}),
                                         'chat': [[{'content': 'History'}]]}])
    lme = tmp_path / 'lme'
    lme.write_text(json.dumps([{'question': 'Probe?', 'answer': 'GOLD', 'haystack_sessions': [[{'content': 'History'}]]}]))
    assert list(audit_module.observed_units(beam, 'beam', lambda: None)) == list(
        audit_module.observed_units(lme, 'longmemeval', lambda: None))


def test_mapping_review_requires_question_blind_proof_and_prior_observation(tmp_path):
    clone = tmp_path
    private = clone / '.local-qualification'
    private.mkdir()
    proof = private / 'source-evidence.txt'
    proof.write_text('Synthetic original work mapping, not questions or answers')
    cid = '0' * 64
    entry = {'context_sha256': cid, 'canonical_work_id': 'work-1', 'original_source_url': 'https://example.org/work1',
             'original_licence': 'CC0-1.0', 'licence_url': 'https://example.org/rights',
             'mapping_proof_path': str(proof), 'mapping_proof_sha256': sha256(proof),
             'intended_use_permitted': True, 'previously_observed': False}
    path = private / 'review.json'
    path.write_text(json.dumps({'reviewer': 'synthetic', 'date': '2026-10-10', 'question_blind': True, 'contexts': [entry]}))
    entries, record = audit_module.source_review(path, clone, lambda: None)
    assert entries[cid] == entry and record['proofs'][str(proof)] == sha256(proof)
    proof.write_text('changed mapping')
    with pytest.raises(ValueError, match='missing/changed'):
        audit_module.source_review(path, clone, lambda: None)


def test_guard_interruption_is_not_a_complete_audit(tmp_path):
    from bench.e2e.guard import ResourceStop
    data = write_rows(tmp_path / 'data', [make_row(0)])
    def denied():
        raise ResourceStop('synthetic swap threshold')
    with pytest.raises(ResourceStop):
        audit_module.audit(data, tmp_path / 'db', [], denied)


def test_audit_cli_retains_no_go_report_checks_provenance_and_refuses_restart(tmp_path, monkeypatch):
    clone = tmp_path / 'clone'
    original_audit = audit_module.__file__
    original_acquisition = acquisition.__file__
    module_path = clone / 'v4/bench/e2e/mab_audit.py'
    module_path.parent.mkdir(parents=True)
    from pathlib import Path
    module_path.write_bytes(Path(original_audit).read_bytes())
    module_path.with_name('mab_acquisition.py').write_bytes(Path(original_acquisition).read_bytes())
    monkeypatch.setattr(audit_module, '__file__', str(module_path))
    directory = clone / '.local-qualification/dataset'
    directory.mkdir(parents=True)
    payload = b'qualified synthetic parquet'
    file_sha = hashlib.sha256(payload).hexdigest()
    monkeypatch.setattr(audit_module, 'FILE_SHA', file_sha)
    (directory / 'mab_lru.parquet').write_bytes(payload)
    data = write_rows(directory / 'mab_lru.jsonl', [make_row(i) for i in range(110)])
    for file in ('README.upstream.md', 'upstream-info.json', 'upstream-tree.json'):
        (directory / file).write_text('synthetic pinned metadata')
    provenance = {'dataset': acquisition.DATASET, 'split': 'Long_Range_Understanding',
                  'pinned_revision': acquisition.REVISION, 'revision': acquisition.REVISION,
                  'file': acquisition.REMOTE, 'licence': 'MIT', 'licence_scope': 'upstream card only',
                  'parquet_sha256': file_sha, 'contexts': 110, 'inference_calls': 0,
                  'download_cap_bytes': acquisition.CAP, 'download_bytes': len(payload),
                  'upstream_physical_bytes': len(payload), 'jsonl_bytes': data.stat().st_size,
                  'jsonl_sha256': sha256(data), 'card_sha256': sha256(directory / 'README.upstream.md'),
                  'upstream_info_sha256': sha256(directory / 'upstream-info.json'),
                  'upstream_tree_sha256': sha256(directory / 'upstream-tree.json'),
                  'acquisition_script_sha256': sha256(module_path.with_name('mab_acquisition.py')),
                  'romain_download_go': {'date': acquisition.GO_DATE, 'quote': acquisition.GO_QUOTE}}
    (directory / 'PROVENANCE.json').write_text(json.dumps(provenance))
    observed = write_rows(clone / '.local-qualification/observed', [])
    registry = clone / '.local-qualification/registry.json'
    required = {name: ('mab', None) for name in audit_module.REQUIRED_OBSERVED}
    monkeypatch.setattr(audit_module, 'REQUIRED_OBSERVED', required)
    registry.write_text(json.dumps({'corpora': [{'name': name, 'path': str(observed), 'format': 'mab'} for name in required]}))
    output = clone / '.local-qualification/audit'
    monkeypatch.setattr(audit_module, 'ensure_priority', lambda _: None)
    monkeypatch.setattr(audit_module, 'sample', lambda _: None)
    monkeypatch.setattr('sys.argv', ['audit', '--acquisition', str(directory), '--observed-registry', str(registry),
                                    '--output', str(output)])
    with pytest.raises(SystemExit) as stop:
        audit_module.main()
    assert stop.value.code == 2
    report = json.loads((output / 'report.json').read_text())
    assert report['complete'] and report['verdict'] == 'NO-GO'
    assert not (output / 'FAILED.json').exists()
    assert (output / 'source-review-template.json').exists()
    original = (output / 'report.json').read_bytes()
    with pytest.raises(SystemExit):
        audit_module.main()
    assert (output / 'report.json').read_bytes() == original
    data.write_text('changed')
    with pytest.raises(ValueError, match='evidence changed'):
        audit_module.verify_acquisition(directory, lambda: None)

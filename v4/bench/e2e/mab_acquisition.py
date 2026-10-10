"""Pinned single-file MemoryAgentBench LRU acquisition, no inference or installs."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import urllib.request

from .data import sha256
from .guard import Limits, ensure_priority, sample

DATASET = 'ai-hyz/MemoryAgentBench'
# Immutable complete upstream commit. Never resolve main or accept a new revision.
REVISION = '7ea066982b140a19337e17e60d45d4076e042faf'
REMOTE = 'data/Long_Range_Understanding-00000-of-00001.parquet'
FILE_SHA = '5ab175461954db67770d4a4cb69e569b513ebb96aceb9ee79b57f67488bcd539'
CAP = 55_000_000
JSONL_CAP = 150_000_000
MAX_ROW = 32_000_000
GO_QUOTE = 'download and audit MemoryAgentBench-LRU'
GO_DATE = '2026-10-10'
FIELDS = {'context', 'questions', 'answers', 'metadata'}


def write_json(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def bounded_get(url, cap, check):
    check()
    with urllib.request.urlopen(urllib.request.Request(url, headers={'Accept-Encoding': 'identity'}),
                                timeout=60) as response:
        data = response.read(cap + 1)
    check()
    if len(data) > cap:
        raise ValueError('Upstream metadata byte budget exceeded')
    return data


def pinned_metadata(check, output):
    info = json.loads(bounded_get(f'https://huggingface.co/api/datasets/{DATASET}/revision/{REVISION}',
                                 1_000_000, check))
    write_json(output / 'upstream-info.json', info)
    revision = info.get('sha', '')
    if not re.fullmatch('[0-9a-f]{40}', revision) or revision != REVISION:
        raise ValueError('Pinned complete commit changed')
    if info.get('cardData', {}).get('license') != 'mit':
        raise ValueError('Pinned dataset card does not declare MIT')
    card = bounded_get(f'https://huggingface.co/datasets/{DATASET}/resolve/{revision}/README.md',
                       128_000, check)
    (output / 'README.upstream.md').write_bytes(card)
    # Check the actual front matter as well as API metadata; retain exact card.
    front = card.decode().split('---')
    if len(front) < 3 or front[0].strip() or not re.search(r'^license:\s*mit\s*$', front[1], re.M):
        raise ValueError('Saved pinned card does not declare MIT')
    tree = json.loads(bounded_get(f'https://huggingface.co/api/datasets/{DATASET}/tree/{revision}/data',
                                 1_000_000, check))
    write_json(output / 'upstream-tree.json', tree)
    files = [entry for entry in tree if entry.get('path') == REMOTE]
    if len(files) != 1:
        raise ValueError('Exactly one approved LRU parquet required')
    entry = files[0]
    size, expected = entry.get('size'), entry.get('lfs', {}).get('oid')
    if type(size) is not int or not 0 < size <= CAP or expected != FILE_SHA:
        raise ValueError('Physical file size exceeds cap or pinned SHA-256 differs; no dataset transfer')
    return revision, size


def acquire(output, check, converter, runtime):
    revision, size = pinned_metadata(check, output)
    url = f'https://huggingface.co/datasets/{DATASET}/resolve/{revision}/{REMOTE}'
    temporary = output / 'mab_lru.parquet.partial'
    digest, total = hashlib.sha256(), 0
    with urllib.request.urlopen(urllib.request.Request(url, headers={'Accept-Encoding': 'identity'}),
                                timeout=60) as response, temporary.open('xb') as target:
        length = response.headers.get('Content-Length')
        if length is not None and int(length) != size:
            raise ValueError('HTTP physical length differs from attested upstream size')
        while block := response.read(1024 * 1024):
            check()
            total += len(block)
            if total > size or total > CAP:
                raise ValueError('Dataset transfer byte budget exceeded; partial evidence retained')
            digest.update(block)
            target.write(block)
    if total != size or digest.hexdigest() != FILE_SHA:
        raise ValueError('Physical size/SHA-256 mismatch; no conversion')
    parquet = output / 'mab_lru.parquet'
    temporary.rename(parquet)
    count, decoded = converter(parquet, output / 'mab_lru.jsonl.partial', check)
    if count != 110:
        raise ValueError('Pinned LRU requires exactly 110 context rows')
    derived = output / 'mab_lru.jsonl'
    (output / 'mab_lru.jsonl.partial').rename(derived)
    provenance = {'dataset': DATASET, 'split': 'Long_Range_Understanding',
                  'pinned_revision': REVISION, 'revision': revision, 'file': REMOTE,
                  'source_url': url, 'licence': 'MIT', 'licence_scope': 'upstream card declaration only',
                  'upstream_physical_bytes': size, 'download_bytes': total, 'download_cap_bytes': CAP,
                  'parquet_sha256': FILE_SHA, 'jsonl_sha256': sha256(derived, check=check),
                  'jsonl_bytes': decoded, 'contexts': count, 'inference_calls': 0,
                  'romain_download_go': {'date': GO_DATE, 'quote': GO_QUOTE,
                                        'scope': 'download + independence audit ONLY; 0 inference'},
                  'card_sha256': sha256(output / 'README.upstream.md', check=check),
                  'upstream_info_sha256': sha256(output / 'upstream-info.json', check=check),
                  'upstream_tree_sha256': sha256(output / 'upstream-tree.json', check=check),
                  'acquisition_script_sha256': sha256(Path(__file__), check=check),
                  'conversion_runtime': runtime,
                  'converter': 'pyarrow.iter_batches(1).to_pylist; strict json.dumps, no coercion'}
    write_json(output / 'PROVENANCE.json', provenance)
    return provenance


def converter_environment():
    import pyarrow
    from pyarrow import parquet
    if not callable(parquet.ParquetFile):
        raise ValueError('Working local pyarrow required before acquisition')
    return {'python': sys.version.split()[0], 'python_executable': sys.executable,
            'pyarrow': pyarrow.__version__}


def convert(parquet, target, check):
    import pyarrow.parquet as pq
    count, total = 0, 0
    file = pq.ParquetFile(parquet)
    if file.metadata.num_rows != 110 or len(file.schema_arrow.names) != 4 or set(file.schema_arrow.names) != FIELDS:
        raise ValueError('Unexpected pinned parquet row count/schema')
    with target.open('xb') as stream:
        for batch in file.iter_batches(batch_size=1):
            check()
            for row in batch.to_pylist():
                if set(row) != FIELDS:
                    raise ValueError('Unexpected converted row fields')
                raw = (json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n').encode()
                count += 1
                total += len(raw)
                if count > 110 or len(raw) > MAX_ROW or total > JSONL_CAP:
                    raise ValueError('Conversion byte/row budget exceeded; partial evidence retained')
                stream.write(raw)
    return count, total


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--romain-download-go', required=True, choices=[GO_QUOTE])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    clone = Path(__file__).resolve().parents[3]
    output = args.output.resolve()
    if not output.is_relative_to(clone / '.local-qualification') or output.exists():
        parser.error('Fresh output under clone/.local-qualification required; failed directories cannot restart')
    output.mkdir(mode=0o700, parents=True)
    write_json(output / 'REQUEST.json', {'go_date': GO_DATE, 'go_quote': args.romain_download_go,
                                       'dataset': DATASET, 'revision': REVISION,
                                       'cap_bytes': CAP, 'inference_calls': 0})
    try:
        ensure_priority(Limits())
        check = lambda: sample(clone)
        check()
        runtime = converter_environment()  # no install, fail BEFORE any network
        provenance = acquire(output, check, convert, runtime)
    except Exception as exc:
        write_json(output / 'FAILED.json', {'complete': False, 'failure_type': type(exc).__name__,
                                          'reason': str(exc), 'inference_calls': 0,
                                          'restart_in_place': False})
        raise
    print(json.dumps({'complete': True, 'revision': provenance['revision'],
                      'download_bytes': provenance['download_bytes'], 'contexts': provenance['contexts'],
                      'parquet_sha256': FILE_SHA, 'inference_calls': 0}))


if __name__ == '__main__':
    main()

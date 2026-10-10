#!/usr/bin/env python3
"""Claude only, after Romain's download GO: bounded single-file acquisition.

No model calls, dataset-library downloads or environment installation.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import urllib.request

REVISION = '3205395e897e7318c7b094ef4e6047b9b82dbb03'
REMOTE = 'data/500K-00000-of-00001.parquet'
CAP = 110_000_000


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--romain-download-go', required=True, help='Dated operator approval, never inferred from elapsed time')
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    clone = Path(__file__).resolve().parents[1]
    out = a.output.resolve()
    if not out.is_relative_to(clone / '.local-qualification') or out.exists():
        p.error('Fresh clone/.local-qualification output only')
    # Guards before/through download/conversion; pyarrow is already installed in
    # /opt/homebrew/bin/python3.11. No new package installation is authorized.
    sys.path.insert(0, str(clone / 'v4'))
    from bench.e2e.guard import Limits, ensure_priority, sample
    ensure_priority(Limits())
    sample(clone)
    api = f'https://huggingface.co/api/datasets/Mohammadta/BEAM/tree/{REVISION}/data'
    with urllib.request.urlopen(api, timeout=30) as response:
        metadata = json.loads(response.read(1_000_001))
    entries = [f for f in metadata if f['path'] == REMOTE]
    if len(entries) != 1:
        raise ValueError('Expected exactly one upstream 500K file')
    entry = entries[0]
    size, expected = entry['size'], entry['lfs']['oid']
    if not 0 < size <= CAP or len(expected) != 64:
        raise ValueError('Upstream metadata exceeds download budget or lacks SHA-256')
    import pyarrow.parquet as pq
    out.mkdir(mode=0o700)
    path = out / 'beam_500k.parquet'
    url = f'https://huggingface.co/datasets/Mohammadta/BEAM/resolve/{REVISION}/{REMOTE}'
    h, total = hashlib.sha256(), 0
    with urllib.request.urlopen(url, timeout=60) as response, path.open('xb') as target:
        while block := response.read(1024 * 1024):
            sample(clone)
            total += len(block)
            if total > size or total > CAP:
                raise ValueError('Download size exceeded; preserve partial evidence')
            h.update(block)
            target.write(block)
    if total != size or h.hexdigest() != expected:
        raise ValueError('Size/SHA-256 mismatch; no conversion or evaluation')
    count = 0
    derived = out / 'beam_500k.jsonl'
    with derived.open('x') as target:
        for batch in pq.ParquetFile(path).iter_batches(batch_size=1):
            sample(clone)
            for row in batch.to_pylist():
                target.write(json.dumps(row, ensure_ascii=False, default=str) + '\n')
                count += 1
    if count != 35:
        raise ValueError('Expected 35 source conversations')
    derived_sha = hashlib.sha256(derived.read_bytes()).hexdigest()
    provenance = {'dataset': 'Mohammadta/BEAM', 'revision': REVISION, 'file': REMOTE,
                  'licence': 'CC-BY-SA-4.0', 'source_url': url, 'download_bytes': total,
                  'parquet_sha256': expected, 'jsonl_sha256': derived_sha,
                  'romain_download_go': a.romain_download_go, 'conversations': count,
                  'converter': 'pyarrow.iter_batches(1).to_pylist; json.dumps(default=str)',
                  'pyarrow': __import__('pyarrow').__version__, 'inference_calls': 0}
    (out / 'PROVENANCE.json').write_text(json.dumps(provenance, indent=2) + '\n')
    print(json.dumps(provenance, indent=2))


if __name__ == '__main__':
    main()

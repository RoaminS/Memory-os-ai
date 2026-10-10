"""Bounded acquisition/conversion tested with synthetic bytes; zero network."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize('mode', ['ok', 'oversized', 'wrong_hash'])
def test_single_file_acquisition_is_bounded_creates_parents_and_attests_converter(tmp_path, monkeypatch, mode):
    original = Path(__file__).resolve().parents[2] / 'scripts/acquire_beam_500k.py'
    spec = importlib.util.spec_from_file_location('beam_acquisition_test', original)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    clone = tmp_path / 'clone'
    fake_script = clone / 'scripts/acquire_beam_500k.py'
    fake_script.parent.mkdir(parents=True)
    fake_script.write_bytes(original.read_bytes())
    module.__file__ = str(fake_script)
    output = clone / '.local-qualification/datasets/beam-500k'
    payload = b'synthetic-parquet-no-model'
    expected = hashlib.sha256(payload).hexdigest()
    metadata = [{'path': module.REMOTE, 'size': len(payload) if mode != 'oversized' else module.CAP + 1,
                 'lfs': {'oid': expected if mode != 'wrong_hash' else '0' * 64}}]
    downloads = []
    def opened(url, **kwargs):
        downloads.append(url)
        return io.BytesIO(json.dumps(metadata).encode() if '/api/' in url else payload)
    monkeypatch.setattr(module.urllib.request, 'urlopen', opened)
    monkeypatch.setattr('bench.e2e.guard.sample', lambda path: None)
    monkeypatch.setattr('bench.e2e.guard.ensure_priority', lambda limits: None)
    batch = SimpleNamespace(to_pylist=lambda: [{'conversation_id': str(i)} for i in range(35)])
    parquet = SimpleNamespace(ParquetFile=lambda path: SimpleNamespace(iter_batches=lambda batch_size: [batch]))
    monkeypatch.setitem(sys.modules, 'pyarrow', SimpleNamespace(__version__='synthetic', parquet=parquet))
    monkeypatch.setitem(sys.modules, 'pyarrow.parquet', parquet)
    monkeypatch.setattr(sys, 'argv', ['acquire', '--romain-download-go', 'synthetic test only', '--output', str(output)])
    if mode != 'ok':
        with pytest.raises(ValueError):
            module.main()
        assert not (output / 'PROVENANCE.json').exists()
        assert len(downloads) == (1 if mode == 'oversized' else 2)
    else:
        module.main()
        origin = json.loads((output / 'PROVENANCE.json').read_text())
        assert origin['parquet_sha256'] == expected and origin['download_bytes'] == len(payload)
        assert origin['converter_script_sha256'] == hashlib.sha256(fake_script.read_bytes()).hexdigest()
        assert origin['conversations'] == 35 and origin['inference_calls'] == 0
        assert len(downloads) == 2 and all(module.REVISION in url for url in downloads)
        assert len((output / 'beam_500k.jsonl').read_text().splitlines()) == 35

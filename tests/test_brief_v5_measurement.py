"""Pure protocol/measurement tests, no performance run, TCP, or real machine guard."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('brief_v5_perf',ROOT/'scripts/qualify_brief_v5_performance.py')
perf=importlib.util.module_from_spec(spec)
spec.loader.exec_module(perf)


def test_measurement_uses_sealed_parameters_and_frozen_query_sequence():
    protocol,digest=perf.sealed_protocol()
    assert len(digest)==64
    assert protocol['performance']['seed_items']==10000
    assert protocol['performance']['warmups']==10
    assert protocol['performance']['sequential_calls']==100
    assert [perf.query(i) for i in range(10)]==[perf.query(i+10) for i in range(10)]
    assert perf.stats(list(range(100))) == {'p50':50,'p95':94,'max':99}


def test_measurement_refuses_tool_errors_and_oversized_envelopes():
    with pytest.raises(ValueError,match='tool error'):
        perf.measure_result(SimpleNamespace(isError=True),len)
    from memory_os_v4.brief import envelope
    with pytest.raises(ValueError,match='budget exceeded'):
        perf.measure_result(envelope({'text':'x'*7000,'wire_format':'compact_v2'}),len)


def test_measurement_refuses_dirty_protocol_seal(tmp_path,monkeypatch):
    monkeypatch.setattr(perf,'FIXTURE',tmp_path)
    (tmp_path/'protocol_v1.json').write_text('{}')
    (tmp_path/'protocol_v1.sha256').write_text('0'*64)
    with pytest.raises(ValueError,match='seal mismatch'):
        perf.sealed_protocol()

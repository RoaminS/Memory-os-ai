"""Small disposable-tree archive tests; never target real qualification data."""
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def module(monkeypatch):
    root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root / 'scripts'))
    spec = importlib.util.spec_from_file_location('archive_case', root / 'scripts/archive_pytest_evidence.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def source_case(tmp_path, module, monkeypatch):
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    run = tmp_path / '.local-qualification/run'
    source = run / 'pytest-tmp'
    source.mkdir(parents=True)
    (source / 'evidence.json').write_text('{"fact":"preserved"}')
    (source / 'outside-link').symlink_to('/path/that/must/not/be/read')
    report = run / 'report.json'
    report.write_text(json.dumps({'ok': False}))
    return source, report, run / 'pytest-tmp.tar.gz'


def test_simulation_never_creates_archive_or_removes_source(tmp_path, module, monkeypatch):
    source, report, output = source_case(tmp_path, module, monkeypatch)
    before = module.inventory(source)
    plan = module.archive(source, report, output)
    assert plan['mode'] == 'simulation' and plan['disk_bytes_reclaimed'] == 0
    assert not output.exists() and module.inventory(source) == before


def test_archive_preserves_bytes_metadata_links_and_source(tmp_path, module, monkeypatch):
    import os
    source, report, output = source_case(tmp_path, module, monkeypatch)
    os.link(source / 'evidence.json', source / 'hardlink.json')
    before = module.inventory(source)
    monkeypatch.setattr(module.os, 'getpriority', lambda *args: 15)
    monkeypatch.setattr(module, 'resources', lambda root: {'free_bytes': 20_000_000_000})
    result = module.archive(source, report, output, apply=True, attest_finished=True)
    assert result['verified'] and result['disk_bytes_reclaimed'] == 0
    module.verify(output, before)
    assert module.inventory(source) == before
    assert module.sha(output) == result['archive_sha256']
    assert Path(str(output) + '.manifest.json').exists()
    with pytest.raises(ValueError, match='Fresh'):
        module.archive(source, report, output, apply=True, attest_finished=True)


def test_machine_guard_refuses_before_output_creation(tmp_path, module, monkeypatch):
    source, report, output = source_case(tmp_path, module, monkeypatch)
    monkeypatch.setattr(module.os, 'getpriority', lambda *args: 15)
    monkeypatch.setattr(module, 'resources', lambda root: {'free_bytes': 3_790_000_000})
    with pytest.raises(ValueError, match='temporary space'):
        module.archive(source, report, output, apply=True, attest_finished=True)
    assert not output.exists() and source.exists()


def test_source_change_keeps_partial_and_refuses_publication(tmp_path, module, monkeypatch):
    source, report, output = source_case(tmp_path, module, monkeypatch)
    monkeypatch.setattr(module.os, 'getpriority', lambda *args: 15)
    monkeypatch.setattr(module, 'resources', lambda root: {'free_bytes': 20_000_000_000})
    original = module.verify

    def changed(archive, expected, **kwargs):
        original(archive, expected, **kwargs)
        (source / 'evidence.json').write_text('changed')

    monkeypatch.setattr(module, 'verify', changed)
    with pytest.raises(ValueError, match='Source/report changed'):
        module.archive(source, report, output, apply=True, attest_finished=True)
    assert not output.exists() and list(output.parent.glob('*.partial-*'))

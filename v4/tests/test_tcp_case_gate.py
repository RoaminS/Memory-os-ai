"""JUnit gate regressions: no TCP, model, guard or operator config access."""
import importlib.util
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parents[2]
support_spec = importlib.util.spec_from_file_location('test_home_guard', ROOT/'scripts/test_home_guard.py')
support = importlib.util.module_from_spec(support_spec)
support_spec.loader.exec_module(support)
spec = importlib.util.spec_from_file_location('qualification_tcp_gate', ROOT/'scripts/qualify_405_test_isolation.py')
gate = importlib.util.module_from_spec(spec)
# Restore only this injected name. patch.dict(sys.modules) would remove every
# dependency first imported inside the block, splitting Pydantic class caches.
with pytest.MonkeyPatch.context() as override:
    override.setitem(sys.modules, 'test_home_guard', support)
    spec.loader.exec_module(gate)


def cases():
    result = []
    for identity in gate.EXPECTED_TCP:
        module, name = identity.split('::', 1)
        result.append(ET.Element('testcase', classname='tests.'+module, name=name))
    return result


def test_all_32_named_cases_qualify_regardless_of_order():
    assert len(gate.EXPECTED_TCP) == len(set(gate.EXPECTED_TCP)) == 32
    result = gate.tcp_evidence(list(reversed(cases())) + [ET.Element('testcase', classname='tests.other', name='unrelated')])
    assert result == {'ok': True, 'expected_cases': 32, 'observed_cases': 32,
                      'missing': [], 'unexpected': [], 'unsuccessful': []}


@pytest.mark.parametrize('change', ['missing', 'added', 'renamed', 'duplicate-replaces-missing'])
def test_case_identity_change_cannot_pass_even_with_equal_count(change):
    nodes = cases()
    if change == 'missing':
        nodes.pop()
    elif change == 'added':
        nodes.append(ET.Element('testcase', classname='tests.test_l3_tcp', name='unapproved'))
    elif change == 'renamed':
        nodes[-1].set('name', 'unapproved')
    else:
        nodes[-1] = nodes[0]
    proof = gate.tcp_evidence(nodes)
    assert not proof['ok'] and (proof['missing'] or proof['unexpected'])
    if change in ('renamed', 'duplicate-replaces-missing'):
        assert proof['observed_cases'] == 32  # former bare-count gate would accept


@pytest.mark.parametrize('status', ['failure', 'error', 'skipped'])
def test_unsuccessful_case_never_qualifies(status):
    nodes = cases()
    ET.SubElement(nodes[0], status)
    proof = gate.tcp_evidence(nodes)
    assert not proof['ok'] and proof['unsuccessful'] == [gate.EXPECTED_TCP[0]]
    assert not proof['missing'] and not proof['unexpected']


def test_gate_import_preserves_pydantic_class_identity():
    from pydantic import RootModel,BaseModel
    assert BaseModel in RootModel.__mro__

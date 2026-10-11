"""A green subset or skipped TCP must not be certified as a full qualification."""
import importlib.util
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest


def load_runner():
    path = Path(__file__).parents[2] / "scripts/qualify_404_increment1.py"
    spec = importlib.util.spec_from_file_location("increment1_gate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_xml(path, count, classname, *, skipped=0):
    root = ET.Element("testsuite", tests=str(count), failures="0", errors="0", skipped=str(skipped))
    for i in range(count):
        ET.SubElement(root, "testcase", classname=classname, name=f"case_{i}")
    ET.ElementTree(root).write(path)


def test_green_subset_and_missing_tcp_are_rejected(tmp_path):
    gate = load_runner().test_counts
    path = tmp_path / "pytest.xml"
    make_xml(path, 1, "tests.test_server_http_e2e")
    with pytest.raises(RuntimeError, match="incomplete"):
        gate(path, "tcp")
    make_xml(path, 1200, "tests.test_unit")
    with pytest.raises(RuntimeError, match="incomplete"):
        gate(path, "full")


def test_tcp_count_and_skip_gate(tmp_path):
    gate = load_runner().test_counts
    path = tmp_path / "pytest.xml"
    make_xml(path, 12, "tests.test_server_http_e2e")
    assert gate(path, "tcp")["tcp_cases"] == 12
    make_xml(path, 12, "tests.test_server_http_e2e", skipped=1)
    with pytest.raises(RuntimeError, match="skipped"):
        gate(path, "tcp")

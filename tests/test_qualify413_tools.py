"""4.1.3 Lot 3: static checks of the Linux-cell and container qualification tooling (it runs on CI runners only)."""
import importlib.util
from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/linux-cells.yml").read_text()
DOCKERFILE = (ROOT / "Dockerfile").read_text()


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools/qualify413" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_action_is_pinned_by_commit_sha_and_nothing_is_published():
    uses = re.findall(r"uses:\s*(\S+)", WORKFLOW)
    assert uses and all(re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", use) for use in uses), uses
    assert "permissions:\n  contents: read" in WORKFLOW
    for forbidden in ("docker push", "docker login", "twine", "pypi-publish", "gh release", "packages: write"):
        assert forbidden not in WORKFLOW


def test_the_workflow_runs_the_tools_that_exist_on_the_retained_runners():
    for path in ("tools/qualify413/linux_cell.py", "tools/qualify413/container_smoke.py",
                 "tools/locks/411/entree_lexical.txt"):
        assert path in WORKFLOW and (ROOT / path).is_file(), path
    assert "runs-on: ubuntu-24.04-arm" in WORKFLOW and "runs-on: ubuntu-24.04\n" in WORKFLOW
    assert 'UV_VERSION: "0.13.9"' in WORKFLOW and "--exclude-newer 2026-11-24T00:00:00Z" in WORKFLOW
    assert "x86_64-manylinux_2_28" in WORKFLOW
    # x86_64 is qualified only on request, once its lock is embedded
    assert "if: github.event_name == 'workflow_dispatch' && inputs.qualify_x86_64" in WORKFLOW


def test_the_dockerfile_contract():
    assert "USER 10001:10001" in DOCKERFILE and "useradd --uid 10001" in DOCKERFILE
    assert '"--container-listen-all"' in DOCKERFILE and '"--profile", "lexical"' in DOCKERFILE
    assert "MEMORY_V4_TOKEN_FILE=/run/memory-os/http.token" in DOCKERFILE
    assert not re.search(r"(?m)^\s*ENV[^\n]*MEMORY_V4_TOKEN=", DOCKERFILE)       # never a token in the environment
    assert "HEALTHCHECK" in DOCKERFILE and "/ready" in DOCKERFILE
    assert "127.0.0.1:8765:8765" in DOCKERFILE                                    # documented loopback publication
    assert "uv python install" in DOCKERFILE and "(3,51,3)" in DOCKERFILE        # standalone CPython, SQLite read back
    assert (ROOT / ".dockerignore").read_text().splitlines()[-2:] == ["*", "!dist/*.whl"]


def test_linux_cell_helpers(tmp_path):
    tool = load("linux_cell")
    text = "notice line\n{\"a\": 1}\nmore\n[{\"status\": \"PASS\"}]\n"
    assert tool.Run.last_json(text) == [{"status": "PASS"}]
    with pytest.raises(tool.Refused):
        tool.Run.last_json("no json at all")
    (tmp_path / "x").write_text("1")
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "y").write_text("2")
    before = tool.inventory(tmp_path, exclude=("logs",))
    (tmp_path / "logs" / "y").write_text("3")
    assert tool.inventory(tmp_path, exclude=("logs",)) == before
    (tmp_path / "x").write_text("changed")
    assert tool.inventory(tmp_path, exclude=("logs",)) != before
    assert set(tool.ALL_GATES) == {"P1", "P3", "P4", "P5", "P6", "P7"}


def test_linux_cell_refuses_outside_linux(monkeypatch, capsys):
    tool = load("linux_cell")
    monkeypatch.setattr(tool.sys, "platform", "darwin")
    assert tool.main(["--cell", "ubuntu2404-arm64-py311", "--dist", "d", "--cpython", "3.11.18", "--output", "o"]) == 2
    assert "Linux runner is required" in capsys.readouterr().err


def test_p6_worker_keeps_the_frozen_data_of_4_1_1():
    worker = load("p6_worker")
    assert worker.PLAN_FORMAT == "qualify413-linux-p6-v1"
    assert len(worker.LEXICAL_FACTS) >= 3 and len(worker.LEXICAL_QUERIES) >= 3
    assert len(worker.fixed_data_sha256()) == 64

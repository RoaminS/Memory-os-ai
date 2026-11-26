"""4.1.3 Lot 3: static checks of the Linux-cell and container qualification tooling (it runs on CI runners only)."""
import importlib.util
import json
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
    # D8: never by default; the opt-in is appended at docker run (Codex Lot 3 r1 item 2)
    assert 'ENTRYPOINT ["/opt/memory-os/venv/bin/memory-os-admin", "serve", "--profile", "lexical"]' in DOCKERFILE
    assert "--container-listen-all\n" in DOCKERFILE and "ENTRYPOINT" not in DOCKERFILE.split("--container-listen-all")[-1].split("\n")[0]
    assert "http_get(s.ready_url" in DOCKERFILE and "urlopen" not in DOCKERFILE   # no proxy, no redirect (item 1)
    assert "MEMORY_V4_TOKEN_FILE=/run/memory-os/http.token" in DOCKERFILE
    assert not re.search(r"(?m)^\s*ENV[^\n]*MEMORY_V4_TOKEN=", DOCKERFILE)       # never a token in the environment
    assert "HEALTHCHECK" in DOCKERFILE and "s.ready_url" in DOCKERFILE
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


def p1_list(cell):
    return json.loads((ROOT / f"tools/qualify413/p1_deselection_{cell}.json").read_text())


@pytest.mark.parametrize("cell", ["ubuntu2404-arm64-py311", "ubuntu2404-x86_64-py311"])
def test_p1_lists_are_motivated_and_name_what_exists_in_the_tree(cell):
    tool = load("linux_cell")
    listed = p1_list(cell)
    assert tool.p1_list_problems(listed, cell) == []
    assert listed["origine"]["format"] == "qual411-p1-deselection-v7" and len(listed["origine"]["sha256"]) == 64
    common, own = listed["lexicale"], listed["par_cellule"][cell]
    assert set(listed["par_cellule"]) == {cell} and common["ignore"] and common["deselect"] and own["deselect"]
    for entry in common["ignore"]:
        assert (ROOT / "v4/tests" / entry["fichier"]).is_file(), entry["fichier"]
    for entry in common["deselect"] + own["deselect"]:
        path, _, rest = entry["noeud"].partition("::")
        source = (ROOT / "v4" / path).read_text()
        assert f"def {rest.split('::')[-1].split('[')[0]}(" in source, entry["noeud"]
    for entry in listed["sauts_prevus"]:
        assert entry["raison"] in (ROOT / "v4/tests" / entry["fichier"]).read_text(), entry
    # ported 4.1.1 entries and new 4.1.3 ones are told apart; dropped v7 entries say why
    assert {e["origine"] for e in common["ignore"] + common["deselect"] + own["deselect"]} <= {
        "v7", "v7 (S1 reclassé b)", "4.1.3"}
    assert all(d["pourquoi"] for d in listed["retirees"])


def test_p1_lists_of_the_two_cells_differ_only_by_the_cell():
    arm, x86 = p1_list("ubuntu2404-arm64-py311"), p1_list("ubuntu2404-x86_64-py311")
    x86.pop("note_cellule")
    for doc, cell in ((arm, "ubuntu2404-arm64-py311"), (x86, "ubuntu2404-x86_64-py311")):
        doc["par_cellule"] = doc["par_cellule"][cell]
        doc.pop("cellule")
    assert arm == x86


def test_p1_list_check_refuses_unmotivated_or_foreign_entries():
    tool = load("linux_cell")
    good = p1_list("ubuntu2404-arm64-py311")
    assert tool.p1_list_problems(good, "ubuntu2404-x86_64-py311")  # another cell's list
    assert tool.p1_list_problems({"format": "qualify413-p1-v1", "tests": []}, "ubuntu2404-arm64-py311")
    for mutate in (lambda d: d["lexicale"]["deselect"][0].pop("preuve"),
                   lambda d: d["lexicale"]["deselect"][0].update(couverture=" "),
                   lambda d: d["lexicale"]["ignore"][0].update(categorie="zz"),
                   lambda d: d["lexicale"]["deselect"].append(dict(d["lexicale"]["deselect"][0])),
                   lambda d: d["lexicale"]["deselect"][0].update(noeud="../tests/x.py::t"),
                   lambda d: d["sauts_prevus"][0].pop("motif"),
                   lambda d: d["par_cellule"].update({"windows-x86_64-py311": {}})):
        bad = json.loads(json.dumps(good))
        mutate(bad)
        assert tool.p1_list_problems(bad, "ubuntu2404-arm64-py311"), mutate


def test_p1_command_is_that_of_the_4_1_1_gate(tmp_path):
    tool = load("linux_cell")
    cell = "ubuntu2404-arm64-py311"
    listed = p1_list(cell)
    argv = tool.p1_pytest_argv(Path("py"), listed, cell, tmp_path / "bt", tmp_path / "j.xml")
    assert "v4/tests" in argv and "tests" not in argv and "-rs" in argv
    assert argv[argv.index("-o") + 1] == "faulthandler_timeout=60" and argv[argv.index("not slow") - 1] == "-m"
    selection = tool.p1_selection(listed, cell)
    n_common = len(listed["lexicale"]["ignore"]) + len(listed["lexicale"]["deselect"])
    assert len(selection) == n_common + len(listed["par_cellule"][cell]["deselect"])
    assert all(s.startswith(("--ignore=v4/tests/", "--deselect=v4/tests/")) for s in selection)
    assert len(tool.p1_selection(listed, "ubuntu2404-x86_64-py311")) == n_common  # no section of another cell
    env = tool.p1_environment(tmp_path / "venv", tmp_path / "home")
    assert env["PYTHONPATH"] == f"{ROOT / 'v4/src'}:{ROOT / 'v4'}" and "MEMORY_V4_BUILD_COMMIT" not in env
    assert env["MEMORY_TEST_HASH_V3"] == "1" and env["MEMORY_V4_EMBED_MODEL"] == "hash"
    probe = {"package": str(ROOT / "v4/src/memory_os_v4/__init__.py"), "distribution_installed": False,
             "pythonpath": env["PYTHONPATH"], "build_commit": None}
    assert tool.p1_probe_ok(probe, env)
    for key, value in (("distribution_installed", True), ("build_commit", "abc"),
                       ("package", "/site-packages/memory_os_v4/__init__.py")):
        assert not tool.p1_probe_ok({**probe, key: value}, env)


def test_p1_junit_judgement(tmp_path):
    tool = load("linux_cell")
    junit = tmp_path / "j.xml"
    junit.write_text('<testsuites><testsuite>'
                     '<testcase classname="v4.tests.test_a" name="t1"/>'
                     '<testcase classname="v4.tests.test_ops_e2e" name="t2"><skipped message="loopback bind forbidden '
                     'in this sandbox"/></testcase>'
                     '</testsuite></testsuites>')
    skips = [{"fichier": "test_ops_e2e.py", "raison": "loopback bind forbidden in this sandbox", "motif": "m"}]
    assert tool.judge_p1_junit(junit, skips)["ok"]
    verdict = tool.judge_p1_junit(junit, [])
    assert not verdict["ok"] and verdict["n_blocking_skips"] == 1
    junit.write_text(junit.read_text().replace('name="t1"/>', 'name="t1"><failure message="x"/></testcase>'))
    assert tool.judge_p1_junit(junit, skips)["n_failures"] == 1 and not tool.judge_p1_junit(junit, skips)["ok"]


def test_p1_workdir_exists_in_a_fresh_checkout(tmp_path):
    tool = load("linux_cell")
    basetemp, junit = tool.p1_workdir(tmp_path, "p1-cell")
    assert basetemp.parent == junit.parent == tmp_path / ".local-qualification" and basetemp.parent.is_dir()
    assert not basetemp.exists() and junit.name == "p1-cell-junit.xml"

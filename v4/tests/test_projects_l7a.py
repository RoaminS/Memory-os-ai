"""Registry approval, immutable source labels, joint lexical scope, both stores."""
import hashlib
import json

import pytest
from pydantic import ValidationError

from memory_os_v4 import Config, MemoryV4, server
from memory_os_v4.projects import ProjectRegistry, RegistryDocument, matches


def document():
    return {"schema_version": 1, "projects": [
        {"id": "prj_alpha", "aliases": ["old-alpha", "alpha-clone"], "contexts": [
            {"id": "main", "root": "/work/alpha", "remote_hint": "same-remote"},
            {"id": "worktree", "root": "/work/wt", "git_root_hint": "/work/alpha"},
            {"id": "clone", "root": "/work/clone", "remote_hint": "same-remote"}]},
        {"id": "prj_fork", "aliases": ["fork"], "contexts": [
            {"id": "fork", "root": "/work/fork", "remote_hint": "same-remote"}]},
        {"id": "prj_notes", "aliases": ["notes"], "contexts": [{"id": "notes", "root": "/notes"}]}]}


def write_registry(tmp_path, doc=None):
    p = tmp_path / "projects.json"
    p.write_text(json.dumps(doc or document()))
    p.chmod(0o600)
    return p


def test_contexts_require_explicit_approval_not_git_hints(tmp_path):
    r = ProjectRegistry.load(str(write_registry(tmp_path)))
    for root in ("/work/alpha", "/work/wt", "/work/clone"):
        assert r.select_context(root)["project_id"] == "prj_alpha"
    assert r.select_context("/work/fork")["project_id"] == "prj_fork"
    assert r.select_context("/notes")["project_id"] == "prj_notes"
    for moved in ("/work/moved", "same-remote", "/work/alpha/unregistered"):
        with pytest.raises(ValueError, match="project_unknown"):
            r.select_context(moved)
    assert r.resolve("old-alpha") == "prj_alpha"
    scope = r.resolve("alpha-clone", read=True)
    assert matches("old-alpha", scope) and not matches("fork", scope)
    assert scope != "old-alpha"  # no magic equality/coercion of historical labels


@pytest.mark.parametrize("mutation", ["id", "alias", "root", "context", "wildcard", "blank", "version", "extra"])
def test_registry_invalid_rejected(tmp_path, mutation):
    d = document()
    first, second = d["projects"][:2]
    if mutation == "id": second["id"] = first["id"]
    if mutation == "alias": second["aliases"] = ["old-alpha"]
    if mutation == "root": second["contexts"][0]["root"] = "/work/alpha"
    if mutation == "context": second["contexts"][0]["id"] = "main"
    if mutation == "wildcard": first["aliases"] = ["*"]
    if mutation == "blank": first["aliases"] = [" "]
    if mutation == "version": d["schema_version"] = 2
    if mutation == "extra": first["automatic_merge"] = True
    with pytest.raises(ValidationError):
        RegistryDocument.model_validate(d)


def test_registry_snapshot_and_safe_file(tmp_path):
    p = write_registry(tmp_path)
    registry = ProjectRegistry.load(str(p))
    digest = hashlib.sha256(p.read_bytes()).hexdigest()
    p.write_text('{"schema_version":1,"schema_version":1,"projects":[]}')
    with pytest.raises(ValueError, match="duplicate"):
        ProjectRegistry.load(str(p))
    assert registry.sha256 == digest and registry.resolve("old-alpha") == "prj_alpha"
    p.chmod(0o666)
    with pytest.raises(ValueError, match="registry_invalid"):
        ProjectRegistry.load(str(p))
    link = tmp_path / "link"
    link.symlink_to(p)
    with pytest.raises(OSError):
        ProjectRegistry.load(str(link))


@pytest.fixture(params=["jsonl", "sqlite_s1"])
def engine(request, tmp_path, monkeypatch):
    config = Config(data_dir=str(tmp_path / "store"), embed_model="hash", hash_dim=16,
                    store_backend=request.param, project_registry=str(write_registry(tmp_path)),
                    use_recency=False, use_entities=False, w_dense=0)
    e = MemoryV4(config=config)
    monkeypatch.setattr(server, "get_engine", lambda: e)
    monkeypatch.setattr(server, "_engine", e)
    monkeypatch.setenv("MEMORY_V4_PROJECT", "*")
    # Simulate pre-existing rows, no relabel/re-index/migration on registry activation.
    for label in ("old-alpha", "alpha-clone", "prj_alpha", "fork"):
        e.add_facts([{"text": f"Unique {label} evidence telescope", "item_id": label}], project=label)
    e.blocks.set("working_rules", "historical instructions", project="old-alpha")
    yield e
    e.close()


def call(tool, **args):
    result = server.dispatch(tool, args)
    assert result.get("ok") is not False, result
    return result


def test_aliases_filter_before_ranking_all_read_surfaces(engine):
    old = {i.id: i.to_dict() for i in engine.store.all(False)}
    args = dict(query="telescope", top_k=3, project="prj_alpha")
    hits = call("memory_search", **args)["hits"]
    assert {h["id"] for h in hits} == {"old-alpha", "alpha-clone", "prj_alpha"}
    assert call("memory_search", **{**args, "project": "old-alpha"})["hits"] == hits
    assert len(engine.retriever.lexical_index(project=engine.projects.resolve("prj_alpha", read=True)).doc_len) == 3
    assert len(call("memory_timeline", project="prj_alpha", query="telescope")["items"]) == 3
    brief = call("memory_session_brief", project="alpha-clone")
    assert {f["id"] for f in brief["key_facts"]} == {"old-alpha", "alpha-clone", "prj_alpha"}
    assert brief["blocks"]["working_rules"] == "historical instructions"
    assert old.keys() == {i.id for i in engine.store.all(False)}
    for i in engine.store.all(False):
        assert i.project == old[i.id]["project"] and i.text == old[i.id]["text"]


def test_writes_canonical_receipt_scope_and_unknown_refusal(engine):
    args = {"project": "old-alpha", "request_id": "stable", "name": "project_state", "text": "canonical"}
    pre = call("memory_request_status", operation="memory_block_set", arguments=args)
    assert json.loads(pre["scope"]) == ["project", "prj_alpha"]
    first = call("memory_block_set", **args)
    assert call("memory_block_set", **{**args, "project": "prj_alpha"}) == first
    assert len(engine.blocks.history("project_state", project="prj_alpha")) == 1
    assert call("memory_block_get", project="alpha-clone")["blocks"]["project_state"] == "canonical"
    for name, arguments in (("memory_search", {"query": "telescope"}),
                            ("memory_block_set", {"name": "project_state", "text": "bad"})):
        result = server.dispatch(name, {**arguments, "project": "unapproved"})
        assert result["ok"] is False and "project_unknown" in result["error"]
    assert engine.blocks.get("project_state", project="unapproved") == ""


def test_conflicting_alias_blocks_fail_closed_then_canonical_override(engine):
    engine.blocks.set("working_rules", "conflict", project="alpha-clone")
    result = server.dispatch("memory_block_get", {"project": "prj_alpha"})
    assert "project_alias_block_conflict" in result["error"]
    call("memory_block_set", project="prj_alpha", name="working_rules", text="approved")
    assert call("memory_block_get", project="old-alpha")["blocks"]["working_rules"] == "approved"
    assert engine.blocks.get("working_rules", project="old-alpha") == "historical instructions"


def test_scoped_forget_cannot_cross_project(engine):
    assert server.dispatch("memory_forget", {"project": "prj_alpha", "item_id": "fork", "reason": "test"})["ok"] is False
    call("memory_forget", project="prj_alpha", item_id="old-alpha", reason="test")
    assert engine.store.get("old-alpha").deleted and not engine.store.get("fork").deleted


@pytest.mark.parametrize("backend", ["jsonl", "sqlite_s1"])
def test_activation_reopen_does_not_rewrite_sources(tmp_path, backend):
    config = Config(data_dir=str(tmp_path / "store"), embed_model="hash", hash_dim=16, store_backend=backend)
    e = MemoryV4(config=config)
    e.add_facts([{"text": "Legacy telescope history", "item_id": "old"}], project="old-alpha")
    e.blocks.set("working_rules", "preserved", project="old-alpha")
    e.flush()
    before = [i.to_dict() for i in (e.store.history("old") if hasattr(e.store, "history") else e.store.all(True))]
    blocks = [vars(b).copy() for b in e.blocks.history("working_rules", project="old-alpha")]
    e.close()
    originals = {p.name: p.read_bytes() for p in (tmp_path / "store").glob("*.jsonl")}
    config.project_registry = str(write_registry(tmp_path))
    opened = MemoryV4(config=config)
    try:
        assert [i.to_dict() for i in (opened.store.history("old") if hasattr(opened.store, "history") else opened.store.all(True))] == before
        assert [vars(b) for b in opened.blocks.history("working_rules", project="old-alpha")] == blocks
        assert {p.name: p.read_bytes() for p in (tmp_path / "store").glob("*.jsonl")} == originals
    finally:
        opened.close()


def test_more_read_surfaces_share_alias_filter(engine):
    engine.add_document("old doc", "Telescope reference old", project="old-alpha", doc_ref="old-doc")
    engine.add_document("other doc", "Telescope reference foreign", project="fork", doc_ref="other-doc")
    assert call("memory_search_occurrences", keyword="telescope", project="prj_alpha")["count"] == 4
    docs = call("memory_list_documents", project="prj_alpha")
    assert "other-doc" not in json.dumps(docs) and "old-doc" in json.dumps(docs)
    context = call("memory_get_context", query="telescope", project="prj_alpha")
    assert "Unique fork evidence" not in json.dumps(context)


def test_invalid_registry_fails_before_store_created(tmp_path):
    p = write_registry(tmp_path, {"schema_version": 9, "projects": []})
    with pytest.raises(ValueError):
        MemoryV4(config=Config(data_dir=str(tmp_path / "never-created"), project_registry=str(p), embed_model="hash"))
    assert not (tmp_path / "never-created").exists()


def test_explicit_global_blocks_do_not_require_default_project(engine):
    call("memory_block_set", scope="global", name="user_profile", text="explicit global")
    assert call("memory_block_get", scope="global")["blocks"]["user_profile"] == "explicit global"
    assert call("memory_block_get", project="prj_alpha")["blocks"]["user_profile"] == "explicit global"

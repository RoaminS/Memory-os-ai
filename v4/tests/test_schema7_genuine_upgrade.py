"""Real fdd60cb source creates schema seven; no DROP/PRAGMA downgrade simulation."""

from contextlib import closing
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time

import pytest

from memory_os_v4.sqlite_store import MIN_SQLITE, SQLiteStore

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from schema8_inventory import inventory, readonly, verify_upgrade  # noqa: E402

OLD = "fdd60cb0c9fee95f2f32471e93d77a2873a3d946"
pytestmark = pytest.mark.skipif(sqlite3.sqlite_version_info < MIN_SQLITE, reason="SQLite runtime too old")
SEED = """
import json, sys
from pathlib import Path
from memory_os_v4 import Config, MemoryV4, server
from memory_os_v4.sqlite_store import SCHEMA_VERSION
assert SCHEMA_VERSION == 7
assert Path(server.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve())
with MemoryV4(config=Config(data_dir=sys.argv[2], store_backend='sqlite_s1', work_state=True, embed_model='hash', hash_dim=16)) as engine:
    server._engine = engine
    def call(name, args):
        r = server.dispatch(name, args)
        assert r.get('ok', True), r
        return r
    r = call('memory_chat_save', {'project_label':'upgrade', 'request_id':'chat',
        'messages':[{'role':'user','content':'The original decision has provenance.'}],
        'facts':[{'text':'The project uses the original limit.'}]})
    old = r['facts']['added'][0]
    call('memory_add_facts', {'project':'upgrade','request_id':'correction',
        'facts':[{'text':'The project uses the corrected limit.', 'replaces':[old]}]})
    r = call('memory_add_facts', {'project':'upgrade','request_id':'to-forget',
        'facts':[{'text':'Temporary fixture fact to forget.'}]})
    call('memory_forget', {'project':'upgrade', 'request_id':'forget', 'item_id':r['added'][0], 'reason':'Synthetic cleanup'})
    block = call('memory_block_compare_and_set', {'project':'upgrade','request_id':'block1',
        'scope':'project', 'name':'state', 'text':'Original block.', 'expected_etag':None, 'reason':'Fixture'})
    call('memory_block_compare_and_set', {'project':'upgrade','request_id':'block2',
        'scope':'project', 'name':'state', 'text':'Corrected block.', 'expected_etag':block['etag'], 'reason':'Fixture update'})
    t = call('memory_task_create', {'project':'upgrade','request_id':'task1', 'title':'Preserve task history', 'owner':{'client_id':'codex'}})
    call('memory_task_update', {'project':'upgrade','request_id':'task2', 'task_id':t['task_id'],
        'expected_etag':t['etag'], 'patch':{'status':'blocked','blocked_reason':'Waiting for operator','next_action':'Ask the operator for review'}, 'reason':'Fixture update'})
    engine.ensure_retrieval()
    assert engine.store.check_integrity()
"""


def genuine_store(tmp_path):
    tree = tmp_path / "fdd60cb-source"
    tree.mkdir()
    archive = subprocess.run(
        ["git", "archive", OLD, "v4/src", "pyproject.toml"], cwd=ROOT, check=True, capture_output=True
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tf:
        tf.extractall(tree, filter="data")
    seed = tree / "seed.py"
    seed.write_text(SEED)
    home = tmp_path / "private-home"
    home.mkdir()
    env = {
        **os.environ,
        "HOME": str(home),
        "PYTHONPATH": str(tree / "v4/src"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "MEMORY_V4_BUILD_COMMIT": OLD,
        "MEMORY_V4_EMBED_MODEL": "hash",
    }
    for key in ("MEMORY_V4_DIR", "MEMORY_V4_PROJECT_REGISTRY", "MEMORY_V4_TOKEN_FILE"):
        env.pop(key, None)
    source = tmp_path / "schema7-source"
    result = subprocess.run(
        [sys.executable, "-B", str(seed), str(tree), str(source)],
        cwd=tree,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return source, tree, env


def test_genuine_schema7_upgrade_preserves_every_existing_row_and_reopen(tmp_path):
    source, tree, env = genuine_store(tmp_path)
    source_bytes = (source / "store.sqlite3").read_bytes()
    with closing(readonly(source / "store.sqlite3")) as con:
        before = inventory(con)
    assert before["schema"] == 7 and "change_events" not in before["tables"]
    assert len(before["tables"]["task_versions"]) == 2
    assert len(before["tables"]["block_versions"]) == 2
    assert len(before["tables"]["request_receipts"]) >= 8
    assert len(before["tables"]["item_versions"]) > len(before["tables"]["items"])
    assert before["tables"]["embedding_cache"]
    target = tmp_path / "candidate-copy"
    shutil.copytree(source, target)
    start = time.time()
    with closing(SQLiteStore(target)) as store:
        after = inventory(store._connection)
        proof = verify_upgrade(before, after, started=start, ended=time.time())
        assert store.check_integrity()
    assert proof["cache_rows_preserved"] > 0 and proof["event_count"] == 0
    with closing(SQLiteStore(target)) as store:
        assert inventory(store._connection) == after  # no second generation / coverage reset
    assert (source / "store.sqlite3").read_bytes() == source_bytes
    # Before any new writes, rollback is an untouched pre-upgrade copy + old runtime.
    rollback = tmp_path / "rollback-copy"
    shutil.copytree(source, rollback)
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            "from contextlib import closing; from memory_os_v4.sqlite_store import SQLiteStore; import sys; "
            "s=SQLiteStore(sys.argv[1]); assert s.check_integrity(); s.close()",
            str(rollback),
        ],
        cwd=tree,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    with closing(readonly(rollback / "store.sqlite3")) as con:
        assert inventory(con) == before


@pytest.mark.parametrize(
    "damage", ["receipt", "task", "block", "history", "cache", "extra_table", "journal_time"]
)
def test_exhaustive_proof_refuses_unexpected_change(tmp_path, damage):
    # Exercise the comparator separately without generating another archived store.
    import copy

    # Use a small current store's exact inventory, then project just migration eight out.
    with closing(SQLiteStore(tmp_path / "db")) as store:
        after = inventory(store._connection)
    before = copy.deepcopy(after)
    before["schema"] = 7
    before["objects"] = [row for row in before["objects"] if row[2] != "change_events"]
    del before["tables"]["change_events"]
    before["tables"]["schema_migrations"] = [r for r in before["tables"]["schema_migrations"] if r[0] < 8]
    before["tables"]["store_metadata"] = [
        r for r in before["tables"]["store_metadata"] if r[0] != "change_journal"
    ]
    # Fresh stores need an identity for the comparator (engine normally enrolls it).
    meta = ["metadata", json.dumps({"store_instance_id": "synthetic"})]
    before["tables"]["store_metadata"] = [meta]
    after["tables"]["store_metadata"] = [meta] + [
        r for r in after["tables"]["store_metadata"] if r[0] == "change_journal"
    ]
    if damage == "extra_table":
        after["tables"]["unexpected"] = []
    elif damage == "journal_time":
        row = next(r for r in after["tables"]["store_metadata"] if r[0] == "change_journal")
        journal = json.loads(row[1])
        journal["coverage_start"] = 0
        row[1] = json.dumps(journal)
    else:
        table = {
            "receipt": "request_receipts",
            "task": "task_versions",
            "block": "block_versions",
            "history": "item_versions",
            "cache": "embedding_cache",
        }[damage]
        after["tables"][table].append(["unexpected modification"])
    with pytest.raises(ValueError):
        verify_upgrade(before, after, started=time.time() - 60, ended=time.time() + 1)


@pytest.mark.parametrize("boundary", ["index", "migration_receipt", "journal_state"])
def test_genuine_schema7_interrupted_upgrade_recovers_without_partial_authority(
    tmp_path, monkeypatch, boundary
):
    source, _, _ = genuine_store(tmp_path)
    with closing(readonly(source / "store.sqlite3")) as con:
        before = inventory(con)
    target = tmp_path / "interrupted-copy"
    shutil.copytree(source, target)
    connect = sqlite3.connect

    class InterruptedConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            hit = (
                (boundary == "index" and sql.startswith("CREATE INDEX change_events_page"))
                or (
                    boundary == "migration_receipt"
                    and sql.startswith("INSERT INTO schema_migrations")
                    and parameters[0] == 8
                )
                or (
                    boundary == "journal_state"
                    and sql.startswith("INSERT INTO store_metadata VALUES('change_journal'")
                )
            )
            if hit:
                raise RuntimeError("Injected migration interruption")
            return super().execute(sql, parameters)

    started = time.time()
    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", lambda *a, **kw: connect(*a, **kw, factory=InterruptedConnection))
        with pytest.raises(RuntimeError, match="Injected migration interruption"):
            SQLiteStore(target)
    with closing(readonly(target / "store.sqlite3")) as con:
        interrupted = inventory(con)
    if boundary != "journal_state":
        assert interrupted == before  # DDL + migration ledger + user_version roll back together.
    else:
        assert interrupted["schema"] == 8  # DDL committed, separate idempotent state init interrupted.
        assert "change_journal" not in dict(interrupted["tables"]["store_metadata"])
        assert interrupted["tables"]["change_events"] == []
    with closing(SQLiteStore(target)) as store:
        assert store.check_integrity()
        proof = verify_upgrade(before, inventory(store._connection), started=started, ended=time.time())
        assert proof["event_count"] == 0

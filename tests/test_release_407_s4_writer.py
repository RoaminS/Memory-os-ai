"""Real online S4 on tiny Hash stores; no sockets, model, production or OS budget."""

import ast
from contextlib import closing
import json
from pathlib import Path

import pytest
from memory_os_v4 import Config, MemoryV4
from memory_os_v4.ops import sqlite_backup as s4, sqlite_probe
from memory_os_v4.sqlite_store import SQLiteStore


@pytest.mark.parametrize("writer_open", [False, True])
def test_installed_harness_s4_requires_live_writer(
    tmp_path, monkeypatch, capsys, writer_open
):
    store = tmp_path / "store"
    with MemoryV4(
        config=Config(
            data_dir=str(store), embed_model="hash", store_backend="sqlite_s1"
        )
    ) as engine:
        engine.add_episode("Synthetic S4 writer witness.", project="p")
    assert not (store / "store.sqlite3-wal").exists()
    assert not (store / "store.sqlite3-shm").exists()
    for key, value in {
        "DIR": store,
        "OPS_DIR": tmp_path / "ops",
        "BACKUP_DIR": tmp_path / "backups",
        "BACKUP_KEY": tmp_path / "key",
        "TOKEN_FILE": tmp_path / "unused.token",
        "URL": "http://127.0.0.1:9/mcp/",
        "SERVICE_LABEL": "",
    }.items():
        monkeypatch.setenv("MEMORY_V4_" + key, str(value))

    # Only synthetic unit fixtures bypass machine instrumentation. Keep actual
    # readonly online guard, snapshot, crypto, archive and recovery code intact.
    class UnitBudget:
        def __init__(self, settings):
            pass

        def check(self):
            pass

    monkeypatch.setattr(s4, "Budget", UnitBudget)
    monkeypatch.setattr(s4, "margin", lambda settings: {"ok": True, "synthetic": True})
    probes = []

    def offline_probe(root, settings, recovery, *, guard):
        assert (store / "store.sqlite3-wal").exists()
        assert (store / "store.sqlite3-shm").exists()
        with closing(s4.readonly(root / "store.sqlite3", sealed=True)) as con:
            inv = s4.inventory(con)
        assert inv["store_instance_id"] == recovery["store_instance_id"]
        assert recovery["restored_from_instance_id"] != recovery["store_instance_id"]
        probes.append(True)
        return {"ok": True, "scope": "unit offline inventory, not HTTP qualification"}

    monkeypatch.setattr(sqlite_probe, "probe", offline_probe)
    script = (
        Path(__file__).resolve().parents[1] / "scripts/qualify_sqlite_release_407.py"
    )
    snippets = [
        n.value.value
        for n in ast.walk(ast.parse(script.read_text()))
        if isinstance(n, ast.Assign)
        and isinstance(n.value, ast.Constant)
        and isinstance(n.value.value, str)
        and "d=s4.restore_drill(" in n.value.value
    ]
    assert len(snippets) == 1
    code = snippets[0]
    marker = "with closing(SQLiteStore(Path(os.environ['MEMORY_V4_DIR']))):"
    assert marker in code
    if not writer_open:
        code = "from contextlib import nullcontext\n" + code.replace(
            marker, "with nullcontext():"
        )
        with pytest.raises(
            RuntimeError, match="Online source requires existing WAL/SHM"
        ):
            exec(compile(code, str(script) + ":S4-without-writer", "exec"), {})
        assert not probes
        assert not (
            store / "store.sqlite3-wal"
        ).exists()  # Backup never repairs the source.
        assert not (store / "store.sqlite3-shm").exists()
    else:
        exec(compile(code, str(script) + ":S4", "exec"), {})
        result = json.loads(capsys.readouterr().out.splitlines()[-1])
        assert result["backup"]["ok"] and result["backup"]["verified"]
        assert result["backup"]["source_sql_writes"] is False
        assert result["drill"]["ok"] and result["drill"]["private_reopen_verified"]
        assert result["drill"]["forget_reconciliation"] == "complete"
        assert probes == [True]
        with closing(SQLiteStore(store)) as reopened:
            assert reopened._connection.execute(
                "PRAGMA integrity_check"
            ).fetchall() == [("ok",)]

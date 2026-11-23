"""G11 (QUALIFY_4_1_0.md rev. 8): the opposite (semantic) open of a brand-new lexical store, through the
qualification worker's own ``refusal``. Real engine on a tmp store, hash encoder, no network, no install.

The engine checks the semantic ML closure BEFORE the profile of the store: an interpreter without FAISS
(the lexical venv) is refused by ``semantic_dependency_missing`` and proves nothing about the store.
Needs FAISS: not part of the ML-free subset.

Run v8 defect (copy step, 900 s budget exceeded): the sealed guard forks ``sysctl`` at every call and the
candidate's ``snapshot`` calls it 389,073 times for the 154 MB cut (540 s of guard for 6 s of copy). The
worker hands the candidate a rate-limited front (``PacedGuard``): same check, same exception, bounded count.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import qualify_sqlite_release_410 as sqlite_gate  # noqa: E402
import sqlite_release_worker_410 as worker  # noqa: E402

pytest.importorskip("faiss")


@pytest.fixture
def lexical_store(tmp_path, monkeypatch):
    from memory_os_v4 import Config, MemoryV4

    for name in ("MEMORY_V4_RETRIEVAL_PROFILE", "MEMORY_V4_DIR", "MEMORY_V4_STORE_BACKEND", "MEMORY_V4_WRITE_MODE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MEMORY_V4_EMBED_MODEL", "hash")
    store = tmp_path / "lexical-store"
    with MemoryV4(config=Config.lexical(data_dir=str(store))) as engine:
        engine.add_facts([{"text": text} for text in worker.LEXICAL_FACTS], project=worker.LEXICAL_PROJECT,
                         event_time=worker.LEXICAL_EVENT_TIME)
    return store


def opposite_open(tmp_path, store, **attested):
    before_rows, before = sqlite_gate.hashed_inventory(store)
    worker.refusal(tmp_path, "semantic-refusal", store, "semantic", **attested)
    after_rows, after = sqlite_gate.hashed_inventory(store)
    assert before_rows and before == after and after_rows == before_rows   # refused without any mutation
    return json.loads((tmp_path / "semantic-refusal.json").read_text())


def test_semantic_open_with_the_ml_closure_reaches_the_store_profile(tmp_path, lexical_store):
    report = opposite_open(tmp_path, lexical_store, package_path="/e/wheel/site-packages/memory_os_v4/__init__.py",
                           ml_find_spec={"faiss": True})
    assert report == {"requested": "semantic", "refused": True, "code": "store_profile_mismatch",
                      "package_path": "/e/wheel/site-packages/memory_os_v4/__init__.py",
                      "ml_find_spec": {"faiss": True}}


def test_semantic_open_without_faiss_stops_before_the_store_profile(tmp_path, lexical_store, monkeypatch):
    """The latent defect: in the lexical venv (no FAISS) the refusal is NOT ``store_profile_mismatch``."""
    monkeypatch.setitem(sys.modules, "faiss", None)
    report = opposite_open(tmp_path, lexical_store)
    assert report == {"requested": "semantic", "refused": True, "code": "semantic_dependency_missing"}


# ---------- run v8: the sealed guard is rate-limited, never weakened ----------

class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Sealed:
    """Stand-in for the sealed guard: counts the real checks, can simulate a low disk."""

    def __init__(self):
        self.calls, self.low = [], False

    def __call__(self, path):
        self.calls.append(path)
        if self.low:
            raise OSError("Disk guard: 1 bytes free < effective minimum 5000000000 bytes")


def wal_store(path, facts=300):
    """Small SQLite store left in WAL with its writer open, as the production source is during the cut."""
    from memory_os_v4 import Config, MemoryV4
    from memory_os_v4.sqlite_store import SQLiteStore

    with MemoryV4(config=Config.lexical(data_dir=str(path))) as engine:
        engine.add_facts([{"text": f"Guard pacing fact number {i} about quay {i % 7}."} for i in range(facts)],
                         project=worker.LEXICAL_PROJECT, event_time=worker.LEXICAL_EVENT_TIME)
    writer = SQLiteStore(path)
    assert (path / "store.sqlite3-wal").exists()
    return writer


def test_paced_guard_checks_first_then_at_most_once_per_period_and_at_transitions(tmp_path):
    clock, sealed = Clock(), Sealed()
    guard = worker.PacedGuard(sealed, clock=clock)
    assert worker.GUARD_PERIOD_SECONDS == 0.5
    for _ in range(10_000):
        guard(tmp_path)
    assert sealed.calls == [tmp_path]                       # first call: full check; then quiet
    clock.now += 0.499
    guard(tmp_path)
    assert len(sealed.calls) == 1
    clock.now += 0.001                                      # one period after the last PASSED check
    guard(tmp_path)
    guard(tmp_path)
    assert len(sealed.calls) == 2
    guard.full(tmp_path)                                    # transition: unconditional, inside the period
    guard.full(tmp_path)
    assert len(sealed.calls) == 4
    clock.now -= 5                                          # a clock stepping back never extends the quiet period
    guard(tmp_path)
    assert len(sealed.calls) == 5
    for period in (0, -1, 1.5):
        with pytest.raises(ValueError):
            worker.PacedGuard(sealed, period=period)


def test_paced_guard_propagates_the_sealed_error_and_never_caches_a_result(tmp_path):
    clock, sealed = Clock(), Sealed()
    guard = worker.PacedGuard(sealed, clock=clock)
    guard(tmp_path)
    sealed.low = True
    guard(tmp_path)                                         # inside the period: not measured yet
    clock.now += 0.5
    for attempt in range(3):                                # a failure is never cached: checked at EVERY call
        with pytest.raises(OSError, match="Disk guard"):
            guard(tmp_path)
        assert len(sealed.calls) == 2 + attempt
    with pytest.raises(OSError, match="Disk guard"):
        guard.full(tmp_path)
    sealed.low = False
    guard(tmp_path)                                         # recovered: measured again, then quiet
    guard(tmp_path)
    assert len(sealed.calls) == 6
    sealed.low = True                                       # a failing transition closes the quiet period too
    with pytest.raises(OSError):
        guard.full(tmp_path)
    sealed.low = False
    guard(tmp_path)
    assert len(sealed.calls) == 8
    failing = worker.PacedGuard(Sealed(), clock=clock)      # a low disk at the very first call is refused
    failing._guard.low = True
    with pytest.raises(OSError, match="Disk guard"):
        failing(tmp_path)


def test_paced_guard_period_runs_from_the_start_of_a_slow_check(tmp_path):
    clock, starts = Clock(), []

    def slow(path):
        starts.append(clock.now)
        clock.now += 0.4                                    # the sealed check itself takes 0.4 s

    guard = worker.PacedGuard(slow, clock=clock)
    guard(tmp_path)                                         # starts at 1000.0, ends at 1000.4
    clock.now += 0.099
    guard(tmp_path)                                         # 1000.499: still inside the period
    assert starts == [1000.0]
    clock.now = 1000.5                                      # 0.5 s after the START, only 0.1 s after the end
    guard(tmp_path)
    assert starts == [1000.0, 1000.5]
    clock.now = 1000.5 + 0.6                                # slower than the period: checked at every call
    guard(tmp_path)
    assert len(starts) == 3 and all(b - a <= 0.6 + 1e-9 for a, b in zip(starts, starts[1:]))


def test_candidate_snapshot_calls_the_guard_thousands_of_times_but_the_sealed_check_stays_bounded(tmp_path):
    from contextlib import closing
    from memory_os_v4.ops import sqlite_backup as s4

    clock, sealed, wrapped = Clock(), Sealed(), []
    guard = worker.PacedGuard(sealed, clock=clock)

    def through():
        wrapped.append(1)
        clock.now += 0.001                                  # 1 ms of injected time per candidate call
        guard(tmp_path)

    with closing(wal_store(tmp_path / "source")):
        logical = s4.snapshot(tmp_path / "source" / s4.DB, tmp_path / "cut.sqlite3", through)
    assert logical["schema_version"] == 8 and (tmp_path / "cut.sqlite3").stat().st_size > 0
    assert len(wrapped) > 1000                              # the candidate does call its guard per row/step
    assert len(sealed.calls) <= len(wrapped) // 499 + 1     # one real check per 0.5 s of injected time
    assert len(sealed.calls) * 100 < len(wrapped)


def test_candidate_snapshot_stops_when_the_sealed_guard_refuses_mid_cut(tmp_path):
    from contextlib import closing
    from memory_os_v4.ops import sqlite_backup as s4

    clock, sealed, wrapped = Clock(), Sealed(), []
    guard = worker.PacedGuard(sealed, clock=clock)

    def through():
        wrapped.append(1)
        clock.now += 0.001
        sealed.low = len(wrapped) > 700                     # the disk drops during the cut
        guard(tmp_path)

    with closing(wal_store(tmp_path / "source")):
        with pytest.raises(Exception) as caught:
            s4.snapshot(tmp_path / "source" / s4.DB, tmp_path / "cut.sqlite3", through)
    assert sealed.calls and len(wrapped) <= 700 + 500 + 1   # refused within one period of the drop
    assert "Disk guard" in str(caught.value) or "interrupt" in str(caught.value).lower()


def test_worker_snapshot_hands_the_candidate_a_paced_guard(tmp_path, monkeypatch):
    """Reproduction of run v8 through ``main``: on 008059d the sealed guard ran once per candidate call."""
    import importlib.metadata as md
    import types
    from contextlib import closing
    import memory_os_v4
    from memory_os_v4.ops import sqlite_backup as s4, sqlite_limits
    from memory_os_v4.readiness import build_identity

    sealed, candidate_calls = Sealed(), []
    monkeypatch.setitem(sys.modules, "qualification_guard", types.SimpleNamespace(guard=sealed))
    monkeypatch.setitem(sys.modules, "qualification_limits", sqlite_limits)
    monkeypatch.setattr(sys, "executable", str(Path(memory_os_v4.__file__).parent.parent / "bin" / "python"))
    original = s4.snapshot

    def counting(source, destination, guard):
        def through():
            candidate_calls.append(1)
            guard()
        return original(source, destination, through)

    monkeypatch.setattr(s4, "snapshot", counting)
    root = tmp_path / "private"
    root.mkdir()
    (root / "copy-owner.json").write_text("{}")
    (root / "plan.json").write_text(json.dumps({"format": worker.PLAN_FORMAT, "version": md.version("memory-os-ai"),
                                                "source_commit": build_identity()["commit"]}))
    monkeypatch.setattr(sys, "argv", ["worker.py", "--plan", str(root / "plan.json"), "--kind", "snapshot",
                                      "--snapshot-source", str(tmp_path / "source")])
    with closing(wal_store(tmp_path / "source")):
        worker.main()
    assert json.loads((root / "snapshot.json").read_text())["schema_version"] == 8
    assert len(candidate_calls) > 1000
    # Start of the worker, before and after the cut, then one check per 0.5 s of a sub-second cut.
    assert 3 <= len(sealed.calls) <= 30 and set(sealed.calls) == {root.resolve()}

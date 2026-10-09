"""Streaming LongMemEval reader. Only the scorer receives annotations."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class Turn:
    id: str
    session: str
    role: str
    content: str
    date: str


@dataclass(frozen=True)
class Inputs:
    id: str  # opaque case id; no question_type / answerability hint
    turns: tuple[Turn, ...]
    question: str
    date: str


@dataclass(frozen=True)
class Gold:
    id: str
    evidence: tuple[str, ...]
    answers: tuple[str, ...]
    category: str
    sessions: tuple[str, ...]  # observable source identities, for clustered CI
    excluded_future_turns: int


def sha256(path: Path, check=None) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            if check:
                check()
            h.update(chunk)
    return h.hexdigest()


def iso_date(value: str) -> str:
    # Dataset dates are treated as UTC, independent of the host timezone.
    dt = datetime.strptime(value, "%Y/%m/%d (%a) %H:%M")
    return dt.replace(tzinfo=timezone.utc).isoformat()


def array_entries(path: Path, max_entry_bytes: int = 32 * 1024 * 1024, check=None) -> Iterator[dict]:
    """Bounded top-level JSON-array reader, including strict comma/end checks."""
    decoder = json.JSONDecoder()
    with path.open(encoding="utf-8") as f:
        buf = ""
        eof = False

        def fill():
            nonlocal buf, eof
            if check:
                check()
            block = f.read(65536)
            eof = not block
            buf += block

        def nonspace():
            nonlocal buf
            while True:
                buf = buf.lstrip()
                if buf or eof:
                    return
                fill()

        nonspace()
        if not buf.startswith("["):
            raise ValueError("LongMemEval must be a top-level JSON array")
        buf = buf[1:]
        nonspace()
        if buf.startswith("]"):
            buf = buf[1:]
        else:
            while True:
                nonspace()
                while True:
                    try:
                        entry, end = decoder.raw_decode(buf)
                        if len(buf[:end].encode("utf-8")) > max_entry_bytes:
                            raise ValueError("Dataset entry exceeds memory budget")
                        break
                    except json.JSONDecodeError:
                        if len(buf.encode("utf-8")) > max_entry_bytes:
                            raise ValueError("Dataset entry exceeds memory budget") from None
                        if eof:
                            raise ValueError("Truncated/invalid dataset") from None
                        fill()
                if not isinstance(entry, dict):
                    raise ValueError("Dataset entry must be an object")
                buf = buf[end:]
                yield entry
                nonspace()
                if buf.startswith("]"):
                    buf = buf[1:]
                    break
                if not buf.startswith(","):
                    raise ValueError("Missing JSON array separator")
                buf = buf[1:]
                nonspace()
                if buf.startswith("]"):
                    raise ValueError("Trailing JSON array comma")
        nonspace()
        if buf:
            raise ValueError("Trailing dataset content")


def cases(path: Path, check=None) -> Iterator[tuple[Inputs, Gold]]:
    seen = set()
    for e in array_entries(path, check=check):
        qid = str(e["question_id"])
        if qid in seen:
            raise ValueError("Duplicate question id")
        seen.add(qid)
        case_id = hashlib.sha256(qid.encode()).hexdigest()[:24]
        sessions, ids, dates = e["haystack_sessions"], e["haystack_session_ids"], e["haystack_dates"]
        if not len(sessions) == len(ids) == len(dates) or len(set(ids)) != len(ids):
            raise ValueError("Ambiguous session alignment")
        query_date = iso_date(e["question_date"])
        turns, evidence = [], []
        excluded = 0
        for session, sid, date in zip(sessions, ids, dates):
            date = iso_date(date)
            for ti, t in enumerate(session):
                tid = f"{sid}:{ti}"
                # Gold is scorer-only, even when the associated turn is future.
                if t.get("has_answer"):
                    evidence.append(tid)
                content = t.get("content") or ""
                if date > query_date:
                    excluded += 1
                    continue
                if t["role"] not in {"user", "assistant", "system", "doc"}:
                    raise ValueError("Unknown speaker role")
                if content.strip():
                    turns.append(Turn(tid, str(sid), t["role"], content, date))
        # Stable chronological replay; preserves source order within a session.
        turns.sort(key=lambda t: t.date)
        answer = e.get("answer")
        answers = answer if isinstance(answer, list) else [answer]
        yield Inputs(case_id, tuple(turns), str(e["question"]), query_date), Gold(
            qid, tuple(evidence), tuple(str(a) for a in answers if a is not None),
            str(e.get("question_type", "unknown")), tuple(map(str, ids)), excluded,
        )


def inventory(path: Path, variant: str, check=None) -> dict:
    counts: dict[str, int] = {}
    n = no_evidence = sessions = future = 0
    all_oracle = True
    for e in array_entries(path, check=check):
        n += 1
        category = str(e.get("question_type", "unknown"))
        counts[category] = counts.get(category, 0) + 1
        sessions += len(e["haystack_sessions"])
        all_oracle &= e["haystack_session_ids"] == e.get("answer_session_ids")
    for inputs, gold in cases(path, check=check):
        no_evidence += not gold.evidence
        future += gold.excluded_future_turns
    if variant == "s" and all_oracle:
        raise ValueError("Oracle-only haystacks cannot be called LongMemEval-S")
    return {"variant": variant, "questions": n, "sessions": sessions,
            "empty_evidence_questions": no_evidence, "categories": counts,
            "future_turns_excluded": future, "all_haystacks_oracle": all_oracle,
            "path": str(path.resolve()), "bytes": path.stat().st_size, "sha256": sha256(path, check=check)}

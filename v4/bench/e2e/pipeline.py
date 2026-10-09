"""Client capture/extraction and response: no LLM, no question-aware enrichment."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re

from .data import Turn


@dataclass(frozen=True)
class Quote:
    id: str
    turn_id: str
    text: str
    date: str


def extract(case_id: str, turns: tuple[Turn, ...], chunk_chars: int = 1024) -> list[Quote]:
    """Lossless quoted spans, NOT LLM-inferred atomic semantic facts.

    Receives only observations already available at capture time. The quote
    wrapper describes what a speaker said; it does not assert its truth.
    """
    if chunk_chars < 1:
        raise ValueError("positive chunk size required")
    out = []
    for turn in turns:
        for start in range(0, len(turn.content), chunk_chars):
            span = turn.content[start:start + chunk_chars]
            identity = [case_id, turn.id, turn.session, turn.role, turn.date, start, span]
            opaque = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
            out.append(Quote(opaque, turn.id,
                             f'{turn.date} observation {turn.id}/{start}: {turn.role} said: "{span}"',
                             turn.date))
    return out


def tokens(text: str) -> list[str]:
    return re.findall(r"\w+", text.casefold())


def pack(ranked: list[Quote], top_k: int, context_bytes: int, full: bool = False,
         unique_turns: bool = False) -> list[Quote]:
    """Identical k/UTF-8-byte context cap for retrieval systems; no gold access."""
    if top_k < 1 or context_bytes < 1:
        raise ValueError("positive context budgets required")
    out, used = [], 0
    if unique_turns and not full:
        seen = set()
        ranked = [q for q in ranked if not (q.turn_id in seen or seen.add(q.turn_id))]
    for quote in ranked if full else ranked[:top_k]:
        size = len(quote.text.encode("utf-8")) + 1
        if used + size > context_bytes:
            break
        out.append(quote)
        used += size
    return out


def respond(question: str, context: list[Quote]) -> str | None:
    """Extractive sentence selection, not an LLM or an official LME judge."""
    if not context:
        return None
    query = set(tokens(question))
    observations = [q.text.split(' said: "', 1)[-1].removesuffix('"') for q in context]
    candidates = [s.strip() for text in observations for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    return max(candidates, key=lambda s: len(query & set(tokens(s))))

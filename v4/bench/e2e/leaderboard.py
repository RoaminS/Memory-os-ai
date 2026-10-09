"""T25 LOCAL DRAFT: Add/Search bridge, NOT an attested official AML wire contract."""
from __future__ import annotations

import hashlib
import json
from itertools import groupby
from datetime import datetime

from .client import project_for
from .data import Turn
from .pipeline import Quote, extract


class AddSearchAdapter:
    """No HTTP endpoint/submission. Inject a tool client over stdio or dispatch.

    One namespace is mandatory per independent benchmark run. User ids cannot
    select production/global projects. Add expects dated raw messages; annotations,
    precomputed facts, question-aware summaries and answers are never accepted.
    """
    def __init__(self, client, namespace: str):
        if not namespace:
            raise ValueError("namespace required")
        self.client, self.namespace = client, namespace

    async def add(self, user_id: str, turns: tuple[Turn, ...]) -> dict:
        project = project_for(self.namespace, user_id)
        quotes = extract(project, turns)
        return await self._capture(user_id, turns, quotes)

    async def _capture(self, user_id: str, turns: tuple[Turn, ...], quotes: list[Quote]) -> dict:
        # Internal benchmark hook, after its timed client extraction. Not part
        # of the public Add request contract; public Add never accepts facts.
        project = project_for(self.namespace, user_id)
        by_turn: dict[str, list[Quote]] = {}
        for q in quotes:
            by_turn.setdefault(q.turn_id, []).append(q)
        calls = 0
        added = deduped = 0
        # Sequential captures in observed session/date order, each using a durable
        # request id. Do not retry a mutation after an uncertain tool failure.
        for (session, date), group in groupby(turns, key=lambda t: (t.session, t.date)):
            group = list(group)
            facts = [{"text": q.text, "item_id": q.id, "kind": "fact"}
                     for t in group for q in by_turn.get(t.id, [])]
            payload = {"project_label": project, "event_time": date,
                       "messages": [{"role": t.role, "content": t.content} for t in group],
                       "facts": facts}
            fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            payload["request_id"] = f"capture-{fingerprint}"
            out = await self.client.call("memory_chat_save", payload)
            calls += 1
            added += len(out["facts"]["added"])
            deduped += len(out["facts"]["deduped"])
            if out["facts"]["superseded"]:
                raise RuntimeError("Unexpected supersession of quoted observations")
        return {"tool_calls": calls, "added": added, "deduped": deduped}

    async def search(self, user_id: str, query: str, limit: int = 10) -> dict:
        if not query.strip() or not 1 <= limit <= 200:
            raise ValueError("Nonempty query and limit 1..200 required")
        out = await self.client.call("memory_search", {
            "project": project_for(self.namespace, user_id), "query": query,
            "top_k": limit, "kinds": ["fact"], "rerank": False,
        })
        return {"results": [{"id": h["id"], "text": h["text"], "score": h["score"]}
                            for h in out["hits"]], "tool_calls": 1}

    async def request(self, payload: dict) -> dict:
        """Versioned LOCAL JSON contract; never assert official AML compatibility.

        Add: op,user_id,messages[{id,session,role,content,date}].
        Search: op,user_id,query,limit (optional). Extra keys are rejected.
        """
        if not isinstance(payload, dict) or not isinstance(payload.get("user_id"), str):
            raise ValueError("Object and explicit string user_id required")
        user = payload["user_id"]
        if payload.get("op") == "Add":
            if set(payload) != {"op", "user_id", "messages"}:
                raise ValueError("Add accepts only raw messages, no annotations/facts/answers")
            if not isinstance(payload["messages"], list) or not payload["messages"]:
                raise ValueError("Nonempty raw message list required")
            turns = []
            for row in payload["messages"]:
                if not isinstance(row, dict) or set(row) != {"id", "session", "role", "content", "date"}:
                    raise ValueError("Invalid raw message schema")
                if any(not isinstance(v, str) or not v for v in row.values()):
                    raise ValueError("Message fields must be nonempty strings")
                if row["role"] not in {"user", "assistant", "system", "doc"}:
                    raise ValueError("Invalid role")
                if datetime.fromisoformat(row["date"]).tzinfo is None:
                    raise ValueError("Explicit timezone required")
                from datetime import timezone
                date = datetime.fromisoformat(row["date"]).astimezone(timezone.utc).isoformat()
                turns.append(Turn(row["id"], row["session"], row["role"], row["content"], date))
            if len({t.id for t in turns}) != len(turns):
                raise ValueError("Unique observation ids required")
            return await self.add(user, tuple(turns))
        if payload.get("op") == "Search":
            if not {"op", "user_id", "query"} <= set(payload) <= {"op", "user_id", "query", "limit"}:
                raise ValueError("Invalid Search schema")
            if not isinstance(payload["query"], str) or type(payload.get("limit", 10)) is not int:
                raise ValueError("String query/integer limit required")
            return await self.search(user, payload["query"], payload.get("limit", 10))
        raise ValueError("Only Add/Search operations supported")

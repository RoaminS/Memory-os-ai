"""Deterministic client-side query expansion; never sees observations or gold."""
import re

STOP = set("a an the i my me have has been is are was were what which how when where do did does can could please on in of for with and to from at it you your".split())


def extra_queries(question: str, mode: str) -> list[str]:
    if mode == "none":
        return []
    if mode != "lexical":
        raise ValueError("Unknown query planner")
    words = [w for w in re.findall(r"\w+", question.casefold()) if w not in STOP]
    keywords = " ".join(dict.fromkeys(words))[:260]
    result = [keywords] if keywords and keywords != question.casefold() else []
    if re.search(r"\b(when|long|before|after|simultaneously|many|time|date)\b", question.casefold()) and keywords:
        result.append(("date year month duration number " + keywords)[:300])
    return result[:2]

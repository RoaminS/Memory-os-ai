"""Non-official extractive response proxies; never used for retrieval/planning."""
from collections import Counter
import re
import unicodedata

VERSION = "answer-proxies-v2"


def answer_tokens(text: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text).casefold()
    text = re.sub(r"[^\w\s]", " ", text).replace("_", " ")
    return [t for t in text.split() if t not in {"a", "an", "the"}]


def normalize(text: str) -> str:
    return " ".join(answer_tokens(text))


def response_metrics(response: str | None, answers: tuple[str, ...]) -> dict:
    references = [answer_tokens(a) for a in answers if answer_tokens(a)]
    if not references:
        return {"answer_em": None, "answer_f1": None, "answer_contains": None}
    prediction = answer_tokens(response) if response is not None else []
    f1s, contains = [], []
    for ref in references:
        overlap = sum((Counter(prediction) & Counter(ref)).values())
        f1s.append(2 * overlap / (len(prediction) + len(ref)) if overlap else 0.0)
        contains.append(any(prediction[i:i + len(ref)] == ref
                            for i in range(len(prediction) - len(ref) + 1)))
    return {"answer_em": float(any(prediction == r for r in references)),
            "answer_f1": max(f1s), "answer_contains": float(any(contains))}

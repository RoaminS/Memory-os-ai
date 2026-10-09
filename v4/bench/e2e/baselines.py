"""Fixed baselines over the SAME client-produced quotes and context budget."""
from __future__ import annotations

from collections import Counter, defaultdict
import json
import math
from pathlib import Path

from .pipeline import Quote, tokens


class BM25:
    def __init__(self, quotes: list[Quote], root: Path, model: str):
        self.quotes = quotes
        self.docs = [Counter(tokens(q.text)) for q in quotes]
        self.lengths = [sum(d.values()) for d in self.docs]
        self.avg = sum(self.lengths) / len(quotes) if quotes else 1
        self.postings = defaultdict(list)
        for i, d in enumerate(self.docs):
            for word in d:
                self.postings[word].append(i)
        # Baselines persist observations too; include this IO in ingestion timing.
        self.path = root / "quotes.jsonl"
        persist(quotes, self.path)

    def search(self, query: str, k: int) -> list[Quote]:
        scores = defaultdict(float)
        for word in tokens(query):
            posting = self.postings[word]
            idf = math.log(1 + (len(self.docs) - len(posting) + .5) / (len(posting) + .5))
            for i in posting:
                tf = self.docs[i][word]
                scores[i] += idf * tf * 2.2 / (tf + 1.2 * (.25 + .75 * self.lengths[i] / (self.avg or 1)))
        return [self.quotes[i] for i in sorted(scores, key=lambda i: (-scores[i], self.quotes[i].id))[:k]]


def persist(quotes: list[Quote], path: Path) -> None:
    import os
    from dataclasses import asdict
    with path.open("w") as f:
        for q in quotes:
            f.write(json.dumps(asdict(q), ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


class Files:
    def __init__(self, quotes: list[Quote], root: Path, model: str):
        self.path = root / "quotes.jsonl"
        persist(quotes, self.path)

    def search(self, query: str, k: int) -> list[Quote]:
        words = set(tokens(query))
        ranked = []
        # Intentionally simple scan (unique-term overlap), no BM25/idf index.
        with self.path.open() as f:
            for line in f:
                q = Quote(**json.loads(line))
                score = len(words & set(tokens(q.text)))
                if score:
                    ranked.append((score, q))
        ranked.sort(key=lambda pair: (-pair[0], pair[1].id))
        return [q for _, q in ranked[:k]]


class FullContext(Files):
    def search(self, query: str, k: int) -> list[Quote]:
        with self.path.open() as f:
            return [Quote(**json.loads(line)) for line in f]


class Dense:
    def __init__(self, quotes: list[Quote], root: Path, model: str):
        if model == "hash":
            raise ValueError("Hash embedding is not a dense neural baseline")
        from memory_os_v4.embed import Embedder
        self.embedder = Embedder(model, device="cpu", max_seq_length=256, batch_size=4)
        self.quotes = quotes
        persist(quotes, root / "quotes.jsonl")
        self.vecs = self.embedder.encode([q.text for q in quotes])

    def search(self, query: str, k: int) -> list[Quote]:
        if not self.quotes:
            return []
        scores = self.vecs @ self.embedder.encode([query])[0]
        order = sorted(range(len(scores)), key=lambda i: (-float(scores[i]), self.quotes[i].id))
        return [self.quotes[i] for i in order[:k]]


BASELINES = {"bm25": BM25, "dense": Dense, "files": Files, "full-context": FullContext}

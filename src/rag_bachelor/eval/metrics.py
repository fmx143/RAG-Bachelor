"""Retrieval metrics at (source, page) granularity."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

Hit = tuple[str, int]  # (source, page)


def recall_at_k(ranked: Sequence[Hit], source: str, pages: Sequence[int], k: int) -> float:
    """Fraction of the expected pages found among the top-k hits."""
    found = {p for s, p in ranked[:k] if s == source}
    return len(found & set(pages)) / len(set(pages))


def reciprocal_rank(ranked: Sequence[Hit], source: str, pages: Sequence[int]) -> float:
    """1/rank of the first hit on an expected page (0.0 if none)."""
    expected = set(pages)
    for rank, (s, p) in enumerate(ranked, start=1):
        if s == source and p in expected:
            return 1.0 / rank
    return 0.0


@dataclass(frozen=True)
class Scored:
    id: str
    recall: dict[int, float]  # k -> recall@k
    rr: float


def aggregate(scored: Sequence[Scored]) -> dict[str, float]:
    """Mean recall@k (keys ``recall@k``) and MRR over all questions."""
    n = len(scored)
    if n == 0:
        return {}
    out = {f"recall@{k}": sum(s.recall[k] for s in scored) / n for k in sorted(scored[0].recall)}
    out["mrr"] = sum(s.rr for s in scored) / n
    return out

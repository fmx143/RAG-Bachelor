"""eval.metrics: recall@k and MRR at (source, page) granularity."""

from __future__ import annotations

import pytest

from rag_bachelor.eval.metrics import Scored, aggregate, recall_at_k, reciprocal_rank

RANKED = [("a.pdf", 5), ("b.pdf", 2), ("a.pdf", 2), ("a.pdf", 9)]


def test_recall_counts_expected_pages_within_k_only() -> None:
    assert recall_at_k(RANKED, "a.pdf", [2, 9], k=3) == 0.5
    assert recall_at_k(RANKED, "a.pdf", [2, 9], k=4) == 1.0
    assert recall_at_k(RANKED, "a.pdf", [7], k=4) == 0.0


def test_recall_ignores_same_page_in_another_source() -> None:
    assert recall_at_k(RANKED, "a.pdf", [2], k=2) == 0.0  # ("b.pdf", 2) is not a hit


def test_reciprocal_rank_is_first_expected_hit() -> None:
    assert reciprocal_rank(RANKED, "a.pdf", [2, 9]) == pytest.approx(1 / 3)
    assert reciprocal_rank(RANKED, "a.pdf", [5]) == 1.0
    assert reciprocal_rank(RANKED, "a.pdf", [7]) == 0.0


def test_aggregate_means() -> None:
    scored = [Scored("q1", {1: 1.0, 3: 1.0}, 1.0), Scored("q2", {1: 0.0, 3: 0.5}, 0.25)]
    assert aggregate(scored) == {"recall@1": 0.5, "recall@3": 0.75, "mrr": 0.625}
    assert aggregate([]) == {}

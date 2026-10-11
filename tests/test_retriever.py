"""Hybrid retrieval building blocks: tokenizer, BM25 and rank fusion."""

from __future__ import annotations

from rag_bachelor.core.retriever import _bm25, _rrf, _tokens


def test_tokens_strip_accents_and_plurals() -> None:
    assert _tokens("Métriques") == _tokens("metrique")


def test_bm25_ranks_exact_term_first_and_zero_when_absent() -> None:
    docs = ["le cycle de Deming PDCA", "gestion des risques du projet", "autre sujet"]
    scores = _bm25("Que signifie PDCA ?", docs)
    assert scores[0] > 0 and scores[1] == scores[2] == 0


def test_rrf_rewards_agreement_between_rankings() -> None:
    assert set(_rrf(["a", "b", "c"], ["c", "b"])[:2]) == {"b", "c"}
    assert _rrf(["a", "b"], []) == ["a", "b"]


def test_rerank_order_puts_highest_score_first() -> None:
    from unittest.mock import MagicMock, patch

    from rag_bachelor.core import rerank

    model = MagicMock()
    model.predict.return_value = [0.1, 0.9, 0.5]
    with patch.object(rerank, "get_reranker", return_value=model):
        assert rerank.rerank_order("q", ["a", "b", "c"]) == [1, 2, 0]

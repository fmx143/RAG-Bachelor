"""Hybrid retrieval building blocks: tokenizer, BM25 and rank fusion."""

from __future__ import annotations

import pytest

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


# ── retrieve(): keywords, source filter and graph fusion ─────────────────────────────────────


def _setup_index(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Two chunks with the same embedding (dense tie) in a.pdf and b.pdf; no reranker."""
    from rag_bachelor.config import settings
    from rag_bachelor.core import retriever
    from rag_bachelor.ingest.index import get_collection

    monkeypatch.setattr(settings, "reranker_model", "")
    monkeypatch.setattr(retriever, "embed", lambda texts: [[1.0, 0.0] for _ in texts])
    ids = ["a.pdf__p1__c0", "b.pdf__p1__c0"]
    get_collection().upsert(
        ids=ids,
        documents=["notions générales", "algorithme de dijkstra"],
        embeddings=[[1.0, 0.0], [1.0, 0.0]],  # type: ignore[arg-type]
        metadatas=[{"source": "a.pdf", "page": 1}, {"source": "b.pdf", "page": 1}],  # type: ignore[arg-type]
    )
    return ids


def test_keywords_feed_the_lexical_ranking(monkeypatch: pytest.MonkeyPatch) -> None:
    from rag_bachelor.core.retriever import retrieve

    _setup_index(monkeypatch)
    hits = retrieve("une question vague", top_k=1, keywords=["dijkstra"])

    assert hits[0].source == "b.pdf"


def test_source_restricts_the_search_and_unknown_source_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rag_bachelor.core.retriever import retrieve

    _setup_index(monkeypatch)

    assert {h.source for h in retrieve("question", top_k=5, source="a.pdf")} == {"a.pdf"}
    assert retrieve("question", top_k=5, source="zzz.pdf") == []


def test_graph_ranking_is_fused_and_stale_ids_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    from rag_bachelor.config import settings
    from rag_bachelor.core import graph
    from rag_bachelor.core.retriever import retrieve

    ids = _setup_index(monkeypatch)
    monkeypatch.setattr(settings, "graph_enabled", True)
    monkeypatch.setattr(graph, "graph_ranking", lambda q, kw, scores: [ids[1], "stale__id"])

    assert retrieve("question", top_k=1)[0].source == "b.pdf"

    monkeypatch.setattr(settings, "graph_enabled", False)
    monkeypatch.setattr(graph, "graph_ranking", lambda *a: pytest.fail("graph must be off"))
    retrieve("question", top_k=1)

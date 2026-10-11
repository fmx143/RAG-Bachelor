"""Optional cross-encoder reranker: re-scores (query, passage) pairs jointly. Local, like embeddings."""

from __future__ import annotations

from sentence_transformers import CrossEncoder

from rag_bachelor.config import settings

_model: CrossEncoder | None = None


def get_reranker() -> CrossEncoder:
    """Return (and lazily load) the singleton cross-encoder."""
    global _model
    if _model is None:
        _model = CrossEncoder(settings.reranker_model, cache_folder=settings.embedding_cache_dir)
    return _model


def rerank_order(query: str, passages: list[str]) -> list[int]:
    """Indices of *passages*, most relevant to *query* first."""
    scores = get_reranker().predict([(query, p) for p in passages], show_progress_bar=False)
    return sorted(range(len(passages)), key=lambda i: -float(scores[i]))

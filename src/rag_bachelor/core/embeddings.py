"""Local sentence-transformers embedding wrapper.

Always runs locally (CPU or GPU) regardless of the chosen LLM provider.
The model is lazily loaded and cached as a module-level singleton so
Streamlit reruns don't reload it on every interaction.
"""

from __future__ import annotations

from sentence_transformers import SentenceTransformer

from rag_bachelor.config import settings

_model: SentenceTransformer | None = None


def get_model() -> SentenceTransformer:
    """Return (and lazily load) the singleton embedding model."""
    global _model
    if _model is None:
        _model = SentenceTransformer(
            settings.embedding_model,
            cache_folder=settings.embedding_cache_dir,
        )
    return _model


def embed(texts: list[str]) -> list[list[float]]:
    """Embed a list of strings and return float vectors (L2-normalised).

    The returned vectors are compatible with cosine similarity: since they are
    unit-norm, dot-product == cosine similarity.
    """
    model = get_model()
    vectors = model.encode(
        texts,
        normalize_embeddings=True,
        show_progress_bar=False,
        batch_size=32,
    )
    return vectors.tolist()

"""Semantic retriever: embed a query and return nearest chunks from ChromaDB."""

from dataclasses import dataclass

from rag_bachelor.config import settings
from rag_bachelor.core.embeddings import embed
from rag_bachelor.ingest.index import get_collection


@dataclass
class SearchResult:
    text: str
    source: str  # PDF filename
    page: int    # 1-indexed page number
    score: float  # cosine similarity [0, 1], higher = more relevant


def retrieve(query: str, top_k: int | None = None) -> list[SearchResult]:
    """Return the *top_k* most semantically similar chunks to *query*.

    Results are ordered by descending cosine similarity.
    """
    k = top_k or settings.retrieval_top_k
    collection = get_collection()

    if collection.count() == 0:
        return []

    query_vec = embed([query])[0]

    results = collection.query(
        query_embeddings=[query_vec],
        n_results=min(k, collection.count()),
        include=["documents", "metadatas", "distances"],
    )

    hits: list[SearchResult] = []
    docs = (results.get("documents") or [[]])[0]
    metas = (results.get("metadatas") or [[]])[0]
    dists = (results.get("distances") or [[]])[0]

    for doc, meta, dist in zip(docs, metas, dists):
        hits.append(
            SearchResult(
                text=doc,
                source=str(meta.get("source", "")),
                page=int(meta.get("page", 0)),
                score=max(0.0, 1.0 - dist),  # cosine distance → similarity
            )
        )

    return hits

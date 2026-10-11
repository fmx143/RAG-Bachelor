"""Hybrid retriever: dense (ChromaDB) + BM25 keyword ranking, fused with RRF."""

import math
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import cast

from rag_bachelor.config import settings
from rag_bachelor.core.embeddings import embed
from rag_bachelor.ingest.index import get_collection


@dataclass
class SearchResult:
    text: str
    source: str  # PDF filename
    page: int  # 1-indexed page number
    score: float  # cosine similarity [0, 1], higher = more relevant


_RRF_K = 60  # standard Reciprocal Rank Fusion constant
_WORD_RE = re.compile(r"\w{3,}")


def _tokens(text: str) -> list[str]:
    """Lowercase, accent-free words (≥3 chars) cut to 6 chars: a crude French stemmer."""
    plain = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    return [w[:6] for w in _WORD_RE.findall(plain)]


def _bm25(query: str, docs: list[str], k1: float = 1.5, b: float = 0.75) -> list[float]:
    """Okapi BM25 score of *query* against each of *docs* (in-memory, rebuilt per call)."""
    tokenized = [_tokens(d) for d in docs]
    avg_len = (sum(map(len, tokenized)) / len(tokenized)) or 1.0
    df = Counter(t for toks in tokenized for t in set(toks))
    n = len(docs)
    scores: list[float] = []
    for toks in tokenized:
        tf = Counter(toks)
        score = 0.0
        for t in set(_tokens(query)):
            if t in tf:
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                score += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * len(toks) / avg_len))
        scores.append(score)
    return scores


def _rrf(*rankings: list[str]) -> list[str]:
    """Fuse several best-first id rankings; ids missing from one ranking only get the others' share."""
    fused: defaultdict[str, float] = defaultdict(float)
    for ranking in rankings:
        for rank, id_ in enumerate(ranking):
            fused[id_] += 1 / (_RRF_K + rank + 1)
    return sorted(fused, key=lambda i: -fused[i])


def retrieve(query: str, top_k: int | None = None) -> list[SearchResult]:
    """Return the *top_k* most semantically similar chunks to *query*.

    Results are ordered by descending cosine similarity.
    """
    k = top_k or settings.retrieval_top_k
    collection = get_collection()

    if collection.count() == 0:
        return []

    query_vec = embed([query])[0]

    # ponytail: dense over the whole index so every hit carries a real cosine score;
    # fine at a few thousand chunks, cap n_results and add a BM25-only lookup beyond that.
    results = collection.query(
        query_embeddings=[query_vec],  # type: ignore[arg-type]  # chroma stubs reject list[list[float]]
        n_results=collection.count(),
        include=["documents", "metadatas", "distances"],
    )
    ids = (results.get("ids") or [[]])[0]
    docs = (results.get("documents") or [[]])[0]
    metas = (results.get("metadatas") or [[]])[0]
    dists = (results.get("distances") or [[]])[0]

    by_id = dict(zip(ids, zip(docs, metas, dists)))
    bm25 = _bm25(query, docs)
    keyword_ranking = [i for _, i in sorted(zip(bm25, ids), key=lambda p: -p[0]) if _ > 0]
    fused = _rrf(ids, keyword_ranking)[
        : max(k, settings.rerank_pool) if settings.reranker_model else k
    ]
    if settings.reranker_model:
        from rag_bachelor.core.rerank import rerank_order

        order = rerank_order(query, [by_id[i][0] for i in fused])
        fused = [fused[i] for i in order[:k]]

    hits: list[SearchResult] = []
    for id_ in fused:
        doc, meta, dist = by_id[id_]
        hits.append(
            SearchResult(
                text=doc,
                source=str(meta.get("source", "")),
                page=int(cast(int, meta.get("page", 0))),
                score=max(0.0, 1.0 - dist),  # cosine distance → similarity
            )
        )

    return hits

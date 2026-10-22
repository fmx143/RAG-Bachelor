"""HippoRAG-style fact graph: LLM triples per chunk, Personalized PageRank at query time.

Facts ("sujet | relation | objet") live in their own Chroma collection, one per extracted triple,
tagged with the chunk they come from. At query time the graph (entities ↔ entities, entities ↔
chunks) is rebuilt from those tags and ranked with Personalized PageRank, seeded by the facts
closest to the question; the chunk ranking it yields is one more input of the RRF fusion in
``core/retriever.py``.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, cast

import networkx as nx

from rag_bachelor.core.embeddings import embed
from rag_bachelor.core.llm import LLMProvider, active_model, get_provider
from rag_bachelor.core.qtypes import loads_object
from rag_bachelor.core.retriever import _tokens
from rag_bachelor.ingest.index import get_collection, get_facts_collection

_WORKERS = 4  # concurrent LLM extraction calls
_TOP_FACTS = 10  # facts closest to the query that seed the PageRank
_PASSAGE_WEIGHT = 0.05  # HippoRAG 2: dense chunk scores seed the walk, but far below the facts
_PPR_ALPHA = 0.5  # HippoRAG 2 damping factor (probability of following an edge)

_PROMPT = (
    "Extrais du passage de cours les faits importants sous forme de triplets "
    "[sujet, relation, objet]. Noms courts et canoniques (ex. « tri rapide », pas une phrase), "
    "10 triplets maximum, uniquement des faits présents dans le texte. "
    'Réponds par un objet JSON : {"triples": [["sujet", "relation", "objet"], …]}.'
)


def _norm(entity: str) -> str:
    """Canonical node name: lowercase, accent-free, crude stem — 'Complexités' == 'complexite'."""
    return " ".join(_tokens(entity))


def _extract(provider: LLMProvider, model: str, text: str) -> list[tuple[str, str, str]]:
    """Triples the LLM finds in *text*; malformed or empty answers yield nothing."""
    raw = provider.chat(
        [{"role": "system", "content": _PROMPT}, {"role": "user", "content": text}],
        model=model,
        json_mode=True,
    )
    data = loads_object(raw) or {}
    triples = data.get("triples")
    out: list[tuple[str, str, str]] = []
    for t in triples if isinstance(triples, list) else []:
        if isinstance(t, list) and len(t) == 3 and all(isinstance(x, str) for x in t):
            s, p, o = (x.strip() for x in t)
            if _norm(s) and _norm(o) and p:
                out.append((s, p, o))
    return out


def build_graph(source: str, on_progress: Callable[[int, int], None] | None = None) -> int:
    """(Re)build the fact graph of *source* from its indexed chunks; returns the fact count.

    One LLM call per chunk. Reads the chunk collection only — nothing is re-indexed.
    """
    chunks = get_collection().get(where={"source": source}, include=["documents"])
    ids = chunks["ids"]
    docs = chunks.get("documents") or []
    get_facts_collection().delete(where={"source": source})

    # Resolve provider and model here: workers must not touch the settings store from threads.
    provider, name = get_provider()
    model = active_model(name)
    n_facts = 0
    with ThreadPoolExecutor(_WORKERS) as pool:
        # map() keeps order; consuming it lazily lets us persist and report chunk by chunk.
        for done, (chunk_id, triples) in enumerate(
            zip(ids, pool.map(lambda d: _extract(provider, model, d), docs)), start=1
        ):
            n_facts += _store_facts(source, chunk_id, triples)
            if on_progress:
                on_progress(done, len(ids))
    return n_facts


def _store_facts(source: str, chunk_id: str, triples: list[tuple[str, str, str]]) -> int:
    if not triples:
        return 0
    texts = [f"{s} | {p} | {o}" for s, p, o in triples]
    get_facts_collection().upsert(
        ids=[f"{chunk_id}__f{i}" for i in range(len(triples))],
        embeddings=embed(texts),  # type: ignore[arg-type]  # chroma stubs reject list[list[float]]
        documents=texts,
        metadatas=[
            {"source": source, "chunk_id": chunk_id, "subject": _norm(s), "object": _norm(o)}
            for s, _, o in triples
        ],
    )
    return len(triples)


def fact_counts() -> dict[str, int]:
    """Number of graph facts per source document."""
    metas = get_facts_collection().get(include=["metadatas"]).get("metadatas") or []
    return dict(Counter(str(m["source"]) for m in metas if m))


def _graph(metas: list[Any]) -> nx.Graph:
    """Entities linked to each other (one edge per fact) and to the chunks they appear in."""
    g: nx.Graph = nx.Graph()
    for m in metas:
        s, o, c = f"e:{m['subject']}", f"e:{m['object']}", f"c:{m['chunk_id']}"
        g.add_edges_from([(s, o), (s, c), (o, c)])
    return g


def graph_ranking(query: str, keywords: list[str], chunk_scores: dict[str, float]) -> list[str]:
    """Chunk ids ranked by Personalized PageRank from the facts closest to the question.

    *chunk_scores* are the dense similarities (chunk id → score) of the candidate chunks; they
    seed the walk too, with a low weight. Returns ``[]`` when there is no graph yet.
    """
    # ponytail: graph rebuilt from the fact metadata on every query (no cache) — fine for a few
    # thousand facts (tens of ms, dwarfed by the reranker); cache it beyond ~50k facts.
    facts = get_facts_collection()
    if facts.count() == 0:
        return []
    hits = facts.query(
        query_embeddings=[embed([" ".join([query, *keywords])])[0]],  # type: ignore[arg-type]
        n_results=min(_TOP_FACTS, facts.count()),
        include=["metadatas", "distances"],
    )
    hit_metas = (hits.get("metadatas") or [[]])[0]
    hit_dists = (hits.get("distances") or [[]])[0]

    seeds: defaultdict[str, float] = defaultdict(float)
    for m, dist in zip(hit_metas, hit_dists):
        for entity in (m["subject"], m["object"]):
            seeds[f"e:{entity}"] += max(0.0, 1.0 - dist)
    total = sum(seeds.values())
    if total == 0:
        return []
    personalization = {n: w / total for n, w in seeds.items()}
    dense_total = sum(chunk_scores.values())
    if dense_total > 0:
        for chunk_id, score in chunk_scores.items():
            personalization[f"c:{chunk_id}"] = _PASSAGE_WEIGHT * score / dense_total

    g = _graph(facts.get(include=["metadatas"]).get("metadatas") or [])
    personalization = {n: w for n, w in personalization.items() if n in g}
    if not personalization:
        return []
    rank = nx.pagerank(g, alpha=_PPR_ALPHA, personalization=personalization)
    chunks = [(n, score) for n, score in rank.items() if n.startswith("c:")]
    return [cast(str, n[2:]) for n, _ in sorted(chunks, key=lambda p: -p[1])]

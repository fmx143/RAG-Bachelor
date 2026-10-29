"""HippoRAG-style fact graph: LLM triples per chunk, Personalized PageRank at query time.

Facts ("sujet | relation | objet") live in their own Chroma collection, one per extracted triple,
tagged with the chunk they come from. At query time the graph (entities ↔ entities, entities ↔
chunks) is rebuilt from those tags and ranked with Personalized PageRank, seeded by the facts
closest to the question; the chunk ranking it yields is one more input of the RRF fusion in
``core/retriever.py``.
"""

from __future__ import annotations

import time
from collections import Counter, defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from itertools import combinations
from typing import Any, cast

import networkx as nx

from rag_bachelor.core.embeddings import embed
from rag_bachelor.core.llm import LLMProvider, active_model, get_provider
from rag_bachelor.core.qtypes import loads_object
from rag_bachelor.core.retriever import _tokens
from rag_bachelor.ingest.index import (
    facts_changed,
    facts_generation,
    get_collection,
    get_facts_collection,
)

_WORKERS = 4  # concurrent LLM extraction calls
_TOP_FACTS = 10  # facts closest to the query that seed the PageRank
_PASSAGE_WEIGHT = 0.05  # HippoRAG 2: dense chunk scores seed the walk, but far below the facts
_RETRIES = 3  # attempts per chunk before it is skipped (provider rate limits, transient 5xx)
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


def _extract_retrying(
    provider: LLMProvider, model: str, text: str
) -> list[tuple[str, str, str]] | Exception:
    """``_extract`` with backoff; the last exception is returned (not raised) once retries run out."""
    for attempt in range(_RETRIES):
        try:
            return _extract(provider, model, text)
        except Exception as exc:  # noqa: BLE001 — any provider error: retry, then report
            if attempt == _RETRIES - 1:
                return exc
            time.sleep(2 ** (attempt + 1))
    raise AssertionError("unreachable")


def build_graph(
    source: str, on_progress: Callable[[int, int], None] | None = None
) -> tuple[int, int]:
    """(Re)build the fact graph of *source* from its indexed chunks.

    Returns ``(fact count, chunks skipped after repeated provider errors)``; raises the provider's
    error if every chunk failed. One LLM call per chunk. Nothing is re-indexed.
    """
    chunks = get_collection().get(where={"source": source}, include=["documents"])
    ids = chunks["ids"]
    docs = chunks.get("documents") or []
    get_facts_collection().delete(where={"source": source})
    facts_changed()

    # Resolve provider and model here: workers must not touch the settings store from threads.
    provider, name = get_provider()
    model = active_model(name)
    n_facts, failures = 0, []
    with ThreadPoolExecutor(_WORKERS) as pool:
        # map() keeps order; consuming it lazily lets us persist and report chunk by chunk.
        for done, (chunk_id, result) in enumerate(
            zip(ids, pool.map(lambda d: _extract_retrying(provider, model, d), docs)), start=1
        ):
            if isinstance(result, Exception):
                failures.append(result)
            else:
                n_facts += _store_facts(source, chunk_id, result)
            if on_progress:
                on_progress(done, len(ids))
    if failures and len(failures) == len(ids):
        raise failures[0]
    return n_facts, len(failures)


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
    facts_changed()  # after the write: a query in between must not cache the old graph
    return len(triples)


def fact_counts() -> dict[str, int]:
    """Number of graph facts per source document."""
    metas = get_facts_collection().get(include=["metadatas"]).get("metadatas") or []
    return dict(Counter(str(m["source"]) for m in metas if m))


def shared_links() -> dict[tuple[str, str], int]:
    """Number of graph entities shared by each pair of source documents (a < b)."""
    metas = get_facts_collection().get(include=["metadatas"]).get("metadatas") or []
    sources_of: dict[str, set[str]] = {}
    for m in metas:
        for e in (m["subject"], m["object"]):
            sources_of.setdefault(str(e), set()).add(str(m["source"]))
    pairs = Counter(pair for srcs in sources_of.values() for pair in combinations(sorted(srcs), 2))
    return dict(pairs)


def _graph(metas: list[Any]) -> nx.Graph:
    """Entities linked to each other (one edge per fact) and to the chunks they appear in."""
    g: nx.Graph = nx.Graph()
    for m in metas:
        s, o, c = f"e:{m['subject']}", f"e:{m['object']}", f"c:{m['chunk_id']}"
        g.add_edges_from([(s, o), (s, c), (o, c)])
    return g


_cache: tuple[int, nx.Graph] | None = None  # (facts generation, graph); one process, one cache


def _cached_graph() -> nx.Graph:
    """The fact graph, rebuilt from the stored facts only when they changed since last time."""
    global _cache
    gen = facts_generation()
    if _cache is None or _cache[0] != gen:
        metas = get_facts_collection().get(include=["metadatas"]).get("metadatas") or []
        _cache = (gen, _graph(metas))
    return _cache[1]


def graph_ranking(query: str, keywords: list[str], chunk_scores: dict[str, float]) -> list[str]:
    """Chunk ids ranked by Personalized PageRank from the facts closest to the question.

    *chunk_scores* are the dense similarities (chunk id → score) of the candidate chunks; they
    seed the walk too, with a low weight. Returns ``[]`` when there is no graph yet.
    """
    # ponytail: in-process cache — a second app process would not see this one's writes; use a
    # shared invalidation key (e.g. a counter row in the DB) if the app ever runs several workers.
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

    g = _cached_graph()
    personalization = {n: w for n, w in personalization.items() if n in g}
    if not personalization:
        return []
    rank = nx.pagerank(g, alpha=_PPR_ALPHA, personalization=personalization)
    chunks = [(n, score) for n, score in rank.items() if n.startswith("c:")]
    return [cast(str, n[2:]) for n, _ in sorted(chunks, key=lambda p: -p[1])]

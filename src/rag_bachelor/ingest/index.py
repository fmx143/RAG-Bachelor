"""ChromaDB persistent vector store: upsert, delete, and query helpers."""

from __future__ import annotations

import chromadb
from chromadb.api import ClientAPI

from rag_bachelor.config import settings
from rag_bachelor.ingest.chunk import Chunk

_COLLECTION_NAME = "bachelor_docs"
_FACTS_NAME = "bachelor_facts"

# Module-level singleton client & collection
_client: ClientAPI | None = None
_collection: chromadb.Collection | None = None


def get_collection() -> chromadb.Collection:
    """Return (and lazily create) the singleton ChromaDB collection.

    Connects to a standalone Chroma server (isolated data-tier container, e.g.
    on a NAS) when ``CHROMA_HOST`` is set, otherwise falls back to an embedded
    ``PersistentClient`` for frictionless local dev.

    We always pass explicit ``embeddings=`` in every ``upsert`` and
    ``query_embeddings=`` in every ``query`` call, so ChromaDB's default
    embedding function is never actually invoked.  We still create the
    collection without specifying one to stay compatible across chromadb
    0.5.x versions.
    """
    global _client, _collection
    if _collection is None:
        if settings.chroma_host:
            _client = chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
        else:
            settings.chroma_dir.mkdir(parents=True, exist_ok=True)
            _client = chromadb.PersistentClient(path=str(settings.chroma_dir))
        _collection = _client.get_or_create_collection(
            name=_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


def heartbeat() -> None:
    """Raise if the Chroma server (or embedded client) does not answer."""
    get_collection()
    assert _client is not None
    _client.heartbeat()


def get_facts_collection() -> chromadb.Collection:
    """Collection of knowledge-graph facts (see ``core/graph.py``), next to the chunks."""
    get_collection()  # makes sure the client exists
    assert _client is not None
    return _client.get_or_create_collection(name=_FACTS_NAME, metadata={"hnsw:space": "cosine"})


# Bumped on every write to the facts collection: core/graph.py caches its graph per generation.
_facts_generation = 0


def facts_generation() -> int:
    return _facts_generation


def facts_changed() -> None:
    """Call after any write to the facts collection, to invalidate the cached graph."""
    global _facts_generation
    _facts_generation += 1


def index_chunks(chunks: list[Chunk]) -> None:
    """Embed *chunks* and upsert them into ChromaDB.

    Chunks are embedded in a single batch call via :mod:`rag_bachelor.core.embeddings`
    to minimise model overhead.
    """
    if not chunks:
        return

    # Import here to avoid circular imports (embeddings → config ← index)
    from rag_bachelor.core.embeddings import embed

    collection = get_collection()
    texts = [c.text for c in chunks]
    vectors = embed(texts)

    ids = [f"{c.source}__p{c.page_num}__c{c.chunk_index}" for c in chunks]
    metadatas: list[dict[str, str | int]] = [
        {"source": c.source, "page": c.page_num} for c in chunks
    ]

    collection.upsert(
        ids=ids,
        embeddings=vectors,  # type: ignore[arg-type]  # chroma stubs reject list[list[float]]
        documents=texts,
        metadatas=metadatas,  # type: ignore[arg-type]
    )


def delete_source(filename: str) -> None:
    """Remove every chunk that originates from *filename*.

    Call this before re-indexing a PDF to avoid stale duplicates. The graph facts extracted
    from those chunks go too, so they never point at chunks that no longer exist.
    """
    collection = get_collection()
    collection.delete(where={"source": filename})
    get_facts_collection().delete(where={"source": filename})
    facts_changed()


def list_sources() -> list[str]:
    """Return sorted unique source filenames currently in the index."""
    collection = get_collection()
    result = collection.get(include=["metadatas"])
    metas = result.get("metadatas") or []
    sources = {str(m["source"]) for m in metas if m and "source" in m}
    return sorted(sources)


def collection_count() -> int:
    """Return the total number of chunks in the index."""
    return get_collection().count()


def reset_collection() -> None:
    """Drop and recreate the collection (used in tests)."""
    global _client, _collection
    if _client is not None:
        _client.delete_collection(_COLLECTION_NAME)
        _collection = None

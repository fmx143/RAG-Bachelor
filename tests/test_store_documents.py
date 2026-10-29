"""documents table, rename across stores, and Chroma re-tagging without re-embedding."""

from __future__ import annotations

from rag_bachelor.ingest import index
from rag_bachelor.study import store
from rag_bachelor.study.store import BankQuestion


def test_documents_crud() -> None:
    store.add_document("a/x.pdf", "h1")
    store.add_document("y.pdf", "h2")
    assert store.document_paths() == {"a/x.pdf": "h1", "y.pdf": "h2"}
    assert store.document_by_sha("h1") == "a/x.pdf"
    assert store.document_by_sha("nope") is None
    store.delete_document("a/x.pdf")
    assert store.document_paths() == {"y.pdf": "h2"}


def test_rename_document_updates_bank_and_cards() -> None:
    store.add_document("x.pdf", "h")
    store.add_bank_questions(
        [
            BankQuestion(
                question="Q?",
                answer="A",
                difficulty="facile",
                source="x.pdf",
                pages=[1],
                chunk_ids=[],
            )
        ]
    )
    store.add_card("Q", "A", "x.pdf", "facile")
    store.add_card("Q2", "A2", "autre", "facile")

    store.rename_document("x.pdf", "Algo/x.pdf")

    assert store.document_paths() == {"Algo/x.pdf": "h"}
    assert [q.source for q in store.list_bank_questions()] == ["Algo/x.pdf"]
    assert sorted(c.topic for c in store.get_all_cards()) == ["Algo/x.pdf", "autre"]


def test_rename_source_keeps_embeddings() -> None:
    docs, facts = index.get_collection(), index.get_facts_collection()
    docs.add(
        ids=["d1", "d2"],
        documents=["un", "deux"],
        embeddings=[[1.0, 0.0], [0.0, 1.0]],
        metadatas=[{"source": "x.pdf", "page": 1}, {"source": "other.pdf", "page": 1}],
    )
    facts.add(
        ids=["d1__f0"],
        documents=["fait"],
        embeddings=[[0.5, 0.5]],
        metadatas=[{"source": "x.pdf", "chunk_id": "d1", "subject": "s", "object": "o"}],
    )

    index.rename_source("x.pdf", "Algo/x.pdf")

    got = docs.get(ids=["d1", "d2"], include=["metadatas", "embeddings"])
    sources = dict(zip(got["ids"], [m["source"] for m in got["metadatas"]], strict=True))
    assert sources == {"d1": "Algo/x.pdf", "d2": "other.pdf"}
    assert [list(e) for e in got["embeddings"]] == [[1.0, 0.0], [0.0, 1.0]]
    assert facts.get(include=["metadatas"])["metadatas"][0]["source"] == "Algo/x.pdf"
    assert docs.get(ids=["d1"])["metadatas"][0]["page"] == 1

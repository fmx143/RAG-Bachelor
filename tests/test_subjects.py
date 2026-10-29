"""Subjects: top-level folders of pdfs_dir scope listings, retrieval, moves and uploads."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rag_bachelor.app.web.routes import documentation as docs_module
from rag_bachelor.app.web.routes import subjects as subjects_module
from rag_bachelor.config import settings
from rag_bachelor.study import store
from rag_bachelor.study.stats import get_topic_stats
from rag_bachelor.study.subjects import (
    active_subject,
    list_subjects,
    scoped_topic,
    set_active_subject,
    subject_of,
)


def test_subject_of_is_the_first_segment() -> None:
    assert subject_of("Bachelor/S1/a.pdf") == "Bachelor"
    assert subject_of("a.pdf") == "a.pdf"


def test_list_and_active_subject() -> None:
    assert list_subjects() == ["Bachelor"]  # empty library -> default
    (settings.pdfs_dir / "ML").mkdir(parents=True)
    (settings.pdfs_dir / "Anglais").mkdir()
    assert list_subjects() == ["Anglais", "ML"]
    assert active_subject() == "Anglais"  # persisted default missing -> first

    set_active_subject("ML")
    assert active_subject() == "ML"
    assert scoped_topic("Cours.pdf") == "ML/Cours.pdf"
    assert scoped_topic("ML/Cours.pdf") == "ML/Cours.pdf"


def test_store_listings_are_scoped_to_the_subject() -> None:
    store.add_card("Q bachelor ?", "R.", topic="Bachelor/a.pdf", difficulty="facile")
    store.add_card("Q ml ?", "R.", topic="ML/b.pdf", difficulty="facile")
    store.add_sheet("ML/b.pdf", 1, 2, "notes")
    store.add_sheet("Bachelor/a.pdf", 1, 2, "notes")

    assert [c.topic for c in store.get_due_cards(limit=10, subject="ML")] == ["ML/b.pdf"]
    assert [c.topic for c in store.get_all_cards(subject="Bachelor")] == ["Bachelor/a.pdf"]
    assert [s.source for s in store.list_sheets("ML")] == ["ML/b.pdf"]
    assert [s.topic for s in get_topic_stats("ML")] == ["ML/b.pdf"]
    assert len(get_topic_stats()) == 2


def test_retrieve_sources_excludes_other_subjects(monkeypatch: pytest.MonkeyPatch) -> None:
    from rag_bachelor.core import retriever
    from rag_bachelor.ingest.index import get_collection

    monkeypatch.setattr(settings, "reranker_model", "")
    monkeypatch.setattr(retriever, "embed", lambda texts: [[1.0, 0.0] for _ in texts])
    get_collection().upsert(
        ids=["Bachelor/a.pdf__p1__c0", "ML/b.pdf__p1__c0"],
        documents=["notions", "réseaux de neurones"],
        embeddings=[[1.0, 0.0], [1.0, 0.0]],  # type: ignore[arg-type]
        metadatas=[{"source": "Bachelor/a.pdf", "page": 1}, {"source": "ML/b.pdf", "page": 1}],  # type: ignore[arg-type]
    )

    hits = retriever.retrieve("question", top_k=5, sources=["ML/b.pdf"])
    assert {h.source for h in hits} == {"ML/b.pdf"}
    assert retriever.retrieve("question", top_k=5, sources=[]) == []


# ── routes ───────────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(docs_module, "extract_pages", lambda path: [])
    monkeypatch.setattr(docs_module, "convert", lambda path: None)
    monkeypatch.setattr(docs_module, "layout_items", lambda doc: [])
    monkeypatch.setattr(docs_module, "section_chunks", lambda items, src: [])
    monkeypatch.setattr(docs_module, "delete_source", lambda name: None)
    monkeypatch.setattr(docs_module, "rename_source", lambda old, new: None)
    monkeypatch.setattr(docs_module, "list_sources", lambda: [])
    monkeypatch.setattr(docs_module, "collection_count", lambda: 0)
    enqueued: list[str] = []
    monkeypatch.setattr(docs_module, "_enqueue", lambda rels: enqueued.extend(rels) or True)
    monkeypatch.setattr(docs_module, "_enqueued", enqueued, raising=False)
    docs_module._PENDING.clear()
    app = FastAPI()
    app.include_router(docs_module.router)
    app.include_router(subjects_module.router)
    with TestClient(app, follow_redirects=False) as c:
        yield c


def _upload(client: TestClient, relpath: str, **data: str) -> int:
    return client.post(
        "/docs/upload",
        files={"file": ("x.pdf", b"%PDF-1.4 " + relpath.encode(), "application/pdf")},
        data={"relpath": relpath, **data},
    ).status_code


def test_create_subject_activates_it_and_rejects_bad_names(client: TestClient) -> None:
    resp = client.post("/subjects", data={"name": "Machine Learning"})
    assert resp.status_code == 303
    assert (settings.pdfs_dir / "Machine Learning").is_dir()
    assert active_subject() == "Machine Learning"

    client.post("/subjects", data={"name": "../evil"})
    client.post("/subjects", data={"name": "a/b"})
    assert not (settings.pdfs_dir.parent / "evil").exists()
    assert list_subjects() == ["Machine Learning"]


def test_upload_outside_active_subject_is_refused(client: TestClient) -> None:
    client.post("/subjects", data={"name": "ML"})

    assert _upload(client, "Bachelor/a.pdf") == 400
    assert _upload(client, "a.pdf") == 400
    assert not (settings.pdfs_dir / "Bachelor").exists()
    assert _upload(client, "ML/Intro/a.pdf") == 200


def test_upload_without_index_does_not_enqueue(client: TestClient) -> None:
    client.post("/subjects", data={"name": "ML"})

    _upload(client, "ML/a.pdf", index="false")
    _upload(client, "ML/b.pdf")

    assert (settings.pdfs_dir / "ML/a.pdf").exists()
    assert docs_module._enqueued == ["ML/b.pdf"]  # type: ignore[attr-defined]


def test_root_pdfs_migrate_into_bachelor_with_their_study_data(client: TestClient) -> None:
    settings.pdfs_dir.mkdir(parents=True, exist_ok=True)
    (settings.pdfs_dir / "cours.pdf").write_bytes(b"%PDF-1.4 c")
    store.add_card("Q ?", "R.", topic="cours.pdf", difficulty="facile")
    store.add_sheet("cours.pdf", 1, 2, "notes")

    assert docs_module._sync_documents() == ["Bachelor/cours.pdf"]

    assert not (settings.pdfs_dir / "cours.pdf").exists()
    assert [c.topic for c in store.get_all_cards(subject="Bachelor")] == ["Bachelor/cours.pdf"]
    assert [s.source for s in store.list_sheets("Bachelor")] == ["Bachelor/cours.pdf"]


def _library(*rels: str) -> None:
    for rel in rels:
        path = settings.pdfs_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF-1.4 " + rel.encode())


def test_move_file_and_folder_across_subjects(client: TestClient) -> None:
    _library("Bachelor/a.pdf", "Bachelor/S1/b.pdf", "Bachelor/S1/c.pdf", "ML/x.pdf")

    msg, err = docs_module._move_paths("Bachelor/a.pdf", "ML/a.pdf", folder=False)
    assert err == "" and "1 document" in msg
    msg, err = docs_module._move_paths("Bachelor/S1", "ML/S1", folder=True)
    assert err == "" and "2 document" in msg

    assert set(docs_module._sync_documents()) == {
        "ML/S1/b.pdf",
        "ML/S1/c.pdf",
        "ML/a.pdf",
        "ML/x.pdf",
    }


def test_move_refuses_conflict_self_nesting_and_busy(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _library("Bachelor/a.pdf", "Bachelor/S1/b.pdf", "ML/a.pdf")

    _, err = docs_module._move_paths("Bachelor/a.pdf", "ML/a.pdf", folder=False)
    assert "existe déjà" in err
    _, err = docs_module._move_paths("Bachelor/S1", "Bachelor/S1/deep", folder=True)
    assert "dans lui-même" in err
    _, err = docs_module._move_paths("Bachelor/zzz.pdf", "ML/z.pdf", folder=False)
    assert "Introuvable" in err

    monkeypatch.setattr(docs_module, "_busy", lambda *paths: "Bachelor/S1/b.pdf")
    _, err = docs_module._move_paths("Bachelor/S1", "ML/S1", folder=True)
    assert "en cours d'indexation" in err
    assert (settings.pdfs_dir / "Bachelor/S1/b.pdf").exists()


def test_move_route_creates_destination_folder(client: TestClient) -> None:
    _library("Bachelor/a.pdf")

    resp = client.post(
        "/docs/move", data={"path": "Bachelor/a.pdf", "dest": "ML/Intro", "name": "a.pdf"}
    )

    assert resp.status_code == 200
    assert (settings.pdfs_dir / "ML/Intro/a.pdf").exists()
    assert not (settings.pdfs_dir / "Bachelor/a.pdf").exists()

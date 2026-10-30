"""Document page › Fichier: re-index, graph, move between Livres/Sources, delete."""

from __future__ import annotations

from collections.abc import Iterator

import fitz  # pymupdf
import pytest
from fastapi.testclient import TestClient

from rag_bachelor.app.web import server
from rag_bachelor.app.web.routes import documentation as docs_module
from rag_bachelor.config import settings
from rag_bachelor.ingest import index
from rag_bachelor.study import store
from rag_bachelor.study.store import Chapter


def _pdf(rel: str) -> None:
    """A real one-page PDF on disk, indexed under *rel*."""
    path = settings.pdfs_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    doc.new_page().insert_text((50, 50), rel)
    doc.save(path)
    index.get_collection().upsert(
        ids=[f"{rel}__p1__c0"],
        documents=["texte"],
        embeddings=[[1.0, 0.0, 0.0]],  # type: ignore[arg-type]
        metadatas=[{"source": rel, "page": 1}],
    )


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(docs_module, "_run_queue", lambda: None)
    monkeypatch.setattr(docs_module, "_run_graph_job", lambda rel: None)
    with TestClient(server.app, follow_redirects=False) as c:
        yield c
    docs_module._PENDING.clear()
    docs_module._JOB.update(running=False, active="")


def test_move_unsorted_into_livres_subfolder_keeps_its_study_data(client: TestClient) -> None:
    _pdf("Bachelor/vrac.pdf")
    store.set_chapters("Bachelor/vrac.pdf", [Chapter("Un", 1, 1)])
    r = client.post(
        "/docs/file/move",
        data={"path": "Bachelor/vrac.pdf", "kind": "livre", "folder": " S3 / Compta ", "name": ""},
    )
    new = "Bachelor/Livres/S3/Compta/vrac.pdf"
    assert r.status_code == 303 and r.headers["location"] == f"/document/{new}?tab=fichier"
    assert (settings.pdfs_dir / new).is_file() and not (
        settings.pdfs_dir / "Bachelor/vrac.pdf"
    ).exists()
    assert new in index.list_sources() and store.list_chapters(new) == [Chapter("Un", 1, 1)]
    page = client.get(f"/document/{new}?tab=fichier").text
    assert 'value="S3/Compta"' in page and 'value="vrac.pdf"' in page


def test_move_rejects_bad_name_existing_target_and_busy_document(client: TestClient) -> None:
    _pdf("Bachelor/Livres/a.pdf")
    _pdf("Bachelor/Sources/a.pdf")
    base = {"path": "Bachelor/Livres/a.pdf", "folder": ""}
    bad = client.post("/docs/file/move", data={**base, "kind": "livre", "name": "x/y.pdf"})
    assert bad.headers["location"].endswith("err=invalid")
    taken = client.post("/docs/file/move", data={**base, "kind": "source", "name": "a.pdf"})
    assert taken.headers["location"].endswith("err=exists")
    assert "existe déjà" not in client.get(taken.headers["location"]).text  # keyed message only
    assert "porte déjà ce nom" in client.get(taken.headers["location"]).text
    client.post("/docs/file/index", data={"path": "Bachelor/Livres/a.pdf"})
    busy = client.post("/docs/file/move", data={**base, "kind": "source", "name": "b.pdf"})
    assert busy.headers["location"].endswith("err=busy")
    assert (
        client.post(
            "/docs/file/move", data={**base, "path": "Bachelor/x.pdf", "kind": "livre"}
        ).status_code
        == 404
    )


def test_index_and_graph_queue_the_document_and_return_to_it(client: TestClient) -> None:
    _pdf("Bachelor/Sources/n.pdf")
    r = client.post("/docs/file/index", data={"path": "Bachelor/Sources/n.pdf"})
    assert r.headers["location"] == "/document/Bachelor/Sources/n.pdf?tab=fichier"
    assert docs_module._PENDING == ["Bachelor/Sources/n.pdf"]
    page = client.get(r.headers["location"]).text
    assert "Indexation en cours" in page and 'hx-trigger="every 3s"' in page
    graph = client.post("/docs/file/graph", data={"path": "Bachelor/Sources/n.pdf"})
    assert graph.headers["location"].endswith("err=busy")  # the indexing job holds the slot


def test_delete_removes_the_document_and_shows_its_grid(client: TestClient) -> None:
    _pdf("Bachelor/Sources/n.pdf")
    r = client.post("/docs/file/delete", data={"path": "Bachelor/Sources/n.pdf"})
    assert r.status_code == 303 and r.headers["location"] == "/sources"
    assert not (settings.pdfs_dir / "Bachelor/Sources/n.pdf").exists()
    assert "Bachelor/Sources/n.pdf" not in index.list_sources()


def test_add_page_offers_book_or_source(client: TestClient) -> None:
    page = client.get("/docs?kind=source").text
    assert "Ajouter des <em>documents</em>" in page
    assert 'value="Sources" checked' in page and 'value="Livres" >' in page
    assert 'href="/docs?kind=livre"' in client.get("/livres").text

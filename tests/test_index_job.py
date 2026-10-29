"""Tests for the background indexing job (src/rag_bachelor/app/web/routes/documentation.py).

Covers the 524-timeout fix: POST /docs/index must return immediately (the actual
indexing runs as a FastAPI BackgroundTask) and progress must be pollable via
GET /docs/index/status until the job finishes.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rag_bachelor.app.web.routes import documentation as docs_module
from rag_bachelor.config import settings
from rag_bachelor.ingest.chunk import Chunk

_N_CHUNKS = 40  # > _BATCH so at least one full batch plus a remainder


def _fake_chunks(n: int) -> list[Chunk]:
    return [
        Chunk(text=f"chunk {i}", source="cours.pdf", page_num=1, chunk_index=i) for i in range(n)
    ]


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A minimal app with only the documentation router — no lifespan, no real model/Chroma."""
    monkeypatch.setattr(settings, "pdfs_dir", tmp_path)
    (tmp_path / "cours.pdf").write_bytes(b"%PDF-1.4 fake")

    monkeypatch.setattr(docs_module, "extract_pages", lambda path: [])
    monkeypatch.setattr(docs_module, "convert", lambda path: None)
    monkeypatch.setattr(docs_module, "layout_items", lambda doc: [])
    monkeypatch.setattr(docs_module, "section_chunks", lambda items, src: _fake_chunks(_N_CHUNKS))
    monkeypatch.setattr(docs_module, "delete_source", lambda name: None)
    monkeypatch.setattr(docs_module, "list_sources", lambda: ["cours.pdf"])
    monkeypatch.setattr(docs_module, "collection_count", lambda: _N_CHUNKS)

    graphs: list[str] = []
    monkeypatch.setattr(
        docs_module, "build_graph", lambda name, progress: graphs.append(name) or (3, 0)
    )
    monkeypatch.setattr(docs_module, "_graphs", graphs, raising=False)

    batch_sizes: list[int] = []
    monkeypatch.setattr(
        docs_module, "index_chunks", lambda chunks, prefix=None: batch_sizes.append(len(chunks))
    )
    monkeypatch.setattr(docs_module, "_batch_sizes", batch_sizes, raising=False)

    docs_module._PENDING.clear()
    docs_module._JOB.update(
        running=False,
        phase="index",
        active="",
        file_index=0,
        file_total=0,
        current="",
        chunks_done=0,
        chunks_total=0,
        message=None,
        error=None,
    )

    app = FastAPI()
    app.include_router(docs_module.router)
    with TestClient(app) as test_client:
        yield test_client


def test_index_all_returns_immediately_and_reports_progress(client: TestClient) -> None:
    resp = client.post("/docs/index")
    assert resp.status_code == 200
    # TestClient runs BackgroundTasks synchronously before returning, so by the
    # time we get the response the job has already finished — this asserts the
    # state machine (batching, message), not real-world request latency.
    assert not docs_module._JOB["running"]

    batch_sizes: list[int] = docs_module._batch_sizes  # type: ignore[attr-defined]
    assert batch_sizes == [16, 16, 8]
    assert sum(batch_sizes) == _N_CHUNKS


def test_indexing_builds_the_graph_unless_auto_build_is_off(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client.post("/docs/index")
    assert docs_module._graphs == ["cours.pdf"]  # type: ignore[attr-defined]

    docs_module._graphs.clear()  # type: ignore[attr-defined]
    monkeypatch.setattr(settings, "graph_auto_build", False)
    client.post("/docs/index")
    assert docs_module._graphs == []  # type: ignore[attr-defined]


def test_graph_button_builds_one_indexed_document(client: TestClient) -> None:
    resp = client.post("/docs/graph/doc", data={"path": "cours.pdf"})

    assert resp.status_code == 200
    assert docs_module._graphs == ["cours.pdf"]  # type: ignore[attr-defined]
    assert "3 faits" in docs_module._JOB["message"]  # type: ignore[operator]
    assert "non indexé" in client.post("/docs/graph/doc", data={"path": "inconnu.pdf"}).text


def test_status_while_running_polls_via_self_reinjecting_trigger(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    docs_module._JOB.update(
        running=True, file_total=1, file_index=1, chunks_total=40, chunks_done=20
    )
    resp = client.get("/docs/index/status")

    assert resp.status_code == 200
    assert 'hx-trigger="load delay:2s"' in resp.text
    assert 'hx-target="this"' in resp.text
    assert 'hx-swap="outerHTML"' in resp.text


def test_status_after_completion_shows_doc_list_and_stops_polling(client: TestClient) -> None:
    client.post("/docs/index")
    resp = client.get("/docs/index/status")

    assert resp.status_code == 200
    assert "indexés" in resp.text
    assert "hx-trigger" not in resp.text


def test_second_start_while_running_does_not_restart_the_job(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    docs_module._JOB.update(
        running=True, file_total=1, file_index=1, chunks_total=40, chunks_done=20
    )
    started = docs_module._try_start(1)
    assert started is False
    assert docs_module._JOB["chunks_done"] == 20  # untouched, not reset to 0


def _upload(client: TestClient, relpath: str, content: bytes) -> str:
    return client.post(
        "/docs/upload",
        files={"file": (relpath.rsplit("/", 1)[-1], content, "application/pdf")},
        data={"relpath": relpath},
    ).text


def test_upload_new_pdf_is_indexed_automatically(client: TestClient, tmp_path: Path) -> None:
    _upload(client, "nouveau.pdf", b"%PDF-1.4 new")

    assert (tmp_path / "nouveau.pdf").read_bytes() == b"%PDF-1.4 new"
    assert sum(docs_module._batch_sizes) == _N_CHUNKS  # type: ignore[attr-defined]


def test_upload_keeps_the_folder_structure(client: TestClient, tmp_path: Path) -> None:
    _upload(client, "Bachelor/S1/Algo/Chapitre 1.pdf", b"%PDF-1.4 algo")

    assert (tmp_path / "Bachelor/S1/Algo/Chapitre 1.pdf").read_bytes() == b"%PDF-1.4 algo"
    assert not (tmp_path / "Chapitre 1.pdf").exists()


def test_same_file_name_in_two_folders_is_kept_twice(client: TestClient, tmp_path: Path) -> None:
    _upload(client, "S1/Chapitre 1.pdf", b"%PDF-1.4 un")
    _upload(client, "S2/Chapitre 1.pdf", b"%PDF-1.4 deux")

    assert (tmp_path / "S1/Chapitre 1.pdf").read_bytes() == b"%PDF-1.4 un"
    assert (tmp_path / "S2/Chapitre 1.pdf").read_bytes() == b"%PDF-1.4 deux"


def test_upload_same_content_same_path_is_already_up_to_date(client: TestClient) -> None:
    text = _upload(client, "cours.pdf", b"%PDF-1.4 fake")

    assert "déjà à jour" in text
    assert docs_module._batch_sizes == []  # type: ignore[attr-defined]


def test_upload_other_content_at_taken_path_is_refused(client: TestClient, tmp_path: Path) -> None:
    resp = client.post(
        "/docs/upload",
        files={"file": ("cours.pdf", b"%PDF-1.4 other", "application/pdf")},
        data={"relpath": "cours.pdf"},
    )

    assert resp.status_code == 409
    assert (tmp_path / "cours.pdf").read_bytes() == b"%PDF-1.4 fake"
    assert docs_module._batch_sizes == []  # type: ignore[attr-defined]


def test_upload_known_content_elsewhere_moves_without_reindexing(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    renamed: list[tuple[str, str]] = []
    monkeypatch.setattr(docs_module, "rename_source", lambda old, new: renamed.append((old, new)))

    text = _upload(client, "Bachelor/S1/cours.pdf", b"%PDF-1.4 fake")

    assert not (tmp_path / "cours.pdf").exists()
    assert (tmp_path / "Bachelor/S1/cours.pdf").read_bytes() == b"%PDF-1.4 fake"
    assert renamed == [("cours.pdf", "Bachelor/S1/cours.pdf")]
    assert "sans réindexation" in text
    assert docs_module._batch_sizes == []  # type: ignore[attr-defined]
    assert docs_module.document_paths().keys() == {"Bachelor/S1/cours.pdf"}


def test_two_uploads_during_a_running_job_are_both_indexed(client: TestClient) -> None:
    docs_module._JOB.update(running=True, file_total=1, active="cours.pdf")

    _upload(client, "a.pdf", b"%PDF-1.4 a")
    _upload(client, "b.pdf", b"%PDF-1.4 b")

    assert docs_module._PENDING == ["a.pdf", "b.pdf"]
    assert docs_module._JOB["file_total"] == 3
    docs_module._JOB["running"] = False
    docs_module._PENDING.clear()


def test_upload_non_pdf_content_is_rejected(client: TestClient, tmp_path: Path) -> None:
    text = _upload(client, "piege.pdf", b"<html>not a pdf</html>")

    assert not (tmp_path / "piege.pdf").exists()
    assert "pas un PDF" in text


def test_upload_too_large_is_rejected(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(docs_module, "_MAX_UPLOAD_BYTES", 10)
    text = _upload(client, "gros.pdf", b"%PDF-1.4 bigger than ten bytes")

    assert not (tmp_path / "gros.pdf").exists()
    assert "trop volumineux" in text


def test_upload_bad_path_is_rejected_with_400(client: TestClient, tmp_path: Path) -> None:
    resp = client.post(
        "/docs/upload",
        files={"file": ("x.pdf", b"%PDF-1.4 x", "application/pdf")},
        data={"relpath": "../evil.pdf"},
    )

    assert resp.status_code == 400
    assert not (tmp_path.parent / "evil.pdf").exists()


def test_tree_counts_documents_per_folder_and_filters(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(docs_module, "rename_source", lambda old, new: None)
    _upload(client, "Bachelor/S1/a.pdf", b"%PDF-1.4 a")
    _upload(client, "Bachelor/S1/b.pdf", b"%PDF-1.4 b")
    _upload(client, "Bachelor/S2/c.pdf", b"%PDF-1.4 c")

    html = client.get("/docs/list").text
    assert "Non classés" in html  # cours.pdf from the fixture
    assert 'tree-count">3<' in html  # Bachelor
    assert 'tree-count">2<' in html  # Bachelor/S1

    filtered = client.get("/docs/list", params={"q": "C.PDF"}).text
    assert "c.pdf" in filtered and "a.pdf" not in filtered


def test_delete_folder_removes_its_files_and_rows(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _upload(client, "S1/a.pdf", b"%PDF-1.4 a")
    _upload(client, "S1/sub/b.pdf", b"%PDF-1.4 b")
    _upload(client, "S2/c.pdf", b"%PDF-1.4 c")

    client.post("/docs/delete/folder", data={"path": "S1"})

    assert not (tmp_path / "S1").exists()
    assert (tmp_path / "S2/c.pdf").exists()
    assert (tmp_path / "cours.pdf").exists()
    assert set(docs_module.document_paths()) == {"cours.pdf", "S2/c.pdf"}


def test_progress_wording_tells_graph_from_indexing(client: TestClient) -> None:
    docs_module._JOB.update(running=True, file_total=1, file_index=1, phase="graph")
    assert "Construction du graphe" in client.get("/docs/index/status").text

    docs_module._JOB.update(phase="index")
    text = client.get("/docs/index/status").text
    assert "Indexation en cours" in text and "graphe" not in text

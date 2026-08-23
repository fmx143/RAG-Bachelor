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
    return [Chunk(text=f"chunk {i}", source="cours.pdf", page_num=1, chunk_index=i) for i in range(n)]


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A minimal app with only the documentation router — no lifespan, no real model/Chroma."""
    monkeypatch.setattr(settings, "pdfs_dir", tmp_path)
    (tmp_path / "cours.pdf").write_bytes(b"%PDF-1.4 fake")

    monkeypatch.setattr(docs_module, "extract_pages", lambda path: [])
    monkeypatch.setattr(docs_module, "chunk_pages", lambda pages: _fake_chunks(_N_CHUNKS))
    monkeypatch.setattr(docs_module, "delete_source", lambda name: None)
    monkeypatch.setattr(docs_module, "list_sources", lambda: ["cours.pdf"])
    monkeypatch.setattr(docs_module, "collection_count", lambda: _N_CHUNKS)

    batch_sizes: list[int] = []
    monkeypatch.setattr(docs_module, "index_chunks", lambda chunks: batch_sizes.append(len(chunks)))
    monkeypatch.setattr(docs_module, "_batch_sizes", batch_sizes, raising=False)

    docs_module._JOB.update(
        running=False, file_index=0, file_total=0, current="",
        chunks_done=0, chunks_total=0, message=None, error=None,
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


def test_status_after_completion_shows_doc_list_and_stops_polling(client: TestClient) -> None:
    client.post("/docs/index")
    resp = client.get("/docs/index/status")

    assert resp.status_code == 200
    assert "indexés" in resp.text
    assert "hx-trigger" not in resp.text


def test_second_start_while_running_does_not_restart_the_job(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    docs_module._JOB.update(running=True, file_total=1, file_index=1, chunks_total=40, chunks_done=20)
    started = docs_module._try_start(1)
    assert started is False
    assert docs_module._JOB["chunks_done"] == 20  # untouched, not reset to 0

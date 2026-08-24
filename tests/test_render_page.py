"""render_page_png() output, and GET /docs/page/{name}/{page}.png guarding."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rag_bachelor.app.web.routes import documentation as docs_module
from rag_bachelor.config import settings
from rag_bachelor.ingest.extract import render_page_png

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def test_render_page_png_returns_png_bytes(sample_pdf: Path) -> None:
    png = render_page_png(sample_pdf, 1)
    assert png.startswith(_PNG_MAGIC)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sample_pdf: Path) -> Iterator[TestClient]:
    monkeypatch.setattr(settings, "pdfs_dir", tmp_path)
    (tmp_path / sample_pdf.name).write_bytes(sample_pdf.read_bytes())

    app = FastAPI()
    app.include_router(docs_module.router)
    with TestClient(app) as test_client:
        yield test_client


def test_page_image_route_returns_png(client: TestClient, sample_pdf: Path) -> None:
    resp = client.get(f"/docs/page/{sample_pdf.name}/1.png")
    assert resp.status_code == 200
    assert resp.content.startswith(_PNG_MAGIC)
    assert resp.headers["content-type"] == "image/png"


def test_page_image_route_rejects_path_traversal(client: TestClient) -> None:
    resp = client.get("/docs/page/..%2f..%2fetc%2fpasswd/1.png")
    assert resp.status_code == 404


def test_page_image_route_rejects_non_pdf_name(client: TestClient) -> None:
    resp = client.get("/docs/page/notes.txt/1.png")
    assert resp.status_code == 404


def test_page_image_route_rejects_out_of_range_page(client: TestClient, sample_pdf: Path) -> None:
    resp = client.get(f"/docs/page/{sample_pdf.name}/999.png")
    assert resp.status_code == 404

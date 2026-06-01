"""📚 Documentation routes — upload, index, and delete PDF documents."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from rag_bachelor.app.web._deps import sidebar_ctx, templates
from rag_bachelor.config import settings
from rag_bachelor.ingest.chunk import chunk_pages
from rag_bachelor.ingest.extract import extract_pages
from rag_bachelor.ingest.index import (
    collection_count,
    delete_source,
    index_chunks,
    list_sources,
)

router = APIRouter()


def _resolve_safe(name: str) -> Path | None:
    """Return the resolved path only if it is a .pdf inside pdfs_dir, else None."""
    safe = Path(name).name  # strip any path components
    if not safe or safe in (".", "..") or not safe.lower().endswith(".pdf"):
        return None
    candidate = (settings.pdfs_dir / safe).resolve()
    try:
        candidate.relative_to(settings.pdfs_dir.resolve())
    except ValueError:
        return None
    return candidate


# ── Helpers ────────────────────────────────────────────────────────────────────


async def _doc_list_ctx() -> dict[str, object]:
    """Build the context needed to render the doc-list partial (non-blocking)."""
    def _sync() -> dict[str, object]:
        all_pdfs: list[Path] = (
            sorted(settings.pdfs_dir.glob("*.pdf")) if settings.pdfs_dir.exists() else []
        )
        indexed: set[str] = set(list_sources())
        pdfs = [{"name": p.name, "indexed": p.name in indexed} for p in all_pdfs]
        return {"pdfs": pdfs, "chunk_count": collection_count()}
    return await asyncio.to_thread(_sync)


async def _index_one(path: Path) -> tuple[int, list[int]]:
    """Index a single PDF in a thread; return (chunk_count, empty_page_numbers)."""
    def _sync() -> tuple[int, list[int]]:
        pages = extract_pages(path)
        empty_pages = [p.page_num for p in pages if p.is_empty]
        chunks = chunk_pages(pages)
        delete_source(path.name)
        index_chunks(chunks)
        return len(chunks), empty_pages
    return await asyncio.to_thread(_sync)


# ── Full-page GET ──────────────────────────────────────────────────────────────


@router.get("/docs", response_class=HTMLResponse)
async def docs_page(request: Request) -> Response:
    ctx: dict[str, object] = {
        "request": request,
        "active_tab": "docs",
        **sidebar_ctx(),
        **(await _doc_list_ctx()),
        "message": None,
        "error": None,
    }
    return templates.TemplateResponse(request, "documentation.html", ctx)


# ── HTMX partials (POST → returns doc_list partial) ───────────────────────────


@router.post("/docs/upload", response_class=HTMLResponse)
async def upload_pdfs(
    request: Request,
    files: Annotated[list[UploadFile], File()],
) -> Response:
    """Save uploaded PDFs to data/pdfs/ and return the refreshed doc-list."""
    messages: list[str] = []
    errors: list[str] = []

    settings.pdfs_dir.mkdir(parents=True, exist_ok=True)
    for f in files:
        # Strip path traversal, then allow only safe characters.
        # This also prevents quote/backslash injection into HTML attributes.
        raw_name = Path(f.filename or "upload.pdf").name
        safe_name = re.sub(r"[^A-Za-z0-9._\- ]", "_", raw_name) or "upload.pdf"
        if not safe_name.lower().endswith(".pdf"):
            safe_name += ".pdf"
        dest = settings.pdfs_dir / safe_name
        try:
            data = await f.read()
            await asyncio.to_thread(dest.write_bytes, data)
            messages.append(f"✅ {safe_name} sauvegardé")
        except Exception as exc:
            errors.append(f"❌ {safe_name} : {exc}")

    ctx: dict[str, object] = {
        "request": request,
        **(await _doc_list_ctx()),
        "message": " · ".join(messages) or None,
        "error": " · ".join(errors) or None,
    }
    return templates.TemplateResponse(request, "partials/doc_list.html", ctx)


@router.post("/docs/index", response_class=HTMLResponse)
async def index_all(request: Request) -> Response:
    """Re-index every PDF in data/pdfs/ and return the refreshed doc-list."""
    pdfs: list[Path] = (
        sorted(settings.pdfs_dir.glob("*.pdf")) if settings.pdfs_dir.exists() else []
    )
    if not pdfs:
        ctx: dict[str, object] = {
            "request": request,
            **(await _doc_list_ctx()),
            "message": None,
            "error": "⚠️ Aucun PDF trouvé dans data/pdfs/.",
        }
        return templates.TemplateResponse(request, "partials/doc_list.html", ctx)

    total_chunks = 0
    for pdf in pdfs:
        n, _ = await _index_one(pdf)
        total_chunks += n

    ctx = {
        "request": request,
        **(await _doc_list_ctx()),
        "message": f"✅ {len(pdfs)} document(s) indexés — {total_chunks} chunks au total",
        "error": None,
    }
    return templates.TemplateResponse(request, "partials/doc_list.html", ctx)


@router.post("/docs/index/{name}", response_class=HTMLResponse)
async def index_one(request: Request, name: str) -> Response:
    """Index a single PDF by filename and return the refreshed doc-list."""
    path = _resolve_safe(name)
    if path is None or not path.exists():
        ctx: dict[str, object] = {
            "request": request,
            **(await _doc_list_ctx()),
            "message": None,
            "error": f"❌ Fichier introuvable : {name}",
        }
        return templates.TemplateResponse(request, "partials/doc_list.html", ctx)

    n_chunks, empty_pages = await _index_one(path)
    warning = (
        f" (pages vides ignorées : {', '.join(map(str, empty_pages))})"
        if empty_pages
        else ""
    )
    ctx = {
        "request": request,
        **(await _doc_list_ctx()),
        "message": f"✅ {name} — {n_chunks} chunks indexés{warning}",
        "error": None,
    }
    return templates.TemplateResponse(request, "partials/doc_list.html", ctx)


@router.post("/docs/delete/{name}", response_class=HTMLResponse)
async def delete_pdf(request: Request, name: str) -> Response:
    """Delete a PDF from disk and from the vector index."""
    path = _resolve_safe(name)
    if path is None:
        ctx: dict[str, object] = {
            "request": request,
            **(await _doc_list_ctx()),
            "message": None,
            "error": f"❌ Nom de fichier invalide : {name}",
        }
        return templates.TemplateResponse(request, "partials/doc_list.html", ctx)
    path.unlink(missing_ok=True)
    delete_source(Path(name).name)

    ctx: dict[str, object] = {
        "request": request,
        **(await _doc_list_ctx()),
        "message": f"🗑️ {name} supprimé",
        "error": None,
    }
    return templates.TemplateResponse(request, "partials/doc_list.html", ctx)


# ── Form helper — chunk count via GET (for the counter badge) ─────────────────

@router.get("/docs/count", response_class=HTMLResponse)
async def chunk_count(request: Request) -> Response:
    """Return a plain-text chunk count (used by the metric badge)."""
    return HTMLResponse(str(collection_count()))



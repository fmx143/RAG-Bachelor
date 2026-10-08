"""📚 Documentation routes — upload, index, and delete PDF documents."""

from __future__ import annotations

import asyncio
import hashlib
import re
import threading
from pathlib import Path
from typing import Annotated

import fitz  # pymupdf
from fastapi import APIRouter, BackgroundTasks, File, Request, UploadFile
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from rag_bachelor.app.web._deps import sidebar_ctx, templates
from rag_bachelor.app.web.routes.settings import vision_captioning_enabled
from rag_bachelor.config import settings
from rag_bachelor.ingest.chunk import chunk_pages
from rag_bachelor.ingest.extract import extract_pages, render_page_png
from rag_bachelor.ingest.index import (
    collection_count,
    delete_source,
    index_chunks,
    list_sources,
)
from rag_bachelor.ingest.vision import caption_page

router = APIRouter()

# Chunks per embed() call while indexing — small enough that the progress bar
# visibly advances (~9s/batch at the measured ~1.8 chunks/s), large enough not
# to lose the batching benefit in embeddings.py.
_BATCH = 16

# Single in-process indexing job. Safe without extra locking around field
# reads/writes (GIL + single uvicorn worker, no --workers — see docker-compose.yml);
# the lock only guards the check-and-set at job start so two POSTs can't both start.
_JOB_LOCK = threading.Lock()
_JOB: dict[str, object] = {
    "running": False,
    "file_index": 0,
    "file_total": 0,
    "current": "",
    "chunks_done": 0,
    "chunks_total": 0,
    "message": None,
    "error": None,
}


def _try_start(file_total: int) -> bool:
    """Atomically claim the job slot. Returns False if a job is already running."""
    with _JOB_LOCK:
        if _JOB["running"]:
            return False
        _JOB.update(
            running=True,
            file_index=0,
            file_total=file_total,
            current="",
            chunks_done=0,
            chunks_total=0,
            message=None,
            error=None,
        )
        return True


def _run_index_job(pdfs: list[Path], notes: str = "") -> None:
    """Index every PDF in *pdfs*, updating _JOB after each chunk batch.

    *notes* (e.g. files skipped as duplicates) is prefixed to the final message.
    Runs in FastAPI's BackgroundTasks threadpool — sync function, blocking
    calls are fine here (there is no event loop to block).
    """
    total_chunks = 0
    empty_pages: list[int] = []
    no_text: list[str] = []
    caption_figures = vision_captioning_enabled()
    try:
        for i, pdf in enumerate(pdfs, start=1):
            pages = extract_pages(pdf)
            if caption_figures:
                for p in pages:
                    if not p.has_images:
                        continue
                    caption = caption_page(pdf, p.page_num)
                    if caption:
                        p.text = f"{p.text}\n\n[Figure] {caption}".strip()
                        p.is_empty = False
            if len(pdfs) == 1:
                empty_pages = [p.page_num for p in pages if p.is_empty]
            chunks = chunk_pages(pages)
            _JOB.update(file_index=i, current=pdf.name, chunks_done=0, chunks_total=len(chunks))
            delete_source(pdf.name)
            for start in range(0, len(chunks), _BATCH):
                index_chunks(chunks[start : start + _BATCH])
                _JOB["chunks_done"] = min(start + _BATCH, len(chunks))
            total_chunks += len(chunks)
            if not chunks:
                no_text.append(pdf.name)

        warning = (
            f" (pages vides ignorées : {', '.join(map(str, empty_pages))})" if empty_pages else ""
        )
        if no_text:
            warning += f" ⚠️ aucun texte extrait (PDF scanné ?) : {', '.join(no_text)}"
        if len(pdfs) == 1:
            msg = f"✅ {pdfs[0].name} — {total_chunks} chunks indexés{warning}"
        else:
            msg = f"✅ {len(pdfs)} document(s) indexés — {total_chunks} chunks au total{warning}"
        _JOB["message"] = f"{notes} · {msg}" if notes else msg
    except Exception as exc:  # noqa: BLE001 — surfaced to the user via the status partial
        _JOB["error"] = f"❌ Échec de l'indexation : {exc}"
    finally:
        _JOB["running"] = False


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


# ── Full-page GET ──────────────────────────────────────────────────────────────


@router.get("/docs", response_class=HTMLResponse)
async def docs_page(request: Request) -> Response:
    ctx: dict[str, object] = {
        "request": request,
        "active_tab": "docs",
        "job_running": _JOB["running"],
        "job": _JOB,
        "pct": _job_pct(),
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
    background_tasks: BackgroundTasks,
    files: Annotated[list[UploadFile], File()],
) -> Response:
    """Save new PDFs to data/pdfs/ and index them in the background.

    A file whose name or content already exists is skipped (no duplicate, no overwrite).
    """
    messages: list[str] = []
    errors: list[str] = []
    new: list[Path] = []

    settings.pdfs_dir.mkdir(parents=True, exist_ok=True)

    def _existing_hashes() -> dict[str, str]:
        return {
            hashlib.sha256(p.read_bytes()).hexdigest(): p.name
            for p in settings.pdfs_dir.glob("*.pdf")
        }

    # ponytail: re-hashes the whole corpus on each upload; cache hashes if it grows large
    known = await asyncio.to_thread(_existing_hashes)
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
            digest = hashlib.sha256(data).hexdigest()
            if dest.exists():
                messages.append(f"⏭️ {safe_name} déjà présent, ignoré")
            elif digest in known:
                messages.append(f"⏭️ {safe_name} identique à {known[digest]}, ignoré")
            else:
                await asyncio.to_thread(dest.write_bytes, data)
                known[digest] = safe_name
                new.append(dest)
        except Exception as exc:
            errors.append(f"❌ {safe_name} : {exc}")

    notes = " · ".join(messages)
    if new:
        if _try_start(len(new)):
            background_tasks.add_task(_run_index_job, new, notes)
            return templates.TemplateResponse(
                request, "partials/index_progress.html", _progress_ctx(request)
            )
        messages.append(
            f"⚠️ {', '.join(p.name for p in new)} sauvegardé(s) mais indexation déjà en cours : "
            "clique « Indexer » une fois terminée"
        )

    ctx: dict[str, object] = {
        "request": request,
        **(await _doc_list_ctx()),
        "message": " · ".join(messages) or None,
        "error": " · ".join(errors) or None,
    }
    return templates.TemplateResponse(request, "partials/doc_list.html", ctx)


def _job_pct() -> int:
    chunks_total = _JOB["chunks_total"]
    return round(100 * _JOB["chunks_done"] / chunks_total) if chunks_total else 0  # type: ignore[operator]


def _progress_ctx(request: Request) -> dict[str, object]:
    return {"request": request, "job": _JOB, "pct": _job_pct()}


@router.post("/docs/index", response_class=HTMLResponse)
async def index_all(request: Request, background_tasks: BackgroundTasks) -> Response:
    """Kick off re-indexing every PDF in data/pdfs/ in the background."""
    pdfs: list[Path] = sorted(settings.pdfs_dir.glob("*.pdf")) if settings.pdfs_dir.exists() else []
    if not pdfs:
        ctx: dict[str, object] = {
            "request": request,
            **(await _doc_list_ctx()),
            "message": None,
            "error": "⚠️ Aucun PDF trouvé dans data/pdfs/.",
        }
        return templates.TemplateResponse(request, "partials/doc_list.html", ctx)

    if _try_start(len(pdfs)):
        background_tasks.add_task(_run_index_job, pdfs)
    return templates.TemplateResponse(
        request, "partials/index_progress.html", _progress_ctx(request)
    )


@router.post("/docs/index/{name}", response_class=HTMLResponse)
async def index_one(request: Request, name: str, background_tasks: BackgroundTasks) -> Response:
    """Kick off indexing a single PDF by filename in the background."""
    path = _resolve_safe(name)
    if path is None or not path.exists():
        ctx: dict[str, object] = {
            "request": request,
            **(await _doc_list_ctx()),
            "message": None,
            "error": f"❌ Fichier introuvable : {name}",
        }
        return templates.TemplateResponse(request, "partials/doc_list.html", ctx)

    if _try_start(1):
        background_tasks.add_task(_run_index_job, [path])
    return templates.TemplateResponse(
        request, "partials/index_progress.html", _progress_ctx(request)
    )


@router.get("/docs/index/status", response_class=HTMLResponse)
async def index_status(request: Request) -> Response:
    """Polled by the progress partial: still-running progress, or the final doc-list once."""
    if _JOB["running"]:
        return templates.TemplateResponse(
            request, "partials/index_progress.html", _progress_ctx(request)
        )

    message, error = _JOB["message"], _JOB["error"]
    _JOB["message"], _JOB["error"] = None, None  # consume once, don't re-flash on next reload
    ctx: dict[str, object] = {
        "request": request,
        **(await _doc_list_ctx()),
        "message": message,
        "error": error,
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

    ctx = {
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


# ── Page image (rendered on demand for citations) ──────────────────────────────


@router.get("/docs/page/{name}/{page}.png")
async def page_image(name: str, page: int) -> Response:
    """Render one page of a PDF to PNG, for the citation preview in answer.html."""
    path = _resolve_safe(name)
    if path is None or not path.exists() or page < 1:
        return Response(status_code=404)

    def _sync() -> bytes | None:
        with fitz.open(str(path)) as doc:
            if page > doc.page_count:
                return None
        return render_page_png(path, page)

    png = await asyncio.to_thread(_sync)
    if png is None:
        return Response(status_code=404)
    return Response(content=png, media_type="image/png")

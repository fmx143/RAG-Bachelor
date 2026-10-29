"""📚 Documentation routes — upload, index, and delete PDF documents."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import threading
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, cast

import fitz  # pymupdf
from fastapi import APIRouter, BackgroundTasks, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse
from starlette.responses import Response

from rag_bachelor.app.web._deps import sidebar_ctx, templates
from rag_bachelor.app.web.routes.settings import vision_captioning_enabled
from rag_bachelor.config import settings
from rag_bachelor.core.graph import build_graph, fact_counts
from rag_bachelor.ingest.extract import extract_pages, render_page_png
from rag_bachelor.ingest.index import (
    collection_count,
    delete_source,
    index_chunks,
    list_sources,
    rename_source,
)
from rag_bachelor.ingest.layout import convert, layout_items, section_chunks
from rag_bachelor.ingest.vision import caption_page
from rag_bachelor.study.store import (
    add_document,
    delete_document,
    document_by_sha,
    document_paths,
    rename_document,
)

router = APIRouter()

# Chunks per embed() call while indexing — small enough that the progress bar
# visibly advances (~9s/batch at the measured ~1.8 chunks/s), large enough not
# to lose the batching benefit in embeddings.py.
_BATCH = 16

# Course books are ~40 MB; the cap stays well under the container's 512 MB /tmp tmpfs,
# where uploads are spooled.
_MAX_UPLOAD_MB = 150
_MAX_UPLOAD_BYTES = _MAX_UPLOAD_MB * 1024 * 1024


# Path limits for a document's relative path (see _safe_relpath).
_MAX_DEPTH = 8  # folders above the file
_MAX_SEGMENT_BYTES = 255  # one path component, UTF-8 (filesystem limit)
_MAX_PATH_CHARS = 1024

# Single in-process indexing job fed by a queue. Safe without extra locking around field
# reads/writes (GIL + single uvicorn worker, no --workers — see docker-compose.yml);
# the lock guards the queue and the check-and-set of "running", so two POSTs can't both start
# a worker and a file queued as the worker finishes is never dropped.
_JOB_LOCK = threading.Lock()
_PENDING: list[str] = []  # relative paths waiting to be indexed
_JOB: dict[str, object] = {
    "running": False,
    "phase": "index",  # "index" or "graph": what the progress partial says is going on
    "active": "",  # relative path being indexed right now
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
        _claim(file_total)
        return True


def _claim(file_total: int) -> None:
    """Reset _JOB for a new job. Caller holds _JOB_LOCK."""
    _JOB.update(
        running=True,
        phase="index",
        active="",
        file_index=0,
        file_total=file_total,
        current="",
        chunks_done=0,
        chunks_total=0,
        message=None,
        error=None,
    )


def _enqueue(rels: list[str]) -> bool:
    """Queue *rels* for indexing. True if the caller must start the worker (none was running)."""
    with _JOB_LOCK:
        added = [r for r in dict.fromkeys(rels) if r not in _PENDING and r != _JOB["active"]]
        _PENDING.extend(added)
        if _JOB["running"]:
            _JOB["file_total"] = cast(int, _JOB["file_total"]) + len(added)
            return False
        if not added:
            return False
        _claim(len(added))
        return True


def _busy(*rels: str) -> str | None:
    """The first of *rels* that is queued or being indexed, else None."""
    with _JOB_LOCK:
        return next((r for r in rels if r in _PENDING or r == _JOB["active"]), None)


def _run_queue() -> None:
    """Index queued PDFs until the queue is empty, updating _JOB after each chunk batch.

    Runs in FastAPI's BackgroundTasks threadpool — sync function, blocking
    calls are fine here (there is no event loop to block).
    """
    done = total_chunks = 0
    empty_pages: list[int] = []
    no_text: list[str] = []
    graph_failed: list[str] = []
    last = ""
    caption_figures = vision_captioning_enabled()
    try:
        while True:
            with _JOB_LOCK:
                if not _PENDING:
                    warning = (
                        f" (pages vides ignorées : {', '.join(map(str, empty_pages))})"
                        if empty_pages and done == 1
                        else ""
                    )
                    if no_text:
                        warning += f" ⚠️ aucun texte extrait (PDF scanné ?) : {', '.join(no_text)}"
                    if graph_failed:
                        warning += (
                            f" ⚠️ graphe incomplet ou non construit : {', '.join(graph_failed)}"
                        )
                    if done == 1:
                        msg = f"✅ {last} — {total_chunks} chunks indexés{warning}"
                    else:
                        msg = f"✅ {done} document(s) indexés — {total_chunks} chunks au total{warning}"
                    _JOB.update(message=msg if done else None, running=False, active="")
                    return
                rel = _PENDING.pop(0)
                _JOB["active"] = rel
            pdf = _resolve(rel)
            if not pdf.is_file():
                continue
            done += 1
            last = rel
            _JOB.update(file_index=done, current=f"{rel} — analyse de la mise en page…")
            sha = document_paths().get(rel) or _hash_file(pdf)
            # PyMuPDF pages only flag figures and empty pages; the text comes from Docling.
            pages = extract_pages(pdf)
            _JOB.update(phase="index", chunks_done=0, chunks_total=0)
            items = layout_items(convert(pdf))
            if caption_figures:
                for p in pages:
                    if not p.has_images:
                        continue
                    caption = caption_page(pdf, p.page_num)
                    if caption:
                        items.append(("text", f"[Figure] {caption}", p.page_num))
                        p.is_empty = False
                items.sort(key=lambda it: it[2])  # stable: each caption ends its own page
            empty_pages = [p.page_num for p in pages if p.is_empty]
            chunks = section_chunks(items, rel)
            _JOB.update(current=rel, chunks_total=len(chunks))
            delete_source(rel)
            for start in range(0, len(chunks), _BATCH):
                index_chunks(chunks[start : start + _BATCH], sha[:16])
                _JOB["chunks_done"] = min(start + _BATCH, len(chunks))
            total_chunks += len(chunks)
            if not chunks:
                no_text.append(rel)
            elif settings.graph_enabled and settings.graph_auto_build:
                try:
                    facts, skipped = _build_graph_for(rel)
                    if skipped:
                        graph_failed.append(_graph_summary(rel, facts, skipped))
                except Exception as exc:  # noqa: BLE001 — indexing succeeded; only the graph failed
                    graph_failed.append(f"{rel} ({_describe(exc)})")
    except Exception as exc:  # noqa: BLE001 — surfaced to the user via the status partial
        with _JOB_LOCK:
            _PENDING.clear()
            _JOB.update(error=f"❌ Échec de l'indexation ({_describe(exc)}).", running=False)
            _JOB["active"] = ""


def _describe(exc: Exception) -> str:
    """Exception type plus HTTP status when there is one — never the message (may echo a key)."""
    status = getattr(exc, "status_code", None)
    return type(exc).__name__ + (f", HTTP {status}" if isinstance(status, int) else "")


def _graph_summary(name: str, facts: int, skipped: int) -> str:
    note = f" ⚠️ {skipped} passage(s) ignoré(s) (erreurs du fournisseur)" if skipped else ""
    return f"{name} — {facts} faits dans le graphe{note}"


def _build_graph_for(name: str) -> tuple[int, int]:
    """Build *name*'s fact graph, mirroring its progress into _JOB (one LLM call per chunk)."""
    _JOB.update(phase="graph", current=name, chunks_done=0, chunks_total=0)

    def progress(done: int, total: int) -> None:
        _JOB.update(chunks_done=done, chunks_total=total)

    return build_graph(name, progress)


def _run_graph_job(name: str) -> None:
    """Background job: (re)build the fact graph of one already-indexed PDF."""
    _JOB["file_index"] = 1
    try:
        facts, skipped = _build_graph_for(name)
        _JOB["message"] = ("⚠️ " if skipped else "✅ ") + _graph_summary(name, facts, skipped)
    except Exception as exc:  # noqa: BLE001 — surfaced to the user via the status partial
        _JOB["error"] = f"❌ Échec du graphe ({_describe(exc)})."
    with _JOB_LOCK:
        if not _PENDING:
            _JOB["running"] = False
            return
        # Documents were queued while the graph was building: this thread now indexes them.
        _JOB.update(phase="index", file_index=0, file_total=len(_PENDING), chunks_done=0)
    _run_queue()


# ── Paths ──────────────────────────────────────────────────────────────────────


def _safe_relpath(raw: str, *, file: bool = True) -> str:
    """Normalise a client-supplied relative path, or raise ValueError.

    Rejects ``..`` segments, absolute paths (POSIX and drive-letter), backslashes, control and
    format characters, hidden segments (``._x.pdf`` AppleDouble files, ``.DS_Store``), and paths
    that are too deep or too long. Empty and ``.`` segments are dropped. With ``file`` the last
    segment must be a ``.pdf``. The result is NFC (macOS sends NFD) with ``/`` separators.
    """
    rel = unicodedata.normalize("NFC", raw)
    if any(unicodedata.category(c) in ("Cc", "Cf") for c in rel):
        raise ValueError("caractère de contrôle")
    if "\\" in rel or rel.startswith("/") or re.match(r"[A-Za-z]:", rel):
        raise ValueError("chemin absolu ou séparateur interdit")
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if not parts:
        raise ValueError("chemin vide")
    if ".." in parts:
        raise ValueError("segment '..'")
    if any(p.startswith(".") for p in parts):
        raise ValueError("segment caché")
    if len(parts) - (1 if file else 0) > _MAX_DEPTH:
        raise ValueError("arborescence trop profonde")
    if any(len(p.encode()) > _MAX_SEGMENT_BYTES for p in parts):
        raise ValueError("nom trop long")
    rel = "/".join(parts)
    if len(rel) > _MAX_PATH_CHARS:
        raise ValueError("chemin trop long")
    if file and not rel.lower().endswith(".pdf"):
        raise ValueError("ce n'est pas un .pdf")
    return rel


def _resolve(rel: str) -> Path:
    """Absolute path of *rel* under pdfs_dir; raises ValueError if it would land outside it."""
    root = settings.pdfs_dir.resolve()
    candidate = (root / rel).resolve()  # follows symlinks: one pointing out is rejected
    if not candidate.is_relative_to(root):
        raise ValueError("hors de pdfs_dir")
    return candidate


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(1 << 20):
            h.update(block)
    return h.hexdigest()


def _prune_empty_dirs(start: Path) -> None:
    """Remove *start* and its parents while they are empty, never pdfs_dir itself."""
    root = settings.pdfs_dir.resolve()
    d = start
    while d != root and d.is_relative_to(root):
        try:
            d.rmdir()
        except OSError:
            return
        d = d.parent


def _sync_documents() -> list[str]:
    """Sorted relative paths of the PDFs on disk; keeps the ``documents`` table in step.

    Files without a row are hashed once (catches up PDFs that predate the table); rows whose
    file is gone are dropped.
    """
    root = settings.pdfs_dir
    on_disk = (
        {p.relative_to(root).as_posix(): p for p in root.rglob("*.pdf") if p.is_file()}
        if root.exists()
        else {}
    )
    known = document_paths()
    for gone in known.keys() - on_disk.keys():
        delete_document(gone)
    hashes = set(known.values())
    for rel in sorted(on_disk.keys() - known.keys()):
        sha = _hash_file(on_disk[rel])
        if sha not in hashes:  # a byte-identical second copy stays listed but unregistered
            add_document(rel, sha)
            hashes.add(sha)
    return sorted(on_disk, key=str.casefold)


# ── Tree ───────────────────────────────────────────────────────────────────────


@dataclass
class _Doc:
    path: str
    name: str
    indexed: bool
    facts: int


@dataclass
class _Folder:
    name: str
    path: str
    subdirs: dict[str, _Folder] = field(default_factory=dict)
    docs: list[_Doc] = field(default_factory=list)
    open: bool = False

    @property
    def count(self) -> int:
        return len(self.docs) + sum(d.count for d in self.subdirs.values())

    @property
    def children(self) -> list[_Folder]:
        return sorted(self.subdirs.values(), key=lambda d: d.name.casefold())


def _fold(text: str) -> str:
    """Case- and accent-insensitive form, for the name filter."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _build_tree(docs: list[_Doc], q: str = "", open_path: str = "") -> _Folder:
    """Folder tree of *docs*; the root's own docs are the unclassified ones."""
    needle = _fold(q.strip())
    root = _Folder("", "")
    for doc in docs:
        if needle and needle not in _fold(doc.path):
            continue
        node = root
        parts = doc.path.split("/")
        for i, seg in enumerate(parts[:-1]):
            sub = "/".join(parts[: i + 1])
            node = node.subdirs.setdefault(seg, _Folder(seg, sub))
            node.open = bool(needle) or open_path.startswith(sub + "/")
        node.docs.append(doc)
    return root


# ── Helpers ────────────────────────────────────────────────────────────────────


async def _doc_list_ctx(q: str = "", open_path: str = "") -> dict[str, object]:
    """Build the context needed to render the doc-list partial (non-blocking)."""

    def _sync() -> dict[str, object]:
        rels = _sync_documents()
        indexed: set[str] = set(list_sources())
        facts = fact_counts()
        docs = [_Doc(r, r.rsplit("/", 1)[-1], r in indexed, facts.get(r, 0)) for r in rels]
        return {
            "tree": _build_tree(docs, q, open_path),
            "total": len(docs),
            "q": q,
            "chunk_count": collection_count(),
        }

    return await asyncio.to_thread(_sync)


async def _list_response(
    request: Request,
    message: str | None = None,
    error: str | None = None,
    q: str = "",
    open_path: str = "",
) -> Response:
    ctx: dict[str, object] = {
        "request": request,
        **(await _doc_list_ctx(q, open_path)),
        "message": message,
        "error": error,
    }
    return templates.TemplateResponse(request, "partials/doc_list.html", ctx)


def _job_pct() -> int:
    chunks_total = _JOB["chunks_total"]
    return round(100 * _JOB["chunks_done"] / chunks_total) if chunks_total else 0  # type: ignore[operator]


def _progress_ctx(request: Request) -> dict[str, object]:
    return {"request": request, "job": _JOB, "pct": _job_pct()}


def _progress_response(request: Request) -> Response:
    return templates.TemplateResponse(
        request, "partials/index_progress.html", _progress_ctx(request)
    )


async def _start_indexing(
    request: Request, background_tasks: BackgroundTasks, rels: list[str]
) -> Response:
    """Queue *rels* and show the progress bar (starting the worker if none is running)."""
    if _enqueue(rels):
        background_tasks.add_task(_run_queue)
    return _progress_response(request)


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


@router.get("/docs/list", response_class=HTMLResponse)
async def doc_list(request: Request, q: str = "") -> Response:
    """The tree, filtered by name (fragment swapped into #doc-list)."""
    return await _list_response(request, q=q)


# ── Upload (one file per request; the browser loops over a folder) ─────────────


def _store_upload(rel: str, sha: str, data: bytes) -> tuple[int, str, bool]:
    """Place an uploaded PDF. Returns ``(HTTP status, message, needs indexing)``."""
    _sync_documents()  # registers the PDFs that predate the table, so their hash is known
    dest = _resolve(rel)
    known = document_by_sha(sha)
    if known == rel:
        return 200, f"⏭️ {rel} déjà à jour", False
    if known:  # same content already stored elsewhere: move it, no re-indexing
        if (busy := _busy(known)) is not None:
            return 409, f"⚠️ {busy} est en cours d'indexation, réessaie ensuite", False
        if dest.exists() or rel in document_paths():
            return 409, f"❌ {rel} : ce chemin est déjà occupé par un autre document", False
        old = _resolve(known)
        dest.parent.mkdir(parents=True, exist_ok=True)
        # ponytail: file → Chroma → SQL with best-effort rollback; a crash in between is not
        # repaired (the next upload of the file retries from whichever side moved).
        os.replace(old, dest)
        try:
            rename_source(known, rel)
            try:
                rename_document(known, rel)
            except Exception:
                rename_source(rel, known)
                raise
        except Exception:
            os.replace(dest, old)
            raise
        _prune_empty_dirs(old.parent)
        return 200, f"📦 {known} → {rel} (déplacé, sans réindexation)", False
    if dest.exists() or rel in document_paths():
        return 409, f"❌ {rel} : un autre document occupe déjà ce chemin, ignoré", False
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    part.write_bytes(data)
    os.replace(part, dest)
    add_document(rel, sha)
    return 200, f"📥 {rel} enregistré", True


@router.post("/docs/upload", response_class=PlainTextResponse)
async def upload_pdf(
    background_tasks: BackgroundTasks,
    file: Annotated[UploadFile, File()],
    relpath: Annotated[str, Form()] = "",
) -> Response:
    """Save one PDF under its relative path and queue it for indexing.

    ``relpath`` is the browser's ``webkitRelativePath`` (or the bare file name). A file whose
    content is already known is moved to the new path instead of re-indexed. Replies with one
    line of plain text, shown by the page's upload loop.
    """
    try:
        rel = _safe_relpath(relpath or file.filename or "")
    except ValueError as exc:
        return PlainTextResponse(f"❌ {relpath or file.filename} : chemin refusé ({exc})", 400)
    if (file.size or 0) > _MAX_UPLOAD_BYTES:
        return PlainTextResponse(f"❌ {rel} : fichier trop volumineux (> {_MAX_UPLOAD_MB} Mo)", 413)
    data = await file.read()
    if len(data) > _MAX_UPLOAD_BYTES:
        return PlainTextResponse(f"❌ {rel} : fichier trop volumineux (> {_MAX_UPLOAD_MB} Mo)", 413)
    if not data.startswith(b"%PDF-"):
        return PlainTextResponse(f"❌ {rel} : ce n'est pas un PDF", 400)
    try:
        sha = await asyncio.to_thread(lambda: hashlib.sha256(data).hexdigest())
        status, message, needs_index = await asyncio.to_thread(_store_upload, rel, sha, data)
    except Exception as exc:  # noqa: BLE001 — reported to the upload loop, never echoing the message
        return PlainTextResponse(f"❌ {rel} : échec de l'enregistrement ({_describe(exc)})", 500)
    if needs_index and _enqueue([rel]):
        background_tasks.add_task(_run_queue)
    return PlainTextResponse(message, status)


# ── HTMX partials (POST → returns doc_list / progress partial) ─────────────────


@router.post("/docs/index", response_class=HTMLResponse)
async def index_all(request: Request, background_tasks: BackgroundTasks) -> Response:
    """Kick off re-indexing every PDF under data/pdfs/ in the background."""
    rels = await asyncio.to_thread(_sync_documents)
    if not rels:
        return await _list_response(request, error="⚠️ Aucun PDF trouvé dans data/pdfs/.")
    return await _start_indexing(request, background_tasks, rels)


@router.post("/docs/index/doc", response_class=HTMLResponse)
async def index_one(
    request: Request, background_tasks: BackgroundTasks, path: Annotated[str, Form()]
) -> Response:
    """Kick off indexing a single PDF by relative path in the background."""
    try:
        rel = _safe_relpath(path)
        exists = _resolve(rel).is_file()
    except ValueError:
        exists = False
    if not exists:
        return await _list_response(request, error=f"❌ Fichier introuvable : {path}")
    return await _start_indexing(request, background_tasks, [rel])


@router.post("/docs/index/folder", response_class=HTMLResponse)
async def index_folder(
    request: Request, background_tasks: BackgroundTasks, path: Annotated[str, Form()]
) -> Response:
    """Kick off re-indexing every PDF under one folder."""
    try:
        folder = _safe_relpath(path, file=False)
    except ValueError:
        return await _list_response(request, error=f"❌ Dossier invalide : {path}")
    rels = [r for r in await asyncio.to_thread(_sync_documents) if r.startswith(folder + "/")]
    if not rels:
        return await _list_response(request, error=f"⚠️ Aucun PDF dans {folder}")
    return await _start_indexing(request, background_tasks, rels)


@router.post("/docs/graph/doc", response_class=HTMLResponse)
async def graph_one(
    request: Request, background_tasks: BackgroundTasks, path: Annotated[str, Form()]
) -> Response:
    """Kick off building the fact graph of a single indexed PDF in the background."""
    try:
        rel = _safe_relpath(path)
        indexed = rel in await asyncio.to_thread(list_sources)
    except ValueError:
        indexed = False
    if not indexed:
        return await _list_response(request, error=f"❌ Document non indexé : {path}")
    if _try_start(1):
        background_tasks.add_task(_run_graph_job, rel)
    return _progress_response(request)


@router.get("/docs/index/status", response_class=HTMLResponse)
async def index_status(request: Request) -> Response:
    """Polled by the progress partial: still-running progress, or the final doc-list once."""
    if _JOB["running"]:
        return _progress_response(request)

    message, error = _JOB["message"], _JOB["error"]
    _JOB["message"], _JOB["error"] = None, None  # consume once, don't re-flash on next reload
    return await _list_response(request, message, error)  # type: ignore[arg-type]


def _delete_docs(rels: list[str]) -> None:
    """Remove PDFs from disk, index, graph and the documents table; prune emptied folders."""
    for rel in rels:
        path = _resolve(rel)
        path.unlink(missing_ok=True)
        delete_source(rel)
        delete_document(rel)
        _prune_empty_dirs(path.parent)


@router.post("/docs/delete/doc", response_class=HTMLResponse)
async def delete_pdf(request: Request, path: Annotated[str, Form()]) -> Response:
    """Delete a PDF from disk and from the vector index."""
    try:
        rel = _safe_relpath(path)
    except ValueError:
        return await _list_response(request, error=f"❌ Chemin invalide : {path}")
    if _busy(rel):
        return await _list_response(request, error=f"⚠️ {rel} est en cours d'indexation")
    await asyncio.to_thread(_delete_docs, [rel])
    return await _list_response(request, f"🗑️ {rel} supprimé", open_path=rel)


@router.post("/docs/delete/folder", response_class=HTMLResponse)
async def delete_folder(request: Request, path: Annotated[str, Form()]) -> Response:
    """Delete every PDF under a folder (the page asks for confirmation first)."""
    try:
        folder = _safe_relpath(path, file=False)
    except ValueError:
        return await _list_response(request, error=f"❌ Dossier invalide : {path}")
    rels = [r for r in await asyncio.to_thread(_sync_documents) if r.startswith(folder + "/")]
    if busy := _busy(*rels):
        return await _list_response(request, error=f"⚠️ {busy} est en cours d'indexation")
    await asyncio.to_thread(_delete_docs, rels)
    return await _list_response(
        request, f"🗑️ Dossier {folder} supprimé ({len(rels)} document(s))", open_path=folder
    )


# ── Form helper — chunk count via GET (for the counter badge) ─────────────────


@router.get("/docs/count", response_class=HTMLResponse)
async def chunk_count(request: Request) -> Response:
    """Return a plain-text chunk count (used by the metric badge)."""
    return HTMLResponse(str(collection_count()))


# ── Page image (rendered on demand for citations) ──────────────────────────────


@router.get("/docs/page.png")
async def page_image(path: str, page: int) -> Response:
    """Render one page of a PDF to PNG, for the citation preview in answer.html."""
    try:
        file = _resolve(_safe_relpath(path))
    except ValueError:
        return Response(status_code=404)
    if not file.is_file() or page < 1:
        return Response(status_code=404)

    def _sync() -> bytes | None:
        with fitz.open(str(file)) as doc:
            if page > doc.page_count:
                return None
        return render_page_png(file, page)

    png = await asyncio.to_thread(_sync)
    if png is None:
        return Response(status_code=404)
    return Response(content=png, media_type="image/png")

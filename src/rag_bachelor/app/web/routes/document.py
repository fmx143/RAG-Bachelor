"""📖 Document page — one book or source with everything made from it.

Overview (chapters, per-chapter stats, targeted revision), its question bank, sheets,
mind maps and audio lessons, each generated for a chapter or a free page range, and its
file (re-index, fact graph, move between Livres/Sources, delete). The forms post to the
existing /bank, /sheets, /maps, /podcasts and /docs/file endpoints.
"""

from __future__ import annotations

import asyncio
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.responses import Response

from rag_bachelor.app.web._deps import sidebar_ctx, templates
from rag_bachelor.app.web.routes import bank, documentation, maps, podcasts, sheets
from rag_bachelor.core.chapters import chapters_for, detect_chapters, page_count
from rag_bachelor.core.qtypes import QTYPE_LABELS, QTYPES
from rag_bachelor.ingest.index import list_sources
from rag_bachelor.study.stats import chapter_stats
from rag_bachelor.study.store import Chapter, list_maps, list_podcasts, list_sheets, set_chapters
from rag_bachelor.study.subjects import doc_kind

router = APIRouter()

TABS = {
    "apercu": "Aperçu",
    "questions": "Questions",
    "fiches": "Fiches",
    "cartes": "Mind maps",
    "audio": "Audio",
    "fichier": "Fichier",
}
KIND_LABELS = {"livre": "Livre", "source": "Source", None: "À classer"}
_ERRORS = {
    "pages": "Chaque chapitre doit avoir des pages valides, comprises dans le document.",
    "overlap": "Deux chapitres se chevauchent : corrige leurs pages.",
    "empty": "Il faut au moins un chapitre.",
    "busy": "Une indexation ou un graphe est en cours : réessaie quand il sera terminé.",
    "exists": "Un document porte déjà ce nom à cet endroit.",
    "invalid": "Type, dossier ou nom invalide.",
    "move": "Le déplacement a échoué.",
}


def _doc_url(path: str, tab: str = "apercu", **query: str) -> str:
    params = "".join(f"&{k}={quote(v, safe='')}" for k, v in query.items())
    return f"/document/{quote(path, safe='/')}?tab={tab}{params}"


def parse_chapters(
    titles: list[str], firsts: list[str], lasts: list[str], pages: int
) -> list[Chapter] | str:
    """Validate the chapter editor's rows; returns the chapters sorted, or an error key.

    Fully blank rows are dropped; a blank title becomes "Chapitre N".
    """
    rows: list[Chapter] = []
    for title, first, last in zip(titles, firsts, lasts, strict=False):
        title, first, last = title.strip(), first.strip(), last.strip()
        if not (title or first or last):
            continue
        if not (first.isdigit() and last.isdigit()) or not 1 <= int(first) <= int(last) <= pages:
            return "pages"
        rows.append(Chapter(title, int(first), int(last)))
    if not rows:
        return "empty"
    rows.sort(key=lambda c: c.page_from)
    if any(a.page_to >= b.page_from for a, b in zip(rows, rows[1:], strict=False)):
        return "overlap"
    return [
        Chapter(c.title[:120] or f"Chapitre {i}", c.page_from, c.page_to)
        for i, c in enumerate(rows, 1)
    ]


@router.get("/document/{path:path}", response_class=HTMLResponse)
async def document_page(
    request: Request, path: str, tab: str = "apercu", err: str = ""
) -> Response:
    if path not in await asyncio.to_thread(list_sources):
        return Response(status_code=404)
    tab = tab if tab in TABS else "apercu"

    def _sync() -> dict[str, object]:
        chapters = chapters_for(path)
        ctx: dict[str, object] = {"chapters": chapters, "pages": page_count(path)}
        if tab == "apercu":
            ctx["chapter_stats"], ctx["unplaced_cards"] = chapter_stats(path, chapters)
        elif tab == "fiches":
            ctx["items"] = list_sheets(source=path)
        elif tab == "cartes":
            ctx["items"] = list_maps(source=path)
        elif tab == "audio":
            ctx["items"] = list_podcasts(source=path)
        return ctx

    ctx: dict[str, object] = {
        "request": request,
        "active_tab": "books" if doc_kind(path) != "source" else "sources",
        "path": path,
        "kind": doc_kind(path),
        "kind_labels": KIND_LABELS,
        "tab": tab,
        "tabs": TABS,
        "error": _ERRORS.get(err),
        **(await asyncio.to_thread(_sync)),
        **sidebar_ctx(),
    }
    if tab == "questions":
        ctx.update(qtypes=QTYPES, qtype_labels=QTYPE_LABELS, **bank.generation_ctx())
        ctx.update(await bank.list_ctx(source=path, difficulty="", search="", limit=20))
    elif tab == "fiches":
        ctx.update(max_pages=sheets.MAX_PAGES, **sheets.status_ctx())
    elif tab == "cartes":
        ctx.update(max_pages=maps.MAX_PAGES, **maps.status_ctx())
    elif tab == "audio":
        ctx.update(max_pages=podcasts.MAX_PAGES, **podcasts.status_ctx())
    elif tab == "fichier":
        parts = path.split("/")
        ctx.update(
            job=documentation.job_status(),
            file_name=parts[-1],
            file_folder="/".join(parts[2:-1] if doc_kind(path) else parts[1:-1]),
        )
    return templates.TemplateResponse(request, "document.html", ctx)


@router.post("/document-chapters")
async def save_chapters(
    path: Annotated[str, Form()],
    title: Annotated[list[str], Form()] = [],  # noqa: B006 — never mutated
    page_from: Annotated[list[str], Form()] = [],  # noqa: B006
    page_to: Annotated[list[str], Form()] = [],  # noqa: B006
    redetect: Annotated[str, Form()] = "",
) -> Response:
    """Replace the document's chapters with the editor's rows, or re-run the detection."""
    if path not in await asyncio.to_thread(list_sources):
        return Response(status_code=404)
    if redetect:
        await asyncio.to_thread(lambda: set_chapters(path, detect_chapters(path)))
        return RedirectResponse(_doc_url(path), status_code=303)
    pages = await asyncio.to_thread(page_count, path)
    parsed = parse_chapters(title, page_from, page_to, pages)
    if isinstance(parsed, str):
        return RedirectResponse(_doc_url(path, err=parsed), status_code=303)
    await asyncio.to_thread(set_chapters, path, parsed)
    return RedirectResponse(_doc_url(path), status_code=303)

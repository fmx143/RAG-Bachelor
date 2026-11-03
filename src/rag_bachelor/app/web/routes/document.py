"""📖 Document page — one book or source with everything made from it.

Overview (chapters, per-chapter stats, targeted revision), its question bank, sheets,
mind maps and audio lessons, each generated for a chapter or a free page range, and its
file (re-index, fact graph, move between Livres/Sources, delete). The forms post to the
existing /bank, /sheets, /maps, /podcasts and /docs/file endpoints.
"""

from __future__ import annotations

import asyncio
import json
import math
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.responses import Response

from rag_bachelor.app.web._deps import sidebar_ctx, templates
from rag_bachelor.app.web.routes import bank, documentation, importer, maps, podcasts, sheets
from rag_bachelor.core.chapters import chapters_for, detect_chapters, page_count
from rag_bachelor.core.qtypes import QTYPE_LABELS, QTYPES
from rag_bachelor.ingest.index import list_sources
from rag_bachelor.study.stats import chapter_stats
from rag_bachelor.study.store import (
    Chapter,
    add_card,
    list_maps,
    list_podcasts,
    list_sheets,
    set_chapters,
)
from rag_bachelor.study.subjects import doc_kind

router = APIRouter()

TABS = {
    "apercu": "Aperçu",
    "questions": "Questions",
    "fiches": "Fiches",
    "cartes": "Mind maps",
    "audio": "Audio",
    "masquage": "Masquage",
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
    "occlusion": "Zones invalides : dessine 1 à 30 zones à l'intérieur de la page.",
}


def _doc_url(path: str, tab: str = "apercu", **query: str) -> str:
    params = "".join(f"&{k}={quote(v, safe='')}" for k, v in query.items())
    return f"/document/{quote(path, safe='/')}?tab={tab}{params}"


MAX_OCCLUSIONS = 30
_MIN_SIDE = 0.5  # % of the page: smaller boxes are accidental clicks


def parse_occlusions(raw: str) -> list[tuple[list[float], str]] | None:
    """Validate the occlusion editor's JSON ``[{x, y, w, h, label}]`` (percent of the page).

    Returns ``[([x, y, w, h], label)]``, or None when empty, too many, malformed or out of the page.
    """
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, list) or not 1 <= len(data) <= MAX_OCCLUSIONS:
        return None
    out: list[tuple[list[float], str]] = []
    for item in data:
        if not isinstance(item, dict):
            return None
        box = [item.get(k) for k in ("x", "y", "w", "h")]
        if not all(isinstance(v, int | float) and math.isfinite(v) for v in box):
            return None
        x, y, w, h = (round(float(v), 2) for v in box)  # type: ignore[arg-type]
        if x < 0 or y < 0 or w < _MIN_SIDE or h < _MIN_SIDE or x + w > 100.01 or y + h > 100.01:
            return None
        out.append(([x, y, w, h], str(item.get("label", "")).strip()[:80]))
    return out


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
    request: Request,
    path: str,
    tab: str = "apercu",
    err: str = "",
    page: int = 1,
    created: int = 0,
) -> Response:
    if path not in await asyncio.to_thread(list_sources):
        return Response(status_code=404)
    tab = tab if tab in TABS else "apercu"

    def _sync() -> dict[str, object]:
        chapters = chapters_for(path)
        pages = page_count(path)
        ctx: dict[str, object] = {"chapters": chapters, "pages": pages}
        if tab == "masquage":
            ctx.update(occ_page=min(max(page, 1), pages), created=max(created, 0))
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
        ctx.update(importer.status_ctx())
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


@router.post("/occlusion/create")
async def create_occlusion(
    source: Annotated[str, Form()],
    page: Annotated[int, Form()],
    rects: Annotated[str, Form()],
    title: Annotated[str, Form()] = "",
) -> Response:
    """One image-occlusion card per drawn zone: all zones hidden, the card asks for one."""
    if source not in await asyncio.to_thread(list_sources):
        return Response(status_code=404)
    zones = parse_occlusions(rects)
    pages = await asyncio.to_thread(page_count, source)
    if zones is None or not 1 <= page <= pages:
        return RedirectResponse(_doc_url(source, "masquage", err="occlusion"), status_code=303)
    boxes = [box for box, _ in zones]
    question = title.strip()[:200] or f"Schéma de la page {page}"

    def _create() -> None:
        for i, (_, label) in enumerate(zones):
            add_card(
                question=question,
                answer=label,
                topic=source,
                difficulty="moyen",
                qtype="occlusion",
                occlusion={"page": page, "rects": boxes, "target": i},
            )

    await asyncio.to_thread(_create)
    return RedirectResponse(
        _doc_url(source, "masquage", page=str(page), created=str(len(zones))), status_code=303
    )

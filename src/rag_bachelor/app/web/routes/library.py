"""📚 Library routes — the Livres and Mes sources grids of the active subject.

A document's kind comes from its folder (``study.subjects.doc_kind``); unsorted ones are
listed on both grids until they are moved. The former per-type list pages (bank, quick
generation, sheets, mind maps, audio) now live in each document's page and redirect here.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.responses import Response

from rag_bachelor.app.web._deps import sidebar_ctx, templates
from rag_bachelor.study.stats import TopicStats, get_topic_stats
from rag_bachelor.study.store import count_bank_questions
from rag_bachelor.study.subjects import BOOKS_DIR, SOURCES_DIR, active_sources, active_subject
from rag_bachelor.study.subjects import doc_kind as kind_of

router = APIRouter()

_PAGES = {
    "livre": ("books", "Livres", "Tes <em>livres</em>", BOOKS_DIR),
    "source": ("sources", "Mes sources", "Tes <em>sources</em>", SOURCES_DIR),
}


@dataclass
class DocTile:
    path: str
    folder: str  # sub-folder under Livres/ or Sources/ ("" at its root)
    questions: int
    stats: TopicStats | None


def _tiles(kind: str | None) -> list[DocTile]:
    stats = {s.topic: s for s in get_topic_stats(active_subject())}
    tiles = []
    for path in active_sources():
        if kind_of(path) != kind:
            continue
        inner = path.split("/")[2:-1] if kind else path.split("/")[1:-1]
        tiles.append(
            DocTile(path, "/".join(inner), count_bank_questions(source=path), stats.get(path))
        )
    return sorted(tiles, key=lambda t: (t.folder.casefold(), t.path.casefold()))


async def _library(request: Request, kind: str) -> Response:
    tab, title, heading, folder = _PAGES[kind]
    # One thread: the store's single DB connection must not be used concurrently.
    docs, unsorted = await asyncio.to_thread(lambda: (_tiles(kind), _tiles(None)))
    ctx: dict[str, object] = {
        "request": request,
        "active_tab": tab,
        "title": title,
        "heading": heading,
        "folder": folder,
        "docs": docs,
        "unsorted": unsorted,
        **sidebar_ctx(),
    }
    return templates.TemplateResponse(request, "library.html", ctx)


@router.get("/livres", response_class=HTMLResponse)
async def books_page(request: Request) -> Response:
    return await _library(request, "livre")


@router.get("/sources", response_class=HTMLResponse)
async def sources_page(request: Request) -> Response:
    return await _library(request, "source")


async def _to_books() -> Response:
    return RedirectResponse("/livres", status_code=303)


for _old in ("/bank", "/generate", "/sheets", "/maps", "/podcasts"):
    router.add_api_route(_old, _to_books, methods=["GET"], include_in_schema=False)

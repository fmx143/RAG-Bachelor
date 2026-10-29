"""📝 Sheets routes — chapter revision sheets (document + page range), persisted."""

from __future__ import annotations

import asyncio
import re
import threading
from typing import Annotated, cast
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Form, Request
from fastapi.responses import HTMLResponse
from markupsafe import Markup
from starlette.responses import Response

from rag_bachelor.app.web._deps import sidebar_ctx, templates
from rag_bachelor.core.sheet import generate_sheet
from rag_bachelor.study.store import delete_sheet, get_sheet, list_sheets
from rag_bachelor.study.subjects import active_sources, active_subject

router = APIRouter()

_MAX_PAGES = 80  # bounds the number of LLM calls for one sheet
_CITE_HTML = re.compile(r"\[p\.(\d+)\]")
_TAG = re.compile(r"(<[^>]*>)")

# Single in-process generation job, same pattern as routes/bank.py.
_JOB_LOCK = threading.Lock()
_JOB: dict[str, object] = {
    "running": False,
    "source": None,
    "done": 0,
    "total": 0,
    "cancel": False,
    "error": None,
    "sheet_id": None,
}


def _try_start(source: str) -> bool:
    """Atomically claim the job slot. Returns False if a job is already running."""
    with _JOB_LOCK:
        if _JOB["running"]:
            return False
        _JOB.update(
            running=True, source=source, done=0, total=0, cancel=False, error=None, sheet_id=None
        )
        return True


def _run_sheet_job(source: str, page_from: int, page_to: int) -> None:
    def _progress(done: int, total: int) -> None:
        _JOB.update(done=done, total=total)

    try:
        _JOB["sheet_id"] = generate_sheet(
            source,
            page_from,
            page_to,
            progress_cb=_progress,
            should_stop=lambda: bool(_JOB["cancel"]),
        )
    except ValueError:
        _JOB["error"] = "Aucun texte indexé dans cette plage de pages."
    except Exception:
        # Never surface the raw exception: the LLM SDKs can echo an API key on auth errors.
        _JOB["error"] = (
            "Erreur lors de la génération. Vérifie la configuration du fournisseur LLM "
            "dans ⚙️ Paramètres."
        )
    finally:
        _JOB["running"] = False


def _status_ctx(error_override: str | None = None) -> dict[str, object]:
    done = cast(int, _JOB["done"])
    total = cast(int, _JOB["total"])
    return {
        "running": _JOB["running"],
        "gen_source": _JOB["source"],
        "gen_error": error_override or _JOB["error"],
        "gen_pct": round(done / total * 100) if total else 0,
        "gen_cancelling": bool(_JOB["cancel"]),
        "gen_sheet_id": _JOB["sheet_id"],
    }


def _render_sheet(content: str, source: str) -> Markup:
    """Markdown → safe HTML, with each ``[p.N]`` linked to its page image."""
    path = quote(source, safe="")

    def link(m: re.Match[str]) -> str:
        return (
            f'<a href="/docs/page.png?path={path}&amp;page={m.group(1)}" '
            f'class="cite" data-page="{m.group(1)}" target="_blank" rel="noopener">p.{m.group(1)}</a>'
        )

    # Only touch text nodes: the md filter escapes < and > inside attribute values, so
    # splitting on tags never lands inside one (a citation in a link title stays inert).
    parts = _TAG.split(str(templates.env.filters["md"](content)))
    return Markup("".join(p if i % 2 else _CITE_HTML.sub(link, p) for i, p in enumerate(parts)))


@router.get("/sheets", response_class=HTMLResponse)
async def sheets_page(request: Request) -> Response:
    ctx: dict[str, object] = {
        "request": request,
        "active_tab": "sheets",
        "sources": await asyncio.to_thread(active_sources),
        "sheets": await asyncio.to_thread(list_sheets, active_subject()),
        "max_pages": _MAX_PAGES,
        **sidebar_ctx(),
        **_status_ctx(),
    }
    return templates.TemplateResponse(request, "sheets.html", ctx)


@router.post("/sheets/generate", response_class=HTMLResponse)
async def start_generation(
    request: Request,
    background_tasks: BackgroundTasks,
    source: Annotated[str, Form()] = "",
    page_from: Annotated[int, Form()] = 1,
    page_to: Annotated[int, Form()] = 1,
) -> Response:
    source = source.strip()
    error: str | None = None
    if not source:
        error = "Choisis un document."
    elif page_from < 1 or page_to < page_from:
        error = "Plage de pages invalide."
    elif page_to - page_from + 1 > _MAX_PAGES:
        error = f"Plage trop large : {_MAX_PAGES} pages maximum."
    elif _try_start(source):
        background_tasks.add_task(_run_sheet_job, source, page_from, page_to)
    else:
        error = "Une génération est déjà en cours."
    return templates.TemplateResponse(
        request, "partials/sheet_status.html", {"request": request, **_status_ctx(error)}
    )


@router.get("/sheets/status", response_class=HTMLResponse)
async def generation_status(request: Request) -> Response:
    return templates.TemplateResponse(
        request, "partials/sheet_status.html", {"request": request, **_status_ctx()}
    )


@router.post("/sheets/cancel", response_class=HTMLResponse)
async def cancel_generation(request: Request) -> Response:
    """Request a stop of the running generation (cooperative: takes effect between steps)."""
    with _JOB_LOCK:
        if _JOB["running"]:
            _JOB["cancel"] = True
    return templates.TemplateResponse(
        request, "partials/sheet_status.html", {"request": request, **_status_ctx()}
    )


@router.get("/sheets/{sheet_id}", response_class=HTMLResponse)
async def sheet_view(request: Request, sheet_id: int) -> Response:
    sheet = await asyncio.to_thread(get_sheet, sheet_id)
    if sheet is None:
        return Response(status_code=404)
    ctx: dict[str, object] = {
        "request": request,
        "active_tab": "sheets",
        "sheet": sheet,
        "html": _render_sheet(sheet.content, sheet.source),
        **sidebar_ctx(),
    }
    return templates.TemplateResponse(request, "sheet_view.html", ctx)


@router.post("/sheets/{sheet_id}/delete", response_class=HTMLResponse)
async def sheet_delete(sheet_id: int) -> Response:
    await asyncio.to_thread(delete_sheet, sheet_id)
    return HTMLResponse("")

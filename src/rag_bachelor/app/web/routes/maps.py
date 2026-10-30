"""🧠 Maps routes — chapter mind maps (document + page range), persisted."""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Annotated, cast

from fastapi import APIRouter, BackgroundTasks, Form, Request
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from rag_bachelor.app.web._deps import nav_tab, sidebar_ctx, templates
from rag_bachelor.core.mindmap import BadTree, generate_map
from rag_bachelor.study.store import delete_map, get_map

router = APIRouter()

MAX_PAGES = 80  # bounds the number of LLM calls for one map

# Single in-process generation job, same pattern as routes/bank.py.
_JOB_LOCK = threading.Lock()
_JOB: dict[str, object] = {
    "running": False,
    "source": None,
    "done": 0,
    "total": 0,
    "cancel": False,
    "error": None,
    "map_id": None,
}


def _try_start(source: str) -> bool:
    """Atomically claim the job slot. Returns False if a job is already running."""
    with _JOB_LOCK:
        if _JOB["running"]:
            return False
        _JOB.update(
            running=True, source=source, done=0, total=0, cancel=False, error=None, map_id=None
        )
        return True


def _run_map_job(source: str, page_from: int, page_to: int) -> None:
    def _progress(done: int, total: int) -> None:
        _JOB.update(done=done, total=total)

    try:
        _JOB["map_id"] = generate_map(
            source,
            page_from,
            page_to,
            progress_cb=_progress,
            should_stop=lambda: bool(_JOB["cancel"]),
        )
    except ValueError:
        _JOB["error"] = "Aucun texte indexé dans cette plage de pages."
    except BadTree:
        _JOB["error"] = "Le modèle n'a pas produit de carte exploitable. Réessaie."
    except Exception:
        # Never surface the raw exception: the LLM SDKs can echo an API key on auth errors.
        _JOB["error"] = (
            "Erreur lors de la génération. Vérifie la configuration du fournisseur LLM "
            "dans Paramètres."
        )
    finally:
        _JOB["running"] = False


def status_ctx(error_override: str | None = None) -> dict[str, object]:
    done = cast(int, _JOB["done"])
    total = cast(int, _JOB["total"])
    return {
        "running": _JOB["running"],
        "gen_source": _JOB["source"],
        "gen_error": error_override or _JOB["error"],
        "gen_pct": round(done / total * 100) if total else 0,
        "gen_cancelling": bool(_JOB["cancel"]),
        "gen_map_id": _JOB["map_id"],
    }


@router.post("/maps/generate", response_class=HTMLResponse)
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
    elif page_to - page_from + 1 > MAX_PAGES:
        error = f"Plage trop large : {MAX_PAGES} pages maximum."
    elif _try_start(source):
        background_tasks.add_task(_run_map_job, source, page_from, page_to)
    else:
        error = "Une génération est déjà en cours."
    return templates.TemplateResponse(
        request, "partials/map_status.html", {"request": request, **status_ctx(error)}
    )


@router.get("/maps/status", response_class=HTMLResponse)
async def generation_status(request: Request) -> Response:
    return templates.TemplateResponse(
        request, "partials/map_status.html", {"request": request, **status_ctx()}
    )


@router.post("/maps/cancel", response_class=HTMLResponse)
async def cancel_generation(request: Request) -> Response:
    """Request a stop of the running generation (cooperative: takes effect between steps)."""
    with _JOB_LOCK:
        if _JOB["running"]:
            _JOB["cancel"] = True
    return templates.TemplateResponse(
        request, "partials/map_status.html", {"request": request, **status_ctx()}
    )


@router.get("/maps/{map_id}", response_class=HTMLResponse)
async def map_view(request: Request, map_id: int) -> Response:
    item = await asyncio.to_thread(get_map, map_id)
    if item is None:
        return Response(status_code=404)
    ctx: dict[str, object] = {
        "request": request,
        "active_tab": nav_tab(item.source),
        "map": item,
        "tree": json.loads(item.content),
        **sidebar_ctx(),
    }
    return templates.TemplateResponse(request, "map_view.html", ctx)


@router.post("/maps/{map_id}/delete", response_class=HTMLResponse)
async def map_delete(map_id: int) -> Response:
    await asyncio.to_thread(delete_map, map_id)
    return HTMLResponse("")

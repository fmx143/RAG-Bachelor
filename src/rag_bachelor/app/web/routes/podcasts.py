"""🎧 Podcasts routes — two-voice audio lessons (document + page range), persisted."""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Annotated, cast

from fastapi import APIRouter, BackgroundTasks, Form, Request
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from rag_bachelor.app.web._deps import sidebar_ctx, templates
from rag_bachelor.core.podcast import generate_podcast
from rag_bachelor.study.store import (
    delete_podcast,
    get_podcast,
    get_podcast_audio,
    list_podcasts,
)
from rag_bachelor.study.subjects import active_sources, active_subject

router = APIRouter()

_MAX_PAGES = 20  # bounds LLM calls, TTS time (CPU) and the stored audio size (~3 MB/min)

# Single in-process generation job, same pattern as routes/bank.py.
_JOB_LOCK = threading.Lock()
_JOB: dict[str, object] = {
    "running": False,
    "source": None,
    "done": 0,
    "total": 0,
    "cancel": False,
    "error": None,
    "podcast_id": None,
}


def _try_start(source: str) -> bool:
    """Atomically claim the job slot. Returns False if a job is already running."""
    with _JOB_LOCK:
        if _JOB["running"]:
            return False
        _JOB.update(
            running=True, source=source, done=0, total=0, cancel=False, error=None, podcast_id=None
        )
        return True


def _run_podcast_job(source: str, page_from: int, page_to: int) -> None:
    def _progress(done: int, total: int) -> None:
        _JOB.update(done=done, total=total)

    try:
        _JOB["podcast_id"] = generate_podcast(
            source,
            page_from,
            page_to,
            progress_cb=_progress,
            should_stop=lambda: bool(_JOB["cancel"]),
        )
    except ValueError:
        _JOB["error"] = "Aucun contenu exploitable dans cette plage de pages."
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
        "gen_podcast_id": _JOB["podcast_id"],
    }


@router.get("/podcasts", response_class=HTMLResponse)
async def podcasts_page(request: Request) -> Response:
    ctx: dict[str, object] = {
        "request": request,
        "active_tab": "podcasts",
        "sources": await asyncio.to_thread(active_sources),
        "podcasts": await asyncio.to_thread(list_podcasts, active_subject()),
        "max_pages": _MAX_PAGES,
        **sidebar_ctx(),
        **_status_ctx(),
    }
    return templates.TemplateResponse(request, "podcasts.html", ctx)


@router.post("/podcasts/generate", response_class=HTMLResponse)
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
        background_tasks.add_task(_run_podcast_job, source, page_from, page_to)
    else:
        error = "Une génération est déjà en cours."
    return templates.TemplateResponse(
        request, "partials/podcast_status.html", {"request": request, **_status_ctx(error)}
    )


@router.get("/podcasts/status", response_class=HTMLResponse)
async def generation_status(request: Request) -> Response:
    return templates.TemplateResponse(
        request, "partials/podcast_status.html", {"request": request, **_status_ctx()}
    )


@router.post("/podcasts/cancel", response_class=HTMLResponse)
async def cancel_generation(request: Request) -> Response:
    """Request a stop of the running generation (cooperative: takes effect between steps)."""
    with _JOB_LOCK:
        if _JOB["running"]:
            _JOB["cancel"] = True
    return templates.TemplateResponse(
        request, "partials/podcast_status.html", {"request": request, **_status_ctx()}
    )


@router.get("/podcasts/{podcast_id}", response_class=HTMLResponse)
async def podcast_view(request: Request, podcast_id: int) -> Response:
    item = await asyncio.to_thread(get_podcast, podcast_id)
    if item is None:
        return Response(status_code=404)
    ctx: dict[str, object] = {
        "request": request,
        "active_tab": "podcasts",
        "podcast": item,
        "lines": json.loads(item.script),
        **sidebar_ctx(),
    }
    return templates.TemplateResponse(request, "podcast_view.html", ctx)


@router.get("/podcasts/{podcast_id}/audio.wav")
async def podcast_audio(podcast_id: int) -> Response:
    audio = await asyncio.to_thread(get_podcast_audio, podcast_id)
    if audio is None:
        return Response(status_code=404)
    return Response(content=audio, media_type="audio/wav")


@router.post("/podcasts/{podcast_id}/delete", response_class=HTMLResponse)
async def podcast_delete(podcast_id: int) -> Response:
    await asyncio.to_thread(delete_podcast, podcast_id)
    return HTMLResponse("")

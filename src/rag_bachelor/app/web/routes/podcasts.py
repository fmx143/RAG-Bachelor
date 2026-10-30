"""🎧 Podcasts routes — two-voice audio lessons (document + page range), persisted."""

from __future__ import annotations

import asyncio
import json
import re
import threading
from typing import Annotated, Any, cast

from fastapi import APIRouter, BackgroundTasks, Form, Request
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from rag_bachelor.app.web._deps import sidebar_ctx, templates
from rag_bachelor.core.podcast import audio_mime, generate_podcast, line_starts
from rag_bachelor.study.store import (
    delete_podcast,
    get_podcast,
    get_podcast_audio,
    get_podcast_audio_size,
    get_podcast_audio_slice,
    list_podcasts,
)
from rag_bachelor.study.subjects import active_sources, active_subject

router = APIRouter()

MAX_PAGES = 40  # bounds LLM calls, TTS time (CPU) and the stored audio size (~0.5 MB/min)
_RANGE = re.compile(r"bytes=(\d*)-(\d*)")

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


def status_ctx(error_override: str | None = None) -> dict[str, object]:
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
        "max_pages": MAX_PAGES,
        **sidebar_ctx(),
        **status_ctx(),
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
    elif page_to - page_from + 1 > MAX_PAGES:
        error = f"Plage trop large : {MAX_PAGES} pages maximum."
    elif _try_start(source):
        background_tasks.add_task(_run_podcast_job, source, page_from, page_to)
    else:
        error = "Une génération est déjà en cours."
    return templates.TemplateResponse(
        request, "partials/podcast_status.html", {"request": request, **status_ctx(error)}
    )


@router.get("/podcasts/status", response_class=HTMLResponse)
async def generation_status(request: Request) -> Response:
    return templates.TemplateResponse(
        request, "partials/podcast_status.html", {"request": request, **status_ctx()}
    )


@router.post("/podcasts/cancel", response_class=HTMLResponse)
async def cancel_generation(request: Request) -> Response:
    """Request a stop of the running generation (cooperative: takes effect between steps)."""
    with _JOB_LOCK:
        if _JOB["running"]:
            _JOB["cancel"] = True
    return templates.TemplateResponse(
        request, "partials/podcast_status.html", {"request": request, **status_ctx()}
    )


@router.get("/podcasts/{podcast_id}", response_class=HTMLResponse)
async def podcast_view(request: Request, podcast_id: int) -> Response:
    item = await asyncio.to_thread(get_podcast, podcast_id)
    if item is None:
        return Response(status_code=404)
    lines: list[dict[str, Any]] = json.loads(item.script)
    if any("start" not in line for line in lines):  # stored before line timings existed
        audio = await asyncio.to_thread(get_podcast_audio, podcast_id)
        if audio is None:  # deleted in between
            return Response(status_code=404)
        starts = await asyncio.to_thread(line_starts, lines, audio)
        lines = [{**line, "start": t} for line, t in zip(lines, starts, strict=True)]
    ctx: dict[str, object] = {
        "request": request,
        "active_tab": "podcasts",
        "podcast": item,
        "lines": lines,
        "podcasts": await asyncio.to_thread(list_podcasts, active_subject()),
        **sidebar_ctx(),
    }
    return templates.TemplateResponse(request, "podcast_view.html", ctx)


def _byte_range(header: str | None, size: int) -> tuple[int, int] | None:
    """``(start, end)`` inclusive for a single-range ``Range`` header, ``None`` for the whole file.

    Raises ``ValueError`` for an unsatisfiable range. Multi-range requests get the whole file.
    """
    m = _RANGE.fullmatch(header.strip()) if header else None
    if m is None or m.group(1) == m.group(2) == "":
        return None
    if m.group(1) == "":  # suffix range: the last N bytes
        start, end = max(size - int(m.group(2)), 0), size - 1
    else:
        start = int(m.group(1))
        end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
    if start >= size or start > end:
        raise ValueError("unsatisfiable range")
    return start, end


@router.get("/podcasts/{podcast_id}/audio")
@router.get("/podcasts/{podcast_id}/audio.wav")  # links from before MP3 storage
async def podcast_audio(request: Request, podcast_id: int) -> Response:
    """Serve the audio with byte-range support: Safari needs it to play, Chrome to seek."""
    size = await asyncio.to_thread(get_podcast_audio_size, podcast_id)
    head = await asyncio.to_thread(get_podcast_audio_slice, podcast_id, 0, 4)
    if size is None or head is None:
        return Response(status_code=404)
    mime = audio_mime(head)
    headers = {"Accept-Ranges": "bytes"}
    try:
        span = _byte_range(request.headers.get("range"), size)
    except ValueError:
        headers["Content-Range"] = f"bytes */{size}"
        return Response(status_code=416, headers=headers)
    start, end = span or (0, size - 1)
    body = await asyncio.to_thread(get_podcast_audio_slice, podcast_id, start, end - start + 1)
    if body is None:  # deleted in between
        return Response(status_code=404)
    if span is None:
        return Response(content=body, media_type=mime, headers=headers)
    headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return Response(content=body, status_code=206, media_type=mime, headers=headers)


@router.post("/podcasts/{podcast_id}/delete", response_class=HTMLResponse)
async def podcast_delete(podcast_id: int) -> Response:
    await asyncio.to_thread(delete_podcast, podcast_id)
    return HTMLResponse("")

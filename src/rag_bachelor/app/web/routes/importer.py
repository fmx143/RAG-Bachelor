"""📥 Question import — copy the questions a PDF already contains into the bank (background job)."""

from __future__ import annotations

import asyncio
import threading
from typing import Annotated, cast

from fastapi import APIRouter, BackgroundTasks, Form, Request
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from rag_bachelor.app.web._deps import templates
from rag_bachelor.core.domains import tag_subject
from rag_bachelor.core.importer import ImportReport, import_questions
from rag_bachelor.ingest.index import list_sources
from rag_bachelor.study.subjects import subject_of

router = APIRouter()

# Single in-process import job, same pattern as routes/sheets.py.
_JOB_LOCK = threading.Lock()
_JOB: dict[str, object] = {
    "running": False,
    "source": None,
    "done": 0,
    "total": 0,
    "cancel": False,
    "error": None,
    "report": None,
}


def _run(source: str) -> None:
    def _progress(done: int, total: int) -> None:
        _JOB.update(done=done, total=total)

    stop = lambda: bool(_JOB["cancel"])  # noqa: E731
    try:
        _JOB["report"] = import_questions(source, _progress, stop)
        try:  # exam domains of the imported questions; the import itself is saved
            tag_subject(subject_of(source), should_stop=stop)
        except Exception:  # noqa: BLE001 — tags can be added later from Paramètres
            pass
    except Exception:  # never surface the raw exception (LLM SDKs can echo a key)
        _JOB["error"] = "Erreur pendant l'import. Vérifie le fournisseur LLM dans Paramètres."
    finally:
        _JOB["running"] = False


def status_ctx(error_override: str | None = None) -> dict[str, object]:
    done, total = cast(int, _JOB["done"]), cast(int, _JOB["total"])
    return {
        "imp_running": _JOB["running"],
        "imp_source": _JOB["source"],
        "imp_pct": round(done / total * 100) if total else 0,
        "imp_cancelling": bool(_JOB["cancel"]),
        "imp_error": error_override or _JOB["error"],
        "imp_report": cast(ImportReport | None, _JOB["report"]),
    }


def _status(request: Request, error: str | None = None) -> Response:
    ctx = {"request": request, **status_ctx(error)}
    return templates.TemplateResponse(request, "partials/import_status.html", ctx)


@router.post("/import/questions", response_class=HTMLResponse)
async def start_import(
    request: Request, background_tasks: BackgroundTasks, source: Annotated[str, Form()]
) -> Response:
    if source not in await asyncio.to_thread(list_sources):
        return _status(request, "Document introuvable.")
    with _JOB_LOCK:
        if _JOB["running"]:
            return _status(request, "Un import est déjà en cours.")
        _JOB.update(running=True, source=source, done=0, total=0, cancel=False, error=None)
        _JOB["report"] = None
    background_tasks.add_task(_run, source)
    return _status(request)


@router.get("/import/status", response_class=HTMLResponse)
async def import_status(request: Request) -> Response:
    return _status(request)


@router.post("/import/cancel", response_class=HTMLResponse)
async def cancel_import(request: Request) -> Response:
    with _JOB_LOCK:
        if _JOB["running"]:
            _JOB["cancel"] = True
    return _status(request)

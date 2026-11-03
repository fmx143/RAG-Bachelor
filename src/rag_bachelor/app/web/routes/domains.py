"""🎯 Exam domains — the frameworks a subject targets and the background tagging job."""

from __future__ import annotations

import asyncio
import threading
from typing import Annotated, cast

from fastapi import APIRouter, BackgroundTasks, Form, Request
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from rag_bachelor.app.web._deps import templates
from rag_bachelor.core.domains import (
    FRAMEWORKS,
    set_subject_frameworks,
    subject_frameworks,
    tag_subject,
)
from rag_bachelor.study.store import bank_questions_missing_domains
from rag_bachelor.study.subjects import active_subject

router = APIRouter()

# Single in-process tagging job, same pattern as routes/sheets.py.
_JOB_LOCK = threading.Lock()
_JOB: dict[str, object] = {"running": False, "done": 0, "total": 0, "cancel": False, "error": None}


def _run(subject: str) -> None:
    def _progress(done: int, total: int) -> None:
        _JOB.update(done=done, total=total)

    try:
        _JOB["tagged"] = tag_subject(subject, _progress, lambda: bool(_JOB["cancel"]))
    except Exception:  # never surface the raw exception (LLM SDKs can echo a key)
        _JOB["error"] = "Erreur pendant le classement. Vérifie le fournisseur LLM."
    finally:
        _JOB["running"] = False


def panel_ctx(request: Request) -> dict[str, object]:
    """Context of partials/domains_panel.html for the active subject."""
    subject = active_subject()
    chosen = subject_frameworks(subject)
    done, total = cast(int, _JOB["done"]), cast(int, _JOB["total"])
    return {
        "request": request,
        "subject": subject,
        "all_frameworks": FRAMEWORKS,
        "chosen_frameworks": chosen,
        "untagged": len(bank_questions_missing_domains(subject, chosen)) if chosen else 0,
        "tag_running": _JOB["running"],
        "tag_pct": round(done / total * 100) if total else 0,
        "tag_error": _JOB["error"],
        "tag_done": _JOB.get("tagged"),
    }


async def _panel(request: Request) -> Response:
    ctx = await asyncio.to_thread(panel_ctx, request)
    return templates.TemplateResponse(request, "partials/domains_panel.html", ctx)


@router.post("/domains/frameworks", response_class=HTMLResponse)
async def save_frameworks(
    request: Request,
    framework: Annotated[list[str], Form()] = [],  # noqa: B006 — never mutated
) -> Response:
    await asyncio.to_thread(lambda: set_subject_frameworks(active_subject(), framework))
    return await _panel(request)


@router.post("/domains/tag", response_class=HTMLResponse)
async def start_tagging(request: Request, background_tasks: BackgroundTasks) -> Response:
    with _JOB_LOCK:
        if not _JOB["running"]:
            _JOB.update(running=True, done=0, total=0, cancel=False, error=None, tagged=None)
            background_tasks.add_task(_run, await asyncio.to_thread(active_subject))
    return await _panel(request)


@router.get("/domains/status", response_class=HTMLResponse)
async def tagging_status(request: Request) -> Response:
    return await _panel(request)


@router.post("/domains/cancel", response_class=HTMLResponse)
async def cancel_tagging(request: Request) -> Response:
    _JOB["cancel"] = True
    return await _panel(request)

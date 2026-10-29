"""📂 Subject routes — switch the active subject or create one (a top-level folder)."""

from __future__ import annotations

import asyncio
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Form, Request
from starlette.responses import RedirectResponse

from rag_bachelor.app.web.routes.documentation import _safe_relpath
from rag_bachelor.config import settings
from rag_bachelor.study.subjects import list_subjects, set_active_subject

router = APIRouter()


def _back(request: Request) -> RedirectResponse:
    """Back to the page the form was sent from (path only: never follow a foreign Referer)."""
    path = urlsplit(request.headers.get("referer", "")).path
    return RedirectResponse(
        path if path.startswith("/") and not path.startswith("//") else "/docs", 303
    )


def _switch(subject: str, name: str) -> None:
    if name:
        folder = _safe_relpath(name, file=False)
        if "/" in folder:
            raise ValueError("un sujet est un seul dossier")
        (settings.pdfs_dir / folder).mkdir(parents=True, exist_ok=True)
        subject = folder
    if subject in list_subjects():
        set_active_subject(subject)


@router.post("/subjects")
async def switch_subject(
    request: Request,
    subject: Annotated[str, Form()] = "",
    name: Annotated[str, Form()] = "",
) -> RedirectResponse:
    """Activate *subject*, or create the subject *name* and activate it."""
    try:
        await asyncio.to_thread(_switch, subject, name.strip())
    except ValueError:
        pass  # invalid name: stay on the current subject
    return _back(request)

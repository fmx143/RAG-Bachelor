"""⚙️ Settings routes — Ollama model info."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from rag_bachelor.app.web._deps import sidebar_ctx, templates
from rag_bachelor.config import settings as cfg

router = APIRouter()


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request) -> Response:
    ctx: dict[str, object] = {
        "request": request,
        "active_tab": "settings",
        "cfg": cfg,
        **sidebar_ctx(),
    }
    return templates.TemplateResponse(request, "settings.html", ctx)

"""⚙️ Settings routes — LLM provider status, toggle, and config."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from rag_bachelor.app.web._deps import sidebar_ctx, templates
from rag_bachelor.config import settings as cfg
from rag_bachelor.core.llm import PROVIDER_SETTING_KEY, VISION_SETTING_KEY
from rag_bachelor.study import store

router = APIRouter()

_VALID_PROVIDERS = ("ollama", "openai")


def vision_captioning_enabled() -> bool:
    """Whether image-only pages should be captioned by a vision model on index."""
    return store.get_setting(VISION_SETTING_KEY, "0") == "1"


def _panel_ctx(request: Request, error: str | None = None) -> dict[str, object]:
    return {
        "request": request,
        "cfg": cfg,
        "active_provider": store.get_setting(PROVIDER_SETTING_KEY, cfg.default_llm_provider),
        # Only a boolean ever reaches the template — the raw key never does.
        "openai_key_configured": bool(cfg.openai_api_key.get_secret_value()),
        "vision_enabled": vision_captioning_enabled(),
        "error": error,
    }


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request) -> Response:
    ctx: dict[str, object] = {
        "active_tab": "settings",
        **_panel_ctx(request),
        **sidebar_ctx(),
    }
    return templates.TemplateResponse(request, "settings.html", ctx)


@router.post("/settings/provider", response_class=HTMLResponse)
async def set_provider(request: Request, provider: Annotated[str, Form()]) -> Response:
    """Persist the active LLM provider (HTMX swaps #provider-panel)."""
    if provider not in _VALID_PROVIDERS:
        return templates.TemplateResponse(
            request, "partials/provider_panel.html", _panel_ctx(request, error="Fournisseur invalide.")
        )
    if provider == "openai" and not cfg.openai_api_key.get_secret_value():
        return templates.TemplateResponse(
            request,
            "partials/provider_panel.html",
            _panel_ctx(request, error="Aucune clé OpenAI configurée — définis OPENAI_API_KEY."),
        )
    store.set_setting(PROVIDER_SETTING_KEY, provider)
    return templates.TemplateResponse(request, "partials/provider_panel.html", _panel_ctx(request))


@router.post("/settings/vision", response_class=HTMLResponse)
async def set_vision(request: Request, enabled: Annotated[bool, Form()] = False) -> Response:
    """Persist the image-captioning-on-index toggle (HTMX swaps #vision-panel)."""
    store.set_setting(VISION_SETTING_KEY, "1" if enabled else "0")
    return templates.TemplateResponse(request, "partials/vision_panel.html", _panel_ctx(request))

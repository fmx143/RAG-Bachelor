"""⚙️ Settings routes — LLM provider status, toggle, and config."""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from rag_bachelor.app.web._deps import sidebar_ctx, templates
from rag_bachelor.config import settings as cfg
from rag_bachelor.core.llm import (
    PROVIDER_SETTING_KEY,
    VISION_SETTING_KEY,
    active_model,
    ollama_client,
)
from rag_bachelor.study import store

router = APIRouter()

_VALID_PROVIDERS = ("ollama", "openai")


def vision_captioning_enabled() -> bool:
    """Whether image-only pages should be captioned by a vision model on index."""
    return store.get_setting(VISION_SETTING_KEY, "0") == "1"


def _cloud_models() -> list[str]:
    """Model names offered by Ollama Cloud; empty on any failure (free-text input still works)."""
    if not cfg.ollama_api_key.get_secret_value():
        return []
    try:
        return sorted(m.model or "" for m in ollama_client().list().models if m.model)
    except Exception:  # noqa: BLE001 — a listing failure must never break the Settings page
        return []


def _panel_ctx(request: Request, error: str | None = None) -> dict[str, object]:
    return {
        "request": request,
        "cfg": cfg,
        "active_provider": store.get_setting(PROVIDER_SETTING_KEY, cfg.default_llm_provider),
        # Only a boolean ever reaches the template — the raw key never does.
        "openai_key_configured": bool(cfg.openai_api_key.get_secret_value()),
        "ollama_key_configured": bool(cfg.ollama_api_key.get_secret_value()),
        "models": {"ollama": active_model("ollama"), "openai": active_model("openai")},
        "vision_enabled": vision_captioning_enabled(),
        "error": error,
    }


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request) -> Response:
    ctx: dict[str, object] = {
        "active_tab": "settings",
        "ollama_models": await asyncio.to_thread(_cloud_models),
        **_panel_ctx(request),
        **sidebar_ctx(),
    }
    return templates.TemplateResponse(request, "settings.html", ctx)


@router.post("/settings/provider", response_class=HTMLResponse)
async def set_provider(request: Request, provider: Annotated[str, Form()]) -> Response:
    """Persist the active LLM provider (HTMX swaps #provider-panel)."""
    if provider not in _VALID_PROVIDERS:
        return templates.TemplateResponse(
            request,
            "partials/provider_panel.html",
            _panel_ctx(request, error="Fournisseur invalide."),
        )
    if provider == "openai" and not cfg.openai_api_key.get_secret_value():
        return templates.TemplateResponse(
            request,
            "partials/provider_panel.html",
            _panel_ctx(request, error="Aucune clé OpenAI configurée — définis OPENAI_API_KEY."),
        )
    if provider == "ollama" and not cfg.ollama_api_key.get_secret_value():
        return templates.TemplateResponse(
            request,
            "partials/provider_panel.html",
            _panel_ctx(request, error="Aucune clé Ollama configurée — définis OLLAMA_API_KEY."),
        )
    store.set_setting(PROVIDER_SETTING_KEY, provider)
    return templates.TemplateResponse(request, "partials/provider_panel.html", _panel_ctx(request))


@router.post("/settings/models", response_class=HTMLResponse)
async def set_models(
    request: Request, ollama_model: Annotated[str, Form()], openai_model: Annotated[str, Form()]
) -> Response:
    """Persist the model chosen for each provider (HTMX swaps #provider-panel)."""
    chosen = {"ollama": ollama_model.strip(), "openai": openai_model.strip()}
    if any(not m or len(m) > 100 for m in chosen.values()):
        return templates.TemplateResponse(
            request,
            "partials/provider_panel.html",
            _panel_ctx(request, error="Nom de modèle invalide (vide ou > 100 caractères)."),
        )
    for name, model in chosen.items():
        store.set_setting(f"{name}_model", model)
    return templates.TemplateResponse(request, "partials/provider_panel.html", _panel_ctx(request))


@router.post("/settings/vision", response_class=HTMLResponse)
async def set_vision(request: Request, enabled: Annotated[bool, Form()] = False) -> Response:
    """Persist the image-captioning-on-index toggle (HTMX swaps #vision-panel)."""
    store.set_setting(VISION_SETTING_KEY, "1" if enabled else "0")
    return templates.TemplateResponse(request, "partials/vision_panel.html", _panel_ctx(request))

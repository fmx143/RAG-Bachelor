"""Shared dependencies for all route modules."""

from __future__ import annotations

import html as html_lib
from pathlib import Path
from urllib.parse import quote

import markdown as md_lib
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from rag_bachelor.config import settings as cfg
from rag_bachelor.core.llm import get_provider

_HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(_HERE / "templates"))


def _md_filter(text: str) -> Markup:
    escaped = html_lib.escape(text, quote=False)
    rendered = md_lib.markdown(escaped, extensions=["nl2br", "fenced_code"])
    return Markup(rendered)


def _url_encode_filter(value: str) -> str:
    return quote(value, safe="")


def asset_url(path: str) -> str:
    """Return a /static URL stamped with the file's mtime so browsers never
    serve a stale cached copy after the file changes on disk."""
    file_path = _HERE / "static" / path
    try:
        version = int(file_path.stat().st_mtime)
    except OSError:
        return f"/static/{path}"
    return f"/static/{path}?v={version}"


templates.env.filters["md"] = _md_filter
templates.env.filters["url_encode"] = _url_encode_filter
templates.env.globals["asset_url"] = asset_url

_PROVIDER_ICONS = {"openai": "☁️", "ollama": "💻"}


def sidebar_ctx() -> dict[str, object]:
    """Return sidebar context consumed by base.html, reflecting the active LLM provider."""
    _provider, name = get_provider()
    label = cfg.openai_model if name == "openai" else cfg.ollama_model
    return {
        "provider_label": label,
        "provider_icon": _PROVIDER_ICONS.get(name, "💻"),
    }

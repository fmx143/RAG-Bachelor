"""Shared dependencies for all route modules."""

from __future__ import annotations

import html as html_lib
from pathlib import Path
from urllib.parse import quote

import markdown as md_lib
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from rag_bachelor.config import settings as cfg

_HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(_HERE / "templates"))


def _md_filter(text: str) -> Markup:
    escaped = html_lib.escape(text, quote=False)
    rendered = md_lib.markdown(escaped, extensions=["nl2br", "fenced_code"])
    return Markup(rendered)


def _url_encode_filter(value: str) -> str:
    return quote(value, safe="")


templates.env.filters["md"] = _md_filter
templates.env.filters["url_encode"] = _url_encode_filter


def sidebar_ctx() -> dict[str, object]:
    """Return sidebar context consumed by base.html."""
    return {
        "provider_label": cfg.ollama_model,
        "provider_icon": "💻",
    }

"""Shared dependencies for all route modules."""

from __future__ import annotations

import html as html_lib
import re
from pathlib import Path
from urllib.parse import quote

import markdown as md_lib
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from rag_bachelor.core.llm import active_model, get_provider

_HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(_HERE / "templates"))


_IMG_TAG = re.compile(r"<img\b[^>]*>")
_HREF = re.compile(r'href="([^"]*)"')


def _safe_href(m: re.Match[str]) -> str:
    url = m.group(1)
    return m.group(0) if url.startswith(("http://", "https://", "#")) else 'href="#"'


def _md_filter(text: str) -> Markup:
    """Render LLM markdown. The text is untrusted (a PDF can inject instructions into
    the answer): raw HTML is escaped, images are dropped (an <img> loads with no click,
    i.e. can exfiltrate data to any URL) and links keep only http(s)/# targets."""
    escaped = html_lib.escape(text, quote=False)
    rendered = md_lib.markdown(escaped, extensions=["nl2br", "fenced_code"])
    return Markup(_HREF.sub(_safe_href, _IMG_TAG.sub("", rendered)))


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


def sidebar_ctx() -> dict[str, object]:
    """Return sidebar context consumed by base.html, reflecting the active LLM provider."""
    _provider, name = get_provider()
    return {"provider_label": active_model(name), "provider_icon": "☁️"}

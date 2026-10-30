"""Shared dependencies for all route modules."""

from __future__ import annotations

import html as html_lib
import math
import re
import time
import zlib
from pathlib import Path
from urllib.parse import quote

import markdown as md_lib
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from rag_bachelor.core.llm import active_model, get_provider
from rag_bachelor.core.qtypes import SELF_GRADED
from rag_bachelor.study.stats import TopicStats
from rag_bachelor.study.store import document_paths, get_due_cards
from rag_bachelor.study.subjects import active_subject, doc_kind, list_subjects, subject_of

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


_CITE = re.compile(r"\[([^\[\]]+?),\s*p\.\s*(\d+)\]")
_TAG_SPLIT = re.compile(r"(<[^>]*>)")


def _md_cite_filter(text: str) -> Markup:
    """Like ``md`` but turns each ``[document, p.N]`` citation into a pill that opens its source card."""

    def pill(m: re.Match[str]) -> str:
        src = html_lib.escape(html_lib.unescape(m.group(1)).strip(), quote=True)
        return (
            f'<a href="#" class="cite" data-src="{src}" data-page="{m.group(2)}">'
            f"{html_lib.escape(_label_filter(html_lib.unescape(m.group(1))))} p.{m.group(2)}</a>"
        )

    parts = _TAG_SPLIT.split(str(_md_filter(text)))
    return Markup("".join(p if i % 2 else _CITE.sub(pill, p) for i, p in enumerate(parts)))


def _url_encode_filter(value: str) -> str:
    return quote(value, safe="")


def _doc_url_filter(path: str) -> str:
    """URL of a document's page (books and sources alike)."""
    return "/document/" + quote(path, safe="/")


def asset_url(path: str) -> str:
    """Return a /static URL stamped with the file's mtime so browsers never
    serve a stale cached copy after the file changes on disk."""
    file_path = _HERE / "static" / path
    try:
        version = int(file_path.stat().st_mtime)
    except OSError:
        return f"/static/{path}"
    return f"/static/{path}?v={version}"


_HUES = (75, 165, 235, 305, 25, 45, 105, 140, 20)  # the design's per-subject palette


_palette: tuple[float, dict[str, int]] = (-1e9, {})


def _doc_palette() -> dict[str, int]:
    """Document path → palette hue, distinct per document within a subject (path order).

    ponytail: cached 5 s so a template looping over many rows does not query each time; a
    document just added may keep a provisional colour for that long.
    """
    global _palette
    if time.monotonic() - _palette[0] > 5:
        by_subject: dict[str, list[str]] = {}
        for p in sorted(document_paths()):
            by_subject.setdefault(subject_of(p), []).append(p)
        _palette = (
            time.monotonic(),
            {p: _HUES[i % len(_HUES)] for ps in by_subject.values() for i, p in enumerate(ps)},
        )
    return _palette[1]


def doc_hues(topics: list[TopicStats]) -> dict[str, int]:
    """Hue of each topic (a document path), as used by the ``hue`` filter."""
    return {t.topic: _hue_filter(t.topic) for t in topics}


def _label_filter(topic: str) -> str:
    """Short display name for a topic path: no folder, extension, numbering or 'Livre -' prefix."""
    name = re.sub(r"\.pdf$", "", topic.rsplit("/", 1)[-1], flags=re.I)
    name = re.sub(r"^\s*\d+\s*[.)-]\s*", "", name)
    name = re.sub(r"^(livre|cours|support)\s*[-:]\s*", "", name, flags=re.I).strip() or topic
    return name if len(name) <= 30 else name[:29].rstrip() + "…"


def _hue_filter(name: str) -> int:
    """Stable hue of a document (path), the same on every page; any other name (a folder) is hashed."""
    return _doc_palette().get(name) or _HUES[zlib.crc32(name.split("/")[0].encode()) % len(_HUES)]


templates.env.filters["md"] = _md_filter
templates.env.filters["md_cite"] = _md_cite_filter
templates.env.filters["hue"] = _hue_filter
templates.env.filters["label"] = _label_filter
templates.env.filters["url_encode"] = _url_encode_filter
templates.env.filters["doc_url"] = _doc_url_filter
templates.env.globals["asset_url"] = asset_url
templates.env.globals["cos"] = math.cos
templates.env.globals["sin"] = math.sin
templates.env.globals["self_graded"] = SELF_GRADED


def nav_tab(path: str) -> str:
    """Sidebar entry a document's pages belong to: Mes sources for sources, else Livres."""
    return "sources" if doc_kind(path) == "source" else "books"


def sidebar_ctx() -> dict[str, object]:
    """Return sidebar context consumed by base.html, reflecting the active LLM provider."""
    _provider, name = get_provider()
    subject = active_subject()
    return {
        "provider_label": active_model(name),
        "subject": subject,
        "subjects": list_subjects(),
        "nav_due": len(get_due_cards(limit=99, subject=subject)),  # sidebar badge
    }

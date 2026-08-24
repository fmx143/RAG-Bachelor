"""Caption image-only pages / figures so they become searchable text."""

from __future__ import annotations

from pathlib import Path

from rag_bachelor.core.llm import get_provider
from rag_bachelor.ingest.extract import render_page_png

_PROMPT = (
    "Décris en français, en une à trois phrases, les graphiques, diagrammes, tableaux "
    "ou schémas visibles sur cette page. Ignore le texte déjà lisible. Si la page ne "
    "contient aucun élément visuel notable, réponds \"(rien à décrire)\"."
)


def caption_page(pdf_path: Path, page_num: int) -> str | None:
    """Return a French caption for the figures on one page, or None on failure."""
    try:
        png = render_page_png(pdf_path, page_num)
        provider, _ = get_provider()
        caption = provider.caption(png, _PROMPT).strip()
    except Exception:  # noqa: BLE001 — captioning is best-effort, never blocks indexing
        return None
    return caption or None

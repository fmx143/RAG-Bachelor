"""Carte du savoir — concept map of the active subject (page, JSON feed, and the « Réviser » jump)."""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.responses import Response

from rag_bachelor.app.web._deps import _label_filter, sidebar_ctx, templates
from rag_bachelor.core.carte_savoir import CarteSavoir, construire_carte, graphe_annote
from rag_bachelor.ingest.index import get_facts_collection
from rag_bachelor.study.subjects import active_subject, doc_kind

logger = logging.getLogger(__name__)
router = APIRouter()


def _carte() -> CarteSavoir:
    subject = active_subject()
    try:
        return construire_carte(graphe_annote(subject, _label_filter), titre=subject)
    except Exception:  # noqa: BLE001  # the fact graph is optional: Chroma may be down or unbuilt
        logger.exception("carte du savoir indisponible")
        return CarteSavoir(titre=subject, modules=[], concepts=[], liens=[])


def _document_of(concept: str) -> str | None:
    """The book where *concept* (a normalised entity) appears most."""
    got = get_facts_collection().get(
        where={"$or": [{"subject": concept}, {"object": concept}]}, include=["metadatas"]
    )
    sources = Counter(
        str(m["source"]) for m in got.get("metadatas") or [] if m and doc_kind(str(m["source"])) == "livre"
    )
    return sources.most_common(1)[0][0] if sources else None


@router.get("/carte-savoir", response_class=HTMLResponse)
async def carte_savoir_page(request: Request) -> Response:
    ctx: dict[str, object] = {"request": request, "active_tab": "carte", **sidebar_ctx()}
    return templates.TemplateResponse(request, "carte_savoir.html", ctx)


@router.get("/api/carte-savoir", response_model=CarteSavoir, name="api_carte_savoir")
async def api_carte_savoir() -> CarteSavoir:
    return await asyncio.to_thread(_carte)


@router.get("/carte-savoir/reviser")
async def reviser_concept(concept: str = "") -> Response:
    """Revise the deck of the concept's document (there is no per-concept deck)."""
    try:
        source = await asyncio.to_thread(_document_of, concept) if concept else None
    except Exception:  # noqa: BLE001
        logger.exception("document du concept introuvable")
        source = None
    return RedirectResponse(f"/revision?source={quote(source)}" if source else "/revision", 303)

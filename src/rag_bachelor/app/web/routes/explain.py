"""📚 Explanations from the subject's books — for bank questions and revision cards (cached)."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from rag_bachelor.app.web._deps import templates
from rag_bachelor.core.explain import NoBooks, explain_from_books
from rag_bachelor.study.store import (
    bank_question_for_card,
    get_bank_question,
    get_card,
    set_book_explanation,
)
from rag_bachelor.study.subjects import BOOKS_DIR, subject_of

router = APIRouter()
logger = logging.getLogger(__name__)


def _explained(bank_id: int | None, card_id: int | None, refresh: bool) -> dict[str, object]:
    """Context for partials/book_explanation.html: cached, freshly computed, or an error."""
    q = get_bank_question(bank_id) if bank_id is not None else None
    if q is None and card_id is not None:
        q = bank_question_for_card(card_id)
    if q is not None:
        if q.to_confirm:
            return {"error": "Confirme d'abord la bonne réponse de cette question."}
        if q.book_explanation and not refresh:
            return {**q.book_explanation, "bank_id": q.id}
        question, options, correct, topic = q.question, q.options, q.correct, q.source
    else:
        card = get_card(card_id) if card_id is not None else None
        if card is None:
            return {"error": "Question introuvable."}
        question, options, correct, topic = card.question, card.options, card.correct, card.topic
    try:
        result = explain_from_books(question, options, correct, topic)
    except NoBooks:
        return {
            "error": f"Aucun livre indexé dans {subject_of(topic)}/{BOOKS_DIR}/ pour l'expliquer."
        }
    except Exception:  # provider/network failure: never surface the raw message
        logger.exception("book explanation failed")
        return {"error": "Impossible de joindre le modèle, réessaie dans un instant."}
    if q is not None and q.id is not None:
        set_book_explanation(q.id, result.text, result.refs)
    return {"text": result.text, "refs": result.refs, "bank_id": q.id if q else None}


async def _render(
    request: Request, bank_id: int | None, card_id: int | None, refresh: bool
) -> Response:
    ctx = await asyncio.to_thread(_explained, bank_id, card_id, refresh)
    return templates.TemplateResponse(
        request, "partials/book_explanation.html", {"request": request, **ctx}
    )


@router.post("/explain/bank/{bank_id}", response_class=HTMLResponse)
async def explain_bank(request: Request, bank_id: int, refresh: bool = False) -> Response:
    return await _render(request, bank_id, None, refresh)


@router.post("/explain/card/{card_id}", response_class=HTMLResponse)
async def explain_card(request: Request, card_id: int, refresh: bool = False) -> Response:
    return await _render(request, None, card_id, refresh)

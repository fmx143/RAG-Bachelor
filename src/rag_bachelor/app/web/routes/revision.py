"""🔄 Revision routes — stateless SM-2 flashcard session.

Each GET /revision shows the next due card from the database.
Each POST /revision/grade/{card_id} persists the grade, then returns
the partial for the next due card — no server-side session state needed.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from rag_bachelor.app.web._deps import sidebar_ctx, templates
from rag_bachelor.study.srs import Card, update_card
from rag_bachelor.study.store import get_all_cards, get_due_cards, save_review

router = APIRouter()

# Valid SM-2 grades used in the UI
_VALID_GRADES: frozenset[int] = frozenset({0, 2, 4, 5})


def _next_card() -> Card | None:
    """Return the first due card, or None if none are due today."""
    due = get_due_cards(limit=1)
    return due[0] if due else None


def _card_by_id(card_id: int) -> Card | None:
    """Look up a card by its primary key."""
    return next((c for c in get_all_cards() if c.id == card_id), None)


@router.get("/revision", response_class=HTMLResponse)
async def revision_page(request: Request) -> Response:
    card = _next_card()
    due_count = len(get_due_cards(limit=200))
    ctx: dict[str, object] = {
        "request": request,
        "active_tab": "revision",
        "card": card,
        "due_count": due_count,
        **sidebar_ctx(),
    }
    return templates.TemplateResponse(request, "revision.html", ctx)


@router.post("/revision/grade/{card_id}", response_class=HTMLResponse)
async def grade_card(
    request: Request,
    card_id: int,
    grade: Annotated[int, Form()],
) -> Response:
    """Grade a card and return the next-card partial (swapped into #revision-content)."""
    if grade not in _VALID_GRADES:
        grade = 0  # treat unknown grades as failure

    card = _card_by_id(card_id)
    if card is not None:
        updated = update_card(card, grade)
        save_review(updated, grade)

    next_card = _next_card()
    due_count = len(get_due_cards(limit=200))

    ctx: dict[str, object] = {
        "request": request,
        "card": next_card,
        "due_count": due_count,
    }
    return templates.TemplateResponse(request, "partials/revision_card.html", ctx)

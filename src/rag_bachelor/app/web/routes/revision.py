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
from rag_bachelor.core.qtypes import QTYPE_LABELS
from rag_bachelor.study.srs import Card, update_card
from rag_bachelor.study.store import get_all_cards, get_due_cards, save_review

router = APIRouter()

# Valid SM-2 grades used in the UI
_VALID_GRADES: frozenset[int] = frozenset({0, 2, 4, 5})

# Auto-grading for structured (0-token) question types: correct/incorrect
# maps directly onto SM-2's pass/fail thresholds (grade >= 3 is a pass).
_AUTO_GRADE_CORRECT = 4
_AUTO_GRADE_INCORRECT = 0


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
        "qtype_labels": QTYPE_LABELS,
        **sidebar_ctx(),
    }
    return templates.TemplateResponse(request, "revision.html", ctx)


@router.get("/revision/next", response_class=HTMLResponse)
async def next_card(request: Request) -> Response:
    """Return the next due card's partial (used by the feedback screen's "Suivant")."""
    card = _next_card()
    due_count = len(get_due_cards(limit=200))
    ctx: dict[str, object] = {
        "request": request,
        "card": card,
        "due_count": due_count,
        "qtype_labels": QTYPE_LABELS,
    }
    return templates.TemplateResponse(request, "partials/revision_card.html", ctx)


@router.post("/revision/answer/{card_id}", response_class=HTMLResponse)
async def answer_card(
    request: Request,
    card_id: int,
    selected: Annotated[list[int], Form()] = [],  # noqa: B006 - never mutated
) -> Response:
    """Auto-grade a structured (QCM/Vrai-Faux) card — no LLM call.

    Correctness is a deterministic set comparison against the stored
    ``correct`` indices; the result maps onto the same SM-2 grade scale used
    by the free-text flow.
    """
    card = _card_by_id(card_id)
    if card is None or card.correct is None:
        return templates.TemplateResponse(
            request,
            "partials/revision_card.html",
            {
                "request": request,
                "card": _next_card(),
                "due_count": len(get_due_cards(limit=200)),
                "qtype_labels": QTYPE_LABELS,
            },
        )

    is_correct = set(selected) == set(card.correct)
    grade = _AUTO_GRADE_CORRECT if is_correct else _AUTO_GRADE_INCORRECT
    updated = update_card(card, grade)
    save_review(updated, grade)

    ctx: dict[str, object] = {
        "request": request,
        "card": card,
        "selected": selected,
        "is_correct": is_correct,
        "due_count": len(get_due_cards(limit=200)),
    }
    return templates.TemplateResponse(request, "partials/revision_feedback.html", ctx)


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
        "qtype_labels": QTYPE_LABELS,
    }
    return templates.TemplateResponse(request, "partials/revision_card.html", ctx)

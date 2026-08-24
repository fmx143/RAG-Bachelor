"""🔄 Revision routes — stateless SM-2 flashcard session.

Each GET /revision shows the next due card from the database, optionally
scoped to a source/difficulty/type filter. When nothing due matches the
filter, the next matching question is pulled straight from the bank
(materialized into a card first — see study.store.materialize_bank_question)
so the bank behaves as an infinite filtered backlog rather than requiring a
manual "add to deck" step first.

Each POST /revision/grade/{card_id} persists the grade, then returns
the partial for the next due card — no server-side session state needed.
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from rag_bachelor.app.web._deps import sidebar_ctx, templates
from rag_bachelor.core.qtypes import DIFFICULTIES, QTYPE_LABELS, QTYPES, score_selection
from rag_bachelor.ingest.index import list_sources
from rag_bachelor.study.srs import Card, update_card
from rag_bachelor.study.store import (
    bank_sources,
    get_card,
    get_due_cards,
    list_bank_questions,
    materialize_bank_question,
    save_review,
)

router = APIRouter()

# Valid SM-2 grades used in the UI
_VALID_GRADES: frozenset[int] = frozenset({0, 2, 4, 5})

def _grade_for_score(score: float) -> int:
    """Map a Jaccard overlap score onto the app's {0,2,4,5} grade scale.

    A partial mcq_multi match (e.g. 1 of 2 correct) still earns a passing
    grade — SM-2 only needs "did they mostly know it", not perfection.
    """
    if score >= 1.0:
        return 5
    if score >= 0.5:
        return 4
    if score > 0.0:
        return 2
    return 0


def _next_card(source: str, difficulty: str, qtype: str) -> Card | None:
    """Return the next card matching the filter: a due card first, else the
    next un-decked bank question for the same filter (materialized on the
    spot), else None.
    """
    src = source or None
    diff = difficulty if difficulty in DIFFICULTIES else None
    qt = qtype if qtype in QTYPES else None

    due = get_due_cards(limit=1, source=src, difficulty=diff, qtype=qt)
    if due:
        return due[0]

    backlog = list_bank_questions(
        source=src, difficulty=diff, qtype=qt, deck="out", order_asc=True, limit=1
    )
    if not backlog:
        return None
    card_id = materialize_bank_question(backlog[0])
    return get_card(card_id)


def _filter_ctx(source: str, difficulty: str, qtype: str) -> dict[str, object]:
    return {
        "sources": bank_sources() or list_sources(),
        "difficulties": DIFFICULTIES,
        "qtypes": QTYPES,
        "qtype_labels": QTYPE_LABELS,
        "filter_source": source,
        "filter_difficulty": difficulty,
        "filter_qtype": qtype,
    }


@router.get("/revision", response_class=HTMLResponse)
async def revision_page(
    request: Request, source: str = "", difficulty: str = "", qtype: str = ""
) -> Response:
    def _sync() -> tuple[Card | None, int]:
        return _next_card(source, difficulty, qtype), len(get_due_cards(limit=200))

    card, due_count = await asyncio.to_thread(_sync)
    ctx: dict[str, object] = {
        "request": request,
        "active_tab": "revision",
        "card": card,
        "due_count": due_count,
        **_filter_ctx(source, difficulty, qtype),
        **sidebar_ctx(),
    }
    return templates.TemplateResponse(request, "revision.html", ctx)


@router.get("/revision/next", response_class=HTMLResponse)
async def next_card(
    request: Request, source: str = "", difficulty: str = "", qtype: str = ""
) -> Response:
    """Return the next matching card's partial (used by "Suivant" and the filter form)."""

    def _sync() -> tuple[Card | None, int]:
        return _next_card(source, difficulty, qtype), len(get_due_cards(limit=200))

    card, due_count = await asyncio.to_thread(_sync)
    ctx: dict[str, object] = {
        "request": request,
        "card": card,
        "due_count": due_count,
        **_filter_ctx(source, difficulty, qtype),
    }
    return templates.TemplateResponse(request, "partials/revision_card.html", ctx)


@router.post("/revision/answer/{card_id}", response_class=HTMLResponse)
async def answer_card(
    request: Request,
    card_id: int,
    selected: Annotated[list[int], Form()] = [],  # noqa: B006 - never mutated
    source: Annotated[str, Form()] = "",
    difficulty: Annotated[str, Form()] = "",
    qtype: Annotated[str, Form()] = "",
) -> Response:
    """Auto-grade a structured (QCM/Vrai-Faux) card — no LLM call.

    Correctness is a Jaccard overlap between the selected and stored
    ``correct`` indices, giving partial credit on ``mcq_multi``; the result
    maps onto the same SM-2 grade scale used by the free-text flow.
    """

    card = await asyncio.to_thread(get_card, card_id)
    if card is None or card.correct is None:
        next_c, due_count = await asyncio.to_thread(
            lambda: (_next_card(source, difficulty, qtype), len(get_due_cards(limit=200)))
        )
        return templates.TemplateResponse(
            request,
            "partials/revision_card.html",
            {
                "request": request,
                "card": next_c,
                "due_count": due_count,
                **_filter_ctx(source, difficulty, qtype),
            },
        )

    score = score_selection(card.correct, selected)
    is_correct = score >= 1.0
    is_partial = 0.0 < score < 1.0
    grade = _grade_for_score(score)

    def _save() -> int:
        updated = update_card(card, grade)
        save_review(updated, grade)
        return len(get_due_cards(limit=200))

    due_count = await asyncio.to_thread(_save)

    ctx: dict[str, object] = {
        "request": request,
        "card": card,
        "selected": selected,
        "is_correct": is_correct,
        "is_partial": is_partial,
        "due_count": due_count,
        **_filter_ctx(source, difficulty, qtype),
    }
    return templates.TemplateResponse(request, "partials/revision_feedback.html", ctx)


@router.post("/revision/grade/{card_id}", response_class=HTMLResponse)
async def grade_card(
    request: Request,
    card_id: int,
    grade: Annotated[int, Form()],
    source: Annotated[str, Form()] = "",
    difficulty: Annotated[str, Form()] = "",
    qtype: Annotated[str, Form()] = "",
) -> Response:
    """Grade a card and return the next-card partial (swapped into #revision-content)."""
    if grade not in _VALID_GRADES:
        grade = 0  # treat unknown grades as failure

    def _sync() -> tuple[Card | None, int]:
        card = get_card(card_id)
        if card is not None:
            updated = update_card(card, grade)
            save_review(updated, grade)
        return _next_card(source, difficulty, qtype), len(get_due_cards(limit=200))

    next_c, due_count = await asyncio.to_thread(_sync)

    ctx: dict[str, object] = {
        "request": request,
        "card": next_c,
        "due_count": due_count,
        **_filter_ctx(source, difficulty, qtype),
    }
    return templates.TemplateResponse(request, "partials/revision_card.html", ctx)

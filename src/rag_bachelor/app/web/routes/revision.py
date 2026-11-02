"""🔄 Revision routes — stateless SM-2 flashcard session.

Each GET /revision shows the next due card from the database, optionally
scoped to a source/difficulty/type filter, and to a page range of that source
(a chapter: ``pages=12-30``, matched on the first page of the card's bank question). When nothing due matches the
filter, the next matching question is pulled straight from the bank
(materialized into a card first — see study.store.materialize_bank_question)
so the bank behaves as an infinite filtered backlog rather than requiring a
manual "add to deck" step first.

Each POST /revision/grade/{card_id} persists the grade, then returns
the partial for the next due card — no server-side session state needed.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from rag_bachelor.app.web._deps import doc_hues, sidebar_ctx, templates
from rag_bachelor.core.qa import answer_question
from rag_bachelor.core.qtypes import (
    DIFFICULTIES,
    QTYPE_LABELS,
    QTYPES,
    SELF_GRADED,
    score_selection,
)
from rag_bachelor.core.retriever import SearchResult
from rag_bachelor.core.tutor import Verdict, explain, grade_answer
from rag_bachelor.study.srs import Card, update_card
from rag_bachelor.study.stats import get_topic_stats
from rag_bachelor.study.store import (
    bank_card_pages,
    bank_question_for_card,
    bank_sources,
    get_card,
    get_due_cards,
    get_review_days,
    list_bank_questions,
    materialize_bank_question,
    save_review,
)
from rag_bachelor.study.subjects import active_sources, active_subject

router = APIRouter()
logger = logging.getLogger(__name__)

# Valid SM-2 grades used in the UI (Difficile = 3: a pass, unlike grade < 3 which resets)
_VALID_GRADES: frozenset[int] = frozenset({0, 3, 4, 5})


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


def _counts() -> dict[str, int]:
    """Session progress: cards still due and reviews already done today."""
    subject = active_subject()
    today = get_review_days(date.today(), subject)
    return {
        "due_count": len(get_due_cards(limit=200, subject=subject)),
        "done_today": today[0][1] if today else 0,
        "ok_today": today[0][2] if today else 0,
    }


def _page_range(pages: str) -> tuple[int, int] | None:
    """``"12-30"`` → ``(12, 30)``; anything else → None (no page filter)."""
    lo, sep, hi = pages.partition("-")
    if not (sep and lo.isdigit() and hi.isdigit()) or int(lo) > int(hi):
        return None
    return int(lo), int(hi)


def _next_card(source: str, difficulty: str, qtype: str, pages: str = "") -> Card | None:
    """Return the next card matching the filter: a due card first, else the
    next un-decked bank question for the same filter (materialized on the
    spot), else None.
    """
    src = source or None
    diff = difficulty if difficulty in DIFFICULTIES else None
    qt = qtype if qtype in QTYPES else None
    span = _page_range(pages) if src else None

    def in_span(page: int) -> bool:
        return span is None or span[0] <= page <= span[1]

    ids = [cid for cid, p in bank_card_pages(src).items() if in_span(p)] if src and span else None
    subject = active_subject()
    due = get_due_cards(
        limit=1, source=src, difficulty=diff, qtype=qt, subject=subject, card_ids=ids
    )
    if due:
        return due[0]

    backlog = list_bank_questions(
        source=src,
        difficulty=diff,
        qtype=qt,
        deck="out",
        order_asc=True,
        limit=1 if span is None else 100_000,
        subject=subject,
    )
    nxt = next((q for q in backlog if q.pages and in_span(min(q.pages))), None)
    if nxt is None:
        return None
    card_id = materialize_bank_question(nxt)
    return get_card(card_id)


def _filter_ctx(source: str, difficulty: str, qtype: str, pages: str = "") -> dict[str, object]:
    span = _page_range(pages) if source else None
    return {
        "filter_pages": f"{span[0]}-{span[1]}" if span else "",
        "hues": doc_hues([t for t in get_topic_stats(active_subject()) if t.total_cards]),
        "sources": bank_sources(active_subject()) or active_sources(),
        "difficulties": DIFFICULTIES,
        "qtypes": QTYPES,
        "qtype_labels": QTYPE_LABELS,
        "filter_source": source,
        "filter_difficulty": difficulty,
        "filter_qtype": qtype,
    }


@router.get("/revision", response_class=HTMLResponse)
async def revision_page(
    request: Request, source: str = "", difficulty: str = "", qtype: str = "", pages: str = ""
) -> Response:
    def _sync() -> tuple[Card | None, dict[str, int]]:
        return _next_card(source, difficulty, qtype, pages), _counts()

    card, counts = await asyncio.to_thread(_sync)
    ctx: dict[str, object] = {
        "request": request,
        "active_tab": "revision",
        "card": card,
        **counts,
        **_filter_ctx(source, difficulty, qtype, pages),
        **sidebar_ctx(),
    }
    return templates.TemplateResponse(request, "revision.html", ctx)


@router.get("/revision/next", response_class=HTMLResponse)
async def next_card(
    request: Request, source: str = "", difficulty: str = "", qtype: str = "", pages: str = ""
) -> Response:
    """Return the next matching card's partial (used by "Suivant" and the filter form)."""

    def _sync() -> tuple[Card | None, dict[str, int]]:
        return _next_card(source, difficulty, qtype, pages), _counts()

    card, counts = await asyncio.to_thread(_sync)
    ctx: dict[str, object] = {
        "request": request,
        "card": card,
        **counts,
        **_filter_ctx(source, difficulty, qtype, pages),
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
    pages: Annotated[str, Form()] = "",
) -> Response:
    """Auto-grade a structured (QCM/Vrai-Faux) card — no LLM call.

    Correctness is a Jaccard overlap between the selected and stored
    ``correct`` indices, giving partial credit on ``mcq_multi``; the result
    maps onto the same SM-2 grade scale used by the free-text flow.
    """

    card = await asyncio.to_thread(get_card, card_id)
    if card is None or card.correct is None:
        next_c, counts = await asyncio.to_thread(
            lambda: (_next_card(source, difficulty, qtype, pages), _counts())
        )
        return templates.TemplateResponse(
            request,
            "partials/revision_card.html",
            {
                "request": request,
                "card": next_c,
                **counts,
                **_filter_ctx(source, difficulty, qtype, pages),
            },
        )

    score = score_selection(card.correct, selected)
    is_correct = score >= 1.0
    is_partial = 0.0 < score < 1.0
    grade = _grade_for_score(score)

    def _save() -> dict[str, int]:
        updated = update_card(card, grade)
        save_review(updated, grade)
        return _counts()

    counts = await asyncio.to_thread(_save)

    ctx: dict[str, object] = {
        "request": request,
        "card": card,
        "selected": selected,
        "selected_text": ", ".join(
            card.options[i] for i in selected if card.options and 0 <= i < len(card.options)
        ),
        "is_correct": is_correct,
        "is_partial": is_partial,
        **counts,
        **_filter_ctx(source, difficulty, qtype, pages),
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
    pages: Annotated[str, Form()] = "",
) -> Response:
    """Grade a card and return the next-card partial (swapped into #revision-content)."""
    if grade not in _VALID_GRADES:
        grade = 0  # treat unknown grades as failure

    def _sync() -> tuple[Card | None, dict[str, int]]:
        card = get_card(card_id)
        if card is not None:
            updated = update_card(card, grade)
            save_review(updated, grade)
        return _next_card(source, difficulty, qtype, pages), _counts()

    next_c, counts = await asyncio.to_thread(_sync)

    ctx: dict[str, object] = {
        "request": request,
        "card": next_c,
        **counts,
        **_filter_ctx(source, difficulty, qtype, pages),
    }
    return templates.TemplateResponse(request, "partials/revision_card.html", ctx)


@router.post("/revision/check/{card_id}", response_class=HTMLResponse)
async def check_answer(
    request: Request,
    card_id: int,
    user_answer: Annotated[str, Form()],
    source: Annotated[str, Form()] = "",
    difficulty: Annotated[str, Form()] = "",
    qtype: Annotated[str, Form()] = "",
    pages: Annotated[str, Form()] = "",
) -> Response:
    """AI-correct a typed answer and suggest a grade; the learner still picks it (``grade_card``)."""

    def _sync() -> tuple[Card | None, bool, Verdict | None, dict[str, int]]:
        card = get_card(card_id)
        if card is None or card.qtype not in SELF_GRADED:
            return _next_card(source, difficulty, qtype, pages), False, None, _counts()
        bq = bank_question_for_card(card_id)
        verdict = None
        try:
            verdict = grade_answer(card, bq.passage if bq else "", user_answer)
        except Exception:  # provider/network failure: fall back to plain self-grading
            logger.exception("AI grading failed for card %s", card_id)
        return card, True, verdict, _counts()

    card, checked, verdict, counts = await asyncio.to_thread(_sync)
    ctx: dict[str, object] = {
        "request": request,
        "card": card,
        "verdict": verdict,
        "user_answer": user_answer,
        **counts,
        **_filter_ctx(source, difficulty, qtype, pages),
    }
    name = "partials/revision_check.html" if checked else "partials/revision_card.html"
    return templates.TemplateResponse(request, name, ctx)


@router.post("/revision/why/{card_id}", response_class=HTMLResponse)
async def why(
    request: Request,
    card_id: int,
    user_answer: Annotated[str, Form()] = "",
    followup: Annotated[str, Form()] = "",
) -> Response:
    """Explain a card from the course, or answer a follow-up question about it (no server state)."""
    question = followup.strip()[:500]

    def _sync() -> tuple[Card | None, str, list[SearchResult]]:
        card = get_card(card_id)
        if card is None:
            return None, "", []
        if question:
            text, chunks = answer_question(question, top_k=4, sources=[card.topic])
        else:
            bq = bank_question_for_card(card_id)
            text, chunks = explain(card, bq.passage if bq else "", user_answer)
        return card, text, chunks

    error = ""
    card: Card | None = None
    text = ""
    chunks: list[SearchResult] = []
    try:
        card, text, chunks = await asyncio.to_thread(_sync)
    except Exception:  # provider/network failure: tell the learner instead of a 500
        logger.exception("AI explanation failed for card %s", card_id)
        error = "Impossible de joindre le modèle, réessaie dans un instant."
    if card is None and not error:
        error = "Carte introuvable."
    return templates.TemplateResponse(
        request,
        "partials/revision_why.html",
        {
            "request": request,
            "card_id": card_id,
            "user_answer": user_answer,
            "question": question,
            "text": text,
            "chunks": chunks,
            "error": error,
        },
    )

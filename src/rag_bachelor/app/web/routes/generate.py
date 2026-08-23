"""🎯 Generate routes — LLM question generation and deck integration."""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from rag_bachelor.app.web._deps import sidebar_ctx, templates
from rag_bachelor.core.qtypes import DIFFICULTIES as _DIFFICULTIES
from rag_bachelor.core.qtypes import QTYPE_LABELS, QTYPES
from rag_bachelor.core.questions import generate_questions
from rag_bachelor.ingest.index import list_sources
from rag_bachelor.study.store import add_card

router = APIRouter()

_DIFF_LABELS = {"facile": "🟢 Facile", "moyen": "🟡 Moyen", "difficile": "🔴 Difficile"}
_MIN_COUNT = 1
_MAX_COUNT = 10


def _safe_json_list(raw: str) -> list[Any] | None:
    """Parse a form field expected to be a JSON list; malformed/blank input degrades to None."""
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, list) else None


def _safe_list(value: object) -> list[Any] | None:
    """Guard a value expected to already be a list; wrong shape degrades to None."""
    return value if isinstance(value, list) else None


@router.get("/generate", response_class=HTMLResponse)
async def generate_page(request: Request) -> Response:
    ctx: dict[str, object] = {
        "request": request,
        "active_tab": "generate",
        "sources": list_sources(),
        "difficulties": _DIFFICULTIES,
        "diff_labels": _DIFF_LABELS,
        "qtypes": QTYPES,
        "qtype_labels": QTYPE_LABELS,
        **sidebar_ctx(),
    }
    return templates.TemplateResponse(request, "generate.html", ctx)


@router.post("/generate", response_class=HTMLResponse)
async def generate_qs(
    request: Request,
    topic: Annotated[str, Form()] = "",
    doc_choice: Annotated[str, Form()] = "",
    difficulty: Annotated[str, Form()] = "moyen",
    qtype: Annotated[str, Form()] = "free",
    count: Annotated[int, Form()] = 3,
) -> Response:
    """Generate questions and return the results partial (swapped into #gen-results)."""
    effective_topic = topic.strip() or doc_choice.strip()
    diff = difficulty if difficulty in _DIFFICULTIES else "moyen"
    qt = qtype if qtype in QTYPES else "free"
    n = min(max(count, _MIN_COUNT), _MAX_COUNT)

    if not effective_topic:
        return templates.TemplateResponse(
            request,
            "partials/gen_questions.html",
            {
                "request": request,
                "error": "Choisis un document ou saisis un sujet.",
                "questions": [],
                "topic": "",
                "difficulty": diff,
                "diff_labels": _DIFF_LABELS,
                "qtype_labels": QTYPE_LABELS,
                "requested": n,
            },
        )

    try:
        questions = await asyncio.to_thread(generate_questions, effective_topic, diff, qt, n)
    except Exception:
        # Never surface the raw exception: LLM SDK auth errors can echo back the API key.
        return templates.TemplateResponse(
            request,
            "partials/gen_questions.html",
            {
                "request": request,
                "error": "Erreur lors de la génération des questions. Vérifie la configuration du fournisseur LLM dans ⚙️ Paramètres.",
                "questions": [],
                "topic": effective_topic,
                "difficulty": diff,
                "diff_labels": _DIFF_LABELS,
                "qtype_labels": QTYPE_LABELS,
                "requested": n,
            },
        )

    return templates.TemplateResponse(
        request,
        "partials/gen_questions.html",
        {
            "request": request,
            "error": None,
            "questions": questions,
            "topic": effective_topic,
            "difficulty": diff,
            "diff_labels": _DIFF_LABELS,
            "qtype_labels": QTYPE_LABELS,
            "requested": n,
        },
    )


@router.post("/generate/add", response_class=HTMLResponse)
async def add_to_deck(
    request: Request,
    question: Annotated[str, Form()],
    answer: Annotated[str, Form()] = "",
    topic: Annotated[str, Form()] = "",
    difficulty: Annotated[str, Form()] = "moyen",
    qtype: Annotated[str, Form()] = "free",
    options: Annotated[str, Form()] = "",
    correct: Annotated[str, Form()] = "",
) -> Response:
    """Add one question to the revision deck; return a small confirmation fragment."""
    diff = difficulty if difficulty in _DIFFICULTIES else "moyen"
    qt = qtype if qtype in QTYPES else "free"
    ans = answer.strip() or "(pas de réponse modèle)"

    opts_list = _safe_json_list(options) if qt != "free" else None
    correct_list = _safe_json_list(correct) if qt != "free" else None

    card_id = add_card(
        question=question.strip(),
        answer=ans,
        topic=topic.strip(),
        difficulty=diff,
        qtype=qt,
        options=opts_list,
        correct=correct_list,
    )
    # Return a small inline confirmation that replaces the "add" button (outerHTML swap)
    return HTMLResponse(
        f'<span class="alert alert-success" style="display:inline-block;padding:.2rem .6rem;">'
        f"✅ Carte #{card_id} ajoutée !</span>"
    )


@router.post("/generate/add-all", response_class=HTMLResponse)
async def add_all_to_deck(
    questions_json: Annotated[str, Form()],
    topic: Annotated[str, Form()] = "",
) -> Response:
    """Add every question from this generation batch to the revision deck at once."""
    try:
        items = json.loads(questions_json)
    except json.JSONDecodeError:
        items = []
    if not isinstance(items, list):
        items = []

    def _sync() -> int:
        added = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            question = str(item.get("question", "")).strip()
            if not question:
                continue
            qt = item.get("qtype", "free")
            qt = qt if qt in QTYPES else "free"
            diff = item.get("difficulty", "moyen")
            diff = diff if diff in _DIFFICULTIES else "moyen"
            answer = str(item.get("answer", "")).strip() or "(pas de réponse modèle)"
            add_card(
                question=question,
                answer=answer,
                topic=topic.strip(),
                difficulty=diff,
                qtype=qt,
                options=_safe_list(item.get("options")) if qt != "free" else None,
                correct=_safe_list(item.get("correct")) if qt != "free" else None,
            )
            added += 1
        return added

    added = await asyncio.to_thread(_sync)
    return HTMLResponse(
        f'<span class="alert alert-success" style="display:inline-block;padding:.3rem .7rem;">'
        f"✅ {added} carte(s) ajoutée(s) !</span>"
    )

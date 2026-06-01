"""🎯 Generate routes — LLM question generation and deck integration."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from rag_bachelor.app.web._deps import sidebar_ctx, templates
from rag_bachelor.core.questions import generate_questions
from rag_bachelor.ingest.index import list_sources
from rag_bachelor.study.store import add_card

router = APIRouter()

_DIFFICULTIES = ("facile", "moyen", "difficile")
_DIFF_LABELS = {"facile": "🟢 Facile", "moyen": "🟡 Moyen", "difficile": "🔴 Difficile"}


@router.get("/generate", response_class=HTMLResponse)
async def generate_page(request: Request) -> Response:
    ctx: dict[str, object] = {
        "request": request,
        "active_tab": "generate",
        "sources": list_sources(),
        "difficulties": _DIFFICULTIES,
        "diff_labels": _DIFF_LABELS,
        **sidebar_ctx(),
    }
    return templates.TemplateResponse(request, "generate.html", ctx)


@router.post("/generate", response_class=HTMLResponse)
async def generate_qs(
    request: Request,
    topic: Annotated[str, Form()] = "",
    doc_choice: Annotated[str, Form()] = "",
    difficulty: Annotated[str, Form()] = "moyen",
) -> Response:
    """Generate questions and return the results partial (swapped into #gen-results)."""
    effective_topic = topic.strip() or doc_choice.strip()
    if not effective_topic:
        return templates.TemplateResponse(
            request,
            "partials/gen_questions.html",
            {
                "request": request,
                "error": "Choisis un document ou saisis un sujet.",
                "questions": [],
                "topic": "",
                "difficulty": difficulty,
                "diff_labels": _DIFF_LABELS,
            },
        )

    diff = difficulty if difficulty in _DIFFICULTIES else "moyen"

    try:
        questions = generate_questions(effective_topic, diff)
    except Exception as exc:
        return templates.TemplateResponse(
            request,
            "partials/gen_questions.html",
            {
                "request": request,
                "error": str(exc),
                "questions": [],
                "topic": effective_topic,
                "difficulty": diff,
                "diff_labels": _DIFF_LABELS,
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
        },
    )


@router.post("/generate/add", response_class=HTMLResponse)
async def add_to_deck(
    request: Request,
    question: Annotated[str, Form()],
    answer: Annotated[str, Form()] = "",
    topic: Annotated[str, Form()] = "",
    difficulty: Annotated[str, Form()] = "moyen",
) -> Response:
    """Add one question to the revision deck; return a small confirmation fragment."""
    diff = difficulty if difficulty in _DIFFICULTIES else "moyen"
    ans = answer.strip() or "(pas de réponse modèle)"
    card_id = add_card(
        question=question.strip(),
        answer=ans,
        topic=topic.strip(),
        difficulty=diff,
    )
    # Return a small inline confirmation that replaces the "add" button (outerHTML swap)
    return HTMLResponse(
        f'<span class="alert alert-success" style="display:inline-block;padding:.2rem .6rem;">'
        f"✅ Carte #{card_id} ajoutée !</span>"
    )

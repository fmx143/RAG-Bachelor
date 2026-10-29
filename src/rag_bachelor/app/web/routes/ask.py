"""❓ Ask routes — RAG question-answering with source citations."""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from rag_bachelor.app.web._deps import sidebar_ctx, templates
from rag_bachelor.core.agent import answer_agentic
from rag_bachelor.core.qa import answer_question
from rag_bachelor.study.subjects import active_sources

router = APIRouter()


@router.get("/ask", response_class=HTMLResponse)
async def ask_page(request: Request) -> Response:
    ctx: dict[str, object] = {
        "request": request,
        "active_tab": "ask",
        "no_docs": not await asyncio.to_thread(active_sources),
        **sidebar_ctx(),
    }
    return templates.TemplateResponse(request, "ask.html", ctx)


@router.post("/ask", response_class=HTMLResponse)
async def ask_question(
    request: Request,
    question: Annotated[str, Form()],
    n_sources: Annotated[int, Form()] = 5,
    agent: Annotated[bool, Form()] = False,
) -> Response:
    """Return the answer partial (swapped into #answer-area by HTMX)."""
    q = question.strip()
    if not q:
        return templates.TemplateResponse(
            request,
            "partials/answer.html",
            {
                "request": request,
                "error": "La question ne peut pas être vide.",
                "answer": None,
                "chunks": [],
            },
        )

    top_k = max(3, min(n_sources, 10))
    try:
        sources = await asyncio.to_thread(active_sources)
        if agent:
            answer, chunks, steps = await asyncio.to_thread(answer_agentic, q, top_k, sources)
        else:
            answer, chunks = await asyncio.to_thread(
                answer_question, q, top_k=top_k, sources=sources
            )
            steps = []
    except Exception:
        # Never surface the raw exception: LLM SDK auth errors can echo back the API key.
        return templates.TemplateResponse(
            request,
            "partials/answer.html",
            {
                "request": request,
                "error": "Erreur lors de la génération de la réponse. Vérifie la configuration du fournisseur LLM dans ⚙️ Paramètres.",
                "answer": None,
                "chunks": [],
            },
        )

    return templates.TemplateResponse(
        request,
        "partials/answer.html",
        {"request": request, "answer": answer, "chunks": chunks, "steps": steps, "error": None},
    )

"""❓ Ask routes — RAG question-answering with source citations."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from rag_bachelor.app.web._deps import sidebar_ctx, templates
from rag_bachelor.core.qa import answer_question
from rag_bachelor.ingest.index import collection_count

router = APIRouter()


@router.get("/ask", response_class=HTMLResponse)
async def ask_page(request: Request) -> Response:
    ctx: dict[str, object] = {
        "request": request,
        "active_tab": "ask",
        "no_docs": collection_count() == 0,
        **sidebar_ctx(),
    }
    return templates.TemplateResponse(request, "ask.html", ctx)


@router.post("/ask", response_class=HTMLResponse)
async def ask_question(
    request: Request,
    question: Annotated[str, Form()],
    n_sources: Annotated[int, Form()] = 5,
) -> Response:
    """Return the answer partial (swapped into #answer-area by HTMX)."""
    q = question.strip()
    if not q:
        return templates.TemplateResponse(
            request,
            "partials/answer.html",
            {"request": request, "error": "La question ne peut pas être vide.", "answer": None, "chunks": []},
        )

    try:
        answer, chunks = answer_question(q, top_k=max(3, min(n_sources, 10)))
    except Exception as exc:
        return templates.TemplateResponse(
            request,
            "partials/answer.html",
            {"request": request, "error": str(exc), "answer": None, "chunks": []},
        )

    return templates.TemplateResponse(
        request,
        "partials/answer.html",
        {"request": request, "answer": answer, "chunks": chunks, "error": None},
    )

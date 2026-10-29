"""📊 Progress routes — per-topic mastery and card statistics."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from rag_bachelor.app.web._deps import sidebar_ctx, templates
from rag_bachelor.study.stats import get_activity, get_topic_stats, reading
from rag_bachelor.study.store import get_all_cards, get_due_cards
from rag_bachelor.study.subjects import active_subject

router = APIRouter()


@router.get("/progress", response_class=HTMLResponse)
async def progress_page(request: Request) -> Response:
    subject = active_subject()
    all_cards = get_all_cards(subject=subject)
    due_cards = get_due_cards(limit=200, subject=subject)
    stats = get_topic_stats(subject)

    avg_ease = sum(c.ease_factor for c in all_cards) / len(all_cards) if all_cards else None

    ctx: dict[str, object] = {
        "request": request,
        "active_tab": "progress",
        "total_cards": len(all_cards),
        "due_count": len(due_cards),
        "avg_ease": round(avg_ease, 2) if avg_ease is not None else None,
        "stats": stats,
        "activity": get_activity(all_cards, subject),
        "rings": stats[:3],
        "reading": reading(stats),
        **sidebar_ctx(),
    }
    return templates.TemplateResponse(request, "progress.html", ctx)

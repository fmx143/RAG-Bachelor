"""📊 Progress routes — per-topic mastery and card statistics."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from rag_bachelor.app.web._deps import sidebar_ctx, templates
from rag_bachelor.study.stats import get_activity, get_topic_stats
from rag_bachelor.study.store import get_all_cards, get_due_cards, list_exams
from rag_bachelor.study.subjects import active_subject

router = APIRouter()


@router.get("/progress", response_class=HTMLResponse)
async def progress_page(request: Request) -> Response:
    subject = active_subject()
    all_cards = get_all_cards(subject=subject)
    due_cards = get_due_cards(limit=200, subject=subject)
    stats = get_topic_stats(subject)

    ctx: dict[str, object] = {
        "request": request,
        "active_tab": "progress",
        "total_cards": len(all_cards),
        "due_count": len(due_cards),
        "stats": stats,
        "activity": get_activity(all_cards, subject),
        "rings": stats[:3],
        # stats are weakest first: the 3 best (best first), then the 3 weakest not already shown.
        "best": stats[::-1][:3],
        "worst": [s for s in stats if s not in stats[::-1][:3]][:3],
        "exams": [e for e in list_exams(subject, 10) if e.finished_at],
        **sidebar_ctx(),
    }
    return templates.TemplateResponse(request, "progress.html", ctx)

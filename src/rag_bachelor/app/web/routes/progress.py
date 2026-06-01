"""📊 Progress routes — per-topic mastery and card statistics."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from rag_bachelor.app.web._deps import sidebar_ctx, templates
from rag_bachelor.study.stats import get_topic_stats
from rag_bachelor.study.store import get_all_cards, get_due_cards

router = APIRouter()


@router.get("/progress", response_class=HTMLResponse)
async def progress_page(request: Request) -> Response:
    all_cards = get_all_cards()
    due_cards = get_due_cards(limit=200)
    stats = get_topic_stats()

    avg_ease = (
        sum(c.ease_factor for c in all_cards) / len(all_cards) if all_cards else None
    )

    ctx: dict[str, object] = {
        "request": request,
        "active_tab": "progress",
        "total_cards": len(all_cards),
        "due_count": len(due_cards),
        "avg_ease": round(avg_ease, 2) if avg_ease is not None else None,
        "stats": stats,
        "weak": [s for s in stats if s.mastery_pct < 40],
        "strong": [s for s in stats if s.mastery_pct >= 70],
        **sidebar_ctx(),
    }
    return templates.TemplateResponse(request, "progress.html", ctx)

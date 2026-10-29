"""Home route — today's session at a glance, weak topics and shortcuts to the study aids."""

from __future__ import annotations

import asyncio
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from rag_bachelor.app.web._deps import sidebar_ctx, templates
from rag_bachelor.study.stats import get_activity, get_topic_stats
from rag_bachelor.study.store import document_paths, get_all_cards, get_due_cards
from rag_bachelor.study.subjects import active_subject, subject_of

router = APIRouter()

_SECONDS_PER_CARD = 40  # ponytail: flat estimate for the "≈ N min" hint, no per-card timing
_DAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
_MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
           "septembre", "octobre", "novembre", "décembre"]  # fmt: skip


def _greeting(now: datetime) -> str:
    return "Bonjour." if now.hour < 18 else "Bonsoir."


def _kicker(now: datetime) -> str:
    return f"{_DAYS[now.weekday()].capitalize()} · {now.day} {_MONTHS[now.month - 1]}"


@router.get("/", response_class=HTMLResponse)
async def home_page(request: Request) -> Response:
    subject = active_subject()

    def _load() -> dict[str, object]:
        cards = get_all_cards(subject=subject)
        due = len(get_due_cards(limit=200, subject=subject))
        topics = get_topic_stats(subject)
        weak = [t for t in topics if t.total_cards][:3]  # sorted by mastery ascending
        return {
            "due": due,
            "minutes": max(1, round(due * _SECONDS_PER_CARD / 60)) if due else 0,
            "activity": get_activity(cards, subject),
            "total_cards": len(cards),
            "weak": weak,
            "n_docs": sum(1 for p in document_paths() if subject_of(p) == subject),
        }

    data = await asyncio.to_thread(_load)
    now = datetime.now()
    ctx: dict[str, object] = {
        "request": request,
        "active_tab": "home",
        "greeting": _greeting(now),
        "kicker": _kicker(now),
        **data,
        **sidebar_ctx(),
    }
    return templates.TemplateResponse(request, "home.html", ctx)

"""Home route — today's session at a glance, weak topics and shortcuts to the study aids."""

from __future__ import annotations

import asyncio
import math
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from rag_bachelor.app.web._deps import doc_hues, sidebar_ctx, templates
from rag_bachelor.core.graph import fact_counts, shared_links
from rag_bachelor.study.stats import TopicStats, get_activity, get_topic_stats
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


def _graph_view(topics: list[TopicStats]) -> dict[str, object]:
    """Knowledge-map layout: topics on an ellipse, linked when their documents share graph entities."""
    try:
        facts, shared = fact_counts(), shared_links()
    except Exception:  # noqa: BLE001  # the fact graph is optional: Chroma may be down or unbuilt
        facts, shared = {}, {}
    n = len(topics)
    hues = doc_hues(topics)
    nodes = []
    for i, t in enumerate(topics):
        a = i / n * math.tau - math.pi / 2
        r = min(14 + math.sqrt(t.total_cards) * 2.2, 30)
        nodes.append({"t": t, "x": 380 + 270 * math.cos(a), "y": 150 + 105 * math.sin(a), "r": r,
                      "facts": facts.get(t.topic, 0), "h": hues[t.topic]})  # fmt: skip
    idx = {t.topic: i for i, t in enumerate(topics)}
    links = [(idx[a], idx[b], k) for (a, b), k in shared.items() if a in idx and b in idx]
    return {"nodes": nodes, "links": links, "total_facts": sum(facts.get(t.topic, 0) for t in topics)}


@router.get("/", response_class=HTMLResponse)
async def home_page(request: Request) -> Response:
    subject = active_subject()

    def _load() -> dict[str, object]:
        cards = get_all_cards(subject=subject)
        due = len(get_due_cards(limit=200, subject=subject))
        topics = get_topic_stats(subject)
        with_cards = [t for t in topics if t.total_cards]
        weak = with_cards[:3]  # sorted by mastery ascending
        return {
            "due": due,
            "minutes": max(1, round(due * _SECONDS_PER_CARD / 60)) if due else 0,
            "activity": get_activity(cards, subject),
            "total_cards": len(cards),
            "weak": weak,
            "topics": with_cards,
            "graph": _graph_view(with_cards),
            "hues": doc_hues(with_cards),
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

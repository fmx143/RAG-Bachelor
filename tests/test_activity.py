"""Progress-page chart data: streak, retention, heatmap shape, forecast and maturity."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from rag_bachelor.app.web.routes import progress as progress_module
from rag_bachelor.study import store
from rag_bachelor.study.srs import Card
from rag_bachelor.study.stats import BAR_DAYS, FORECAST_DAYS, HEATMAP_WEEKS, _build_activity

TODAY = date(2026, 10, 29)  # a Thursday


def _card(**kw: object) -> Card:
    return Card(id=1, question="q", answer="a", topic="Bachelor/a.pdf", difficulty="facile", **kw)  # type: ignore[arg-type]


def test_streak_retention_forecast_and_maturity() -> None:
    days = {
        TODAY: (4, 3),
        TODAY - timedelta(days=1): (2, 1),
        TODAY - timedelta(days=3): (5, 5),  # gap yesterday-1 breaks the streak
    }
    cards = [
        _card(due_date=TODAY),  # new, due today
        _card(repetitions=3, interval=30, due_date=TODAY + timedelta(days=2)),  # mature
        _card(repetitions=1, interval=3, due_date=TODAY - timedelta(days=5)),  # overdue
    ]
    start = TODAY - timedelta(days=TODAY.weekday() + 7 * (HEATMAP_WEEKS - 1))

    a = _build_activity(days, cards, TODAY, start)

    assert a.streak == 2
    assert a.reviews_30d == 11 and a.retention_pct == round(100 * 9 / 11)
    assert len(a.heatmap) == HEATMAP_WEEKS and all(len(w) == 7 for w in a.heatmap)
    assert len(a.bars) == BAR_DAYS and len(a.forecast) == FORECAST_DAYS
    assert a.forecast[0][1] == 2 and a.forecast[2][1] == 1  # overdue counted today
    assert [n for _, n, _ in a.maturity] == [1, 1, 1]


def test_empty_history_has_no_streak_or_retention() -> None:
    a = _build_activity({}, [], TODAY, TODAY - timedelta(days=77))
    assert a.streak == 0 and a.retention_pct is None and a.bar_max == 0


def test_progress_page_renders_charts() -> None:
    card = store.get_card(store.add_card("Q ?", "R.", topic="Bachelor/a.pdf", difficulty="facile"))
    assert card is not None
    store.save_review(card, 4)
    app = FastAPI()
    app.include_router(progress_module.router)

    html = TestClient(app).get("/progress").text

    assert 'class="o-heat"' in html and 'class="o-ring"' in html and "7 derniers jours" in html


def test_progress_lists_the_three_best_and_three_weakest_topics() -> None:
    from rag_bachelor.app.web import server  # noqa: F401 — registers the template globals
    from rag_bachelor.study.stats import TopicStats

    long_path = "Bachelor/Livres/Semestre 3/Comptabilité/9 - Livre - Comptabilité analytique.pdf"
    stats = [  # weakest first, as get_topic_stats returns them
        TopicStats(f"Bachelor/Livres/t{i}.pdf", 2, 0, 3.0, float(10 * i)) for i in range(6)
    ] + [TopicStats(long_path, 2, 1, 3.0, 95.0)]
    app = FastAPI()
    app.include_router(progress_module.router)
    with patch.object(progress_module, "get_topic_stats", return_value=stats):
        html = TestClient(app).get("/progress").text
    best = html.split("Les mieux maîtrisés", 1)[1].split("À retravailler", 1)[0]
    worst = html.split("À retravailler", 1)[1].split("À venir", 1)[0]
    assert best.index("Comptabilité analytique") < best.index("t5") < best.index("t4")
    assert all(f"t{i}<" in worst for i in (0, 1, 2)) and "t5<" not in worst
    assert "Ton point fort" not in html  # the long generated remarks are gone
    with patch.object(progress_module, "get_topic_stats", return_value=stats[:2]):
        html = TestClient(app).get("/progress").text
    assert "Les mieux maîtrisés" in html and "À retravailler" not in html

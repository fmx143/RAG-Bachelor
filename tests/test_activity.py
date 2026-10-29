"""Progress-page chart data: streak, retention, heatmap shape, forecast and maturity."""

from __future__ import annotations

from datetime import date, timedelta

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
        _card(),  # new, due today
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


def test_reading_picks_best_stuck_and_busiest_topics() -> None:
    from rag_bachelor.study.stats import TopicStats, reading

    def ts(topic: str, due: int, interval: float, mastery: float) -> TopicStats:
        return TopicStats(topic, 5, due, 2.0, interval, mastery)

    out = reading([ts("S/a.pdf", 4, 1.0, 20), ts("S/b.pdf", 0, 9.0, 80)])

    assert out == [
        "Ton point fort : b.pdf (80 % de maîtrise).",
        "a.pdf stagne : les cartes reviennent encore chaque jour.",
        "4 carte(s) de a.pdf attendent ta révision.",
    ]
    assert reading([]) == []

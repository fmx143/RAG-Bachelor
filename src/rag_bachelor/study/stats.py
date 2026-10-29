"""Per-topic mastery statistics derived from the SM-2 card state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from rag_bachelor.study.srs import Card
from rag_bachelor.study.store import get_review_days, get_topic_stats_rows

# Ease factor bounds used for mastery normalisation (SM-2 range ≈ 1.3–3.5)
_EASE_MIN = 1.3
_EASE_MAX = 3.5


@dataclass
class TopicStats:
    topic: str
    total_cards: int
    due_cards: int  # cards due today or overdue
    avg_ease: float  # average ease factor across all cards in this topic
    avg_interval: float  # average current interval (days)
    mastery_pct: float  # 0–100; higher = more mastered


def get_topic_stats(subject: str | None = None) -> list[TopicStats]:
    """Return one :class:`TopicStats` per topic, sorted by mastery ascending.

    Topics with lower mastery are listed first so weak subjects are prominent
    in the UI.
    """
    rows = get_topic_stats_rows(subject)

    stats: list[TopicStats] = []
    for r in rows:
        avg_ease = r["avg_ease"] or _EASE_MIN
        # Normalise ease into 0–100 % mastery
        mastery = (avg_ease - _EASE_MIN) / (_EASE_MAX - _EASE_MIN) * 100.0
        mastery = min(100.0, max(0.0, mastery))

        stats.append(
            TopicStats(
                topic=r["topic"],
                total_cards=r["total"],
                due_cards=r["due"] or 0,
                avg_ease=avg_ease,
                avg_interval=r["avg_interval"] or 1.0,
                mastery_pct=mastery,
            )
        )

    # Weakest first
    stats.sort(key=lambda s: s.mastery_pct)
    return stats


HEATMAP_WEEKS = 12
BAR_DAYS = 30
FORECAST_DAYS = 14
_MATURE_DAYS = 21  # interval from which a card counts as mastered (Anki's convention)


@dataclass
class Activity:
    streak: int  # consecutive days with a review, ending today (or yesterday)
    retention_pct: int | None  # share of reviews graded >= 3 over the last 30 days
    reviews_30d: int
    heatmap: list[list[tuple[date, int]]]  # columns = weeks (Mon..Sun), count per day
    heat_max: int
    bars: list[tuple[date, int, int]]  # (day, reviews, successes) for the last BAR_DAYS days
    bar_max: int
    forecast: list[tuple[date, int]]  # cards falling due per day, today (incl. overdue) first
    forecast_max: int
    maturity: list[tuple[str, int, str]]  # (label, cards, css colour class)


def get_activity(
    cards: list[Card], subject: str | None = None, today: date | None = None
) -> Activity:
    """Chart data for the progress page, from review history and the SM-2 state of *cards*."""
    today = today or date.today()
    # Heatmap starts on the Monday HEATMAP_WEEKS-1 weeks back, so columns are whole weeks.
    start = today - timedelta(days=today.weekday() + 7 * (HEATMAP_WEEKS - 1))
    days = {d: (n, ok) for d, n, ok in get_review_days(start, subject)}
    return _build_activity(days, cards, today, start)


def _build_activity(
    days: dict[date, tuple[int, int]], cards: list[Card], today: date, start: date
) -> Activity:
    def count(d: date) -> int:
        return days.get(d, (0, 0))[0]

    heatmap = [
        [(d, count(d)) for d in (start + timedelta(days=7 * w + i) for i in range(7))]
        for w in range(HEATMAP_WEEKS)
    ]
    streak, d = 0, today if count(today) else today - timedelta(days=1)
    while count(d):
        streak, d = streak + 1, d - timedelta(days=1)

    bars = [
        (d, *days.get(d, (0, 0)))
        for d in (today - timedelta(days=i) for i in range(BAR_DAYS - 1, -1, -1))
    ]
    n30 = sum(b[1] for b in bars)
    forecast = [
        (today + timedelta(days=i), sum(1 for c in cards if _due_on(c, today, i)))
        for i in range(FORECAST_DAYS)
    ]
    new = sum(1 for c in cards if c.repetitions == 0)
    mature = sum(1 for c in cards if c.repetitions > 0 and c.interval >= _MATURE_DAYS)
    return Activity(
        streak=streak,
        retention_pct=round(100 * sum(b[2] for b in bars) / n30) if n30 else None,
        reviews_30d=n30,
        heatmap=heatmap,
        heat_max=max((n for week in heatmap for _, n in week), default=0),
        bars=bars,
        bar_max=max((b[1] for b in bars), default=0),
        forecast=forecast,
        forecast_max=max((n for _, n in forecast), default=0),
        maturity=[
            ("Nouvelles", new, "new"),
            ("En apprentissage", len(cards) - new - mature, "learning"),
            ("Maîtrisées", mature, "mature"),
        ],
    )


def _due_on(card: Card, today: date, offset: int) -> bool:
    """Due on day *today + offset*; day 0 also counts everything overdue."""
    return (
        card.due_date <= today if offset == 0 else card.due_date == today + timedelta(days=offset)
    )


def _short(topic: str) -> str:
    return topic.split("/", 1)[-1]


def reading(stats: list[TopicStats]) -> list[str]:
    """Up to three plain-language remarks on *stats* (weakest first), from fixed rules (no LLM)."""
    if not stats:
        return []
    out: list[str] = []
    best = stats[-1]
    if best.mastery_pct >= 50:
        out.append(f"Ton point fort : {_short(best.topic)} ({best.mastery_pct:.0f} % de maîtrise).")
    # ponytail: "stuck" = short average interval on a few cards; no per-card lapse history is read.
    stuck = [s for s in stats if s.total_cards >= 3 and s.avg_interval <= 1.5]
    if stuck:
        s = min(stuck, key=lambda s: s.mastery_pct)
        out.append(f"{_short(s.topic)} stagne : les cartes reviennent encore chaque jour.")
    busiest = max(stats, key=lambda s: s.due_cards)
    if busiest.due_cards:
        out.append(
            f"{busiest.due_cards} carte(s) de {_short(busiest.topic)} attendent ta révision."
        )
    return out

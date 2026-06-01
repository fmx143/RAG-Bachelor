"""Per-topic mastery statistics derived from the SM-2 card state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from rag_bachelor.study.store import get_conn

# Ease factor bounds used for mastery normalisation (SM-2 range ≈ 1.3–3.5)
_EASE_MIN = 1.3
_EASE_MAX = 3.5


@dataclass
class TopicStats:
    topic: str
    total_cards: int
    due_cards: int        # cards due today or overdue
    avg_ease: float       # average ease factor across all cards in this topic
    avg_interval: float   # average current interval (days)
    mastery_pct: float    # 0–100; higher = more mastered


def get_topic_stats() -> list[TopicStats]:
    """Return one :class:`TopicStats` per topic, sorted by mastery ascending.

    Topics with lower mastery are listed first so weak subjects are prominent
    in the UI.
    """
    conn = get_conn()
    today = date.today().isoformat()

    rows = conn.execute(
        """
        SELECT
            topic,
            COUNT(*)                                              AS total,
            SUM(CASE WHEN due_date <= :today THEN 1 ELSE 0 END)  AS due,
            AVG(ease_factor)                                      AS avg_ease,
            AVG(interval)                                         AS avg_interval
        FROM cards
        GROUP BY topic
        ORDER BY topic
        """,
        {"today": today},
    ).fetchall()

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

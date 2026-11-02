"""FSRS-5 spaced-repetition scheduler.

Reference: https://github.com/open-spaced-repetition/awesome-fsrs/wiki/The-Algorithm
(default 19 weights, forgetting curve ``R = (1 + 19/81 · t/S) ** -0.5``). The success / lapse
stability updates are the FSRS v4 ones, which FSRS-5 keeps.

Each card tracks a *stability* S (days for retrievability to fall to 90 %) and a *difficulty*
D in [1, 10]. A review at retrievability R updates both, and the next interval is the delay at
which R falls to ``settings.fsrs_desired_retention``.

App grade scale (0–5, the UI only sends 0 / 3 / 4 / 5) → FSRS rating:
  < 3 → Again (1) · 3 → Hard (2) · 4 → Good (3) · 5 → Easy (4)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

from rag_bachelor.config import settings

# Default FSRS-5 weights w0..w18 (no per-user optimiser: ponytail — add one only if the
# default schedule measurably misses the learner's real retention).
_W = (
    0.40255, 1.18385, 3.173, 15.69105, 7.1949, 0.5345, 1.4604, 0.0046, 1.54575, 0.1192,
    1.01925, 1.9395, 0.11, 0.29605, 2.2698, 0.2315, 2.9898, 0.51655, 0.6621,
)  # fmt: skip

_DECAY = -0.5
_FACTOR = 19 / 81  # makes R(S, S) = 0.9
_MAX_INTERVAL = 36_500  # days


@dataclass
class Card:
    id: int
    question: str
    answer: str
    topic: str
    difficulty: str  # "facile" | "moyen" | "difficile" (generation difficulty)

    # Question type: "free" | "cloze" | "mcq_single" | "mcq_multi" | "tf".
    # options / correct are only meaningful for structured types.
    qtype: str = "free"
    options: list[str] | None = None
    correct: list[int] | None = None

    # Schedule state
    interval: int = 1  # days until next review
    repetitions: int = 0  # number of consecutive successful reviews
    due_date: date = field(default_factory=date.today)

    # FSRS memory state: stability 0 = never reviewed
    stability: float = 0.0
    fsrs_difficulty: float = 0.0
    last_review: date | None = None


def retrievability(elapsed_days: int, stability: float) -> float:
    """Probability of recalling a card *elapsed_days* after its last review."""
    return float((1 + _FACTOR * elapsed_days / stability) ** _DECAY)


def retention(card: Card, today: date | None = None) -> float:
    """Predicted recall of *card* today (0 for a card never reviewed)."""
    if card.stability <= 0 or card.last_review is None:
        return 0.0
    return retrievability(((today or date.today()) - card.last_review).days, card.stability)


def _initial_difficulty(rating: int) -> float:
    return _W[4] - math.exp(_W[5] * (rating - 1)) + 1


def _clamp_difficulty(d: float) -> float:
    return min(10.0, max(1.0, d))


def _next_difficulty(d: float, rating: int) -> float:
    damped = d + -_W[6] * (rating - 3) * (10 - d) / 9  # linear damping
    return _clamp_difficulty(_W[7] * _initial_difficulty(4) + (1 - _W[7]) * damped)


def _next_stability(d: float, s: float, r: float, rating: int) -> float:
    if rating == 1:  # lapse: never above the previous stability
        lapse = _W[11] * d ** -_W[12] * ((s + 1) ** _W[13] - 1) * math.exp(_W[14] * (1 - r))
        return float(min(lapse, s))
    hard = _W[15] if rating == 2 else 1.0
    easy = _W[16] if rating == 4 else 1.0
    gain = math.exp(_W[8]) * (11 - d) * s ** -_W[9] * (math.exp(_W[10] * (1 - r)) - 1)
    return float(s * (1 + gain * hard * easy))


def update_card(card: Card, grade: int, today: date | None = None) -> Card:
    """Apply FSRS to *card* for the given *grade* (0–5), reviewed on *today*.

    Mutates *card* in place and also returns it for convenience.
    ponytail: day-granular — no intra-day learning steps, so a failed card returns after 1 day.
    """
    if not 0 <= grade <= 5:
        raise ValueError(f"Grade must be 0–5, got {grade}")
    today = today or date.today()
    rating = 1 if grade < 3 else grade - 1

    if card.stability <= 0 or card.last_review is None:
        card.stability = _W[rating - 1]
        card.fsrs_difficulty = _clamp_difficulty(_initial_difficulty(rating))
    else:
        r = retrievability(max(0, (today - card.last_review).days), card.stability)
        # Stability uses the difficulty *before* this review's update.
        card.stability = _next_stability(card.fsrs_difficulty, card.stability, r, rating)
        card.fsrs_difficulty = _next_difficulty(card.fsrs_difficulty, rating)
    card.stability = max(card.stability, 0.01)

    ideal = card.stability / _FACTOR * (settings.fsrs_desired_retention ** (1 / _DECAY) - 1)
    # A lapse is seen again tomorrow whatever its stability (no relearning steps to bridge it).
    card.interval = 1 if rating == 1 else min(_MAX_INTERVAL, max(1, round(ideal)))
    card.repetitions = 0 if rating == 1 else card.repetitions + 1
    card.last_review = today
    card.due_date = today + timedelta(days=card.interval)
    return card

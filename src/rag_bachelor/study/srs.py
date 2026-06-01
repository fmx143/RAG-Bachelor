"""SM-2 spaced-repetition algorithm.

Reference: Piotr Wozniak's original SM-2 specification.
https://www.supermemo.com/en/blog/application-of-a-computer-to-improve-the-results-obtained-in-working-with-the-supermemo-method

Grade scale (0–5):
  0 – Complete blackout (wrong answer, hard to recall even after)
  1 – Incorrect response, but upon seeing the answer it felt familiar
  2 – Incorrect response, but the answer was easy to recall once seen
  3 – Correct response, recalled with serious difficulty
  4 – Correct response, recalled after a moment of hesitation
  5 – Perfect response, immediate and accurate
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

# Minimum ease factor — SM-2 lower bound
_MIN_EASE = 1.3

# Initial ease factor — SM-2 starting value
_INITIAL_EASE = 2.5


@dataclass
class Card:
    id: int
    question: str
    answer: str
    topic: str
    difficulty: str  # "facile" | "moyen" | "difficile" (generation difficulty)

    # SM-2 state
    interval: int = 1          # days until next review
    repetitions: int = 0       # number of consecutive successful reviews
    ease_factor: float = field(default=_INITIAL_EASE)
    due_date: date = field(default_factory=date.today)


def update_card(card: Card, grade: int) -> Card:
    """Apply SM-2 to *card* for the given *grade* (0–5).

    Mutates *card* in place and also returns it for convenience.
    """
    if not 0 <= grade <= 5:
        raise ValueError(f"Grade must be 0–5, got {grade}")

    if grade < 3:
        # Failed review: reset progress but keep the ease factor penalty
        card.repetitions = 0
        card.interval = 1
    else:
        # Passed
        if card.repetitions == 0:
            card.interval = 1
        elif card.repetitions == 1:
            card.interval = 6
        else:
            card.interval = math.ceil(card.interval * card.ease_factor)
        card.repetitions += 1

    # Update ease factor (SM-2 formula)
    card.ease_factor = max(
        _MIN_EASE,
        card.ease_factor + 0.1 - (5 - grade) * (0.08 + (5 - grade) * 0.02),
    )

    card.due_date = date.today() + timedelta(days=card.interval)
    return card

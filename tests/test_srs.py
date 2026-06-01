"""Tests for the SM-2 spaced-repetition algorithm."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from rag_bachelor.study.srs import _INITIAL_EASE, _MIN_EASE, Card, update_card


def _card(**kwargs) -> Card:  # type: ignore[no-untyped-def]
    defaults = dict(id=1, question="Q?", answer="A", topic="maths", difficulty="moyen")
    defaults.update(kwargs)
    return Card(**defaults)


# ── Grade validation ──────────────────────────────────────────────────────────


def test_invalid_grade_raises() -> None:
    card = _card()
    with pytest.raises(ValueError):
        update_card(card, grade=6)
    with pytest.raises(ValueError):
        update_card(card, grade=-1)


# ── Failed review (grade < 3) ─────────────────────────────────────────────────


def test_failed_review_resets_repetitions() -> None:
    card = _card(repetitions=5, interval=30)
    update_card(card, grade=0)
    assert card.repetitions == 0


def test_failed_review_resets_interval_to_one() -> None:
    card = _card(repetitions=3, interval=20)
    update_card(card, grade=2)
    assert card.interval == 1


def test_failed_review_due_tomorrow() -> None:
    card = _card()
    update_card(card, grade=0)
    assert card.due_date == date.today() + timedelta(days=1)


# ── Successful review (grade ≥ 3) ─────────────────────────────────────────────


def test_first_success_interval_is_one() -> None:
    card = _card(repetitions=0)
    update_card(card, grade=3)
    assert card.interval == 1
    assert card.repetitions == 1


def test_second_success_interval_is_six() -> None:
    card = _card(repetitions=1, interval=1)
    update_card(card, grade=4)
    assert card.interval == 6
    assert card.repetitions == 2


def test_third_success_multiplies_by_ease() -> None:
    card = _card(repetitions=2, interval=6, ease_factor=2.5)
    update_card(card, grade=5)
    assert card.interval == 15  # ceil(6 * 2.5) = 15
    assert card.repetitions == 3


def test_multiple_successes_grow_interval() -> None:
    card = _card()
    prev_interval = card.interval
    for _ in range(5):
        update_card(card, grade=4)
    assert card.interval > prev_interval


# ── Ease factor ───────────────────────────────────────────────────────────────


def test_perfect_score_increases_ease() -> None:
    card = _card(ease_factor=_INITIAL_EASE)
    update_card(card, grade=5)
    assert card.ease_factor > _INITIAL_EASE


def test_low_passing_grade_decreases_ease() -> None:
    card = _card(ease_factor=_INITIAL_EASE)
    update_card(card, grade=3)
    assert card.ease_factor < _INITIAL_EASE


def test_ease_never_falls_below_minimum() -> None:
    card = _card(ease_factor=1.31)
    for _ in range(10):
        update_card(card, grade=0)
    assert card.ease_factor >= _MIN_EASE


# ── Due date ──────────────────────────────────────────────────────────────────


def test_due_date_in_future_after_success() -> None:
    card = _card(repetitions=2, interval=6, ease_factor=2.5)
    update_card(card, grade=4)
    assert card.due_date > date.today()


def test_due_date_is_date_today_plus_interval() -> None:
    card = _card(repetitions=0)
    update_card(card, grade=5)
    assert card.due_date == date.today() + timedelta(days=card.interval)

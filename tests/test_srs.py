"""Tests for the FSRS-5 scheduler."""

from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from rag_bachelor.config import settings
from rag_bachelor.study.srs import Card, retention, retrievability, update_card

D0 = date(2026, 1, 1)


def _card(**kwargs) -> Card:  # type: ignore[no-untyped-def]
    defaults = dict(id=1, question="Q?", answer="A", topic="maths", difficulty="moyen")
    defaults.update(kwargs)
    return Card(**defaults)


def _reviewed(grade: int, on: date = D0) -> Card:
    return update_card(_card(), grade, on)


def test_invalid_grade_raises() -> None:
    for bad in (6, -1):
        with pytest.raises(ValueError):
            update_card(_card(), grade=bad)


def test_first_review_uses_published_initial_values() -> None:
    # Hand-computed from the FSRS-5 defaults: S0 = w[rating-1], D0 = w4 - e^(w5·(G-1)) + 1.
    good = _reviewed(4)
    assert good.stability == pytest.approx(3.173)
    assert good.fsrs_difficulty == pytest.approx(7.1949 - math.exp(0.5345 * 2) + 1)
    assert good.interval == 3
    assert _reviewed(5).interval == 16
    assert _reviewed(3).interval == 1  # S0 = 1.18385 → 1 day


def test_first_review_intervals_grow_with_grade() -> None:
    intervals = [_reviewed(g).stability for g in (0, 3, 4, 5)]
    assert intervals == sorted(intervals)


def test_interval_equals_stability_at_ninety_percent_retention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "fsrs_desired_retention", 0.9)
    card = _reviewed(5)
    assert card.interval == round(card.stability)
    assert retrievability(card.interval, card.stability) == pytest.approx(0.9, abs=0.01)


def test_higher_desired_retention_shortens_intervals(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "fsrs_desired_retention", 0.97)
    assert _reviewed(5).interval < 16


def test_success_on_time_raises_stability_and_lapse_lowers_it() -> None:
    card = _reviewed(4)
    before = card.stability
    on_time = card.due_date
    assert update_card(_card(**vars(card)), 4, on_time).stability > before
    assert update_card(_card(**vars(card)), 0, on_time).stability < before


def test_easy_beats_good_beats_hard_on_a_second_review() -> None:
    stabilities = []
    for grade in (3, 4, 5):
        card = _reviewed(4)
        stabilities.append(update_card(card, grade, card.due_date).stability)
    assert stabilities == sorted(stabilities)


def test_lapse_resets_repetitions_and_schedules_tomorrow() -> None:
    card = _reviewed(4)
    update_card(card, 4, card.due_date)
    assert card.repetitions == 2
    update_card(card, 0, card.due_date)
    assert (card.repetitions, card.interval) == (0, 1)


def test_legacy_grade_2_counts_as_a_lapse() -> None:
    card = _reviewed(4)
    update_card(card, 2, card.due_date)
    assert card.repetitions == 0


def test_difficulty_stays_within_bounds() -> None:
    card = _reviewed(0)
    day = D0
    for _ in range(60):
        day += timedelta(days=1)
        update_card(card, 0, day)
    assert 1.0 <= card.fsrs_difficulty <= 10.0
    for _ in range(60):
        day += timedelta(days=card.interval)
        update_card(card, 5, day)
    assert 1.0 <= card.fsrs_difficulty <= 10.0


def test_reviewing_late_gains_more_stability_than_on_time() -> None:
    on_time, late = _reviewed(4), _reviewed(4)
    update_card(on_time, 4, on_time.due_date)
    update_card(late, 4, late.due_date + timedelta(days=20))
    assert late.stability > on_time.stability


def test_due_date_follows_interval() -> None:
    card = _reviewed(5)
    assert card.due_date == D0 + timedelta(days=card.interval)
    assert card.last_review == D0


def test_retention_is_zero_for_new_cards_and_decays() -> None:
    assert retention(_card()) == 0.0
    card = _reviewed(4)
    assert retention(card, D0) == pytest.approx(1.0)
    assert retention(card, D0 + timedelta(days=3)) == pytest.approx(0.9, abs=0.01)
    assert retention(card, D0 + timedelta(days=30)) < retention(card, D0 + timedelta(days=3))

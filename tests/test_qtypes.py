"""Tests for structured question-type validation and parsing (core/qtypes.py)."""

from __future__ import annotations

from rag_bachelor.core import qtypes

# ── mcq_single ───────────────────────────────────────────────────────────────


def test_mcq_single_valid() -> None:
    raw = (
        '{"items": [{"question": "Q ?", "options": ["A", "B", "C"], "correct": [1], '
        '"answer": "car B.", "difficulty": "facile"}]}'
    )
    items = qtypes.parse_structured_items(raw, "mcq_single")
    assert items == [
        {
            "question": "Q ?",
            "answer": "car B.",
            "difficulty": "facile",
            "qtype": "mcq_single",
            "options": ["A", "B", "C"],
            "correct": [1],
        }
    ]


def test_mcq_single_rejects_multiple_correct() -> None:
    raw = '{"items": [{"question": "Q ?", "options": ["A", "B"], "correct": [0, 1]}]}'
    assert qtypes.parse_structured_items(raw, "mcq_single") == []


def test_mcq_single_rejects_out_of_range_index() -> None:
    raw = '{"items": [{"question": "Q ?", "options": ["A", "B"], "correct": [2]}]}'
    assert qtypes.parse_structured_items(raw, "mcq_single") == []


def test_mcq_single_rejects_too_few_options() -> None:
    raw = '{"items": [{"question": "Q ?", "options": ["A"], "correct": [0]}]}'
    assert qtypes.parse_structured_items(raw, "mcq_single") == []


# ── mcq_multi ────────────────────────────────────────────────────────────────


def test_mcq_multi_valid_multiple_correct() -> None:
    raw = '{"items": [{"question": "Q ?", "options": ["A", "B", "C"], "correct": [0, 2]}]}'
    items = qtypes.parse_structured_items(raw, "mcq_multi")
    assert items[0]["correct"] == [0, 2]
    assert items[0]["qtype"] == "mcq_multi"


def test_mcq_multi_requires_at_least_one_correct() -> None:
    raw = '{"items": [{"question": "Q ?", "options": ["A", "B"], "correct": []}]}'
    assert qtypes.parse_structured_items(raw, "mcq_multi") == []


def test_mcq_multi_with_single_correct_is_relabeled_mcq_single() -> None:
    raw = '{"items": [{"question": "Q ?", "options": ["A", "B", "C"], "correct": [1]}]}'
    items = qtypes.parse_structured_items(raw, "mcq_multi")
    assert items[0]["qtype"] == "mcq_single"
    assert items[0]["correct"] == [1]


# ── tf (Vrai/Faux) ───────────────────────────────────────────────────────────


def test_tf_valid_forces_vrai_faux_options() -> None:
    raw = '{"items": [{"question": "Affirmation.", "correct": [0], "answer": "car..."}]}'
    items = qtypes.parse_structured_items(raw, "tf")
    assert items == [
        {
            "question": "Affirmation.",
            "answer": "car...",
            "difficulty": "moyen",
            "qtype": "tf",
            "options": ["Vrai", "Faux"],
            "correct": [0],
        }
    ]


def test_tf_rejects_out_of_range_correct() -> None:
    raw = '{"items": [{"question": "Affirmation.", "correct": [2]}]}'
    assert qtypes.parse_structured_items(raw, "tf") == []


def test_tf_rejects_multiple_correct() -> None:
    raw = '{"items": [{"question": "Affirmation.", "correct": [0, 1]}]}'
    assert qtypes.parse_structured_items(raw, "tf") == []


# ── malformed input ────────────────────────────────────────────────────────────


def test_parse_structured_items_garbage_returns_empty() -> None:
    assert qtypes.parse_structured_items("désolé, je ne peux pas", "mcq_single") == []
    assert qtypes.parse_structured_items('{"items": "pas une liste"}', "tf") == []


def test_parse_structured_items_drops_non_dict_entries() -> None:
    raw = '{"items": ["not a dict", {"question": "Q ?", "correct": [0]}]}'
    assert qtypes.parse_structured_items(raw, "tf") == [
        {
            "question": "Q ?",
            "answer": "",
            "difficulty": "moyen",
            "qtype": "tf",
            "options": ["Vrai", "Faux"],
            "correct": [0],
        }
    ]

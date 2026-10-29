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


def test_loads_object_accepts_raw_newlines_inside_strings() -> None:
    raw = '{"action": "answer", "text": "1. un\n2. deux\tfin"}'  # what a Markdown answer looks like

    assert qtypes.loads_object(raw) == {"action": "answer", "text": "1. un\n2. deux\tfin"}


def test_cloze_valid_normalizes_blanks() -> None:
    item = qtypes.validate_item(
        {
            "question": "Le protocole ___ est fiable, ... non.",
            "answer": "TCP",
            "difficulty": "facile",
        },
        "cloze",
    )
    assert item is not None
    assert item["question"] == "Le protocole ____ est fiable, ... non."
    assert item["qtype"] == "cloze" and item["options"] is None and item["correct"] is None


def test_cloze_blank_and_term_counts_must_match() -> None:
    entry = {"question": "Le ____ et le ____ sont des protocoles.", "answer": "TCP"}
    assert qtypes.validate_item(entry, "cloze") is None
    entry = {"question": "Le ____ et le ____ sont des protocoles.", "answer": "TCP ; UDP"}
    item = qtypes.validate_item(entry, "cloze")
    assert item is not None and item["answer"] == "TCP ; UDP"


def test_cloze_rejects_leaked_term_and_missing_blank() -> None:
    assert (
        qtypes.validate_item({"question": "TCP est ____ et fiable.", "answer": "TCP"}, "cloze")
        is None
    )
    assert qtypes.validate_item({"question": "Aucun trou ici.", "answer": "trou"}, "cloze") is None

"""Shared question-type definitions, prompt fragments, and LLM-output parsing.

Four question types are supported end to end (generation, storage, revision):
``free`` (texte libre, auto-évalué), ``mcq_single`` (QCM une réponse),
``mcq_multi`` (QCM plusieurs réponses) et ``tf`` (Vrai/Faux). The three
structured types are auto-graded — no LLM call needed at review time.

This module centralizes what used to be duplicated between
``core/questions.py`` and ``core/bank.py``: the fence-strip + regex JSON
parsing, and now the per-item validation for structured types.
"""

from __future__ import annotations

import json
import re
from typing import TypedDict

QTYPES: tuple[str, ...] = ("free", "mcq_single", "mcq_multi", "tf")

QTYPE_LABELS: dict[str, str] = {
    "free": "Texte libre",
    "mcq_single": "QCM — une réponse",
    "mcq_multi": "QCM — plusieurs réponses",
    "tf": "Vrai / Faux",
}

DIFFICULTIES: tuple[str, ...] = ("facile", "moyen", "difficile")

_TF_OPTIONS = ["Vrai", "Faux"]

# Regex to extract a JSON object from potentially noisy LLM output.
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


class QuestionItem(TypedDict):
    """A normalized, validated question ready to store."""

    question: str
    answer: str  # model answer (free) or explanation (structured)
    difficulty: str
    qtype: str
    options: list[str] | None
    correct: list[int] | None


# ── Prompt fragments ────────────────────────────────────────────────────────

# Per-type description of the exact JSON shape one item must have, embedded
# into the system prompts of both generators.
_ITEM_SCHEMAS: dict[str, str] = {
    "free": (
        '{"question": "...", "answer": "...", "difficulty": "facile|moyen|difficile"}\n'
        '"answer" est la réponse modèle, complète et exacte.'
    ),
    "mcq_single": (
        '{"question": "...", "options": ["...", "...", "...", "..."], '
        '"correct": [i], "answer": "...", "difficulty": "facile|moyen|difficile"}\n'
        'Propose 3 à 5 options plausibles. "correct" contient l\'indice (0-based) '
        "de l'unique bonne réponse. \"answer\" justifie brièvement la bonne réponse."
    ),
    "mcq_multi": (
        '{"question": "...", "options": ["...", "...", "...", "..."], '
        '"correct": [i, j, ...], "answer": "...", "difficulty": "facile|moyen|difficile"}\n'
        'Formule la question au pluriel (« Quels sont… ? », « Lesquelles de ces '
        'affirmations sont exactes ? ») pour que PLUSIEURS options soient vraies. '
        'Propose 4 ou 5 options plausibles dont OBLIGATOIREMENT 2 ou 3 correctes — '
        'une seule bonne réponse est une erreur de format. "correct" contient les '
        'indices (0-based) de toutes les bonnes réponses. "answer" justifie '
        'brièvement chacune.'
    ),
    "tf": (
        '{"question": "affirmation à évaluer", "correct": [0] si vraie ou [1] si '
        'fausse, "answer": "...", "difficulty": "facile|moyen|difficile"}\n'
        '"question" est une affirmation (pas une question ouverte). "answer" '
        "justifie pourquoi l'affirmation est vraie ou fausse."
    ),
}


def item_schema_instructions(qtype: str) -> str:
    """Return the French JSON-shape instructions for one item of *qtype*."""
    return _ITEM_SCHEMAS.get(qtype, _ITEM_SCHEMAS["free"])


# ── Parsing ──────────────────────────────────────────────────────────────────


def loads_object(raw: str) -> dict[str, object] | None:
    """Parse *raw* into a JSON object, tolerating Markdown fences and noise."""
    cleaned = re.sub(r"```(?:json)?", "", raw).strip().strip("`").strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = _JSON_RE.search(cleaned)
        if not match:
            return None
        try:
            data = json.loads(match.group())
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


def _normalize_difficulty(value: object) -> str:
    difficulty = str(value or "").strip().lower()
    return difficulty if difficulty in DIFFICULTIES else "moyen"


def _validate_mcq(entry: dict[str, object], qtype: str) -> QuestionItem | None:
    question = str(entry.get("question", "")).strip()
    if not question:
        return None

    raw_options = entry.get("options")
    if not isinstance(raw_options, list):
        return None
    options = [str(o).strip() for o in raw_options if str(o).strip()]
    if len(options) < 2:
        return None

    raw_correct = entry.get("correct")
    if not isinstance(raw_correct, list) or not raw_correct:
        return None
    try:
        correct = sorted({int(i) for i in raw_correct})
    except (TypeError, ValueError):
        return None
    if any(i < 0 or i >= len(options) for i in correct):
        return None
    if qtype == "mcq_multi" and len(correct) < 2:
        # The LLM wrote a single-answer question despite the multi prompt:
        # relabel rather than drop, so the checkbox UI never promises a
        # multiple choice the question can't deliver.
        qtype = "mcq_single"
    if qtype == "mcq_single" and len(correct) != 1:
        return None

    return {
        "question": question,
        "answer": str(entry.get("answer", "")).strip(),
        "difficulty": _normalize_difficulty(entry.get("difficulty")),
        "qtype": qtype,
        "options": options,
        "correct": correct,
    }


def _validate_tf(entry: dict[str, object]) -> QuestionItem | None:
    question = str(entry.get("question", "")).strip()
    if not question:
        return None

    raw_correct = entry.get("correct")
    if not isinstance(raw_correct, list) or len(raw_correct) != 1:
        return None
    try:
        idx = int(raw_correct[0])
    except (TypeError, ValueError):
        return None
    if idx not in (0, 1):
        return None

    return {
        "question": question,
        "answer": str(entry.get("answer", "")).strip(),
        "difficulty": _normalize_difficulty(entry.get("difficulty")),
        "qtype": "tf",
        "options": list(_TF_OPTIONS),
        "correct": [idx],
    }


def _validate_free(entry: dict[str, object]) -> QuestionItem | None:
    question = str(entry.get("question", "")).strip()
    answer = str(entry.get("answer", "")).strip()
    if not question or not answer:
        return None
    return {
        "question": question,
        "answer": answer,
        "difficulty": _normalize_difficulty(entry.get("difficulty")),
        "qtype": "free",
        "options": None,
        "correct": None,
    }


def validate_item(entry: object, qtype: str) -> QuestionItem | None:
    """Validate and normalize one raw LLM item for *qtype*.

    Returns ``None`` if the entry is malformed (missing/invalid fields, an
    out-of-range correct index, wrong number of correct answers, ...).
    """
    if not isinstance(entry, dict):
        return None
    if qtype in ("mcq_single", "mcq_multi"):
        return _validate_mcq(entry, qtype)
    if qtype == "tf":
        return _validate_tf(entry)
    return _validate_free(entry)


def score_selection(correct: list[int], selected: list[int]) -> float:
    """Jaccard overlap between the selected and correct option indices.

    1.0 for an exact match, 0.0 for no overlap, partial credit in between —
    used to grade ``mcq_multi`` answers instead of an all-or-nothing check.
    """
    correct_set, selected_set = set(correct), set(selected)
    union = correct_set | selected_set
    if not union:
        return 1.0
    return len(correct_set & selected_set) / len(union)


def parse_structured_items(
    raw: str, qtype: str, items_key: str = "items"
) -> list[QuestionItem]:
    """Extract and validate every item under *items_key* in a (noisy) LLM reply.

    Invalid entries are dropped rather than raising — generation degrades
    gracefully to fewer questions instead of failing outright.
    """
    data = loads_object(raw)
    if data is None:
        return []
    raw_items = data.get(items_key)
    if not isinstance(raw_items, list):
        return []
    items: list[QuestionItem] = []
    for entry in raw_items:
        item = validate_item(entry, qtype)
        if item is not None:
            items.append(item)
    return items

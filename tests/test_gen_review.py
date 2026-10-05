"""scripts/gen_review.py: overlap percentage between a question and its page text."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "gen_review", Path(__file__).parents[1] / "scripts" / "gen_review.py"
)
assert _spec and _spec.loader
gen_review = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen_review)


def test_copy_pct_ignores_stopwords_case_and_soft_hyphen() -> None:
    page = "Le fonction\u00adnement du Test unitaire"  # soft hyphen glued inside a word
    # content words: unitaire, intégration, fonctionnement -> "intégration" is missing
    question = "Quel est le UNITAIRE, l'intégration du fonctionnement ?"
    assert round(gen_review.copy_pct(question, [page])) == 67
    assert gen_review.copy_pct("test unitaire fonctionnement", [page]) == 100.0


def test_copy_pct_uses_union_of_pages_and_handles_empty_question() -> None:
    assert gen_review.copy_pct("alpha beta", ["alpha", "beta"]) == 100.0
    assert gen_review.copy_pct("le la de", ["alpha"]) == 0.0

"""eval.dataset: JSONL parsing and validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rag_bachelor.eval.dataset import load_questions


def _write(path: Path, *rows: object) -> Path:
    path.write_text("\n".join(r if isinstance(r, str) else json.dumps(r) for r in rows))
    return path


def _row(**over: object) -> dict[str, object]:
    return {
        "id": "q1",
        "question": "Qu'est-ce que X ?",
        "source": "a.pdf",
        "pages": [3, 2, 3],
        **over,
    }


def test_valid_file_loads_with_defaults_and_sorted_unique_pages(tmp_path: Path) -> None:
    qs = load_questions(
        _write(tmp_path / "q.jsonl", _row(), "", _row(id="q2", status="valide", type="lexical"))
    )
    assert [q.id for q in qs] == ["q1", "q2"]
    assert qs[0].pages == (2, 3)
    assert (qs[0].status, qs[1].status) == ("brouillon", "valide")


@pytest.mark.parametrize(
    "bad",
    [
        "{not json",
        "[1]",
        _row(question=" "),
        _row(pages=[]),
        _row(pages=[0]),
        _row(pages=["1"]),
        _row(pages=[True]),
        _row(status="ok"),
        _row(status="validé"),
        _row(status="valide"),  # validated without a type
        _row(type="inconnu"),
        _row(type="hors_sujet"),  # hors_sujet must have empty pages
        _row(type="lexical", pages=[]),
        _row(note=3),
        {**_row(), "statut": "valide"},  # misspelled field must not silently fall back to draft
    ],
)
def test_invalid_rows_are_rejected_with_line_number(tmp_path: Path, bad: object) -> None:
    with pytest.raises(ValueError, match="line 1"):
        load_questions(_write(tmp_path / "q.jsonl", bad))


def test_type_and_note_are_loaded_and_hors_sujet_has_no_pages(tmp_path: Path) -> None:
    qs = load_questions(
        _write(
            tmp_path / "q.jsonl",
            _row(type="multi_page", note="n", status="valide"),
            _row(id="q2", type="hors_sujet", pages=[]),
            {"id": "q3", "question": "x", "source": "a.pdf", "type": "hors_sujet"},  # pages omitted
        )
    )
    assert [(q.type, q.pages, q.note) for q in qs] == [
        ("multi_page", (2, 3), "n"),
        ("hors_sujet", (), None),
        ("hors_sujet", (), None),
    ]


def test_source_is_optional_only_for_hors_sujet(tmp_path: Path) -> None:
    off = {"id": "q1", "question": "x ?", "type": "hors_sujet"}
    assert load_questions(_write(tmp_path / "q.jsonl", off))[0].source == ""
    no_source = {k: v for k, v in _row(type="lexical").items() if k != "source"}
    with pytest.raises(ValueError, match="'source'"):
        load_questions(_write(tmp_path / "q2.jsonl", no_source))


def test_duplicate_id_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="duplicate id"):
        load_questions(_write(tmp_path / "q.jsonl", _row(), _row()))

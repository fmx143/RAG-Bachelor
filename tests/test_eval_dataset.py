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
    return {"id": "q1", "question": "Qu'est-ce que X ?", "source": "a.pdf", "pages": [3, 2, 3], **over}


def test_valid_file_loads_with_defaults_and_sorted_unique_pages(tmp_path: Path) -> None:
    qs = load_questions(_write(tmp_path / "q.jsonl", _row(), "", _row(id="q2", status="valide")))
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
    ],
)
def test_invalid_rows_are_rejected_with_line_number(tmp_path: Path, bad: object) -> None:
    with pytest.raises(ValueError, match="line 1"):
        load_questions(_write(tmp_path / "q.jsonl", bad))


def test_duplicate_id_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="duplicate id"):
        load_questions(_write(tmp_path / "q.jsonl", _row(), _row()))

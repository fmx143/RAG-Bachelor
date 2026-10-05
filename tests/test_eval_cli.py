"""rag-eval: scoring loop, report file, comparison and the explicit-CHROMA_DIR guard."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rag_bachelor.config import settings
from rag_bachelor.eval import cli
from rag_bachelor.eval.dataset import EvalQuestion

Q = [
    EvalQuestion("q1", "alpha ?", "a.pdf", (1,), "valide"),
    EvalQuestion("q2", "beta ?", "a.pdf", (4,), "valide"),
]


def test_evaluate_scores_each_question_with_injected_search() -> None:
    hits = {"alpha ?": [("a.pdf", 1)], "beta ?": [("a.pdf", 2), ("a.pdf", 4)]}
    metrics, _ = cli.evaluate(Q, lambda q, k: hits[q], k=3)
    assert metrics == {"recall@1": 0.5, "recall@3": 1.0, "mrr": 0.75}


def test_evaluate_excludes_hors_sujet_and_by_type_breaks_down() -> None:
    qs = [
        EvalQuestion("q1", "alpha ?", "a.pdf", (1,), "valide", "lexical"),
        EvalQuestion("q2", "beta ?", "a.pdf", (4,), "valide", "paraphrase"),
        EvalQuestion("q3", "meteo ?", "a.pdf", (), "valide", "hors_sujet"),
    ]
    hits = {"alpha ?": [("a.pdf", 1)], "beta ?": [("a.pdf", 2)]}
    metrics, scored = cli.evaluate(qs, lambda q, k: hits[q], k=1)  # hors_sujet never searched
    assert metrics == {"recall@1": 0.5, "mrr": 0.5}
    assert cli.by_type(qs, scored) == {
        "lexical": {"n": 1, "recall@1": 1.0, "mrr": 1.0},
        "paraphrase": {"n": 1, "recall@1": 0.0, "mrr": 0.0},
    }


def test_by_category_groups_uncategorized_and_skips_hors_sujet() -> None:
    qs = [
        EvalQuestion("q1", "alpha ?", "a.pdf", (1,), "valide", "lexical", category="c1"),
        EvalQuestion("q2", "beta ?", "a.pdf", (4,), "valide", "lexical", category="c1"),
        EvalQuestion("q3", "gamma ?", "a.pdf", (5,), "valide", "lexical"),
        EvalQuestion("q4", "meteo ?", "a.pdf", (), "valide", "hors_sujet", category="c2"),
    ]
    hits = {"alpha ?": [("a.pdf", 1)], "beta ?": [("a.pdf", 2)], "gamma ?": [("a.pdf", 5)]}
    _, scored = cli.evaluate(qs, lambda q, k: hits[q], k=1)
    assert cli.by_category(qs, scored) == {
        "c1": {"n": 2, "recall@1": 0.5, "mrr": 0.5},
        "non classée": {"n": 1, "recall@1": 1.0, "mrr": 1.0},
    }


def _write_questions(path: Path, status: str) -> Path:
    rows = [
        {
            "id": "q1",
            "question": "alpha ?",
            "source": "a.pdf",
            "pages": [1],
            "status": status,
            "type": "lexical",
            "category": "c1",
        }
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows))
    return path


def _explicit_chroma(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "__pydantic_fields_set__", {"chroma_dir"})


def test_main_writes_report_and_prints_delta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _explicit_chroma(monkeypatch)
    monkeypatch.setattr(cli, "_search", lambda q, k: [("a.pdf", 1)])
    questions = _write_questions(tmp_path / "q.jsonl", "valide")
    old = tmp_path / "old.json"
    old.write_text(json.dumps({"metrics": {"mrr": 0.5}}))
    out = tmp_path / "r.json"

    rc = cli.main(["--questions", str(questions), "--out", str(out), "--compare", str(old)])

    assert rc == 0
    report = json.loads(out.read_text())
    assert report["metrics"]["mrr"] == 1.0
    assert report["by_category"]["c1"]["n"] == 1
    assert report["per_question"][0]["category"] == "c1"
    assert "+0.500 vs baseline" in capsys.readouterr().out


def test_main_ignores_drafts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _explicit_chroma(monkeypatch)
    questions = _write_questions(tmp_path / "q.jsonl", "brouillon")
    assert cli.main(["--questions", str(questions), "--out", str(tmp_path / "r.json")]) == 1
    assert not (tmp_path / "r.json").exists()


def test_main_refuses_without_explicit_chroma_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "__pydantic_fields_set__", set())
    questions = _write_questions(tmp_path / "q.jsonl", "valide")
    assert cli.main(["--questions", str(questions)]) == 2

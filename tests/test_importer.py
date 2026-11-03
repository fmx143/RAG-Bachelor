"""Question import: verbatim checks against the PDF, stated answers only, dedup, confirmation."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import fitz  # pymupdf
import pytest
from fastapi.testclient import TestClient

from rag_bachelor.app.web import server
from rag_bachelor.app.web.routes import importer as importer_routes
from rag_bachelor.config import settings
from rag_bachelor.core import importer
from rag_bachelor.ingest import index
from rag_bachelor.study import store
from rag_bachelor.study.textnorm import fold

SRC = "PMI/Sources/Quiz.pdf"
PAGE_1 = """Quiz Results
Question 4 0 / 1 point
A project to build a bird sanctuary is scheduled to last 5 years and is now entering its
second year. The project manager wants to perform an audit on the project to ensure the
team is adequately measuring and checking various project constraints.
What should the project manager check?
Select the best option.
A. Project controls
B. Baselines
C. Project management plan
D. Lessons learned
Hide question 4 feedback
Feedback
Option A is correct. Project controls ensure the constraints are measured and checked."""
PAGE_2 = """Question 5 0 / 1 point
Which documents are inputs to scope validation?
Select two options.
A. Requirements traceability matrix
B. Risk register
C. Verified deliverables
D. Team charter
Feedback
Options A and C are correct. Both are inputs to Validate Scope.
Question 6
Which tool helps prioritize requirements?
A. MoSCoW
B. Gantt chart"""

Q4 = (
    "A project to build a bird sanctuary is scheduled to last 5 years and is now entering its "
    "second year. The project manager wants to perform an audit on the project to ensure the "
    "team is adequately measuring and checking various project constraints. What should the "
    "project manager check?"
)


def _pdf(*pages: str) -> None:
    path = settings.pdfs_dir / SRC
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    for text in pages:
        doc.new_page().insert_text((40, 40), text, fontsize=8)
    doc.save(path)


def _entries() -> list[dict[str, object]]:
    return [
        {
            "question": Q4,
            "multi": False,
            "options": [
                "A. Project controls",
                "Baselines",
                "Project management plan",
                "Lessons learned",
            ],
            "explanation": "Option A is correct. Project controls ensure the constraints are measured and checked.",
        },
        {
            "question": "Which documents are inputs to scope validation?",
            "multi": True,
            "options": [
                "Requirements traceability matrix",
                "Risk register",
                "Verified deliverables",
                "Team charter",
            ],
            "explanation": "Options A and C are correct. Both are inputs to Validate Scope.",
        },
        {  # no written answer in the document: kept, awaiting confirmation
            "question": "Which tool helps prioritize requirements?",
            "options": ["MoSCoW", "Gantt chart"],
            "explanation": "",
        },
        {  # an option the model made up: rejected
            "question": "Which tool helps prioritize requirements?",
            "options": ["MoSCoW", "Kano model"],
            "explanation": "Option B is correct.",
        },
        {"question": "Half a question", "options": ["Only one"]},  # incomplete: ignored silently
    ]


def _provider(entries: list[dict[str, object]]) -> MagicMock:
    provider = MagicMock()
    provider.chat.return_value = json.dumps({"questions": entries})
    return provider


def test_fold_and_stated_answers() -> None:
    assert fold("Project manage-\nment plan.") == fold("project management plan")
    assert fold("Équipe") == "equipe"
    assert importer.stated_answer("Feedback: Option A is correct.")[0] == ["A"]
    assert importer.stated_answer("Options A and C are correct")[0] == ["A", "C"]
    assert importer.stated_answer("Correct answer: B")[0] == ["B"]
    assert importer.stated_answer("Réponse : D")[0] == ["D"]
    assert importer.stated_answer("The answer is a project charter.")[0] == []
    assert importer.stated_answer("No statement here") == ([], "")


def test_import_keeps_verbatim_questions_and_stated_answers_only() -> None:
    _pdf(PAGE_1, PAGE_2)
    with patch.object(importer, "get_provider", return_value=(_provider(_entries()), "ollama")):
        report = importer.import_questions(SRC)
    assert (report.imported, report.to_confirm, report.rejected, report.pages) == (3, 1, 1, 2)
    qs = {
        q.question: q
        for q in store.list_bank_questions(source=SRC, limit=10, with_unconfirmed=True)
    }
    q4 = qs[Q4]
    assert q4.origin == "imported" and q4.qtype == "mcq_single" and q4.correct == [0]
    assert q4.options == [
        "Project controls",
        "Baselines",
        "Project management plan",
        "Lessons learned",
    ]
    assert q4.answer.startswith("Option A is correct") and q4.pages == [1]
    multi = qs["Which documents are inputs to scope validation?"]
    assert multi.qtype == "mcq_multi" and multi.correct == [0, 2]
    pending = qs["Which tool helps prioritize requirements?"]
    assert pending.to_confirm and pending.correct == []

    # Re-importing the same quiz (or the same questions in another PDF) adds nothing.
    with patch.object(importer, "get_provider", return_value=(_provider(_entries()), "ollama")):
        again = importer.import_questions(SRC)
    assert again.imported == 0 and again.duplicates == 3


def test_answer_must_be_written_in_the_document() -> None:
    _pdf(PAGE_1)
    guessed = [{**_entries()[0], "explanation": "Option B is correct."}]  # not in the PDF
    with patch.object(importer, "get_provider", return_value=(_provider(guessed), "ollama")):
        report = importer.import_questions(SRC)
    assert report.imported == 1 and report.to_confirm == 1


def test_unconfirmed_questions_stay_out_of_revision_until_confirmed() -> None:
    _pdf(PAGE_2)
    store.add_bank_questions(
        [store.BankQuestion("Pick one ?", "", "moyen", SRC, [1], [], qtype="mcq_single",
                            options=["X", "Y"], correct=[], origin="imported", to_confirm=True)]
    )  # fmt: skip
    q = store.list_bank_questions(source=SRC, with_unconfirmed=True)[0]
    assert store.list_bank_questions(source=SRC) == []  # default filters skip it
    assert store.count_bank_questions(origin="to_confirm") == 1
    with pytest.raises(ValueError):
        store.materialize_bank_question(q)
    assert q.id is not None
    assert not store.confirm_bank_answer(q.id, [])
    assert not store.confirm_bank_answer(q.id, [5])
    with TestClient(server.app) as client:
        r = client.post(f"/bank/{q.id}/confirm", data={f"c{q.id}": "1"})
    assert "Réponse confirmée" in r.text
    done = store.get_bank_question(q.id)
    assert done is not None and not done.to_confirm and done.correct == [1]
    assert done.qtype == "mcq_single" and store.list_bank_questions(source=SRC)[0].id == q.id


def test_import_route_runs_the_job_and_reports() -> None:
    index.get_collection().upsert(
        ids=[f"{SRC}__p1__c0"],
        documents=["Quiz"],
        embeddings=[[1.0, 0.0, 0.0]],  # type: ignore[arg-type]
        metadatas=[{"source": SRC, "page": 1}],
    )
    report = importer.ImportReport(imported=12, to_confirm=2, duplicates=1, pages=4)
    with (
        TestClient(server.app) as client,
        patch.object(importer_routes, "import_questions", return_value=report) as run,
        patch.object(importer_routes, "tag_subject") as tag,
    ):
        assert (
            "Lecture des questions" in client.post("/import/questions", data={"source": SRC}).text
        )
        status = client.get("/import/status").text
        assert (
            client.post("/import/questions", data={"source": "PMI/x.pdf"}).text.count("introuvable")
            == 1
        )
    assert run.call_args.args[0] == SRC and tag.call_args.args[0] == "PMI"
    assert (
        "<strong>12</strong> questions importées" in status
        and "2</strong> réponses à confirmer" in status
    )

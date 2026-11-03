"""Mock exams: eligible questions, server-side timing, grading, results, mistakes to revision."""

from __future__ import annotations

import random
from datetime import timedelta

from fastapi.testclient import TestClient

from rag_bachelor.app.web import server
from rag_bachelor.config import settings
from rag_bachelor.core import exam as ex
from rag_bachelor.study import store
from rag_bachelor.study.store import BankQuestion, Chapter
from rag_bachelor.study.subjects import set_active_subject

QUIZ = "PMI/Sources/quiz.pdf"
BOOK = "PMI/Livres/PMBOK.pdf"


def _q(text: str, source: str = QUIZ, page: int = 1, **kw: object) -> BankQuestion:
    fields: dict[str, object] = {"qtype": "mcq_single", "options": ["A", "B", "C"], "correct": [0]}
    fields.update(kw)
    return BankQuestion(text, "Because.", "moyen", source, [page], [], **fields)  # type: ignore[arg-type]


def _seed() -> dict[str, int]:
    store.add_bank_questions(
        [
            _q("Q1 ?", origin="imported", domains={"PMP": "Process"}),
            _q("Q2 ?", page=8, qtype="mcq_multi", options=["A", "B", "C"], correct=[0, 2]),
            _q("Q3 ?", BOOK, page=3, qtype="tf", options=["True", "False"], correct=[1]),
            _q("Free ?", qtype="free", options=None, correct=None),  # self-graded: never drawn
            _q("Pending ?", correct=[], origin="imported", to_confirm=True),  # unconfirmed
        ]
    )
    return {q.question: q.id for q in store.list_bank_questions(limit=50, with_unconfirmed=True)}  # type: ignore[misc]


def _activate() -> None:
    (settings.pdfs_dir / "PMI").mkdir(parents=True, exist_ok=True)
    set_active_subject("PMI")


def test_eligible_questions_and_scope() -> None:
    _seed()
    names = lambda qs: sorted(q.question for q in qs)  # noqa: E731
    assert names(ex.eligible("PMI", ex.Scope())) == ["Q1 ?", "Q2 ?", "Q3 ?"]
    assert names(ex.eligible("PMI", ex.Scope(sources=[BOOK]))) == ["Q3 ?"]
    assert names(ex.eligible("PMI", ex.Scope(ranges={QUIZ: [(5, 9)]}))) == ["Q2 ?", "Q3 ?"]
    assert names(ex.eligible("PMI", ex.Scope(origin="imported"))) == ["Q1 ?"]
    assert ex.eligible("Bachelor", ex.Scope()) == []


def test_global_exam_flow_grades_and_sends_mistakes_to_revision() -> None:
    _activate()
    ids = _seed()
    store.set_chapters(QUIZ, [Chapter("Ch1", 1, 5), Chapter("Ch2", 6, 10)])
    with TestClient(server.app, follow_redirects=False) as client:
        assert "3 questions corrigeables" in client.get("/exam").text
        r = client.post(
            "/exam/start", data={"count": "10", "timing": "global", "minutes": "5", "preset": "PMP"}
        )
        exam_id = int(r.headers["location"].rsplit("/", 1)[1])
        exam = store.get_exam(exam_id)
        assert (
            exam is not None and exam.total == 3 and exam.seconds == 300 and exam.framework is None
        )
        items = {i.snapshot["question"]: i.pos for i in store.exam_items(exam_id)}
        page = client.get(f"/exam/{exam_id}/q/{items['Q1 ?']}").text
        assert 'class="exam-timer"' in page and "Marquer à revoir" in page
        # Q1 right, Q2 half right (wrong: all-or-nothing), Q3 left unanswered but flagged.
        client.post(f"/exam/{exam_id}/q/{items['Q1 ?']}", data={"selected": "0", "go": "next"})
        client.post(f"/exam/{exam_id}/q/{items['Q2 ?']}", data={"selected": "0", "go": "next"})
        r = client.post(
            f"/exam/{exam_id}/q/{items['Q3 ?']}", data={"flagged": "true", "go": "review"}
        )
        assert r.headers["location"].endswith("/review")
        assert "1 question(s) sans réponse" in client.get(f"/exam/{exam_id}/review").text
        assert client.post(f"/exam/{exam_id}/finish").headers["location"].endswith("/results")
        results = client.get(f"/exam/{exam_id}/results").text
        assert "33 %" in results and "1/3 justes" in results and "Par chapitre" in results
        assert "Ch1" in results and "Ch2" in results
        # Answering after the end changes nothing.
        client.post(f"/exam/{exam_id}/q/{items['Q3 ?']}", data={"selected": "1"})
        assert (
            "/exam" in client.get("/progress").text and "1/3 · 33 %" in client.get("/progress").text
        )
    q2 = store.get_bank_question(ids["Q2 ?"])
    q1 = store.get_bank_question(ids["Q1 ?"])
    assert q2 is not None and q2.card_id is not None and q2.last_result == 0  # into revision
    assert q1 is not None and q1.card_id is None and q1.last_result == 1
    assert store.get_bank_question(ids["Q3 ?"]).card_id is not None  # type: ignore[union-attr]


def test_breakdown_by_domain_document_and_chapter() -> None:
    _activate()
    _seed()
    store.set_chapters(QUIZ, [Chapter("Ch1", 1, 5)])
    exam_id = ex.start("PMI", ex.Scope(), 10, "PMP", "PMP", "global", 60, random.Random(1))
    exam = store.get_exam(exam_id)
    assert exam is not None
    for item in store.exam_items(exam_id):
        store.save_exam_answer(exam_id, item.pos, item.snapshot["correct"], False)
    ex.finish(exam)
    exam = store.get_exam(exam_id)
    assert exam is not None and exam.right == 3
    groups = ex.breakdowns(exam, store.exam_items(exam_id))
    assert {(b.label, b.total) for b in groups["domain"]} == {("Process", 1), ("Non classée", 2)}
    assert {b.label for b in groups["document"]} == {QUIZ, BOOK}
    assert [(b.label, b.doc, b.pct) for b in groups["chapter"]] == [("Ch1", QUIZ, 100)]


def test_per_question_timing_is_forward_only_and_enforced_by_the_server() -> None:
    _activate()
    _seed()
    exam_id = ex.start("PMI", ex.Scope(), 3, "custom", None, "per_question", 30, random.Random(2))
    with TestClient(server.app, follow_redirects=False) as client:
        assert client.get(f"/exam/{exam_id}").headers["location"].endswith("/q/1")
        assert (
            client.get(f"/exam/{exam_id}/q/2").headers["location"].endswith("/q/1")
        )  # no skipping
        assert "Valider et continuer" in client.get(f"/exam/{exam_id}/q/1").text
        # The clock of question 1 started 40 s ago (> 30 s + grace): its answer no longer counts.
        past = ex.stamp(ex.now() - timedelta(seconds=40))
        store.get_conn().execute(
            store._adapt("UPDATE exam_items SET shown_at = ? WHERE exam_id = ? AND pos = 1"),
            (past, exam_id),
        )
        store.get_conn().commit()
        client.post(f"/exam/{exam_id}/q/1", data={"selected": "0"})
        assert store.exam_items(exam_id)[0].selected is None
        assert client.get(f"/exam/{exam_id}").headers["location"].endswith("/q/2")
        client.get(f"/exam/{exam_id}/q/2")
        client.post(f"/exam/{exam_id}/q/2", data={})  # time ran out in the browser: empty submit
        assert store.exam_items(exam_id)[1].selected == []
        client.get(f"/exam/{exam_id}/q/3")
        client.post(f"/exam/{exam_id}/q/3", data={"selected": "0"})
        assert client.get(f"/exam/{exam_id}").headers["location"].endswith("/results")
    exam = store.get_exam(exam_id)
    assert exam is not None and exam.finished_at is not None


def test_global_deadline_closes_the_exam_and_empty_scope_is_refused() -> None:
    _activate()
    _seed()
    exam_id = ex.start("PMI", ex.Scope(), 2, "custom", None, "global", 60)
    store.get_conn().execute(
        store._adapt("UPDATE exams SET started_at = ? WHERE id = ?"),
        (ex.stamp(ex.now() - timedelta(minutes=5)), exam_id),
    )
    store.get_conn().commit()
    with TestClient(server.app, follow_redirects=False) as client:
        assert client.get(f"/exam/{exam_id}/q/1").headers["location"].endswith("/results")
        empty = client.post(
            "/exam/start", data={"origin": "generated", "doc": BOOK, "chapter": f"{BOOK}|50-60"}
        )
        assert empty.headers["location"] == "/exam?error=empty"
        assert client.post(f"/exam/{exam_id}/delete").status_code == 303
    assert store.get_exam(exam_id) is None

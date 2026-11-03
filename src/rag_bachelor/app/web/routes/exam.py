"""🧪 Mock exam routes — setup, one question per page, review, results (plain forms, no htmx).

Every page re-reads the exam's timestamps, so the timer survives reloads; see core/exam.py.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.responses import Response

from rag_bachelor.app.web._deps import sidebar_ctx, templates
from rag_bachelor.core import exam as ex
from rag_bachelor.core.domains import SHORT, subject_frameworks
from rag_bachelor.core.qtypes import QTYPE_LABELS
from rag_bachelor.study.store import (
    Exam,
    ExamItem,
    delete_exam,
    exam_items,
    get_exam,
    list_chapters,
    list_exams,
    mark_exam_item_shown,
    save_exam_answer,
)
from rag_bachelor.study.subjects import active_sources, active_subject

router = APIRouter()

_ORIGINS = {"": "Toutes", "imported": "Importées", "generated": "Générées"}


def _go(path: str) -> Response:
    return RedirectResponse(path, status_code=303)


def _setup_ctx() -> dict[str, object]:
    subject = active_subject()
    pool = ex.eligible(subject, ex.Scope())
    per_doc: dict[str, int] = {}
    for q in pool:
        per_doc[q.source] = per_doc.get(q.source, 0) + 1
    docs = [s for s in active_sources() if s in per_doc]
    return {
        "presets": ex.PRESETS,
        "frameworks": subject_frameworks(subject),
        "docs": docs,
        "per_doc": per_doc,
        "chapters": {d: list_chapters(d) for d in docs},
        "eligible": len(pool),
        "origins": _ORIGINS,
        "history": list_exams(subject),
    }


@router.get("/exam", response_class=HTMLResponse)
async def exam_setup(request: Request, error: str = "") -> Response:
    ctx: dict[str, object] = {
        "request": request,
        "active_tab": "revision",
        "error": {"empty": "Aucune question corrigeable automatiquement dans cette sélection."}.get(
            error
        ),
        **(await asyncio.to_thread(_setup_ctx)),
        **sidebar_ctx(),
    }
    return templates.TemplateResponse(request, "exam_setup.html", ctx)


@router.post("/exam/start")
async def exam_start(
    preset: Annotated[str, Form()] = "custom",
    count: Annotated[int, Form()] = 20,
    timing: Annotated[str, Form()] = "global",
    minutes: Annotated[int, Form()] = 30,
    per_question: Annotated[int, Form()] = 75,
    origin: Annotated[str, Form()] = "",
    doc: Annotated[list[str], Form()] = [],  # noqa: B006 — never mutated
    chapter: Annotated[list[str], Form()] = [],  # noqa: B006
) -> Response:
    """Draw the questions and open the exam. ``chapter`` values are ``<document>|<from>-<to>``."""
    subject = await asyncio.to_thread(active_subject)
    ranges: dict[str, list[tuple[int, int]]] = {}
    for value in chapter:
        source, _, span = value.rpartition("|")
        lo, _, hi = span.partition("-")
        if source in doc and lo.isdigit() and hi.isdigit():
            ranges.setdefault(source, []).append((int(lo), int(hi)))
    scope = ex.Scope(sources=doc or None, ranges=ranges, origin=origin or None)
    timing = timing if timing in ("global", "per_question") else "global"
    seconds = max(minutes, 1) * 60 if timing == "global" else min(max(per_question, 10), 600)
    frameworks = subject_frameworks(subject)
    framework = preset if preset in frameworks else (frameworks[0] if frameworks else None)
    try:
        exam_id = await asyncio.to_thread(
            ex.start,
            subject,
            scope,
            min(max(count, 1), ex.MAX_QUESTIONS),
            preset if preset in ex.PRESETS else "custom",
            framework,
            timing,
            seconds,
        )
    except ValueError:
        return _go("/exam?error=empty")
    return _go(f"/exam/{exam_id}")


def _load(exam_id: int) -> tuple[Exam | None, list[ExamItem]]:
    exam = get_exam(exam_id)
    return exam, exam_items(exam_id) if exam else []


def _close_if_over(exam: Exam, items: list[ExamItem]) -> bool:
    """Finish the exam when its time is up (global) or every question is done (per question)."""
    if exam.finished_at:
        return True
    over = (
        ex.now() > (ex.deadline(exam) or ex.now()) + timedelta(seconds=ex.GRACE_S)
        if exam.timing == "global"
        else ex.current_pos(exam, items) is None
    )
    if over:
        ex.finish(exam)
    return over


@router.get("/exam/{exam_id}")
async def exam_resume(exam_id: int) -> Response:
    exam, items = await asyncio.to_thread(_load, exam_id)
    if exam is None:
        return Response(status_code=404)
    if await asyncio.to_thread(_close_if_over, exam, items):
        return _go(f"/exam/{exam_id}/results")
    if exam.timing == "per_question":
        return _go(f"/exam/{exam_id}/q/{ex.current_pos(exam, items)}")
    first = next((i.pos for i in items if i.selected is None), 1)
    return _go(f"/exam/{exam_id}/q/{first}")


@router.get("/exam/{exam_id}/q/{pos}", response_class=HTMLResponse)
async def exam_question(request: Request, exam_id: int, pos: int) -> Response:
    exam, items = await asyncio.to_thread(_load, exam_id)
    if exam is None or not 1 <= pos <= len(items):
        return Response(status_code=404)
    if await asyncio.to_thread(_close_if_over, exam, items):
        return _go(f"/exam/{exam_id}/results")
    if exam.timing == "per_question":
        current = ex.current_pos(exam, items)
        if pos != current:  # forward only: always the question whose clock is running
            return _go(f"/exam/{exam_id}/q/{current}")
        await asyncio.to_thread(mark_exam_item_shown, exam_id, pos, ex.stamp(ex.now()))
        exam, items = await asyncio.to_thread(_load, exam_id)
        assert exam is not None
    item = items[pos - 1]
    limit = ex.deadline(exam, item)
    ctx: dict[str, object] = {
        "request": request,
        "active_tab": "revision",
        "exam": exam,
        "items": items,
        "cur": item,  # not "item": base.html defines a nav macro of that name
        "q": item.snapshot,
        "qtype_labels": QTYPE_LABELS,
        "deadline_ms": int(limit.timestamp() * 1000) if limit else None,
        "now_ms": int(ex.now().timestamp() * 1000),
        "answered": sum(i.selected is not None for i in items),
        **sidebar_ctx(),
    }
    return templates.TemplateResponse(request, "exam_question.html", ctx)


@router.post("/exam/{exam_id}/q/{pos}")
async def exam_answer(
    exam_id: int,
    pos: int,
    selected: Annotated[list[int], Form()] = [],  # noqa: B006 — never mutated
    flagged: Annotated[bool, Form()] = False,
    go: Annotated[str, Form()] = "next",
) -> Response:
    """Save the answer (if still in time), then move: next, prev, review, finish or a position."""
    exam, items = await asyncio.to_thread(_load, exam_id)
    if exam is None or not 1 <= pos <= len(items):
        return Response(status_code=404)
    if exam.finished_at:
        return _go(f"/exam/{exam_id}/results")
    item = items[pos - 1]
    options = len(item.snapshot.get("options") or [])
    chosen = sorted({i for i in selected if 0 <= i < options})
    if ex.in_time(exam, item) and (
        exam.timing == "global" or item.pos == ex.current_pos(exam, items)
    ):
        # Per question an empty submit (the browser's clock ran out) still closes the question;
        # with a global clock, nothing ticked means unanswered.
        answer = chosen if exam.timing == "per_question" else (chosen or None)
        await asyncio.to_thread(save_exam_answer, exam_id, pos, answer, flagged)
    if go == "finish":
        return _go(f"/exam/{exam_id}/finish")
    if exam.timing == "per_question":
        return _go(f"/exam/{exam_id}")
    if go == "review":
        return _go(f"/exam/{exam_id}/review")
    target = pos - 1 if go == "prev" else int(go) if go.isdigit() else pos + 1
    if target > len(items):
        return _go(f"/exam/{exam_id}/review")
    return _go(f"/exam/{exam_id}/q/{max(target, 1)}")


@router.get("/exam/{exam_id}/review", response_class=HTMLResponse)
async def exam_review(request: Request, exam_id: int) -> Response:
    exam, items = await asyncio.to_thread(_load, exam_id)
    if exam is None:
        return Response(status_code=404)
    if await asyncio.to_thread(_close_if_over, exam, items):
        return _go(f"/exam/{exam_id}/results")
    limit = ex.deadline(exam)
    ctx: dict[str, object] = {
        "request": request,
        "active_tab": "revision",
        "exam": exam,
        "items": items,
        "deadline_ms": int(limit.timestamp() * 1000) if limit else None,
        "now_ms": int(ex.now().timestamp() * 1000),
        **sidebar_ctx(),
    }
    return templates.TemplateResponse(request, "exam_review.html", ctx)


@router.get("/exam/{exam_id}/finish")
@router.post("/exam/{exam_id}/finish")
async def exam_finish(exam_id: int) -> Response:
    exam = await asyncio.to_thread(get_exam, exam_id)
    if exam is None:
        return Response(status_code=404)
    if not exam.finished_at:
        await asyncio.to_thread(ex.finish, exam)
    return _go(f"/exam/{exam_id}/results")


@router.get("/exam/{exam_id}/results", response_class=HTMLResponse)
async def exam_results(request: Request, exam_id: int) -> Response:
    exam, items = await asyncio.to_thread(_load, exam_id)
    if exam is None:
        return Response(status_code=404)
    if not exam.finished_at:
        return _go(f"/exam/{exam_id}")
    end = ex.parse(exam.finished_at)
    ctx: dict[str, object] = {
        "request": request,
        "active_tab": "revision",
        "exam": exam,
        "items": items,
        "groups": ex.breakdowns(exam, items),
        "domain_short": SHORT,
        "elapsed_min": max(round((end - ex.parse(exam.started_at)).total_seconds() / 60), 0),
        "unanswered": sum(i.selected is None for i in items),
        **sidebar_ctx(),
    }
    return templates.TemplateResponse(request, "exam_results.html", ctx)


@router.post("/exam/{exam_id}/delete")
async def exam_delete(exam_id: int) -> Response:
    await asyncio.to_thread(delete_exam, exam_id)
    return _go("/exam")

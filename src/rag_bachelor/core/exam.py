"""Mock exams: timed sessions of auto-graded bank questions, results by domain/document/chapter.

Only questions the app can grade by itself are drawn (QCM and true/false with a confirmed
answer). Each question is snapshotted when the exam starts. Timing is enforced by the server
from stored timestamps, so reloading a page never resets the clock: ``global`` gives one
deadline for the whole exam (free navigation, auto-submitted at the end), ``per_question``
gives each question its own countdown from its first display (forward only).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from rag_bachelor.study.srs import update_card
from rag_bachelor.study.store import (
    BankQuestion,
    Exam,
    ExamItem,
    create_exam,
    exam_items,
    finish_exam,
    get_bank_question,
    get_card,
    list_bank_questions,
    list_chapters,
    materialize_bank_question,
    record_bank_attempt,
    save_review,
)

GRADABLE = ("mcq_single", "mcq_multi", "tf")
# Defaults of the real exams (editable in the form): question count and total minutes.
PRESETS: dict[str, tuple[int, int]] = {"PMP": (180, 230), "CAPM": (150, 180)}
GRACE_S = 5  # network latency allowance after a deadline
MAX_QUESTIONS = 250


def now() -> datetime:
    return datetime.now(UTC)


def stamp(when: datetime) -> str:
    return when.isoformat(timespec="seconds")


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


@dataclass
class Scope:
    """Where the questions come from: documents (None = the whole subject), page ranges per
    document (a chosen chapter), and the bank origin ("imported" / "generated" / None)."""

    sources: list[str] | None = None
    ranges: dict[str, list[tuple[int, int]]] = field(default_factory=dict)
    origin: str | None = None


def eligible(subject: str, scope: Scope) -> list[BankQuestion]:
    """Bank questions of *subject* the exam can grade, within *scope*."""
    items = list_bank_questions(subject=subject, origin=scope.origin, limit=100_000, order_asc=True)
    out = []
    for q in items:
        if q.qtype not in GRADABLE or not q.correct or not q.options:
            continue
        if scope.sources is not None and q.source not in scope.sources:
            continue
        spans = scope.ranges.get(q.source)
        if spans and not any(lo <= min(q.pages or [0]) <= hi for lo, hi in spans):
            continue
        out.append(q)
    return out


def snapshot(q: BankQuestion) -> dict[str, Any]:
    return {
        "question": q.question,
        "qtype": q.qtype,
        "options": q.options,
        "correct": q.correct,
        "answer": q.answer,
        "source": q.source,
        "page": min(q.pages) if q.pages else None,
        "domains": q.domains,
    }


def start(
    subject: str,
    scope: Scope,
    count: int,
    preset: str,
    framework: str | None,
    timing: str,
    seconds: int,
    rng: random.Random | None = None,
) -> int:
    """Draw up to *count* eligible questions at random and open the exam; ``ValueError`` if none."""
    pool = eligible(subject, scope)
    if not pool:
        raise ValueError("no gradable question in that scope")
    drawn = (rng or random.Random()).sample(pool, min(count, len(pool), MAX_QUESTIONS))
    return create_exam(
        subject,
        preset,
        framework,
        timing,
        seconds,
        stamp(now()),
        [(q.id, snapshot(q)) for q in drawn],
    )


def deadline(exam: Exam, item: ExamItem | None = None) -> datetime | None:
    """When answers stop counting: the whole exam (global) or *item* once shown (per question)."""
    if exam.timing == "global":
        return parse(exam.started_at) + timedelta(seconds=exam.seconds)
    if item is None or item.shown_at is None:
        return None
    return parse(item.shown_at) + timedelta(seconds=exam.seconds)


def in_time(exam: Exam, item: ExamItem, at: datetime | None = None) -> bool:
    limit = deadline(exam, item)
    return limit is None or (at or now()) <= limit + timedelta(seconds=GRACE_S)


def current_pos(exam: Exam, items: list[ExamItem], at: datetime | None = None) -> int | None:
    """Per-question timing: the first question neither answered nor timed out (None = all done)."""
    for item in items:
        if item.selected is None and (item.shown_at is None or in_time(exam, item, at)):
            return item.pos
    return None


def is_right(item: ExamItem) -> bool:
    """All-or-nothing, like the real exam: the selected set must equal the right set."""
    return item.selected is not None and set(item.selected) == set(item.snapshot["correct"])


def finish(exam: Exam) -> None:
    """Grade every question, close the exam, and send the mistakes to spaced repetition."""
    items = exam_items(exam.id)
    results = {item.pos: is_right(item) for item in items}
    finish_exam(exam.id, results, stamp(now()))
    for item in items:
        q = get_bank_question(item.bank_id) if item.bank_id is not None else None
        if q is None or q.id is None or q.to_confirm:
            continue
        if item.selected is not None:
            record_bank_attempt(q.id, results[item.pos])
        if results[item.pos]:
            continue
        card = get_card(q.card_id) if q.card_id is not None else None
        if card is None:
            materialize_bank_question(q)  # a new card, due today
        else:
            save_review(update_card(card, 0), 0)  # a lapse: the card comes back soon


@dataclass
class Breakdown:
    label: str  # domain, document path or chapter title
    doc: str = ""  # the chapter's document
    right: int = 0
    total: int = 0

    @property
    def pct(self) -> int:
        return round(100 * self.right / self.total) if self.total else 0


def breakdowns(exam: Exam, items: list[ExamItem]) -> dict[str, list[Breakdown]]:
    """Results grouped by domain (of the exam's framework), document and chapter, weakest first."""
    groups: dict[str, dict[str, Breakdown]] = {"domain": {}, "document": {}, "chapter": {}}
    chapters: dict[str, list[Any]] = {}

    def add(kind: str, label: str, ok: bool, doc: str = "") -> None:
        b = groups[kind].setdefault(f"{doc}/{label}", Breakdown(label, doc))
        b.total += 1
        b.right += int(ok)

    for item in items:
        snap, ok = item.snapshot, bool(item.correct)
        if exam.framework:
            add("domain", snap.get("domains", {}).get(exam.framework, "Non classée"), ok)
        source = str(snap.get("source", ""))
        add("document", source, ok)
        if source not in chapters:
            chapters[source] = list_chapters(source)
        page = snap.get("page") or 0
        title = next((c.title for c in chapters[source] if c.page_from <= page <= c.page_to), None)
        if title:
            add("chapter", title, ok, source)
    return {k: sorted(v.values(), key=lambda b: (b.pct, -b.total)) for k, v in groups.items()}

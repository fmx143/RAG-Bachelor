"""Reference questions for retrieval evaluation (JSONL, one question per line)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

STATUSES = ("brouillon", "valide")


@dataclass(frozen=True)
class EvalQuestion:
    id: str
    question: str
    source: str  # PDF filename, as stored in the index metadata
    pages: tuple[int, ...]  # expected 1-indexed pages (any hit on one of them counts)
    status: str  # "brouillon" until validated by hand, then "valide"


def _parse(line: str, lineno: int) -> EvalQuestion:
    try:
        raw = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError(f"line {lineno}: invalid JSON ({exc.msg})") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"line {lineno}: expected a JSON object")

    for key in ("id", "question", "source"):
        if not isinstance(raw.get(key), str) or not raw[key].strip():
            raise ValueError(f"line {lineno}: '{key}' must be a non-empty string")
    pages = raw.get("pages")
    if (
        not isinstance(pages, list)
        or not pages
        or not all(isinstance(p, int) and not isinstance(p, bool) and p >= 1 for p in pages)
    ):
        raise ValueError(f"line {lineno}: 'pages' must be a non-empty list of integers >= 1")
    status = raw.get("status", "brouillon")
    if status not in STATUSES:
        raise ValueError(f"line {lineno}: 'status' must be one of {STATUSES}")

    return EvalQuestion(
        id=raw["id"],
        question=raw["question"].strip(),
        source=raw["source"],
        pages=tuple(sorted(set(pages))),
        status=status,
    )


def load_questions(path: Path) -> list[EvalQuestion]:
    """Parse and validate a JSONL file. Blank lines are ignored; ids must be unique."""
    questions: list[EvalQuestion] = []
    seen: set[str] = set()
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        q = _parse(line, lineno)
        if q.id in seen:
            raise ValueError(f"line {lineno}: duplicate id '{q.id}'")
        seen.add(q.id)
        questions.append(q)
    return questions

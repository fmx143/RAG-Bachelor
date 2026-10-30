"""Book chapters: page ranges detected from the PDF's bookmarks or its numbered headings.

Detected once per book, then stored (``study.store.set_chapters``) so the user can correct
them. Order of preference: bookmarks (the shallowest level with at least two entries),
then "Chapitre N" / "Partie N" headings, then plain "N Titre" headings — a heading
sequence only counts when its numbers run 1, 2, 3… in page order — and finally a single
whole-book chapter.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import fitz  # pymupdf

from rag_bachelor.config import settings
from rag_bachelor.core.bank import SourceChunk, get_source_chunks
from rag_bachelor.study.store import Chapter, list_chapters, set_chapters

WHOLE_BOOK = "Tout le livre"
FRONT_MATTER = "Début du livre"
_MAX_TITLE = 120

_NAMED = re.compile(
    r"(?:chapitre|chapter|partie|part|module|unit[ée]|le[çc]on|th[èe]me|s[ée]quence)\s+"
    r"(\d{1,3}|[IVXLC]{1,7})\b",
    re.IGNORECASE,
)
_NUMBERED = re.compile(r"(\d{1,2})(?:[.)]|\s*[-–—:])?\s+(?=[A-ZÀ-ÖØ-Ý])")
_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}


def _number(token: str) -> int:
    if token.isdigit():
        return int(token)
    values = [_ROMAN[c] for c in token.upper()]
    return sum(-v if v < nxt else v for v, nxt in zip(values, [*values[1:], 0], strict=True))


def _ranges(starts: Sequence[tuple[int, str]], page_count: int) -> list[Chapter]:
    """Chapters from ``(first page, title)`` starts: each runs up to the next one's start."""
    firsts: dict[int, str] = {}
    for page, title in sorted(starts, key=lambda s: s[0]):
        if 1 <= page <= page_count:
            firsts.setdefault(page, " ".join(title.split())[:_MAX_TITLE] or f"Page {page}")
    pages = sorted(firsts)
    chapters = [
        Chapter(firsts[p], p, (pages[i + 1] - 1) if i + 1 < len(pages) else page_count)
        for i, p in enumerate(pages)
    ]
    if chapters and chapters[0].page_from > 1:
        chapters.insert(0, Chapter(FRONT_MATTER, 1, chapters[0].page_from - 1))
    return chapters


def from_toc(toc: Sequence[Sequence[object]], page_count: int) -> list[Chapter]:
    """Chapters from PyMuPDF's ``get_toc()`` rows ``[level, title, page]``."""
    rows = [(int(str(r[0])), str(r[1]), int(str(r[2]))) for r in toc if len(r) >= 3]
    for level in sorted({lvl for lvl, _, _ in rows}):
        starts = [(page, title) for lvl, title, page in rows if lvl == level and page >= 1]
        if len({page for page, _ in starts}) >= 2:
            return _ranges(starts, page_count)
    return []


def from_headings(chunks: Sequence[SourceChunk], page_count: int) -> list[Chapter]:
    """Chapters from numbered headings on the first line of the indexed chunks."""
    lines = [(c.page, c.text.strip().split("\n", 1)[0].strip()) for c in chunks]
    for pattern in (_NAMED, _NUMBERED):
        expected, starts = 1, []
        for page, line in lines:
            m = pattern.match(line)
            if m and len(line) <= _MAX_TITLE and _number(m.group(1)) == expected:
                starts.append((page, line))
                expected += 1
        if len({page for page, _ in starts}) >= 2:
            return _ranges(starts, page_count)
    return []


def detect_chapters(source: str) -> list[Chapter]:
    """Best-effort chapters of *source*; always at least one, covering the whole book."""
    chunks = get_source_chunks(source)
    page_count = max((c.page for c in chunks), default=1)
    toc: list[list[object]] = []
    pdf = settings.pdfs_dir / source
    if pdf.is_file():
        with fitz.open(pdf) as doc:
            page_count = doc.page_count or page_count
            toc = doc.get_toc(simple=True)
    return (
        from_toc(toc, page_count)
        or from_headings(chunks, page_count)
        or [Chapter(WHOLE_BOOK, 1, page_count)]
    )


def chapters_for(source: str) -> list[Chapter]:
    """*source*'s stored chapters, detecting and storing them on first use."""
    chapters = list_chapters(source)
    if not chapters:
        chapters = detect_chapters(source)
        set_chapters(source, chapters)
    return chapters

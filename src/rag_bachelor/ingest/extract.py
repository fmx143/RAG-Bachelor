"""Extract text pages from PDFs using PyMuPDF."""

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

import fitz  # pymupdf

# A page whose text content is shorter than this (after stripping) is treated
# as effectively empty — likely a cover image, blank page, or failed OCR page.
_EMPTY_PAGE_THRESHOLD = 20

# Running headers/footers of scanned books: a line among the first/last few of a
# page that recurs (fuzzily — OCR misreads "IDEC" as "1DEC") on this share of pages.
_EDGE_LINES = 3
_BOILERPLATE_MIN_SHARE = 0.25
_BOILERPLATE_MIN_LEN = 10
_FUZZY_RATIO = 0.85
# OCR'd checkbox/arrow bullet glyphs ("J ", "•/ ") at line start.
_OCR_BULLET = re.compile(r"^(?:J|•/)[ \t]+", re.MULTILINE)


@dataclass
class Page:
    source: str  # PDF filename (not the full path)
    page_num: int  # 1-indexed page number, used in citations
    text: str  # extracted text content
    is_empty: bool  # True when text is suspiciously short
    has_images: bool = False  # True when the page embeds at least one image/figure


def _similar(a: str, b: str) -> bool:
    sm = SequenceMatcher(None, a, b)
    return (
        sm.real_quick_ratio() >= _FUZZY_RATIO
        and sm.quick_ratio() >= _FUZZY_RATIO
        and sm.ratio() >= _FUZZY_RATIO
    )


def _edges(lines: list[str]) -> list[str]:
    return [
        ln for ln in lines[:_EDGE_LINES] + lines[-_EDGE_LINES:] if len(ln) >= _BOILERPLATE_MIN_LEN
    ]


def _boilerplate_lines(pages_lines: list[list[str]]) -> set[str]:
    """Return every variant of the header/footer lines repeated across many pages."""
    reps: list[str] = []
    variants: list[set[str]] = []
    counts: list[int] = []
    for lines in pages_lines:
        seen: set[int] = set()
        for line in _edges(lines):
            j = next((k for k, rep in enumerate(reps) if _similar(line, rep)), None)
            if j is None:
                reps.append(line)
                variants.append(set())
                counts.append(0)
                j = len(reps) - 1
            variants[j].add(line)
            if j not in seen:
                seen.add(j)
                counts[j] += 1
    min_count = max(3, _BOILERPLATE_MIN_SHARE * len(pages_lines))
    return {v for vs, n in zip(variants, counts, strict=True) if n >= min_count for v in vs}


def _strip_boilerplate(line: str, boiler: set[str]) -> str:
    """Drop *line* if it is boilerplate, or the boilerplate OCR glued onto its end."""
    if line in boiler:
        return ""
    return next((line.removesuffix(b) for b in boiler if line.endswith(b)), line)


def clean_pages(texts: list[str]) -> list[str]:
    """Strip scan/OCR artifacts: soft hyphens, running headers/footers, bullet glyphs."""
    texts = [t.replace("\xad\n", "").replace("\xad", "") for t in texts]
    pages_lines = [[ln.strip() for ln in t.splitlines()] for t in texts]
    boiler = _boilerplate_lines(pages_lines)
    cleaned: list[str] = []
    for lines in pages_lines:
        kept = [s for ln in lines if (s := _strip_boilerplate(ln, boiler))]
        cleaned.append(_OCR_BULLET.sub("• ", "\n".join(kept)).strip())
    return cleaned


def extract_pages(pdf_path: Path) -> list[Page]:
    """Extract all text pages from a PDF.

    Returns a :class:`Page` per physical page. Empty pages (below threshold)
    are flagged so the caller can warn the user; they are NOT indexed.
    """
    with fitz.open(str(pdf_path)) as doc:
        raw = [(page.get_text(), bool(page.get_images())) for page in doc]
    texts = clean_pages([text for text, _ in raw])
    return [
        Page(
            source=pdf_path.name,
            page_num=i,
            text=text,
            is_empty=len(text) < _EMPTY_PAGE_THRESHOLD,
            has_images=has_images,
        )
        for i, (text, (_, has_images)) in enumerate(zip(texts, raw, strict=True), start=1)
    ]


def render_page_png(pdf_path: Path, page_num: int, dpi: int = 110) -> bytes:
    """Render one 1-indexed page of *pdf_path* to PNG bytes."""
    with fitz.open(str(pdf_path)) as doc:
        page = doc[page_num - 1]
        png: bytes = page.get_pixmap(dpi=dpi).tobytes("png")
        return png

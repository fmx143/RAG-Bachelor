"""Extract text pages from PDFs using PyMuPDF."""

from dataclasses import dataclass
from pathlib import Path

import fitz  # pymupdf

# A page whose text content is shorter than this (after stripping) is treated
# as effectively empty — likely a cover image, blank page, or failed OCR page.
_EMPTY_PAGE_THRESHOLD = 20


@dataclass
class Page:
    source: str     # PDF filename (not the full path)
    page_num: int   # 1-indexed page number, used in citations
    text: str       # extracted text content
    is_empty: bool  # True when text is suspiciously short


def extract_pages(pdf_path: Path) -> list[Page]:
    """Extract all text pages from a PDF.

    Returns a :class:`Page` per physical page. Empty pages (below threshold)
    are flagged so the caller can warn the user; they are NOT indexed.
    """
    pages: list[Page] = []
    with fitz.open(str(pdf_path)) as doc:
        for i, page in enumerate(doc, start=1):
            text = page.get_text().strip()
            pages.append(
                Page(
                    source=pdf_path.name,
                    page_num=i,
                    text=text,
                    is_empty=len(text) < _EMPTY_PAGE_THRESHOLD,
                )
            )
    return pages

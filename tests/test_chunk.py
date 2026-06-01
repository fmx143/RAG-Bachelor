"""Tests for the PDF extraction and chunking pipeline."""

from __future__ import annotations

from pathlib import Path

import fitz

from rag_bachelor.ingest.chunk import chunk_pages
from rag_bachelor.ingest.extract import Page, extract_pages

# ── Extraction ────────────────────────────────────────────────────────────────


def test_extract_returns_correct_page_count(sample_pdf: Path) -> None:
    pages = extract_pages(sample_pdf)
    assert len(pages) == 2


def test_extract_page_numbers_are_one_indexed(sample_pdf: Path) -> None:
    pages = extract_pages(sample_pdf)
    assert [p.page_num for p in pages] == [1, 2]


def test_extract_captures_source_filename(sample_pdf: Path) -> None:
    pages = extract_pages(sample_pdf)
    for p in pages:
        assert p.source == sample_pdf.name


def test_extract_non_empty_pages(sample_pdf: Path) -> None:
    pages = extract_pages(sample_pdf)
    assert all(not p.is_empty for p in pages)


def test_extract_french_text_present(sample_pdf: Path) -> None:
    pages = extract_pages(sample_pdf)
    combined = " ".join(p.text for p in pages).lower()
    assert "algorithme" in combined


def test_empty_page_flagged(tmp_path: Path) -> None:
    """A page with no text should be flagged as empty."""
    pdf_path = tmp_path / "blank.pdf"
    doc = fitz.open()
    doc.new_page()  # blank
    doc.save(str(pdf_path))
    doc.close()

    pages = extract_pages(pdf_path)
    assert len(pages) == 1
    assert pages[0].is_empty


# ── Chunking ──────────────────────────────────────────────────────────────────


def test_chunk_pages_produces_chunks(sample_pdf: Path) -> None:
    pages = extract_pages(sample_pdf)
    chunks = chunk_pages(pages)
    assert len(chunks) > 0


def test_chunks_have_non_empty_text(sample_pdf: Path) -> None:
    pages = extract_pages(sample_pdf)
    chunks = chunk_pages(pages)
    for c in chunks:
        assert c.text.strip(), "All chunks must have non-empty text"


def test_chunks_carry_source_and_page(sample_pdf: Path) -> None:
    pages = extract_pages(sample_pdf)
    chunks = chunk_pages(pages)
    for c in chunks:
        assert c.source == sample_pdf.name
        assert c.page_num in (1, 2)


def test_empty_pages_not_chunked(tmp_path: Path) -> None:
    """Empty pages must produce zero chunks."""
    blank = Page(source="blank.pdf", page_num=1, text="", is_empty=True)
    chunks = chunk_pages([blank])
    assert chunks == []


def test_chunk_size_respected(sample_pdf: Path) -> None:
    """No chunk should exceed the configured chunk_size by more than one overlap."""
    from rag_bachelor.config import settings

    pages = extract_pages(sample_pdf)
    chunks = chunk_pages(pages)
    for c in chunks:
        # A small tolerance: a chunk may slightly exceed chunk_size due to separator logic
        assert len(c.text) <= settings.chunk_size + settings.chunk_overlap, (
            f"Chunk too large: {len(c.text)} chars"
        )

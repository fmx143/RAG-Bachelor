"""Tests for the PDF extraction and chunking pipeline."""

from __future__ import annotations

from pathlib import Path

import fitz

from rag_bachelor.ingest.chunk import _SEPARATORS, _split_text, _with_overlap
from rag_bachelor.ingest.extract import extract_pages

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


def test_consecutive_pieces_overlap() -> None:
    """Pieces split at a normal separator must still share text across the boundary."""
    words = " ".join(f"mot{i}" for i in range(400))
    pieces = _with_overlap(_split_text(words, _SEPARATORS, 900, 150), 150)
    assert len(pieces) > 1
    for prev, cur in zip(pieces, pieces[1:]):
        assert set(prev.split()) & set(cur.split())


def test_split_respects_chunk_size(sample_pdf: Path) -> None:
    text = "\n\n".join(p.text for p in extract_pages(sample_pdf)) * 20
    assert all(len(piece) <= 900 for piece in _split_text(text, _SEPARATORS, 900, 150))

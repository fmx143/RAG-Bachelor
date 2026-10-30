"""Chapter detection (bookmarks, numbered headings, fallback), storage and card mapping."""

from __future__ import annotations

from unittest.mock import patch

import fitz  # pymupdf

from rag_bachelor.config import settings
from rag_bachelor.core import chapters
from rag_bachelor.core.bank import SourceChunk
from rag_bachelor.study import store
from rag_bachelor.study.store import Chapter


def _chunks(*lines: tuple[int, str]) -> list[SourceChunk]:
    return [
        SourceChunk(f"b.pdf__p{p}__c{i}", p, i, f"{t}\ncorps") for i, (p, t) in enumerate(lines)
    ]


def test_from_toc_uses_shallowest_level_with_two_entries_and_adds_front_matter() -> None:
    toc = [
        [1, "Mon livre", 1],
        [2, "Chapitre 1  Bases", 3],
        [3, "1.1 Détail", 4],
        [2, "Chapitre 2 Suite", 10],
        [2, "Doublon", 10],
        [2, "Hors livre", 99],
    ]
    assert chapters.from_toc(toc, 20) == [
        Chapter(chapters.FRONT_MATTER, 1, 2),
        Chapter("Chapitre 1 Bases", 3, 9),
        Chapter("Chapitre 2 Suite", 10, 20),
    ]
    assert chapters.from_toc([[1, "Seul", 1]], 5) == []
    assert chapters.from_toc([], 5) == []


def test_from_headings_named_then_numbered_sequences() -> None:
    named = _chunks(
        (1, "Sommaire"),
        (2, "Chapitre I Les bases"),
        (3, "Chapitre I Les bases"),  # same section, next chunk
        (5, "Chapitre 3 Hors séquence"),  # skipped: II expected
        (7, "Chapitre II La suite"),
    )
    assert chapters.from_headings(named, 9) == [
        Chapter(chapters.FRONT_MATTER, 1, 1),
        Chapter("Chapitre I Les bases", 2, 6),
        Chapter("Chapitre II La suite", 7, 9),
    ]
    numbered = _chunks((1, "1. Introduction"), (2, "1.1 Détail"), (4, "2 Méthodes"))
    assert chapters.from_headings(numbered, 6) == [
        Chapter("1. Introduction", 1, 3),
        Chapter("2 Méthodes", 4, 6),
    ]
    one_page = _chunks((3, "1. Un"), (3, "2. Deux"))  # a list on one page, not chapters
    assert chapters.from_headings(one_page, 5) == []


def test_detect_reads_bookmarks_then_falls_back_to_whole_book() -> None:
    settings.pdfs_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    for _ in range(4):
        doc.new_page()
    doc.set_toc([[1, "Partie A", 1], [1, "Partie B", 3]])
    doc.save(settings.pdfs_dir / "b.pdf")
    with patch.object(chapters, "get_source_chunks", return_value=[]):
        assert chapters.detect_chapters("b.pdf") == [
            Chapter("Partie A", 1, 2),
            Chapter("Partie B", 3, 4),
        ]
    with patch.object(chapters, "get_source_chunks", return_value=_chunks((6, "Texte"))):
        assert chapters.detect_chapters("absent.pdf") == [Chapter(chapters.WHOLE_BOOK, 1, 6)]


def test_chapters_for_detects_once_then_keeps_edits() -> None:
    with patch.object(chapters, "detect_chapters", return_value=[Chapter("Auto", 1, 5)]) as det:
        assert chapters.chapters_for("b.pdf") == [Chapter("Auto", 1, 5)]
        store.set_chapters("b.pdf", [Chapter("Corrigé", 1, 2), Chapter("Suite", 3, 5)])
        assert [c.title for c in chapters.chapters_for("b.pdf")] == ["Corrigé", "Suite"]
    assert det.call_count == 1


def test_chapters_follow_rename_and_delete() -> None:
    store.add_document("a.pdf", "sha-a")
    store.set_chapters("a.pdf", [Chapter("Un", 1, 3)])
    store.rename_document("a.pdf", "S/a.pdf")
    assert store.list_chapters("a.pdf") == []
    assert store.list_chapters("S/a.pdf") == [Chapter("Un", 1, 3)]
    store.delete_document("S/a.pdf")
    assert store.list_chapters("S/a.pdf") == []


def test_bank_card_pages_and_due_cards_by_ids() -> None:
    items = [
        store.BankQuestion("Q1 ?", "R.", "facile", source="b.pdf", pages=[5, 2], chunk_ids=[]),
        store.BankQuestion("Q2 ?", "R.", "facile", source="b.pdf", pages=[8], chunk_ids=[]),
        store.BankQuestion("Q3 ?", "R.", "facile", source="b.pdf", pages=[9], chunk_ids=[]),
    ]
    store.add_bank_questions(items)
    q1, q2, _ = store.list_bank_questions(source="b.pdf", limit=10, order_asc=True)
    c1, c2 = store.materialize_bank_question(q1), store.materialize_bank_question(q2)
    pages = store.bank_card_pages("b.pdf")
    assert pages == {c1: 2, c2: 8}  # Q3 is not in the deck
    first_chapter = [cid for cid, p in pages.items() if 1 <= p <= 4]
    assert [c.id for c in store.get_due_cards(limit=10, card_ids=first_chapter)] == [c1]
    assert store.get_due_cards(limit=10, card_ids=[]) == []

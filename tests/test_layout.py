"""Section-based chunking of Docling layout items (src/rag_bachelor/ingest/layout.py)."""

from __future__ import annotations

import pytest

from rag_bachelor.config import settings
from rag_bachelor.ingest.layout import section_chunks


def test_section_chunks_split_on_headers_and_page_breaks() -> None:
    table = "| a | b |\n|---|---|\n| X |   |"
    items = [
        ("section_header", "1.1 Résultats", 10),
        ("text", "Les résultats sont la finalité.", 10),
        ("list_item", "J leur rentabilité.", 11),
        ("section_header", "1.2 Rôles", 11),
        ("table", table, 12),
    ]
    chunks = section_chunks(items, "livre.pdf")

    assert [c.text for c in chunks] == [
        "1.1 Résultats\nLes résultats sont la finalité.",
        "1.1 Résultats\nLes résultats sont la finalité. • leur rentabilité.",
        f"1.2 Rôles\n{table}",
    ]
    assert [c.page_num for c in chunks] == [10, 11, 12]
    assert [c.chunk_index for c in chunks] == [0, 1, 2]


def test_long_table_split_by_rows_repeats_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "chunk_size", 60)
    head = "| a | b |\n|---|---|"
    rows = [f"| ligne {i} | X |" for i in range(6)]
    items = [("text", "Intro.", 1), ("table", "\n".join([head, *rows]), 2), ("text", "Fin.", 2)]
    chunks = section_chunks(items, "livre.pdf")

    tables = [c.text for c in chunks if c.text.startswith("| a")]
    assert len(tables) > 1
    assert all(t.startswith(head + "\n| ligne") for t in tables)
    assert [r for t in tables for r in t.splitlines()[2:]] == rows
    assert chunks[0].text == "Intro." and chunks[-1].text == "Fin."


def test_huge_section_title_is_capped() -> None:
    items = [("section_header", "T" * 100_000, 1)] + [("text", f"para {i}", 1) for i in range(50)]
    chunks = section_chunks(items, "livre.pdf")
    assert all(len(c.text) <= settings.chunk_size + 201 for c in chunks)


def test_huge_table_head_is_not_repeated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "chunk_size", 60)
    head = "| " + "h" * 100_000 + " |\n|---|"
    table = "\n".join([head, *(f"| ligne {i} |" for i in range(20))])
    chunks = section_chunks([("table", table, 1)], "livre.pdf")
    assert sum(len(c.text) for c in chunks) < 2 * len(table)

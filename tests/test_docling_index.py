"""scripts/docling_index.py: section-based chunking of Docling items."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "docling_index", Path(__file__).parents[1] / "scripts" / "docling_index.py"
)
assert _spec and _spec.loader
docling_index = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(docling_index)


def test_section_chunks_split_on_headers_and_keep_first_page() -> None:
    table = "| a | b |\n|---|---|\n| X |   |"
    items = [
        ("section_header", "1.1 Résultats", 10),
        ("text", "Les résultats sont la finalité.", 10),
        ("list_item", "J leur rentabilité.", 11),
        ("section_header", "1.2 Rôles", 11),
        ("table", table, 12),
    ]
    chunks = docling_index.section_chunks(items, "livre.pdf")

    assert [c.text for c in chunks] == [
        "1.1 Résultats\nLes résultats sont la finalité.\n\n• leur rentabilité.",
        f"1.2 Rôles\n{table}",
    ]
    assert [c.page_num for c in chunks] == [10, 12]
    assert [c.chunk_index for c in chunks] == [0, 1]


def test_long_table_split_by_rows_repeats_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(docling_index.settings, "chunk_size", 60)
    head = "| a | b |\n|---|---|"
    rows = [f"| ligne {i} | X |" for i in range(6)]
    items = [("text", "Intro.", 1), ("table", "\n".join([head, *rows]), 2), ("text", "Fin.", 2)]
    chunks = docling_index.section_chunks(items, "livre.pdf")

    tables = [c.text for c in chunks if c.text.startswith("| a")]
    assert len(tables) > 1
    assert all(t.startswith(head + "\n| ligne") for t in tables)
    assert [r for t in tables for r in t.splitlines()[2:]] == rows
    assert chunks[0].text == "Intro." and chunks[-1].text == "Fin."

"""Revision-sheet tests: page-range filter, citation cleaning, map/reduce, store."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from rag_bachelor.core import sheet
from rag_bachelor.core.bank import SourceChunk
from rag_bachelor.study import store


def _chunks() -> list[SourceChunk]:
    return [SourceChunk(f"c.pdf__p{p}__c0", p, 0, f"texte page {p}") for p in (1, 2, 3, 4)]


def _run(chat: MagicMock, lo: int, hi: int) -> int | None:
    provider = MagicMock()
    provider.chat = chat
    with (
        patch.object(sheet, "get_source_chunks", return_value=_chunks()),
        patch.object(sheet, "get_provider", return_value=(provider, "ollama")),
    ):
        return sheet.generate_sheet("c.pdf", lo, hi)


def test_single_window_skips_reduce_and_strips_foreign_citations() -> None:
    chat = MagicMock(return_value="Notion A [p.2]. Notion B [p.9].")
    sheet_id = _run(chat, 2, 3)
    assert sheet_id is not None
    assert chat.call_count == 1
    sent = chat.call_args.args[0][1]["content"]
    assert "page 2" in sent and "page 3" in sent and "page 1" not in sent and "page 4" not in sent
    saved = store.get_sheet(sheet_id)
    assert saved is not None and saved.content == "Notion A [p.2]. Notion B ."


def test_multiple_windows_are_merged(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(sheet, "_WINDOW_SIZE", 2)
    chat = MagicMock(side_effect=["notes 1", "notes 2", "fiche finale [p.1]"])
    sheet_id = _run(chat, 1, 4)
    assert chat.call_count == 3  # 2 map + 1 reduce
    assert sheet_id is not None
    saved = store.get_sheet(sheet_id)
    assert saved is not None and saved.content == "fiche finale [p.1]"


def test_empty_range_raises() -> None:
    import pytest

    with pytest.raises(ValueError):
        _run(MagicMock(), 50, 60)


def test_store_list_delete_and_rename() -> None:
    sid = store.add_sheet("a.pdf", 1, 5, "contenu")
    assert [s.id for s in store.list_sheets()] == [sid]
    assert store.list_sheets()[0].content == ""
    store.rename_document("a.pdf", "b.pdf")
    renamed = store.get_sheet(sid)
    assert renamed is not None and renamed.source == "b.pdf"
    store.delete_sheet(sid)
    assert store.get_sheet(sid) is None


def test_routes_render_link_citations_and_delete() -> None:
    from fastapi.testclient import TestClient

    from rag_bachelor.app.web import server

    sid = store.add_sheet(
        "Bachelor/Cours/a b.pdf", 1, 5, "## Titre\n\nUn fait [p.2] <script>x</script>"
    )
    with TestClient(server.app) as client:
        assert "Bachelor/Cours/a b.pdf" in client.get("/sheets").text
        page = client.get(f"/sheets/{sid}").text
        assert "/docs/page.png?path=Bachelor%2FCours%2Fa%20b.pdf&amp;page=2" in page
        assert "<script>x" not in page
        assert client.get("/sheets/999").status_code == 404
        assert (
            client.post(
                "/sheets/generate", data={"source": "a", "page_from": 5, "page_to": 1}
            ).text.count("Plage de pages invalide")
            == 1
        )
        assert client.post(f"/sheets/{sid}/delete").status_code == 200
    assert store.get_sheet(sid) is None


def test_citation_inside_link_title_is_not_rewritten() -> None:
    from rag_bachelor.app.web.routes.sheets import _render_sheet

    html = str(_render_sheet('Vu [p.2] et [x](http://a.fr "t [p.3]")', "a.pdf"))
    assert 'data-page="2"' in html and 'page=2"' in html
    assert 'title="t [p.3]"' in html and html.count("<a ") == 2

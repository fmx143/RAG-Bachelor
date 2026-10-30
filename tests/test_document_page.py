"""Document page: kind by folder, chapter editor, per-chapter stats and chapter revision."""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from rag_bachelor.app.web import server
from rag_bachelor.app.web.routes import bank as bank_routes
from rag_bachelor.app.web.routes.document import parse_chapters
from rag_bachelor.ingest import index
from rag_bachelor.study import store
from rag_bachelor.study.stats import chapter_stats
from rag_bachelor.study.store import BankQuestion, Chapter
from rag_bachelor.study.subjects import doc_kind

DOC = "Bachelor/Livres/Cours un.pdf"


def _index(source: str = DOC, pages: int = 6) -> None:
    ids = [f"{source}__p{p}__c0" for p in range(1, pages + 1)]
    index.get_collection().upsert(
        ids=ids,
        documents=[f"Texte page {p}" for p in range(1, pages + 1)],
        embeddings=[[1.0, 0.0, 0.0]] * pages,  # type: ignore[arg-type]
        metadatas=[{"source": source, "page": p} for p in range(1, pages + 1)],  # type: ignore[arg-type]
    )


def _bank(*first_pages: int) -> list[int]:
    """One bank question per page given, each materialized as a deck card; returns card ids."""
    store.add_bank_questions(
        [BankQuestion(f"Q p{p} ?", "R.", "facile", DOC, [p], []) for p in first_pages]
    )
    items = store.list_bank_questions(source=DOC, limit=50, order_asc=True)
    return [store.materialize_bank_question(q) for q in items]


def test_doc_kind_follows_the_folder() -> None:
    assert doc_kind("Bachelor/Livres/a.pdf") == "livre"
    assert doc_kind("Bachelor/Livres/Compta/a.pdf") == "livre"
    assert doc_kind("Bachelor/Sources/a.pdf") == "source"
    assert doc_kind("Bachelor/a.pdf") is None
    assert doc_kind("Bachelor/Divers/Livres/a.pdf") is None
    assert doc_kind("Livres/a.pdf") is None  # "Livres" as the subject itself


def test_parse_chapters_validates_sorts_and_names() -> None:
    assert parse_chapters(["B", "", " "], ["4", "1", ""], ["6", "3", ""], 6) == [
        Chapter("Chapitre 1", 1, 3),
        Chapter("B", 4, 6),
    ]
    assert parse_chapters(["A"], ["2"], ["9"], 6) == "pages"
    assert parse_chapters(["A"], ["x"], ["2"], 6) == "pages"
    assert parse_chapters(["A", "B"], ["1", "3"], ["4", "6"], 6) == "overlap"
    assert parse_chapters([""], [""], [""], 6) == "empty"


def test_chapter_stats_place_cards_by_first_page() -> None:
    c2, c5 = _bank(2, 5)
    store.add_card("Hors banque ?", "R.", topic=DOC, difficulty="facile")
    chapters = [Chapter("Un", 1, 3), Chapter("Deux", 4, 6)]
    stats, unplaced = chapter_stats(DOC, chapters)
    assert [(s.questions, s.cards, s.due) for s in stats] == [(1, 1, 1), (1, 1, 1)]
    assert stats[0].mastery_pct is not None and unplaced == 1
    assert chapter_stats(DOC, [Chapter("Vide", 1, 1)])[0][0].mastery_pct is None


def test_document_page_tabs_and_unknown_document() -> None:
    _index()
    store.add_sheet(DOC, 1, 3, "## Fiche")
    store.add_sheet("Bachelor/Livres/autre.pdf", 1, 2, "## Autre")
    with TestClient(server.app) as client:
        url = "/document/Bachelor/Livres/Cours%20un.pdf"
        page = client.get(url).text
        assert "Livre · 6 pages" in page and "Tout le livre" in page  # no PDF on disk: fallback
        for tab in ("questions", "fiches", "cartes", "audio"):
            assert client.get(f"{url}?tab={tab}").status_code == 200
        sheets = client.get(f"{url}?tab=fiches").text
        assert sheets.count('href="/sheets/') == 1  # only this document's sheet
        assert client.get("/document/Bachelor/Livres/absent.pdf").status_code == 404


def test_chapter_editor_saves_rejects_and_redetects() -> None:
    _index()
    with TestClient(server.app) as client:
        client.get("/document/" + DOC)  # first view stores the detected chapters
        form = {
            "path": DOC,
            "title": ["Intro", "Suite"],
            "page_from": ["1", "3"],
            "page_to": ["2", "6"],
        }
        r = client.post("/document-chapters", data=form, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].endswith("?tab=apercu")
        assert [c.title for c in store.list_chapters(DOC)] == ["Intro", "Suite"]
        bad = {**form, "page_to": ["4", "6"]}
        r = client.post("/document-chapters", data=bad, follow_redirects=False)
        assert r.headers["location"].endswith("err=overlap")
        assert "se chevauchent" in client.get(r.headers["location"]).text
        client.post("/document-chapters", data={"path": DOC, "redetect": "1"})
        assert [c.title for c in store.list_chapters(DOC)] == ["Tout le livre"]


def test_revision_by_chapter_and_bank_generation_by_range() -> None:
    _index()
    _bank(2, 5)
    with TestClient(server.app) as client:
        first = client.get("/revision", params={"source": DOC, "pages": "4-6"}).text
        assert "Q p5 ?" in first and "Q p2 ?" not in first
        assert 'name="pages" value="4-6"' in first
        with patch.object(bank_routes, "generate_bank_for_source") as gen:
            client.post("/bank/generate", data={"source": DOC, "page_from": "4", "page_to": "6"})
            client.post("/bank/generate", data={"source": DOC})
        assert gen.call_args_list[0].kwargs["page_range"] == (4, 6)
        assert gen.call_args_list[1].kwargs["page_range"] is None

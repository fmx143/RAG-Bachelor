"""Per-subject content language: English subjects get English output, French ones are unchanged."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from rag_bachelor.app.web import server
from rag_bachelor.config import settings
from rag_bachelor.core import keywords, lang, qa, sheet, tutor
from rag_bachelor.core.bank import SourceChunk
from rag_bachelor.core.qtypes import _normalize_difficulty
from rag_bachelor.study.srs import Card
from rag_bachelor.study.subjects import set_active_subject, set_subject_lang, subject_lang


def _activate(subject: str) -> None:
    (settings.pdfs_dir / subject).mkdir(parents=True, exist_ok=True)  # subjects = folders on disk
    set_active_subject(subject)


def _provider(reply: str = "{}") -> MagicMock:
    provider = MagicMock()
    provider.chat.return_value = reply
    return provider


def _system(provider: MagicMock, call: int = 0) -> str:
    return str(provider.chat.call_args_list[call].args[0][0]["content"])


def test_subject_lang_defaults_to_french_and_validates() -> None:
    assert subject_lang("PMI") == "fr"
    set_subject_lang("PMI", "en")
    assert subject_lang("PMI") == "en" and subject_lang("Bachelor") == "fr"
    with pytest.raises(ValueError):
        set_subject_lang("PMI", "de")
    assert lang.write_in("fr") == "" and lang.keywords_in("fr") == ""
    assert "ANGLAIS" in lang.write_in("en") and "anglais" in lang.keywords_in("en")
    assert lang.lang_of("PMI/Livres/PMBOK.pdf") == "en"


def test_generators_follow_the_document_subject() -> None:
    set_subject_lang("PMI", "en")
    chunks = [SourceChunk("x__p1__c0", 1, 0, "Project charter.")]
    for source, english in (("PMI/Livres/PMBOK.pdf", True), ("Bachelor/Livres/c.pdf", False)):
        provider = _provider("## Notes [p.1]")
        with (
            patch.object(sheet, "get_source_chunks", return_value=chunks),
            patch.object(sheet, "get_provider", return_value=(provider, "ollama")),
        ):
            sheet.generate_sheet(source, 1, 1)
        assert ("ANGLAIS" in _system(provider)) is english


def test_ask_and_keywords_follow_the_active_subject() -> None:
    set_subject_lang("PMI", "en")
    _activate("PMI")
    provider = _provider('{"bas_niveau": ["charter"], "haut_niveau": []}')
    with patch.object(keywords, "get_provider", return_value=(provider, "ollama")):
        assert keywords.extract_keywords("Qu'est-ce que la charte ?") == ["charter"]
    assert "en anglais" in _system(provider)

    hit = MagicMock(source="PMI/Livres/PMBOK.pdf", page=3, text="Charter.")
    provider = _provider("The charter…")
    with (
        patch.object(qa, "retrieve", return_value=[hit]),
        patch.object(qa, "extract_keywords", return_value=[]),
        patch.object(qa, "get_provider", return_value=(provider, "ollama")),
    ):
        qa.answer_question("Qu'est-ce que la charte ?")
    assert "ANGLAIS" in _system(provider)


def test_tutor_feedback_language_and_english_enums() -> None:
    set_subject_lang("PMI", "en")
    card = Card(id=1, question="Q?", answer="A.", topic="PMI/Sources/q.pdf", difficulty="moyen")
    provider = _provider('{"verdict": "correct", "feedback": "Good.", "manque": ""}')
    with patch.object(tutor, "get_provider", return_value=(provider, "ollama")):
        verdict = tutor.grade_answer(card, "", "my answer")
    assert verdict is not None and verdict.verdict == "juste"
    assert "en anglais" in _system(provider)
    card.topic = "Bachelor/Livres/c.pdf"
    assert "en français" in tutor._in_lang(tutor._GRADE_PROMPT, card)
    assert [_normalize_difficulty(d) for d in ("Easy", "medium", "HARD", "??")] == [
        "facile",
        "moyen",
        "difficile",
        "moyen",
    ]


def test_settings_toggle_sets_the_active_subject_language() -> None:
    _activate("PMI")
    with TestClient(server.app) as client:
        assert "Langue du contenu" in client.get("/settings").text
        r = client.post("/settings/lang", data={"lang": "en"})
        assert r.status_code == 200 and 'value="en" checked' in r.text
        client.post("/settings/lang", data={"lang": "xx"})
    assert subject_lang("PMI") == "en"

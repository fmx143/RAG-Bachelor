"""Exam domains: official-name matching, LLM classification, storage, filters and routes."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from rag_bachelor.app.web import server
from rag_bachelor.app.web.routes import bank as bank_routes
from rag_bachelor.app.web.routes import domains as domain_routes
from rag_bachelor.config import settings
from rag_bachelor.core import domains
from rag_bachelor.study import store
from rag_bachelor.study.subjects import set_active_subject

SRC = "PMI/Sources/quiz.pdf"


def _activate() -> None:
    (settings.pdfs_dir / "PMI").mkdir(parents=True, exist_ok=True)
    set_active_subject("PMI")


def _bank(*questions: str) -> list[store.BankQuestion]:
    store.add_bank_questions(
        [store.BankQuestion(q, "A.", "moyen", SRC, [1], [], qtype="free") for q in questions]
    )
    return store.list_bank_questions(source=SRC, limit=50, order_asc=True)


def _provider(items: list[dict[str, object]]) -> MagicMock:
    provider = MagicMock()
    provider.chat.return_value = json.dumps({"items": items})
    return provider


def test_match_domain_accepts_official_short_and_long_prefix_only() -> None:
    assert domains.match_domain("PMP", "process") == "Process"
    assert domains.match_domain("PMP", "Business Env.") == "Business Environment"
    assert domains.match_domain("CAPM", "Agile") == "Agile Frameworks/Methodologies"
    assert (
        domains.match_domain("CAPM", "Predictive, Plan") == "Predictive, Plan-Based Methodologies"
    )
    assert domains.match_domain("PMP", "P") is None  # too short to be a prefix
    assert domains.match_domain("PMP", "Leadership") is None
    assert domains.match_domain("XYZ", "Process") is None


def test_classify_keeps_valid_tags_only() -> None:
    qs = _bank("Q1 ?", "Q2 ?", "Q3 ?")
    provider = _provider(
        [
            {"n": 1, "PMP": "People", "CAPM": "Agile"},
            {"n": 2, "PMP": "Leadership"},  # unknown domain: nothing kept
            {"n": 9, "PMP": "Process"},  # out of range
            "junk",
        ]
    )
    with patch.object(domains, "get_provider", return_value=(provider, "ollama")):
        tags = domains.classify(qs, ["PMP", "CAPM"])
    assert tags == {qs[0].id: {"PMP": "People", "CAPM": "Agile Frameworks/Methodologies"}}


def test_tag_subject_fills_gaps_without_overwriting_manual_tags() -> None:
    domains.set_subject_frameworks("PMI", ["CAPM", "PMP", "nope"])
    assert domains.subject_frameworks("PMI") == ["PMP", "CAPM"]
    q1, q2 = _bank("Q1 ?", "Q2 ?")
    assert q1.id is not None and q2.id is not None
    store.set_bank_domains(q1.id, {"PMP": "Business Environment"})  # corrected by hand
    provider = _provider(
        [
            {"n": 1, "PMP": "People", "CAPM": "Fundamentals"},
            {"n": 2, "PMP": "Process", "CAPM": "Business Analysis"},
        ]
    )
    with patch.object(domains, "get_provider", return_value=(provider, "ollama")):
        assert domains.tag_subject("PMI") == 2
    got = {q.id: q.domains for q in store.list_bank_questions(source=SRC, limit=10)}
    assert got[q1.id] == {
        "PMP": "Business Environment",
        "CAPM": "Project Management Fundamentals and Core Concepts",
    }
    assert got[q2.id] == {"PMP": "Process", "CAPM": "Business Analysis Frameworks"}
    assert store.bank_questions_missing_domains("PMI", ["PMP", "CAPM"]) == []
    assert domains.tag_subject("Bachelor") == 0  # no framework: no LLM call


def test_domain_filter_and_manual_correction_routes() -> None:
    _activate()
    domains.set_subject_frameworks("PMI", ["PMP"])
    q1, q2 = _bank("Scope creep ?", "Team conflict ?")
    assert q1.id is not None and q2.id is not None
    store.set_bank_domains(q1.id, {"PMP": "Process"})
    store.set_bank_domains(q2.id, {"PMP": "People"})
    assert [q.id for q in store.list_bank_questions(domain="PMP:People")] == [q2.id]
    assert store.count_bank_questions(domain="PMP:Process") == 1
    with TestClient(server.app) as client:
        page = client.get("/bank/list", params={"source": SRC, "domain": "PMP:People"}).text
        assert "Team conflict ?" in page and "Scope creep ?" not in page
        assert 'name="domain" value="PMP:People"' in page
        r = client.post(f"/bank/{q1.id}/domains", data={f"d{q1.id}_PMP": "People", "ids": "x"})
        assert "Domaines enregistrés" in r.text
        client.post(f"/bank/{q2.id}/domains", data={f"d{q2.id}_PMP": ""})  # back to untagged
    assert store.count_bank_questions(domain="PMP:People") == 1
    assert store.get_bank_question(q2.id).domains == {}  # type: ignore[union-attr]


def test_settings_panel_and_tagging_job() -> None:
    _activate()
    _bank("Q1 ?")
    with TestClient(server.app) as client:
        assert "Examens visés" in client.get("/settings").text
        r = client.post("/domains/frameworks", data={"framework": ["PMP", "CAPM"]})
        assert "Classer 1 question sans domaine" in r.text
        with patch.object(domain_routes, "tag_subject", return_value=1) as tag:
            assert "Classement en cours" in client.post("/domains/tag").text
        assert tag.call_args.args[0] == "PMI"  # the job ran after the response
        assert "1 question(s) classée(s)" in client.get("/domains/status").text


def test_bank_generation_tags_new_questions() -> None:
    with (
        patch.object(bank_routes, "generate_bank_for_source"),
        patch.object(bank_routes, "tag_subject") as tag,
    ):
        bank_routes._run_bank_job(SRC, "free", 5)
    assert tag.call_args.args[0] == "PMI"

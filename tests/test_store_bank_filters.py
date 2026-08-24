"""get_due_cards() filters, and materialize_bank_question() linking/idempotency."""

from __future__ import annotations

from rag_bachelor.study import store


def _bq(question: str, source: str = "cours.pdf", difficulty: str = "facile") -> store.BankQuestion:
    return store.BankQuestion(
        question=question,
        answer="Une réponse.",
        difficulty=difficulty,
        source=source,
        pages=[1],
        chunk_ids=[f"{source}__p1__c0"],
    )


def test_get_due_cards_filters_by_source_difficulty_qtype() -> None:
    store.add_card("Q maths facile ?", "R.", topic="maths.pdf", difficulty="facile", qtype="free")
    store.add_card("Q maths difficile ?", "R.", topic="maths.pdf", difficulty="difficile", qtype="free")
    store.add_card("Q info ?", "R.", topic="info.pdf", difficulty="facile", qtype="tf")

    assert [c.topic for c in store.get_due_cards(limit=10, source="maths.pdf")] == [
        "maths.pdf",
        "maths.pdf",
    ]
    assert [c.difficulty for c in store.get_due_cards(limit=10, source="maths.pdf", difficulty="difficile")] == [
        "difficile"
    ]
    assert store.get_due_cards(limit=10, qtype="tf") == [c for c in store.get_due_cards(limit=10) if c.qtype == "tf"]
    assert store.get_due_cards(limit=10, source="inconnu.pdf") == []


def test_materialize_bank_question_links_and_copies_fields() -> None:
    store.add_bank_questions([_bq("Question test ?")])
    q = store.list_bank_questions()[0]
    assert q.id is not None and q.card_id is None

    card_id = store.materialize_bank_question(q)

    card = store.get_card(card_id)
    assert card is not None
    assert card.question == q.question
    assert card.topic == q.source
    assert card.difficulty == q.difficulty

    refreshed = store.get_bank_question(q.id)
    assert refreshed is not None and refreshed.card_id == card_id


def test_materialize_bank_question_called_twice_creates_two_cards() -> None:
    """Not idempotent by itself — callers must check card_id/deck first; this
    documents that materializing the same row twice creates a second card."""
    store.add_bank_questions([_bq("Question test ?")])
    q = store.list_bank_questions()[0]

    first_card_id = store.materialize_bank_question(q)
    second_card_id = store.materialize_bank_question(q)

    assert first_card_id != second_card_id
    assert store.get_card(first_card_id) is not None
    assert store.get_card(second_card_id) is not None

"""Question-bank tests: store round-trip/dedupe/filters, and generation windowing/dedup."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from rag_bachelor.core import bank
from rag_bachelor.study import store


def _mock_provider(reply: str) -> MagicMock:
    provider = MagicMock()
    provider.chat.return_value = reply
    return provider


def _bq(question: str, difficulty: str = "facile", source: str = "cours.pdf") -> store.BankQuestion:
    return store.BankQuestion(
        question=question,
        answer="Une réponse.",
        difficulty=difficulty,
        source=source,
        pages=[3, 4],
        chunk_ids=[f"{source}__p3__c0", f"{source}__p4__c0"],
    )


def test_bank_store_roundtrip_and_dedupe() -> None:
    inserted = store.add_bank_questions([_bq("Q1 ?"), _bq("Q2 ?", "difficile")])
    assert inserted == 2
    # Same source+question is ignored on rerun
    assert store.add_bank_questions([_bq("Q1 ?")]) == 0

    items = store.list_bank_questions()
    assert len(items) == 2
    assert items[0].pages == [3, 4]
    assert items[0].chunk_ids[0].startswith("cours.pdf__p3")
    assert store.count_bank_questions() == 2
    assert store.count_bank_questions(difficulty="difficile") == 1
    assert store.count_bank_questions(source="autre.pdf") == 0
    assert store.bank_sources() == ["cours.pdf"]


def test_bank_filters_and_card_link() -> None:
    store.add_bank_questions([_bq("Q1 ?"), _bq("Q2 ?", "moyen", source="autre.pdf")])
    only_autre = store.list_bank_questions(source="autre.pdf")
    assert [q.question for q in only_autre] == ["Q2 ?"]

    q = only_autre[0]
    assert q.id is not None and q.card_id is None
    card_id = store.add_card(q.question, q.answer, topic=q.source, difficulty=q.difficulty)
    store.link_bank_card(q.id, card_id)
    refreshed = store.get_bank_question(q.id)
    assert refreshed is not None and refreshed.card_id == card_id

    store.delete_bank_question(q.id)
    assert store.get_bank_question(q.id) is None


def test_bank_search_filters_by_question_text() -> None:
    store.add_bank_questions(
        [_bq("Qu'est-ce qu'un algorithme ?"), _bq("Quelle est la complexité du tri rapide ?")]
    )
    assert [q.question for q in store.list_bank_questions(search="algorithme")] == [
        "Qu'est-ce qu'un algorithme ?"
    ]
    assert store.count_bank_questions(search="complexité") == 1
    assert store.count_bank_questions(search="inexistant") == 0
    # SQLite's LIKE folds ASCII case, Postgres' doesn't — _bank_where() must
    # LOWER() both sides itself rather than rely on backend LIKE semantics.
    assert store.count_bank_questions(search="ALGORITHME") == 1


def test_delete_bank_questions_bulk() -> None:
    store.add_bank_questions([_bq("Q1 ?"), _bq("Q2 ?"), _bq("Q3 ?")])
    ids = [q.id for q in store.list_bank_questions() if q.id is not None]
    assert len(ids) == 3

    removed = store.delete_bank_questions(ids[:2])
    assert removed == 2
    assert store.count_bank_questions() == 1

    assert store.delete_bank_questions([]) == 0


def test_record_bank_attempt_stores_int_not_bool() -> None:
    store.add_bank_questions([_bq("Q1 ?")])
    q = store.list_bank_questions()[0]
    assert q.id is not None
    store.record_bank_attempt(q.id, True)
    refreshed = store.get_bank_question(q.id)
    assert refreshed is not None
    # Must be an int (1), not a bool: psycopg maps Python bool -> PG boolean,
    # which PG refuses to store into an INTEGER column.
    assert refreshed.last_result == 1
    assert type(refreshed.last_result) is int


# ── windowing / generation ─────────────────────────────────────────────────────


def test_get_source_chunks_sorted_by_page_then_index() -> None:
    fake = {
        "ids": ["c.pdf__p2__c0", "c.pdf__p1__c1", "c.pdf__p1__c0", "weird-id"],
        "documents": ["t3", "t2", "t1", "tx"],
    }
    with patch.object(bank, "get_collection") as coll:
        coll.return_value.get.return_value = fake
        chunks = bank.get_source_chunks("c.pdf")
    assert [(c.page, c.index, c.text) for c in chunks] == [(1, 0, "t1"), (1, 1, "t2"), (2, 0, "t3")]


def test_generate_bank_refs_come_from_window_not_llm() -> None:
    """References must be the window's pages/chunk IDs, whatever the LLM says."""
    fake = {
        "ids": [f"c.pdf__p{p}__c0" for p in range(1, 6)],  # 5 chunks → windows of 4 + 1
        "documents": [f"texte {p}" for p in range(1, 6)],
    }
    llm_reply = (
        '{"items": [{"question": "Q ?", "answer": "R (voir page 999).", "difficulty": "facile"}]}'
    )
    provider = _mock_provider(llm_reply)
    progress: list[tuple[int, int, int]] = []
    with (
        patch.object(bank, "get_collection") as coll,
        patch.object(bank, "get_provider", return_value=(provider, "ollama")),
        patch.object(bank, "embed", return_value=[[1.0, 0.0]]) as mock_embed,
    ):
        coll.return_value.get.return_value = fake
        inserted = bank.generate_bank_for_source(
            "c.pdf", progress_cb=lambda i, t, s: progress.append((i, t, s))
        )

    assert provider.chat.call_count == 2  # two windows
    assert mock_embed.call_count == 2
    # Second window returns the same question with an identical embedding →
    # skipped both as an exact-text repeat and as a near-duplicate.
    assert progress == [(1, 999_999, 0), (1, 999_999, 1)]
    assert inserted == 1

    saved = store.list_bank_questions(source="c.pdf")
    assert store.count_bank_questions(source="c.pdf") == 1
    assert saved[0].pages == [1, 2, 3, 4]  # first window's pages, not the LLM's "page 999"
    assert saved[0].chunk_ids == [f"c.pdf__p{p}__c0" for p in range(1, 5)]


def test_generate_bank_stops_at_target() -> None:
    """Generation stops once *target* unique questions have been inserted."""
    fake = {
        "ids": [f"c.pdf__p{p}__c0" for p in range(1, 13)],  # 12 chunks → 3 windows of 4
        "documents": [f"texte {p}" for p in range(1, 13)],
    }
    calls = {"n": 0}

    def _reply(*_args: object, **_kwargs: object) -> str:
        calls["n"] += 1
        n = calls["n"]
        return f'{{"items": [{{"question": "Q{n} ?", "answer": "R.", "difficulty": "facile"}}]}}'

    def _fake_embed(texts: list[str]) -> list[list[float]]:
        # Distinct, orthogonal-ish vectors so nothing is treated as a near-duplicate.
        return [[0.0] * calls["n"] + [1.0] for _ in texts]

    provider = MagicMock()
    provider.chat.side_effect = _reply
    with (
        patch.object(bank, "get_collection") as coll,
        patch.object(bank, "get_provider", return_value=(provider, "ollama")),
        patch.object(bank, "embed", side_effect=_fake_embed),
    ):
        coll.return_value.get.return_value = fake
        inserted = bank.generate_bank_for_source("c.pdf", target=2)

    assert inserted == 2
    assert provider.chat.call_count == 2  # stopped after the 2nd window filled the target
    assert store.count_bank_questions(source="c.pdf") == 2


def test_generate_bank_skips_near_duplicate_embedding() -> None:
    """A candidate whose embedding is near-identical to an existing one is skipped."""
    fake = {
        "ids": [f"c.pdf__p{p}__c0" for p in range(1, 3)],
        "documents": ["texte 1", "texte 2"],
    }
    llm_reply = (
        '{"items": [{"question": "Question reformulée ?", "answer": "R.", "difficulty": "facile"}]}'
    )
    with (
        patch.object(bank, "get_collection") as coll,
        patch.object(bank, "get_provider", return_value=(_mock_provider(llm_reply), "ollama")),
        patch.object(bank, "embed", return_value=[[0.6, 0.8]]),
        patch.object(bank, "bank_question_embeddings", return_value=[[0.6, 0.8]]),
    ):
        coll.return_value.get.return_value = fake
        inserted = bank.generate_bank_for_source("c.pdf")

    assert inserted == 0  # cosine similarity to the pre-existing vector is 1.0 > threshold
    assert store.count_bank_questions(source="c.pdf") == 0


def test_generate_bank_skips_mismatched_dimension_embedding() -> None:
    """A stale stored vector of a different length must never crash or false-match."""
    fake = {
        "ids": [f"c.pdf__p{p}__c0" for p in range(1, 3)],
        "documents": ["texte 1", "texte 2"],
    }
    llm_reply = '{"items": [{"question": "Q ?", "answer": "R.", "difficulty": "facile"}]}'
    with (
        patch.object(bank, "get_collection") as coll,
        patch.object(bank, "get_provider", return_value=(_mock_provider(llm_reply), "ollama")),
        patch.object(bank, "embed", return_value=[[0.6, 0.8]]),
        # Simulates a vector stored under a previous, differently-sized EMBEDDING_MODEL.
        patch.object(bank, "bank_question_embeddings", return_value=[[0.6, 0.8, 0.1]]),
    ):
        coll.return_value.get.return_value = fake
        inserted = bank.generate_bank_for_source("c.pdf")

    assert inserted == 1
    assert store.count_bank_questions(source="c.pdf") == 1


def test_generate_bank_should_stop_halts_before_next_window() -> None:
    fake = {
        "ids": [f"c.pdf__p{p}__c0" for p in range(1, 9)],  # 8 chunks → 2 windows of 4
        "documents": [f"texte {p}" for p in range(1, 9)],
    }
    llm_reply = '{"items": [{"question": "Q ?", "answer": "R.", "difficulty": "facile"}]}'
    provider = _mock_provider(llm_reply)
    with (
        patch.object(bank, "get_collection") as coll,
        patch.object(bank, "get_provider", return_value=(provider, "ollama")),
        patch.object(bank, "embed", return_value=[[1.0, 0.0]]),
    ):
        coll.return_value.get.return_value = fake
        inserted = bank.generate_bank_for_source("c.pdf", should_stop=lambda: True)

    assert inserted == 0
    provider.chat.assert_not_called()

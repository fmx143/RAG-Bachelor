"""core/questions.py: selectable count for single-topic generation."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from rag_bachelor.core import questions


def _reply(n: int) -> str:
    items = ", ".join(
        f'{{"question": "Q{i} ?", "answer": "R{i}.", "difficulty": "moyen"}}' for i in range(n)
    )
    return f'{{"items": [{items}]}}'


def _mock_provider(reply: str) -> MagicMock:
    provider = MagicMock()
    provider.chat.return_value = reply
    return provider


def test_generate_questions_respects_count() -> None:
    provider = _mock_provider(_reply(5))
    with (
        patch.object(questions, "retrieve", return_value=[]),
        patch.object(questions, "get_provider", return_value=(provider, "ollama")),
    ):
        items = questions.generate_questions("Sujet", "moyen", count=5)

    assert len(items) == 5
    # The requested count is baked into the system prompt sent to the LLM.
    system_prompt = provider.chat.call_args[0][0][0]["content"]
    assert "5 questions" in system_prompt


def test_generate_questions_slices_to_count_even_if_llm_returns_more() -> None:
    provider = _mock_provider(_reply(8))
    with (
        patch.object(questions, "retrieve", return_value=[]),
        patch.object(questions, "get_provider", return_value=(provider, "ollama")),
    ):
        items = questions.generate_questions("Sujet", "facile", count=3)

    assert len(items) == 3


def test_generate_questions_overrides_difficulty_on_every_item() -> None:
    provider = _mock_provider(_reply(2))
    with (
        patch.object(questions, "retrieve", return_value=[]),
        patch.object(questions, "get_provider", return_value=(provider, "ollama")),
    ):
        items = questions.generate_questions("Sujet", "DIFFICILE", count=2)

    assert all(item["difficulty"] == "difficile" for item in items)

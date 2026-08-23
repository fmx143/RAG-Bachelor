"""Generate easy / medium / hard study questions for a topic via the LLM.

Supports free-text questions as well as the three auto-gradable structured
types (QCM single/multi, Vrai/Faux) — see ``core/qtypes.py``.
"""

from __future__ import annotations

from rag_bachelor.core.llm import get_provider
from rag_bachelor.core.qtypes import QuestionItem, item_schema_instructions, parse_structured_items
from rag_bachelor.core.retriever import retrieve

# ── Difficulty instructions ────────────────────────────────────────────────────

_DIFFICULTY_INSTRUCTIONS: dict[str, str] = {
    "facile": (
        "Génère exactement {count} questions de compréhension de base (définitions, faits clés)."
    ),
    "moyen": (
        "Génère exactement {count} questions de compréhension intermédiaires qui demandent "
        "d'expliquer des concepts, de faire des liens entre eux ou d'illustrer par des exemples."
    ),
    "difficile": (
        "Génère exactement {count} questions d'analyse avancées qui demandent de synthétiser "
        "plusieurs concepts, de comparer des approches ou d'appliquer les connaissances "
        "à des situations concrètes."
    ),
}


def _system_prompt(qtype: str, difficulty: str, count: int) -> str:
    instruction = _DIFFICULTY_INSTRUCTIONS.get(difficulty, _DIFFICULTY_INSTRUCTIONS["moyen"])
    return (
        "Tu es un professeur qui crée des questions de révision pour un étudiant de licence.\n"
        f"{instruction.format(count=count)}\n"
        "Réponds UNIQUEMENT avec un JSON valide de cette forme exacte :\n"
        f'{{"items": [ {item_schema_instructions(qtype)} , ... ({count} items) ]}}\n'
        "N'ajoute aucun texte, commentaire ou balise Markdown en dehors du JSON."
    )


def generate_questions(
    topic: str, difficulty: str, qtype: str = "free", count: int = 3
) -> list[QuestionItem]:
    """Generate *count* study questions about *topic* at the given *difficulty*.

    *difficulty* must be one of ``"facile"``, ``"moyen"``, ``"difficile"``.
    *qtype* selects the question format (see ``core/qtypes.QTYPES``).
    Returns up to *count* normalized, validated question items.
    """
    difficulty = difficulty.lower()

    # Retrieve relevant context from the index
    chunks = retrieve(topic, top_k=6)
    context = "\n\n".join(c.text for c in chunks[:4]) if chunks else "(aucun extrait disponible)"

    messages = [
        {"role": "system", "content": _system_prompt(qtype, difficulty, count)},
        {"role": "user", "content": f"Sujet : {topic}\n\nExtraits de cours :\n{context}"},
    ]

    provider, _name = get_provider()
    raw = provider.chat(messages, json_mode=True)
    items = parse_structured_items(raw, qtype)[:count]
    # The requested difficulty is authoritative — override whatever the LLM echoed back.
    for item in items:
        item["difficulty"] = difficulty
    return items

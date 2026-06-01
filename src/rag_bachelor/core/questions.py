"""Generate easy / medium / hard study questions for a topic via the LLM."""

from __future__ import annotations

import json
import re

from rag_bachelor.core.llm import get_provider
from rag_bachelor.core.retriever import retrieve

# ── Difficulty instructions ────────────────────────────────────────────────────

_DIFFICULTY_INSTRUCTIONS: dict[str, str] = {
    "facile": (
        "Génère exactement 3 questions de compréhension de base (définitions, faits clés). "
        "Chaque question doit avoir une réponse courte et factuelle."
    ),
    "moyen": (
        "Génère exactement 3 questions de compréhension intermédiaires qui demandent "
        "d'expliquer des concepts, de faire des liens entre eux ou d'illustrer par des exemples."
    ),
    "difficile": (
        "Génère exactement 3 questions d'analyse avancées qui demandent de synthétiser "
        "plusieurs concepts, de comparer des approches ou d'appliquer les connaissances "
        "à des situations concrètes."
    ),
}

_SYSTEM_PROMPT = (
    "Tu es un professeur qui crée des questions de révision pour un étudiant de licence.\n"
    "Réponds UNIQUEMENT avec un JSON valide de cette forme exacte :\n"
    '{"questions": ["Question 1 ?", "Question 2 ?", "Question 3 ?"]}\n'
    "N'ajoute aucun texte, commentaire ou balise Markdown en dehors du JSON."
)

# Regex to extract a JSON object from potentially noisy LLM output
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_questions(raw: str) -> list[str]:
    """Extract the questions list from a (possibly noisy) LLM reply."""
    # Strip Markdown code fences if present
    cleaned = re.sub(r"```(?:json)?", "", raw).strip().strip("`").strip()
    # Try direct parse first
    try:
        data = json.loads(cleaned)
        return [str(q) for q in data.get("questions", [])][:3]
    except (json.JSONDecodeError, AttributeError):
        pass
    # Fallback: regex for JSON object anywhere in the string
    match = _JSON_RE.search(cleaned)
    if match:
        try:
            data = json.loads(match.group())
            return [str(q) for q in data.get("questions", [])][:3]
        except (json.JSONDecodeError, AttributeError):
            pass
    # Last resort: extract lines that look like questions
    lines = [ln.strip() for ln in raw.split("\n") if "?" in ln and len(ln.strip()) > 10]
    return lines[:3]


def generate_questions(topic: str, difficulty: str) -> list[str]:
    """Generate 3 study questions about *topic* at the given *difficulty*.

    *difficulty* must be one of ``"facile"``, ``"moyen"``, ``"difficile"``.
    Returns a list of up to 3 question strings.
    """
    difficulty = difficulty.lower()
    instruction = _DIFFICULTY_INSTRUCTIONS.get(difficulty, _DIFFICULTY_INSTRUCTIONS["moyen"])

    # Retrieve relevant context from the index
    chunks = retrieve(topic, top_k=6)
    context = "\n\n".join(c.text for c in chunks[:4]) if chunks else "(aucun extrait disponible)"

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Sujet : {topic}\n\n"
                f"Extraits de cours :\n{context}\n\n"
                f"{instruction}"
            ),
        },
    ]

    provider, name = get_provider()

    # Use the more powerful model for hard questions when online
    model: str | None = None
    # Ollama uses a single model for all difficulties
    _ = name

    raw = provider.chat(messages, model=model)
    return _parse_questions(raw)

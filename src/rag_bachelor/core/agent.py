"""Agentic RAG: the LLM decides what to search (query, keywords, document) before answering."""

from __future__ import annotations

from dataclasses import dataclass

from rag_bachelor.config import settings
from rag_bachelor.core.llm import get_provider
from rag_bachelor.core.qa import NO_DOC_MSG, SYSTEM_PROMPT
from rag_bachelor.core.qtypes import loads_object
from rag_bachelor.core.retriever import SearchResult, retrieve
from rag_bachelor.ingest.index import collection_count, list_sources

_AGENT_PROMPT = (
    SYSTEM_PROMPT + "\n\n"
    "Tu peux chercher dans les cours avant de répondre. À chaque tour, réponds par UN seul objet "
    "JSON :\n"
    '- {"action": "search", "query": "…", "keywords": ["…"], "source": null} pour chercher. '
    "« source » = nom exact d'un des documents ci-dessous pour limiter la recherche, sinon null.\n"
    '- {"action": "answer", "text": "…"} pour répondre (Markdown, avec citations [document, p.N]).\n'
    "Consignes : cherche toujours avant de répondre ; si la question touche plusieurs notions, "
    "fais une recherche par notion ; reformule si les extraits sont hors sujet ; si les extraits "
    "ne contiennent pas la réponse, dis-le dans « text » au lieu d'inventer.\n"
    "Documents disponibles : "
)


@dataclass
class Step:
    """One search the agent ran, for display."""

    query: str
    keywords: list[str]
    source: str | None
    found: int  # new chunks this search added


def _format(chunks: list[SearchResult]) -> str:
    return "\n\n---\n\n".join(f"[{c.source}, p.{c.page}]\n{c.text}" for c in chunks)


def answer_agentic(question: str, top_k: int = 5) -> tuple[str, list[SearchResult], list[Step]]:
    """Answer *question* with up to ``settings.agent_max_steps`` self-directed searches.

    Returns ``(answer, chunks, steps)``: every distinct chunk retrieved (first found first) and
    the searches that produced them.
    """
    if collection_count() == 0:
        return NO_DOC_MSG, [], []

    sources = list_sources()
    provider, _ = get_provider()
    messages = [
        {"role": "system", "content": _AGENT_PROMPT + ", ".join(sources)},
        {"role": "user", "content": question},
    ]
    seen: set[tuple[str, int, str]] = set()
    chunks: list[SearchResult] = []
    steps: list[Step] = []

    for _ in range(settings.agent_max_steps):
        raw = provider.chat(messages, json_mode=True)
        data = loads_object(raw)
        if data is None:  # no JSON at all: the model just answered
            return raw, chunks, steps
        messages.append({"role": "assistant", "content": raw})

        if data.get("action") == "answer" and isinstance(data.get("text"), str) and steps:
            return str(data["text"]), chunks, steps
        query = data.get("query")
        if data.get("action") != "search" or not isinstance(query, str) or not query.strip():
            messages.append({"role": "user", "content": "Cherche d'abord dans les cours."})
            continue

        kw = data.get("keywords")
        keywords = [k for k in kw if isinstance(k, str)] if isinstance(kw, list) else []
        src = data.get("source")
        source = src if isinstance(src, str) and src in sources else None
        new = [
            c
            for c in retrieve(query, top_k=top_k, keywords=keywords, source=source)
            if (c.source, c.page, c.text) not in seen
        ]
        seen.update((c.source, c.page, c.text) for c in new)
        chunks += new
        steps.append(Step(query, keywords, source, len(new)))
        messages.append(
            {
                "role": "user",
                "content": f"Nouveaux extraits :\n\n{_format(new)}"
                if new
                else "Aucun nouvel extrait.",
            }
        )

    messages.append({"role": "user", "content": "Plus de recherche possible : réponds maintenant."})
    raw = provider.chat(messages, json_mode=True)
    data = loads_object(raw)
    text = data.get("text") if data else None
    return (text if isinstance(text, str) else raw), chunks, steps

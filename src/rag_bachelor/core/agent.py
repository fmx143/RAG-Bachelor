"""Agentic RAG: the LLM decides what to search (query, keywords, document) before answering."""

from __future__ import annotations

from dataclasses import dataclass

from rag_bachelor.config import settings
from rag_bachelor.core.keywords import extract_keywords
from rag_bachelor.core.llm import get_provider
from rag_bachelor.core.qa import NO_DOC_MSG, SYSTEM_PROMPT
from rag_bachelor.core.qtypes import loads_object
from rag_bachelor.core.retriever import SearchResult, retrieve
from rag_bachelor.ingest.index import collection_count, list_sources

_AGENT_PROMPT = (
    SYSTEM_PROMPT + "\n\n"
    "On a déjà cherché ta question dans les cours : les extraits te sont donnés. À chaque tour, "
    "réponds par UN seul objet JSON :\n"
    '- {"action": "answer", "text": "…"} pour répondre (Markdown, avec citations [document, p.N]).\n'
    '- {"action": "search", "query": "…", "keywords": ["…"], "source": null} pour chercher '
    "encore. « source » = nom exact d'un des documents ci-dessous pour limiter la recherche, "
    "sinon null.\n"
    "Consignes : réponds dès que les extraits suffisent ; si la question touche plusieurs "
    "notions, fais une recherche par notion manquante ; reformule si les extraits sont hors "
    "sujet ; si les extraits ne contiennent pas la réponse, dis-le dans « text » au lieu "
    "d'inventer.\n"
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
    """Answer *question*: search it as asked first, then up to ``settings.agent_max_steps - 1``
    self-directed searches before the answer.

    Returns ``(answer, chunks, steps)``: every distinct chunk retrieved (first found first) and
    the searches that produced them.
    """
    if collection_count() == 0:
        return NO_DOC_MSG, [], []

    sources = list_sources()
    provider, _ = get_provider()
    seen: set[tuple[str, int, str]] = set()
    chunks: list[SearchResult] = []
    steps: list[Step] = []

    def run_search(query: str, keywords: list[str], source: str | None) -> str:
        """Retrieve, keep only unseen chunks, record the step; return the message for the LLM."""
        new = [
            c
            for c in retrieve(query[:500], top_k=top_k, keywords=keywords, source=source)
            if (c.source, c.page, c.text) not in seen
        ]
        seen.update((c.source, c.page, c.text) for c in new)
        chunks.extend(new)
        steps.append(Step(query, keywords, source, len(new)))
        return f"Nouveaux extraits :\n\n{_format(new)}" if new else "Aucun nouvel extrait."

    # The question as asked always comes first: the agent can only add to a plain search.
    first = run_search(question, extract_keywords(question), None)
    messages = [
        {"role": "system", "content": _AGENT_PROMPT + ", ".join(sources)},
        {"role": "user", "content": f"{question}\n\n{first}"},
    ]

    for _ in range(settings.agent_max_steps - 1):
        raw = provider.chat(messages, json_mode=True)
        data = loads_object(raw)
        messages.append({"role": "assistant", "content": raw})
        if data is None:
            if raw.lstrip().startswith("{") or '"action"' in raw:  # a broken action, not prose
                messages.append(
                    {"role": "user", "content": "JSON invalide : renvoie UN objet JSON."}
                )
                continue
            return raw, chunks, steps  # no JSON at all: the model just answered

        if data.get("action") == "answer" and isinstance(data.get("text"), str):
            return str(data["text"]), chunks, steps
        query = data.get("query")
        if data.get("action") != "search" or not isinstance(query, str) or not query.strip():
            messages.append({"role": "user", "content": "Réponds ou cherche, en JSON."})
            continue

        kw = data.get("keywords")
        keywords = [k[:80] for k in kw[:10] if isinstance(k, str)] if isinstance(kw, list) else []
        src = data.get("source")
        source = src if isinstance(src, str) and src in sources else None
        messages.append({"role": "user", "content": run_search(query, keywords, source)})

    messages.append({"role": "user", "content": "Plus de recherche possible : réponds maintenant."})
    raw = provider.chat(messages, json_mode=True)
    data = loads_object(raw)
    text = data.get("text") if data else None
    return (text if isinstance(text, str) else raw), chunks, steps

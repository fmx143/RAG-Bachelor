"""Explain a question from the subject's reference books (Livres), with checked page citations.

Imported quiz questions come with the quiz's own feedback; this adds the "why" from the books
(e.g. the PMBOK). The model sees numbered excerpts and cites them as ``[n]``; code turns each
valid number into ``[document, p.N]`` and drops the others, so a citation can't be invented.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rag_bachelor.core.keywords import extract_keywords
from rag_bachelor.core.lang import write_in
from rag_bachelor.core.llm import get_provider
from rag_bachelor.core.retriever import SearchResult, retrieve
from rag_bachelor.ingest.index import list_sources
from rag_bachelor.study.subjects import doc_kind, subject_lang, subject_of

_TOP_K = 6
_REF = re.compile(r"\[(\d{1,2}(?:\s*[,;]\s*\d{1,2})*)\]")
_OWN_CITE = re.compile(r"\[[^\[\]]*\bp\.\s*\d+\]")  # a "[doc, p.N]" the model wrote itself

_PROMPT = (
    "Tu es un tuteur qui prépare un étudiant à un examen. À partir UNIQUEMENT des extraits "
    "numérotés des livres de référence, explique en 3 à 6 phrases pourquoi la bonne réponse est "
    "correcte, puis en une phrase pourquoi les autres options le sont moins. Cite chaque "
    "affirmation par le numéro de son extrait entre crochets, par exemple [2]. Si les extraits ne "
    "suffisent pas à justifier la réponse, dis-le simplement au lieu d'inventer."
)


@dataclass
class BookExplanation:
    text: str  # Markdown with [document, p.N] citations
    refs: list[tuple[str, int]]  # (document path, page) actually cited, in order


class NoBooks(Exception):
    """The subject has no indexed book to explain from."""


def books_of(subject: str) -> list[str]:
    """Indexed book paths (``<subject>/Livres/…``) of *subject*."""
    return [s for s in list_sources() if subject_of(s) == subject and doc_kind(s) == "livre"]


def resolve_refs(text: str, chunks: list[SearchResult]) -> BookExplanation:
    """Replace ``[n]`` / ``[n, m]`` by ``[document, p.N]`` for valid excerpt numbers only."""
    refs: list[tuple[str, int]] = []

    def swap(m: re.Match[str]) -> str:
        cites = []
        for n in (int(x) for x in re.split(r"\s*[,;]\s*", m.group(1))):
            if 1 <= n <= len(chunks):
                c = chunks[n - 1]
                cites.append(f"[{c.source}, p.{c.page}]")
                if (c.source, c.page) not in refs:
                    refs.append((c.source, c.page))
        return "".join(cites)

    return BookExplanation(_REF.sub(swap, _OWN_CITE.sub("", text)).strip(), refs)


def explain_from_books(
    question: str, options: list[str] | None, correct: list[int] | None, topic: str
) -> BookExplanation:
    """Why the right answer to *question* (from the document *topic*) holds, per the books."""
    subject = subject_of(topic)
    books = books_of(subject)
    if not books:
        raise NoBooks(subject)
    right = [options[i] for i in correct or [] if options and 0 <= i < len(options)]
    query = f"{question} {' '.join(right)}".strip()
    chunks = retrieve(query[:1000], top_k=_TOP_K, keywords=extract_keywords(query), sources=books)
    # Numbers only: the model never sees a document name or page it could cite by itself.
    excerpts = "\n\n".join(f"[{n}]\n{c.text}" for n, c in enumerate(chunks, 1))
    listed = "\n".join(f"- {o}" for o in options or [])
    provider, _ = get_provider()
    raw = provider.chat(
        [
            {"role": "system", "content": _PROMPT + write_in(subject_lang(subject))},
            {
                "role": "user",
                "content": f"Extraits :\n\n{excerpts or '(aucun)'}\n\nQuestion : {question}\n"
                f"{f'Options :{chr(10)}{listed}{chr(10)}' if listed else ''}"
                f"Bonne réponse : {' ; '.join(right) or '(non indiquée)'}",
            },
        ]
    )
    return resolve_refs(raw, chunks)

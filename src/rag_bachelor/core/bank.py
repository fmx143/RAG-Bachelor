"""Whole-document question-bank generation.

Walks every chunk of an indexed PDF in reading order, grouped into windows,
and asks the LLM for one question per difficulty per window. References
(pages + chunk IDs) are attached programmatically from the window itself —
the LLM is never trusted for citations.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from rag_bachelor.core.embeddings import embed
from rag_bachelor.core.llm import get_provider
from rag_bachelor.core.qtypes import item_schema_instructions, parse_structured_items
from rag_bachelor.ingest.index import get_collection
from rag_bachelor.study.store import BankQuestion, add_bank_questions, bank_question_embeddings

# Consecutive chunks per LLM call (~4 × 900 chars ≈ one solid excerpt).
_WINDOW_SIZE = 4

# Cosine similarity above which a new candidate is treated as a near-duplicate
# of an existing bank question for the same source and skipped. embed()
# (core/embeddings.py) returns unit-norm vectors regardless of provider, so
# dot product == cosine similarity here too.
# ponytail: 0.88 was tuned for OpenAI's text-embedding-3-small in revibank;
# bge-m3 (RAG-Bachelor's local default) has a narrower similarity band and may
# need retuning if near-duplicates start slipping through or good candidates
# start getting skipped — no measurement done here yet.
_SIMILARITY_THRESHOLD = 0.88

_DIFFICULTY_BRIEF = {
    "facile": "définition ou fait clé, réponse courte et factuelle",
    "moyen": "expliquer un concept, faire un lien, donner un exemple",
    "difficile": "synthétiser, comparer ou appliquer les connaissances",
}


def _system_prompt(qtype: str) -> str:
    return (
        "Tu es un professeur qui crée une banque de questions de révision pour un étudiant "
        "de licence, à partir d'un extrait de cours.\n"
        "Génère exactement 3 questions basées UNIQUEMENT sur l'extrait fourni, une par "
        "niveau de difficulté :\n"
        f"- « facile » : {_DIFFICULTY_BRIEF['facile']} ;\n"
        f"- « moyen » : {_DIFFICULTY_BRIEF['moyen']} ;\n"
        f"- « difficile » : {_DIFFICULTY_BRIEF['difficile']}.\n"
        "Chaque question doit être entièrement justifiée par l'extrait.\n"
        "Le champ « answer » tient en 1 à 5 phrases : uniquement ce qui répond à la question, "
        "sans reprendre le contexte de l'extrait.\n"
        "Ne demande jamais d'énumérer ou de citer de mémoire tous les éléments d'une liste "
        "(« Quels sont les N… ? » en réponse libre) : en texte libre, interroge un seul "
        "élément de la liste ; en QCM à plusieurs réponses, prends des éléments réels de la "
        "liste comme bonnes réponses et des éléments plausibles absents de l'extrait comme "
        "mauvaises réponses.\n"
        "Réponds UNIQUEMENT avec un JSON valide de cette forme exacte :\n"
        f'{{"items": [ {item_schema_instructions(qtype)} , ... (3 items, une par difficulté) ]}}\n'
        "N'ajoute aucun texte, commentaire ou balise Markdown en dehors du JSON."
    )


_MAX_PASSAGE_SENTENCES = 5
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_WORD_RE = re.compile(r"\w{4,}")  # length filter stands in for a French stopword list

# Chroma IDs follow {source}__p{page}__c{index} (see ingest/index.py).
_ID_RE = re.compile(r"__p(\d+)__c(\d+)$")


@dataclass
class SourceChunk:
    chunk_id: str
    page: int
    index: int
    text: str


# ── Chunk retrieval ────────────────────────────────────────────────────────────


def get_source_chunks(source: str) -> list[SourceChunk]:
    """Return every indexed chunk of *source* in reading order (page, index)."""
    result = get_collection().get(where={"source": source}, include=["documents"])
    ids = result.get("ids") or []
    docs = result.get("documents") or []

    chunks: list[SourceChunk] = []
    for chunk_id, text in zip(ids, docs):
        m = _ID_RE.search(chunk_id)
        if not m:
            continue
        chunks.append(SourceChunk(chunk_id, int(m.group(1)), int(m.group(2)), text or ""))
    chunks.sort(key=lambda c: (c.page, c.index))
    return chunks


def _passage(excerpt: str, query: str) -> str:
    """Return the 1-5 consecutive sentences of *excerpt* that best match *query*.

    Chosen by code, never by the LLM (same rule as the page references): start from
    the sentence sharing the most words with *query*, then grow to the neighbour
    that still shares words, up to ``_MAX_PASSAGE_SENTENCES``. Returns ``""`` when
    nothing overlaps.
    """
    sentences = _SENTENCE_RE.split(" ".join(excerpt.replace("\u00ad", "").split()))
    wanted = set(_WORD_RE.findall(query.lower()))
    scores = [len(wanted & set(_WORD_RE.findall(s.lower()))) for s in sentences]
    if not any(scores):
        return ""
    lo = hi = scores.index(max(scores))
    while hi - lo + 1 < _MAX_PASSAGE_SENTENCES:
        left = scores[lo - 1] if lo > 0 else 0
        right = scores[hi + 1] if hi + 1 < len(scores) else 0
        if not (left or right):
            break
        if left >= right:
            lo -= 1
        else:
            hi += 1
    return " ".join(sentences[lo : hi + 1])


# ── Generation ─────────────────────────────────────────────────────────────────


def generate_bank_for_source(
    source: str,
    qtype: str = "free",
    target: int = 999_999,
    progress_cb: Callable[[int, int, int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> int:
    """Generate up to *target* unique bank questions covering *source*.

    Iterates chunk windows in reading order; one LLM call per window. Each
    window's candidates are embedded and compared against every question
    already stored for *source* (plus everything accepted earlier in this
    run) — a candidate whose cosine similarity to an existing one exceeds
    ``_SIMILARITY_THRESHOLD`` is a near-duplicate and is skipped. Surviving
    candidates are inserted immediately, so progress survives an interruption;
    reruns skip both exact repeats (``UNIQUE(source, question)``) and
    near-duplicates (embedding check).

    *qtype* selects the question format (see ``core/qtypes.QTYPES``).
    *target* is how many unique questions to stop at (default: effectively
    unbounded, i.e. cover the whole document).
    *progress_cb* receives ``(inserted_so_far, target, skipped_so_far)``.
    *should_stop* is polled before each window to support cancellation.
    Returns the total number of questions inserted.
    """
    chunks = get_source_chunks(source)
    windows = [chunks[i : i + _WINDOW_SIZE] for i in range(0, len(chunks), _WINDOW_SIZE)]

    system_prompt = _system_prompt(qtype)
    provider, _name = get_provider()
    existing_vecs = bank_question_embeddings(source)
    inserted = 0
    skipped = 0
    # ponytail: sequential LLM calls (~few s/window); parallelize with a
    # thread pool if a 500-page PDF hurts.
    for window in windows:
        if inserted >= target or (should_stop and should_stop()):
            break

        excerpt = "\n\n".join(c.text for c in window)
        raw = provider.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Extrait de cours ({source}) :\n\n{excerpt}"},
            ],
            json_mode=True,
        )
        candidates = parse_structured_items(raw, qtype)
        if not candidates:
            if progress_cb:
                progress_cb(inserted, target, skipped)
            continue

        pages = sorted({c.page for c in window})
        chunk_ids = [c.chunk_id for c in window]
        vectors = embed([item["question"] for item in candidates])

        questions: list[BankQuestion] = []
        for item, vec in zip(candidates, vectors):
            if any(_dot(vec, other) > _SIMILARITY_THRESHOLD for other in existing_vecs):
                skipped += 1
                continue
            questions.append(
                BankQuestion(
                    question=item["question"],
                    answer=item["answer"],
                    difficulty=item["difficulty"],
                    source=source,
                    pages=pages,
                    chunk_ids=chunk_ids,
                    qtype=item["qtype"],
                    options=item["options"],
                    correct=item["correct"],
                    embedding=vec,
                    passage=_passage(excerpt, f"{item['question']} {item['answer']}"),
                )
            )
            existing_vecs.append(vec)
            if inserted + len(questions) >= target:
                break  # trim this window so we don't overshoot the target

        if questions:
            inserted += add_bank_questions(questions)
        if progress_cb:
            progress_cb(inserted, target, skipped)
    return inserted


def _dot(a: list[float], b: list[float]) -> float:
    """Dot product of two equal-length vectors.

    Equivalent to cosine similarity here since embed() (core/embeddings.py)
    always returns unit-norm vectors. Returns -1.0 (never a near-duplicate)
    on a length mismatch — e.g. a stale vector left over from a since-changed
    EMBEDDING_MODEL — rather than silently truncating via zip().
    """
    if len(a) != len(b):
        return -1.0
    return sum(x * y for x, y in zip(a, b))

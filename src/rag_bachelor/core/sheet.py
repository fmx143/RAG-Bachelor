"""Chapter revision sheet: map each window of a page range to notes, then merge them.

Page citations ``[p.N]`` are validated by code against the requested range — the LLM
is never trusted for references (same rule as ``core/bank.py``).
"""

from __future__ import annotations

import re
from collections.abc import Callable

from rag_bachelor.core.bank import get_source_chunks
from rag_bachelor.core.llm import get_provider
from rag_bachelor.study.store import add_sheet

# Consecutive chunks per map call (~8 × 900 chars ≈ two pages of prose).
_WINDOW_SIZE = 8

_CITE_RE = re.compile(r"\[p\.(\d+)\]")

_MAP_PROMPT = (
    "Tu es un professeur qui prépare une fiche de révision pour un étudiant de licence.\n"
    "À partir de l'extrait fourni (chaque passage est précédé de sa page entre crochets), "
    "écris des notes en Markdown : notions clés, définitions, formules, exemples utiles.\n"
    "Base-toi UNIQUEMENT sur l'extrait. Termine chaque point par la page d'origine au format "
    "[p.N]. Réponds en français, sans introduction ni conclusion."
)

_REDUCE_PROMPT = (
    "Tu es un professeur qui rédige une fiche de révision pour un étudiant de licence.\n"
    "Fusionne les notes fournies (dans l'ordre du cours) en une fiche structurée en Markdown : "
    "des sections `##` par grande notion, puis « Définitions », « Points clés » et "
    "« Pièges à éviter » quand c'est pertinent. Supprime les redondances, n'invente rien et "
    "conserve les citations de page [p.N]. Réponds en français."
)


def _clean_citations(text: str, page_from: int, page_to: int) -> str:
    """Drop every ``[p.N]`` whose page lies outside the requested range."""

    def keep(m: re.Match[str]) -> str:
        return m.group(0) if page_from <= int(m.group(1)) <= page_to else ""

    return _CITE_RE.sub(keep, text)


def generate_sheet(
    source: str,
    page_from: int,
    page_to: int,
    progress_cb: Callable[[int, int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> int | None:
    """Build, store and return the id of a sheet for ``source`` pages ``page_from..page_to``.

    *progress_cb* receives ``(steps_done, steps_total)`` (one step per window, plus the
    final merge). *should_stop* is polled before each step; a stop returns ``None`` and
    stores nothing. Raises ``ValueError`` when the range holds no indexed text.
    """
    chunks = [c for c in get_source_chunks(source) if page_from <= c.page <= page_to]
    if not chunks:
        raise ValueError("no indexed text in that page range")
    windows = [chunks[i : i + _WINDOW_SIZE] for i in range(0, len(chunks), _WINDOW_SIZE)]
    total = len(windows) + (1 if len(windows) > 1 else 0)
    provider, _name = get_provider()

    notes: list[str] = []
    for done, window in enumerate(windows):
        if should_stop and should_stop():
            return None
        excerpt = "\n\n".join(f"[p.{c.page}]\n{c.text}" for c in window)
        notes.append(
            provider.chat(
                [
                    {"role": "system", "content": _MAP_PROMPT},
                    {"role": "user", "content": f"Extrait de cours ({source}) :\n\n{excerpt}"},
                ]
            )
        )
        if progress_cb:
            progress_cb(done + 1, total)

    if len(notes) == 1:
        sheet = notes[0]
    else:
        if should_stop and should_stop():
            return None
        sheet = provider.chat(
            [
                {"role": "system", "content": _REDUCE_PROMPT},
                {"role": "user", "content": "\n\n---\n\n".join(notes)},
            ]
        )
        if progress_cb:
            progress_cb(total, total)

    return add_sheet(source, page_from, page_to, _clean_citations(sheet, page_from, page_to))

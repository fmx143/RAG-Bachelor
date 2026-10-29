"""Mind map of a page range: map each window to an outline, then merge into one JSON tree.

The tree is validated and size-bounded by code (the LLM output is untrusted); it is stored
as JSON text in ``mind_maps`` and rendered as nested ``<details>`` — no JS, no dependency.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable

from rag_bachelor.core.bank import get_source_chunks
from rag_bachelor.core.llm import get_provider
from rag_bachelor.core.qtypes import loads_object
from rag_bachelor.core.sheet import _WINDOW_SIZE
from rag_bachelor.study.store import add_map

_MAX_DEPTH = 4  # root + 3 levels
_MAX_CHILDREN = 8
_MAX_TITLE = 80

_MAP_PROMPT = (
    "Tu analyses un extrait de cours (chaque passage est précédé de sa page entre crochets).\n"
    "Écris un plan hiérarchique en Markdown (listes à puces imbriquées) des notions "
    "importantes et de leurs sous-notions, en quelques mots chacune. Base-toi UNIQUEMENT "
    "sur l'extrait. Réponds dans la langue de l'extrait, sans introduction ni conclusion."
)

_TREE_PROMPT = (
    "Tu construis la carte mentale d'un chapitre de cours à partir de plans partiels, "
    "donnés dans l'ordre du cours. Fusionne-les en UN arbre JSON :\n"
    '{"title": "thème central", "children": [{"title": "...", "children": [...]}]}\n'
    f"Au plus {_MAX_DEPTH} niveaux, {_MAX_CHILDREN} enfants par nœud, titres de moins de "
    "8 mots, dans la langue de l'extrait. Regroupe les notions voisines, supprime les doublons, "
    "n'invente rien. Réponds uniquement avec le JSON."
)


class BadTree(Exception):
    """The LLM did not return a usable tree."""


def _clean(node: object, depth: int = 1) -> dict[str, object] | None:
    """Keep only ``{title, children}`` nodes with bounded depth, width and title length."""
    if not isinstance(node, dict):
        return None
    title = re.sub(r"\s+", " ", str(node.get("title", ""))).strip()[:_MAX_TITLE]
    if not title:
        return None
    raw = node.get("children")
    kids = raw if isinstance(raw, list) and depth < _MAX_DEPTH else []
    children = [c for c in (_clean(k, depth + 1) for k in kids[:_MAX_CHILDREN]) if c]
    return {"title": title, "children": children}


def generate_map(
    source: str,
    page_from: int,
    page_to: int,
    progress_cb: Callable[[int, int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> int | None:
    """Build, store and return the id of a mind map for ``source`` pages ``page_from..page_to``.

    Same contract as ``core.sheet.generate_sheet``: *progress_cb* gets ``(done, total)``,
    a stop returns ``None``, ``ValueError`` means no indexed text, ``BadTree`` an unusable reply.
    """
    chunks = [c for c in get_source_chunks(source) if page_from <= c.page <= page_to]
    if not chunks:
        raise ValueError("no indexed text in that page range")
    windows = [chunks[i : i + _WINDOW_SIZE] for i in range(0, len(chunks), _WINDOW_SIZE)]
    total = len(windows) + 1
    provider, _name = get_provider()

    outlines: list[str] = []
    for done, window in enumerate(windows):
        if should_stop and should_stop():
            return None
        excerpt = "\n\n".join(f"[p.{c.page}]\n{c.text}" for c in window)
        outlines.append(
            provider.chat(
                [
                    {"role": "system", "content": _MAP_PROMPT},
                    {"role": "user", "content": f"Extrait de cours ({source}) :\n\n{excerpt}"},
                ]
            )
        )
        if progress_cb:
            progress_cb(done + 1, total)

    if should_stop and should_stop():
        return None
    raw = provider.chat(
        [
            {"role": "system", "content": _TREE_PROMPT},
            {"role": "user", "content": "\n\n---\n\n".join(outlines)},
        ],
        json_mode=True,
    )
    tree = _clean(loads_object(raw))
    if tree is None:
        raise BadTree
    if progress_cb:
        progress_cb(total, total)
    return add_map(source, page_from, page_to, json.dumps(tree, ensure_ascii=False))

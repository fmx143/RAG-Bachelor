"""Recursive text splitter that turns pages into overlapping chunks."""

from dataclasses import dataclass

from rag_bachelor.config import settings
from rag_bachelor.ingest.extract import Page

# Separators tried in order from coarsest to finest.
_SEPARATORS = ["\n\n", "\n", ". ", " "]


@dataclass
class Chunk:
    text: str
    source: str    # PDF filename
    page_num: int  # 1-indexed source page
    chunk_index: int  # position within the page's chunks (0-based)


def _split_text(text: str, separators: list[str], chunk_size: int, overlap: int) -> list[str]:
    """Recursively split *text* using *separators* into pieces ≤ *chunk_size*.

    When a separator produces a candidate that would exceed *chunk_size*, the
    function recurses with the next finer separator. The character-level fallback
    at the end guarantees termination.
    """
    if not separators:
        # Character-level fallback: fixed-size windows with overlap
        pieces: list[str] = []
        start = 0
        while start < len(text):
            end = start + chunk_size
            piece = text[start:end]
            if piece.strip():
                pieces.append(piece)
            start = end - overlap
        return pieces

    sep = separators[0]
    parts = text.split(sep)
    result: list[str] = []
    current = ""

    for part in parts:
        candidate = current + (sep if current else "") + part
        if len(candidate) <= chunk_size:
            current = candidate
        else:
            if current:
                result.append(current)
            if len(part) > chunk_size:
                # Part alone is too big — recurse with a finer separator
                result.extend(_split_text(part, separators[1:], chunk_size, overlap))
                current = ""
            else:
                current = part

    if current:
        result.append(current)

    return result


def chunk_pages(pages: list[Page]) -> list[Chunk]:
    """Convert non-empty pages into overlapping :class:`Chunk` objects."""
    chunks: list[Chunk] = []

    for page in pages:
        if page.is_empty:
            continue

        texts = _split_text(
            page.text,
            _SEPARATORS,
            settings.chunk_size,
            settings.chunk_overlap,
        )

        for i, text in enumerate(texts):
            stripped = text.strip()
            if stripped:
                chunks.append(
                    Chunk(
                        text=stripped,
                        source=page.source,
                        page_num=page.page_num,
                        chunk_index=i,
                    )
                )

    return chunks

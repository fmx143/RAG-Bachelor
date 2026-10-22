"""Chunk type and the recursive text splitter used by section chunking."""

from dataclasses import dataclass

# Separators tried in order from coarsest to finest.
_SEPARATORS = ["\n\n", "\n", ". ", " "]


@dataclass
class Chunk:
    text: str
    source: str  # PDF filename
    page_num: int  # 1-indexed source page
    chunk_index: int  # position within the document's chunks (0-based, unique per source)


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


def _with_overlap(texts: list[str], overlap: int) -> list[str]:
    """Prefix each piece with the last ~*overlap* chars of the previous one, cut at a word start.

    ``_split_text`` only overlaps in its character-level fallback; this gives every
    boundary the overlap, so an answer straddling two chunks stays retrievable.
    """
    out = texts[:1]
    for prev, cur in zip(texts, texts[1:]):
        tail = prev[-overlap:]
        if len(prev) > overlap:
            # The window may start mid-word: drop everything up to the first space.
            tail = tail.partition(" ")[2]
        out.append(f"{tail.strip()} {cur}" if tail.strip() else cur)
    return out

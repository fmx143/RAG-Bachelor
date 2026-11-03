"""Text folding for verbatim checks: compare content, not layout."""

from __future__ import annotations

import re
import unicodedata

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def fold(text: str) -> str:
    """Lowercase letters and digits only, accents removed.

    Line breaks, hyphenation at a line end, punctuation, quotes and spacing all vanish, so
    "Project manage-\\nment plan." and "project management plan" fold to the same string.
    """
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return _NON_ALNUM.sub("", "".join(c for c in decomposed if not unicodedata.combining(c)))

"""Subjects: the top-level folders of ``pdfs_dir``. The active one scopes every tab."""

from __future__ import annotations

from rag_bachelor.config import settings
from rag_bachelor.ingest.index import list_sources
from rag_bachelor.study.store import get_setting, set_setting

DEFAULT_SUBJECT = "Bachelor"
BOOKS_DIR = "Livres"  # <subject>/Livres/… holds course books
SOURCES_DIR = "Sources"  # <subject>/Sources/… holds other documents (notes, articles…)


def subject_of(path: str) -> str:
    """The subject a document path (or card topic) belongs to: its first segment."""
    return path.split("/", 1)[0]


def doc_kind(path: str) -> str | None:
    """``"livre"`` / ``"source"`` from the document's folder, ``None`` while it is unsorted."""
    parts = path.split("/")
    if len(parts) >= 3 and parts[1] == BOOKS_DIR:
        return "livre"
    if len(parts) >= 3 and parts[1] == SOURCES_DIR:
        return "source"
    return None


LANGS = {"fr": "Français", "en": "English"}  # content language of a subject's generated text


def subject_lang(subject: str) -> str:
    """Language generated content takes for *subject* (``fr`` unless set otherwise)."""
    lang = get_setting(f"subject_lang:{subject}", "fr")
    return lang if lang in LANGS else "fr"


def set_subject_lang(subject: str, lang: str) -> None:
    if lang not in LANGS:
        raise ValueError(f"unknown language: {lang}")
    set_setting(f"subject_lang:{subject}", lang)


def list_subjects() -> list[str]:
    """Sorted subject names found on disk; the default one when the library is empty."""
    root = settings.pdfs_dir
    names = (
        {p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")}
        if root.exists()
        else set()
    )
    return sorted(names or {DEFAULT_SUBJECT}, key=str.casefold)


def active_subject() -> str:
    """The persisted active subject, or the first one if it no longer exists."""
    subjects = list_subjects()
    current = get_setting("active_subject", DEFAULT_SUBJECT)
    return current if current in subjects else subjects[0]


def set_active_subject(name: str) -> None:
    set_setting("active_subject", name)


def scoped_topic(topic: str) -> str:
    """*topic* as a card topic of the active subject (document paths already are)."""
    subject = active_subject()
    return topic if topic.startswith(subject + "/") else f"{subject}/{topic}"


def active_sources() -> list[str]:
    """Indexed document paths of the active subject."""
    subject = active_subject()
    return [s for s in list_sources() if subject_of(s) == subject]


# Cover icons: ids of the symbols in static/book-icons.svg (Lucide).
ICON_NAMES = (
    "book-open book-text library graduation-cap brain atom flask-conical calculator code-xml cpu "
    "database network globe languages scale landmark briefcase chart-column users shield-check "
    "wrench palette heart-pulse leaf rocket"
).split()


def icon_for(path: str, icons: dict[str, str]) -> str:
    """Cover icon of a document: the user's choice, else a book or a plain document."""
    chosen = icons.get(path, "")
    if chosen in ICON_NAMES:
        return chosen
    return "book-open" if doc_kind(path) == "livre" else "file-text"

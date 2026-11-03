"""Content language: every generated text follows the language set for its subject.

Prompts stay in French; these directives are appended to them. ``fr`` (the default) adds
nothing, so French subjects keep their existing behaviour; ``en`` forces English output.
The UI stays French, and audio lessons stay French (their voices only speak French).
"""

from __future__ import annotations

from rag_bachelor.study.subjects import active_subject, subject_lang, subject_of

_WRITE_IN = {
    "fr": "",
    "en": (
        "\nRédige tout le texte produit en ANGLAIS (consignes en français, réponse en anglais), "
        "titres compris. Garde exactement les clés JSON et les valeurs imposées "
        "(difficulté, verdict, locuteur…) telles qu'indiquées."
    ),
}


def lang_of(path: str | None = None) -> str:
    """Language of *path*'s subject (a document path or card topic), else the active subject's."""
    return subject_lang(subject_of(path) if path else active_subject())


def write_in(lang: str) -> str:
    """Suffix for a system prompt fixing the output language (empty for French)."""
    return _WRITE_IN.get(lang, _WRITE_IN["fr"])


def keywords_in(lang: str) -> str:
    """Search keywords must match the course text, whatever the question's language."""
    if lang == "en":
        return " Les cours sont en anglais : donne les mots-clés en anglais, traduits si besoin."
    return ""


def spoken_terms(lang: str) -> str:
    """Audio lessons stay French; for an English course keep its technical terms in English."""
    if lang == "en":
        return (
            " Le cours est en anglais : garde en anglais les termes techniques, sigles et noms "
            "de processus, de documents et d'outils, sans les traduire."
        )
    return ""

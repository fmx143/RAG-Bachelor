"""LightRAG-style query keywords: precise terms and broad themes, to widen lexical and graph search."""

from rag_bachelor.core.llm import get_provider
from rag_bachelor.core.qtypes import loads_object

_PROMPT = (
    "Extrais de la question d'un étudiant deux listes de mots-clés pour chercher dans ses cours : "
    "« bas_niveau » = termes précis (notions, noms, sigles, formules) ; "
    "« haut_niveau » = thèmes généraux ou synonymes que le cours pourrait employer. "
    '3 à 5 éléments par liste. Réponds par un objet JSON : {"bas_niveau": […], "haut_niveau": […]}.'
)


def extract_keywords(question: str) -> list[str]:
    """Keywords of *question* (low-level first); ``[]`` if the model answers something unusable."""
    provider, _ = get_provider()
    raw = provider.chat(
        [{"role": "system", "content": _PROMPT}, {"role": "user", "content": question}],
        json_mode=True,
    )
    data = loads_object(raw) or {}
    words: list[str] = []
    for key in ("bas_niveau", "haut_niveau"):
        value = data.get(key)
        if isinstance(value, list):
            words += [w.strip() for w in value if isinstance(w, str) and w.strip()]
    return list(dict.fromkeys(words))

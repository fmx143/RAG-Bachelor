"""clean_pages(): OCR artifact removal for scanned books."""

from rag_bachelor.ingest.extract import clean_pages

_BODIES = [
    "Le chef de projet planifie.",
    "La recette valide le produit.",
    "Un risque se mesure.",
    "Les tests unitaires isolent.",
    "Le budget est suivi chaque mois.",
    "La qualité se définit tôt.",
    "Une équipe se motive.",
    "Le client exprime un besoin.",
    "Les jalons rythment le planning.",
    "Le livrable est documenté.",
]


def test_clean_pages_strips_scan_artifacts() -> None:
    footers = ["© IDEC 2024, version 3, toute reproduction interdite."] * 9 + [
        "© 1DEC 2024, version 3, toute reproduction interdite."
    ]
    pages = [
        f"Un utili\xad\nsateur : {b}\nJ {b[::-1]}\n{f}"
        for b, f in zip(_BODIES, footers, strict=True)
    ]
    out = clean_pages(pages)
    assert out[0] == "Un utilisateur : Le chef de projet planifie.\n• .eifinalp tejorp ed fehc eL"
    assert "DEC" not in out[-1]


def test_clean_pages_keeps_lines_unique_to_few_pages() -> None:
    assert clean_pages(_BODIES) == _BODIES

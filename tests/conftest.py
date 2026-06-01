"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import fitz  # pymupdf
import pytest


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    """Create a two-page French PDF with readable text for testing."""
    pdf_path = tmp_path / "sample_cours.pdf"
    doc = fitz.open()

    # Page 1
    p1 = doc.new_page()
    p1.insert_text(
        (50, 50),
        (
            "Chapitre 1 : Introduction aux algorithmes\n\n"
            "Un algorithme est une suite finie et non ambiguë d'instructions permettant "
            "de résoudre un problème donné.\n"
            "Il doit satisfaire trois propriétés fondamentales : être déterministe, "
            "terminer en un nombre fini d'étapes, et produire un résultat correct.\n\n"
            "Exemples classiques : tri à bulles, tri rapide, recherche binaire, "
            "algorithme de Dijkstra pour les plus courts chemins."
        ),
        fontsize=11,
    )

    # Page 2
    p2 = doc.new_page()
    p2.insert_text(
        (50, 50),
        (
            "Chapitre 2 : Complexité algorithmique\n\n"
            "La complexité temporelle mesure le nombre d'opérations élémentaires "
            "effectuées par un algorithme en fonction de la taille n de son entrée.\n\n"
            "Notations de Landau :\n"
            "  O(1)       — temps constant\n"
            "  O(log n)   — temps logarithmique\n"
            "  O(n)       — temps linéaire\n"
            "  O(n log n) — quasi-linéaire (tri rapide en moyenne)\n"
            "  O(n²)      — temps quadratique (tri à bulles)\n\n"
            "La complexité spatiale mesure la mémoire utilisée."
        ),
        fontsize=11,
    )

    doc.save(str(pdf_path))
    doc.close()
    return pdf_path

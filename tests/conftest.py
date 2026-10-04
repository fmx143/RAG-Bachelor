"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import fitz  # pymupdf
import pytest

_REPO_DATA = Path(__file__).resolve().parent.parent / "data"


def pytest_sessionstart(session: pytest.Session) -> None:
    """Abort the whole run if the environment points tests at a real backend."""
    from rag_bachelor.config import Settings

    env = Settings()
    for name in ("chroma_host", "postgres_host"):
        if getattr(env, name):
            pytest.exit(
                f"Refusing to run tests: {name.upper()} is set (would hit a real backend). "
                "Unset it (e.g. run pytest without `doppler run --`).",
                returncode=2,
            )


@pytest.fixture(autouse=True)
def isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point SQLite, Chroma and the PDFs folder at tmp_path and force the embedded backends.

    Tests must never reach data/, a remote Chroma server or a Postgres instance.
    """
    from rag_bachelor.config import settings
    from rag_bachelor.ingest import index
    from rag_bachelor.study import store

    monkeypatch.setattr(settings, "db_path", tmp_path / "test_app.db")
    monkeypatch.setattr(settings, "chroma_dir", tmp_path / "chroma")
    monkeypatch.setattr(settings, "pdfs_dir", tmp_path / "pdfs")
    monkeypatch.setattr(settings, "chroma_host", "")
    monkeypatch.setattr(settings, "postgres_host", "")
    for path in (settings.db_path, settings.chroma_dir, settings.pdfs_dir):
        if path.resolve().is_relative_to(_REPO_DATA):
            pytest.exit(f"Refusing to run tests: {path} is under the real data/ folder", returncode=2)
    store._conn = None
    index._client = None
    index._collection = None
    yield
    if store._conn is not None:
        store._conn.close()
    store._conn = None
    index._client = None
    index._collection = None


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

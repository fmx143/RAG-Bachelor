"""📚 Documentation tab — upload, index and manage PDFs."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from rag_bachelor.config import settings
from rag_bachelor.ingest.chunk import chunk_pages
from rag_bachelor.ingest.extract import extract_pages
from rag_bachelor.ingest.index import (
    collection_count,
    delete_source,
    index_chunks,
    list_sources,
)


def render() -> None:
    st.header("📚 Gestion des documents")

    # ── Upload ────────────────────────────────────────────────────────────
    st.subheader("Ajouter des PDFs")
    uploaded_files = st.file_uploader(
        "Dépose tes PDFs ici (plusieurs fichiers acceptés)",
        type=["pdf"],
        accept_multiple_files=True,
        help="Les fichiers sont sauvegardés dans data/pdfs/. Indexe-les ensuite ci-dessous.",
    )

    if uploaded_files:
        settings.pdfs_dir.mkdir(parents=True, exist_ok=True)
        for f in uploaded_files:
            # Sanitise filename: keep only safe characters
            safe_name = Path(f.name).name
            dest = settings.pdfs_dir / safe_name
            dest.write_bytes(f.read())
            st.success(f"✅ **{safe_name}** sauvegardé")

    # ── Index actions ─────────────────────────────────────────────────────
    st.subheader("Indexation")
    col_btn, col_count = st.columns([2, 1])
    with col_btn:
        if st.button("🔄 (Re)indexer tous les PDFs", type="primary", use_container_width=True):
            _index_all()
    with col_count:
        st.metric("Chunks indexés", collection_count())

    # ── File list ─────────────────────────────────────────────────────────
    st.subheader("Documents")
    all_pdfs = sorted(settings.pdfs_dir.glob("*.pdf")) if settings.pdfs_dir.exists() else []
    indexed_set = set(list_sources())

    if not all_pdfs:
        st.info("Aucun PDF trouvé dans `data/pdfs/`. Dépose des fichiers ci-dessus.")
        return

    for pdf in all_pdfs:
        col_name, col_idx, col_del = st.columns([4, 1, 1])
        status = "✅" if pdf.name in indexed_set else "⚠️"
        with col_name:
            st.write(f"{status} **{pdf.name}**")
        with col_idx:
            if st.button("Indexer", key=f"idx_{pdf.name}", use_container_width=True):
                _index_one(pdf)
                st.rerun()
        with col_del:
            if st.button("🗑️", key=f"del_{pdf.name}", use_container_width=True, help="Supprimer"):
                pdf.unlink(missing_ok=True)
                delete_source(pdf.name)
                st.success(f"{pdf.name} supprimé de l'index et du disque.")
                st.rerun()


# ── Internal helpers ──────────────────────────────────────────────────────────


def _index_one(path: Path) -> None:
    """Index a single PDF and surface any warnings."""
    with st.spinner(f"Indexation de **{path.name}**…"):
        pages = extract_pages(path)
        empty_pages = [p.page_num for p in pages if p.is_empty]
        chunks = chunk_pages(pages)

        # Remove stale chunks before re-inserting
        delete_source(path.name)
        index_chunks(chunks)

    if empty_pages:
        st.warning(
            f"⚠️ Pages vides détectées dans **{path.name}** "
            f"(p. {', '.join(map(str, empty_pages))}). "
            "Ces pages n'ont pas été indexées."
        )
    st.success(f"✅ **{path.name}** — {len(chunks)} chunks indexés")


def _index_all() -> None:
    """Re-index every PDF in pdfs_dir."""
    pdfs = sorted(settings.pdfs_dir.glob("*.pdf")) if settings.pdfs_dir.exists() else []
    if not pdfs:
        st.warning("Aucun PDF à indexer dans `data/pdfs/`.")
        return

    progress_bar = st.progress(0.0, text="Indexation en cours…")
    for i, pdf in enumerate(pdfs):
        _index_one(pdf)
        progress_bar.progress((i + 1) / len(pdfs), text=f"Indexation… {i + 1}/{len(pdfs)}")
    progress_bar.empty()
    st.success(f"✅ {len(pdfs)} document(s) indexés — {collection_count()} chunks au total")

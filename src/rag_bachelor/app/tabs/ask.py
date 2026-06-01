"""❓ Poser une question tab — RAG Q&A with source citations."""

from __future__ import annotations

import streamlit as st

from rag_bachelor.core.qa import answer_question
from rag_bachelor.ingest.index import collection_count


def render() -> None:
    st.header("❓ Poser une question")

    if collection_count() == 0:
        st.warning(
            "⚠️ Aucun document indexé. "
            "Va dans l'onglet **📚 Documentation** pour indexer tes cours."
        )
        return

    question = st.text_area(
        "Ta question",
        placeholder="Explique-moi la différence entre la complexité O(n log n) et O(n²)…",
        height=100,
        label_visibility="collapsed",
    )

    col_slider, col_help = st.columns([1, 3])
    with col_slider:
        n_sources = st.slider("Sources utilisées", min_value=3, max_value=10, value=5)
    with col_help:
        st.caption("Nombre de passages de cours utilisés pour construire la réponse.")

    can_submit = bool(question.strip())
    if st.button("🔍 Obtenir une réponse", type="primary", disabled=not can_submit):
        with st.spinner("Recherche dans tes cours et génération de la réponse…"):
            try:
                answer, chunks = answer_question(question.strip(), top_k=n_sources)
            except Exception as exc:
                st.error(f"Erreur lors de la génération : {exc}")
                return

        st.markdown("### Réponse")
        st.markdown(answer)

        if chunks:
            with st.expander("📖 Sources utilisées", expanded=False):
                for i, chunk in enumerate(chunks, 1):
                    st.markdown(
                        f"**{i}. `{chunk.source}` — p. {chunk.page}** "
                        f"*(pertinence : {chunk.score:.0%})*"
                    )
                    preview = chunk.text[:400] + ("…" if len(chunk.text) > 400 else "")
                    st.text(preview)
                    if i < len(chunks):
                        st.divider()

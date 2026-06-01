"""🎯 Générer des questions tab — LLM question generation with deck integration."""

from __future__ import annotations

import streamlit as st

from rag_bachelor.core.questions import generate_questions
from rag_bachelor.ingest.index import list_sources
from rag_bachelor.study.store import add_card

_DIFFICULTIES = ["facile", "moyen", "difficile"]
_DIFF_LABELS = {"facile": "🟢 Facile", "moyen": "🟡 Moyen", "difficile": "🔴 Difficile"}


def render() -> None:
    st.header("🎯 Générer des questions")

    sources = list_sources()

    # ── Inputs ────────────────────────────────────────────────────────────
    col_left, col_right = st.columns([2, 1])
    with col_left:
        doc_choice = st.selectbox(
            "Document source",
            ["(choisir un document)"] + sources,
            help="Génère des questions basées sur ce document.",
        )
        custom_topic = st.text_input(
            "Ou entrer un sujet libre",
            placeholder="Algorithmes de tri, Probabilités, Théorie des graphes…",
            help="Remplace le choix ci-dessus si renseigné.",
        )
    with col_right:
        difficulty = st.radio(
            "Difficulté",
            _DIFFICULTIES,
            format_func=lambda d: _DIFF_LABELS[d],
            index=1,
        )

    # Effective topic: custom text overrides the dropdown
    effective_topic = custom_topic.strip() or (
        doc_choice if doc_choice != "(choisir un document)" else ""
    )

    if not sources and not custom_topic.strip():
        st.warning("⚠️ Aucun document indexé. Indexe d'abord tes cours dans **📚 Documentation**.")

    can_generate = bool(effective_topic)
    if st.button(
        "✨ Générer des questions", type="primary", disabled=not can_generate, use_container_width=False
    ):
        with st.spinner(f"Génération de questions **{_DIFF_LABELS[difficulty]}** sur « {effective_topic} »…"):
            try:
                questions = generate_questions(effective_topic, difficulty)
            except Exception as exc:
                st.error(f"Erreur lors de la génération : {exc}")
                return

        if not questions:
            st.warning("Aucune question générée. Essaie un autre sujet ou difficulté.")
            return

        st.session_state["gen_questions"] = questions
        st.session_state["gen_topic"] = effective_topic
        st.session_state["gen_difficulty"] = difficulty

    # ── Display generated questions ───────────────────────────────────────
    if "gen_questions" not in st.session_state:
        return

    q_list: list[str] = st.session_state["gen_questions"]
    q_topic: str = st.session_state["gen_topic"]
    q_diff: str = st.session_state["gen_difficulty"]

    st.divider()
    st.subheader(f"Questions générées — *{q_topic}* ({_DIFF_LABELS[q_diff]})")

    for i, question in enumerate(q_list):
        with st.expander(f"**Question {i + 1}** — {question[:70]}{'…' if len(question) > 70 else ''}", expanded=True):
            st.write(question)
            answer = st.text_area(
                "Réponse modèle *(optionnelle — tu peux la laisser vide)*",
                key=f"ans_{q_topic}_{q_diff}_{i}",
                height=80,
                placeholder="Écris ici la réponse attendue pour t'aider lors de la révision…",
            )
            if st.button("➕ Ajouter à la révision", key=f"add_{q_topic}_{q_diff}_{i}"):
                card_id = add_card(
                    question=question,
                    answer=answer.strip() or "(pas de réponse modèle)",
                    topic=q_topic,
                    difficulty=q_diff,
                )
                st.success(f"✅ Carte #{card_id} ajoutée à ton deck de révision !")

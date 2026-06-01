"""🔄 Révision tab — SM-2 spaced-repetition session."""

from __future__ import annotations

import streamlit as st

from rag_bachelor.study.srs import update_card
from rag_bachelor.study.store import get_due_cards, save_review

# Mapping from button label to SM-2 grade
_GRADES: dict[str, tuple[int, str]] = {
    "😰 Raté":    (0, "red"),
    "🤔 Difficile": (2, "orange"),
    "😊 Bien":    (4, "green"),
    "🌟 Parfait": (5, "green"),
}


def render() -> None:
    st.header("🔄 Révision par répétition espacée")

    due_cards = get_due_cards(limit=20)

    if not due_cards:
        st.success("🎉 Bravo ! Aucune carte à réviser pour aujourd'hui.")
        st.balloons()
        return

    total = len(due_cards)
    st.info(f"📅 **{total}** carte(s) à réviser aujourd'hui")

    # Persist the session index across Streamlit reruns
    if "review_idx" not in st.session_state:
        st.session_state.review_idx = 0

    idx: int = st.session_state.review_idx

    if idx >= total:
        st.success("✅ Session de révision terminée !")
        if st.button("🔄 Recommencer la session"):
            st.session_state.review_idx = 0
            st.rerun()
        return

    card = due_cards[idx]

    # Progress bar
    st.progress(idx / total, text=f"Carte **{idx + 1}** / {total}")

    # Difficulty badge
    diff_badges = {"facile": "🟢 Facile", "moyen": "🟡 Moyen", "difficile": "🔴 Difficile"}
    badge = diff_badges.get(card.difficulty, card.difficulty)
    st.caption(f"Sujet : **{card.topic}** &nbsp;·&nbsp; {badge}")

    st.divider()
    st.markdown(f"### ❓ {card.question}")

    # Show/hide answer toggle
    show = st.checkbox("👁️ Afficher la réponse", key=f"show_{card.id}_{idx}")
    if show:
        st.info(f"**Réponse :** {card.answer}")
        st.markdown("**Comment tu t'en es sorti ?**")

        cols = st.columns(len(_GRADES))
        for col, (label, (grade, _)) in zip(cols, _GRADES.items()):
            with col:
                if st.button(label, key=f"grade_{card.id}_{grade}", use_container_width=True):
                    updated = update_card(card, grade)
                    save_review(updated, grade)
                    st.session_state.review_idx = idx + 1
                    st.rerun()

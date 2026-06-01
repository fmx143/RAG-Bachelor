"""📊 Progrès tab — per-topic mastery bars and weak/strong subject summary."""

from __future__ import annotations

import streamlit as st

from rag_bachelor.study.stats import TopicStats, get_topic_stats
from rag_bachelor.study.store import get_all_cards, get_due_cards


def render() -> None:
    st.header("📊 Progrès & Sujets")

    all_cards = get_all_cards()
    due_cards = get_due_cards(limit=200)

    # ── Summary metrics ───────────────────────────────────────────────────
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("🃏 Total cartes", len(all_cards))
    with col2:
        st.metric("📅 À réviser aujourd'hui", len(due_cards))
    with col3:
        if all_cards:
            avg_ease = sum(c.ease_factor for c in all_cards) / len(all_cards)
            st.metric("⚖️ Facilité moyenne", f"{avg_ease:.2f}")
        else:
            st.metric("⚖️ Facilité moyenne", "—")

    st.divider()

    stats: list[TopicStats] = get_topic_stats()

    if not stats:
        st.info(
            "Aucune carte de révision créée. "
            "Va dans **🎯 Générer des questions** pour commencer."
        )
        return

    # ── Per-topic mastery bars ────────────────────────────────────────────
    st.subheader("Maîtrise par sujet")
    st.caption("Les sujets les plus faibles sont affichés en premier.")

    for s in stats:
        if s.mastery_pct < 40:
            icon = "🔴"
        elif s.mastery_pct < 70:
            icon = "🟡"
        else:
            icon = "🟢"

        col_label, col_meta = st.columns([3, 1])
        with col_label:
            st.markdown(f"{icon} **{s.topic}** — {s.mastery_pct:.0f}%")
            st.progress(s.mastery_pct / 100)
        with col_meta:
            st.caption(
                f"📅 {s.due_cards} due(s)  \n"
                f"🃏 {s.total_cards} carte(s)  \n"
                f"⏱️ {s.avg_interval:.0f}j moy."
            )

    st.divider()

    # ── Weak vs strong split ──────────────────────────────────────────────
    col_weak, col_strong = st.columns(2)

    with col_weak:
        st.subheader("📚 À renforcer")
        weak = [s for s in stats if s.mastery_pct < 40]
        if weak:
            for s in sorted(weak, key=lambda x: x.mastery_pct):
                st.markdown(f"🔴 **{s.topic}** ({s.mastery_pct:.0f}%)")
        else:
            st.success("✅ Aucun sujet critique pour le moment !")

    with col_strong:
        st.subheader("💪 Points forts")
        strong = [s for s in stats if s.mastery_pct >= 70]
        if strong:
            for s in sorted(strong, key=lambda x: -x.mastery_pct):
                st.markdown(f"🟢 **{s.topic}** ({s.mastery_pct:.0f}%)")
        else:
            st.info("Continue à réviser pour faire apparaître tes points forts !")

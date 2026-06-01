"""Streamlit application entry point."""

import streamlit as st

from rag_bachelor.app.tabs import (
    ask,
    documentation,
    generate_questions,
    progress,
    revision,
    settings_tab,
)
from rag_bachelor.config import settings as cfg
from rag_bachelor.core.llm import is_online

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="🎓 Assistant Révision",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={
        "About": "Assistant de révision RAG local — licence universitaire",
    },
)

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("🎓 Assistant Révision")
    st.caption("Révise tes cours avec l'IA — en ligne ou hors ligne.")
    st.divider()

    # Provider status badge
    online = is_online() and bool(cfg.openai_api_key)
    force = cfg.force_provider.strip().lower()

    if force == "ollama":
        mode_label, mode_icon = "Ollama (forcé)", "💻"
    elif force == "openai":
        mode_label, mode_icon = "OpenAI (forcé)", "🌐"
    elif online:
        mode_label, mode_icon = "OpenAI — en ligne", "🌐"
    else:
        mode_label, mode_icon = "Ollama — hors ligne", "💻"

    st.metric(f"{mode_icon} Mode", mode_label)

    st.divider()
    st.markdown(
        "**Raccourcis**\n"
        "- Dépose tes PDFs → **📚 Docs**\n"
        "- Pose une question → **❓ Question**\n"
        "- Révise tes cartes → **🔄 Révision**\n"
        "- Génère des QCM → **🎯 Générer**\n"
        "- Vois ta progression → **📊 Progrès**"
    )

# ── Main tabs ─────────────────────────────────────────────────────────────────
tab_docs, tab_ask, tab_rev, tab_gen, tab_prog, tab_cfg = st.tabs([
    "📚 Documentation",
    "❓ Poser une question",
    "🔄 Révision",
    "🎯 Générer des questions",
    "📊 Progrès",
    "⚙️ Paramètres",
])

with tab_docs:
    documentation.render()

with tab_ask:
    ask.render()

with tab_rev:
    revision.render()

with tab_gen:
    generate_questions.render()

with tab_prog:
    progress.render()

with tab_cfg:
    settings_tab.render()

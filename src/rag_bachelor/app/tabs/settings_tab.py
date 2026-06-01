"""⚙️ Paramètres tab — provider status, manual override, model info."""

from __future__ import annotations

import streamlit as st

from rag_bachelor.config import settings as cfg
from rag_bachelor.core.llm import _ConnectivityCache, is_online


def render() -> None:
    st.header("⚙️ Paramètres")

    # ── Provider status ───────────────────────────────────────────────────
    st.subheader("Fournisseur LLM actuel")

    online = is_online()
    has_key = bool(cfg.openai_api_key)
    force = cfg.force_provider.strip().lower()

    if force == "openai":
        active_provider = "OpenAI (forcé)"
        provider_icon = "🌐"
    elif force == "ollama":
        active_provider = "Ollama (forcé)"
        provider_icon = "💻"
    elif has_key and online:
        active_provider = "OpenAI (auto)"
        provider_icon = "🌐"
    else:
        active_provider = "Ollama — mode hors ligne"
        provider_icon = "💻"

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric(f"{provider_icon} Fournisseur", active_provider)
    with col2:
        st.metric("🌍 Internet", "✅ Connecté" if online else "❌ Hors ligne")
    with col3:
        st.metric("🔑 Clé OpenAI", "✅ Configurée" if has_key else "❌ Absente")

    if st.button("🔄 Vérifier la connexion"):
        _ConnectivityCache.invalidate()
        st.rerun()

    st.divider()

    # ── OpenAI key guidance ───────────────────────────────────────────────
    st.subheader("Clé API OpenAI")
    if has_key:
        st.success("✅ La clé API est configurée dans votre fichier `.env`.")
    else:
        st.warning("⚠️ Aucune clé OpenAI détectée — seul Ollama (hors ligne) est disponible.")
        st.code("# Ajoute dans ton fichier .env :\nOPENAI_API_KEY=sk-...")

    st.divider()

    # ── Force provider ────────────────────────────────────────────────────
    st.subheader("Forcer un mode")
    st.markdown(
        "Pour forcer un fournisseur spécifique, modifie `FORCE_PROVIDER` dans ton `.env` "
        "et relance l'application."
    )
    options = {"" : "Auto (recommandé)", "openai": "OpenAI uniquement", "ollama": "Ollama uniquement"}
    current_label = options.get(cfg.force_provider.strip().lower(), "Auto (recommandé)")
    st.info(f"**Réglage actuel :** `FORCE_PROVIDER={cfg.force_provider!r}` → {current_label}")

    with st.expander("Exemples de configuration `.env`"):
        st.code(
            "# Détection automatique (défaut)\n"
            "FORCE_PROVIDER=\n\n"
            "# Forcer OpenAI même hors ligne (déconseillé)\n"
            "FORCE_PROVIDER=openai\n\n"
            "# Forcer Ollama même avec internet disponible\n"
            "FORCE_PROVIDER=ollama",
            language="bash",
        )

    st.divider()

    # ── Model config ──────────────────────────────────────────────────────
    st.subheader("Modèles configurés")

    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("**OpenAI**")
        st.write(f"• Standard : `{cfg.openai_model_default}`")
        st.write(f"• Difficile : `{cfg.openai_model_hard}`")
    with col_b:
        st.markdown("**Ollama**")
        st.write(f"• Modèle : `{cfg.ollama_model}`")
        st.write(f"• Hôte : `{cfg.ollama_host}`")

    st.divider()

    st.subheader("Embeddings")
    st.write(f"• Modèle : `{cfg.embedding_model}`")
    st.write(f"• Cache : `{cfg.embedding_cache_dir}`")

    st.caption(
        "Pour changer les modèles, modifie le fichier `.env` et relance l'application. "
        "Changer le modèle d'embeddings nécessite de **re-indexer** tous tes documents."
    )

"""RAG question-answering: retrieve relevant chunks then generate a French answer."""

from rag_bachelor.core.llm import get_provider
from rag_bachelor.core.retriever import SearchResult, retrieve

# System prompt in French — instructs the model to cite sources and stay grounded.
_SYSTEM_PROMPT = (
    "Tu es un assistant pédagogique expert qui aide un étudiant à comprendre ses cours "
    "universitaires. Tu réponds en français, de manière claire, précise et structurée.\n"
    "Lorsque tu cites tes sources, indique le document et la page entre crochets, "
    "par exemple [cours.pdf, p.3].\n"
    "Si l'information ne se trouve pas dans les extraits fournis, dis-le clairement "
    "plutôt que d'inventer."
)


def answer_question(
    question: str,
    top_k: int = 5,
) -> tuple[str, list[SearchResult]]:
    """Answer *question* using RAG.

    Returns:
        A tuple ``(answer, source_chunks)`` where *answer* is the LLM reply
        and *source_chunks* are the retrieved passages used as context.
    """
    chunks = retrieve(question, top_k=top_k)

    if not chunks:
        no_doc_msg = (
            "Je n'ai trouvé aucun document indexé. "
            "Va dans l'onglet 📚 Documentation pour indexer tes cours."
        )
        return no_doc_msg, []

    context_parts = [
        f"[{c.source}, p.{c.page}]\n{c.text}" for c in chunks
    ]
    context = "\n\n---\n\n".join(context_parts)

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Extraits de cours pertinents :\n\n{context}\n\n"
                f"Question : {question}"
            ),
        },
    ]

    provider, _ = get_provider()
    answer = provider.chat(messages)
    return answer, chunks

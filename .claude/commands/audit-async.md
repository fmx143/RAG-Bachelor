# Audit async route handlers for blocking calls

Scan every `async def` route handler in `src/rag_bachelor/app/web/routes/` for synchronous calls that block the event loop. For each file, report:

1. **Blocking DB calls** — any call to ChromaDB, SQLite, or study store functions (`get_collection`, `list_sources`, `collection_count`, `get_all_cards`, `get_due_cards`, `save_review`, `get_topic_stats`) that is NOT wrapped in `asyncio.to_thread`.

2. **Blocking file I/O** — any `Path.write_bytes`, `Path.read_bytes`, `Path.unlink`, `Path.mkdir`, `glob` or similar that is NOT wrapped in `asyncio.to_thread`.

3. **Blocking CPU work** — any call to `extract_pages`, `chunk_pages`, `index_chunks`, `generate_questions`, `answer_question`, `get_provider`, or `OllamaProvider.chat` that is NOT wrapped in `asyncio.to_thread`.

Note: `sidebar_ctx()` is synchronous by design (no I/O) — do NOT flag it.

For each finding output:
- File and line number
- The blocking call
- A one-line fix showing the `asyncio.to_thread` wrapper

Then summarise: total blocking calls found, and which routes are safe vs. still have issues.

After reporting, ask: "Fix all of these now?"

"""Persistence layer for flashcards, review history, and the question bank.

Connects to a standalone PostgreSQL server (isolated data-tier container, e.g.
on a NAS) when ``POSTGRES_HOST`` is set, otherwise falls back to a local SQLite
file for frictionless local dev. Both backends expose the exact same function
surface below, so callers never need to know which one is active.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import date
from typing import Any

import psycopg
from psycopg.rows import dict_row

from rag_bachelor.config import settings
from rag_bachelor.study.srs import Card

logger = logging.getLogger(__name__)

_conn: sqlite3.Connection | psycopg.Connection[dict[str, Any]] | None = None
_conn_lock = threading.Lock()  # background jobs share the connection: one reconnect at a time

_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    question     TEXT    NOT NULL,
    answer       TEXT    NOT NULL,
    topic        TEXT    NOT NULL,
    difficulty   TEXT    NOT NULL,
    interval     INTEGER NOT NULL DEFAULT 1,
    repetitions  INTEGER NOT NULL DEFAULT 0,
    ease_factor  REAL    NOT NULL DEFAULT 2.5,
    due_date     TEXT    NOT NULL,
    created_at   TEXT    NOT NULL DEFAULT (date('now'))
);

CREATE TABLE IF NOT EXISTS reviews (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id     INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    grade       INTEGER NOT NULL,
    reviewed_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS question_bank (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    question    TEXT NOT NULL,
    answer      TEXT NOT NULL,
    difficulty  TEXT NOT NULL,
    qtype       TEXT NOT NULL DEFAULT 'free',
    options     TEXT,
    correct     TEXT,
    source      TEXT NOT NULL,
    pages       TEXT NOT NULL,
    chunk_ids   TEXT NOT NULL,
    passage     TEXT NOT NULL DEFAULT '',
    card_id     INTEGER REFERENCES cards(id) ON DELETE SET NULL,
    embedding   TEXT,
    last_result INTEGER,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(source, question)
);

CREATE TABLE IF NOT EXISTS documents (
    path   TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS revision_sheets (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    source     TEXT NOT NULL,
    page_from  INTEGER NOT NULL,
    page_to    INTEGER NOT NULL,
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS mind_maps (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    source     TEXT NOT NULL,
    page_from  INTEGER NOT NULL,
    page_to    INTEGER NOT NULL,
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS podcasts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    source     TEXT NOT NULL,
    page_from  INTEGER NOT NULL,
    page_to    INTEGER NOT NULL,
    script     TEXT NOT NULL,
    audio      BLOB NOT NULL,
    duration   REAL NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS chapters (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    source     TEXT NOT NULL,
    ord        INTEGER NOT NULL,
    title      TEXT NOT NULL,
    page_from  INTEGER NOT NULL,
    page_to    INTEGER NOT NULL
);
"""

_POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    id           INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    question     TEXT    NOT NULL,
    answer       TEXT    NOT NULL,
    topic        TEXT    NOT NULL,
    difficulty   TEXT    NOT NULL,
    interval     INTEGER          NOT NULL DEFAULT 1,
    repetitions  INTEGER          NOT NULL DEFAULT 0,
    ease_factor  DOUBLE PRECISION NOT NULL DEFAULT 2.5,
    due_date     TEXT             NOT NULL,
    created_at   TEXT             NOT NULL DEFAULT CURRENT_DATE
);

CREATE TABLE IF NOT EXISTS reviews (
    id          INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    card_id     INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    grade       INTEGER NOT NULL,
    reviewed_at TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS question_bank (
    id          INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    question    TEXT NOT NULL,
    answer      TEXT NOT NULL,
    difficulty  TEXT NOT NULL,
    qtype       TEXT NOT NULL DEFAULT 'free',
    options     TEXT,
    correct     TEXT,
    source      TEXT NOT NULL,
    pages       TEXT NOT NULL,
    chunk_ids   TEXT NOT NULL,
    passage     TEXT NOT NULL DEFAULT '',
    card_id     INTEGER REFERENCES cards(id) ON DELETE SET NULL,
    embedding   TEXT,
    last_result INTEGER,
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    -- A pathologically long generated question could exceed Postgres' ~2704-byte
    -- btree index row limit; SQLite has no such ceiling. Generated questions are
    -- one sentence, so this is left unguarded.
    UNIQUE(source, question)
);

CREATE TABLE IF NOT EXISTS documents (
    path   TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS revision_sheets (
    id         INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source     TEXT NOT NULL,
    page_from  INTEGER NOT NULL,
    page_to    INTEGER NOT NULL,
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mind_maps (
    id         INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source     TEXT NOT NULL,
    page_from  INTEGER NOT NULL,
    page_to    INTEGER NOT NULL,
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS podcasts (
    id         INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source     TEXT NOT NULL,
    page_from  INTEGER NOT NULL,
    page_to    INTEGER NOT NULL,
    script     TEXT NOT NULL,
    audio      BYTEA NOT NULL,
    duration   DOUBLE PRECISION NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS chapters (
    id         INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source     TEXT NOT NULL,
    ord        INTEGER NOT NULL,
    title      TEXT NOT NULL,
    page_from  INTEGER NOT NULL,
    page_to    INTEGER NOT NULL
);
"""

# Columns added to `cards` / `question_bank` after the initial release. The CREATE TABLE IF NOT
# EXISTS above only affects brand-new databases, so existing ones (the local
# SQLite file and the NAS Postgres) need an ALTER. Postgres has ADD COLUMN IF
# NOT EXISTS; SQLite doesn't, so there we check PRAGMA table_info first.
_COLUMNS_ADDED = {
    "cards": {
        "qtype": "TEXT NOT NULL DEFAULT 'free'",
        "options": "TEXT",
        "correct": "TEXT",
    },
    # Same for `question_bank`: the supporting excerpt shown under each answer.
    "question_bank": {"passage": "TEXT NOT NULL DEFAULT ''"},
}


def _use_postgres() -> bool:
    return bool(settings.postgres_host)


def _adapt(sql: str) -> str:
    """Translate sqlite3's ``?`` placeholders to psycopg's ``%s`` when needed."""
    return sql.replace("?", "%s") if _use_postgres() else sql


def _in_subject(column: str, subject: str) -> tuple[str, str]:
    """SQL clause + parameter keeping rows whose *column* starts with ``subject/``."""
    prefix = subject + "/"
    return f"substr({column}, 1, {len(prefix)}) = ?", prefix


def _migrate(conn: sqlite3.Connection | psycopg.Connection[dict[str, Any]]) -> None:
    """Add any missing columns, and backfill stale data. Idempotent on both backends."""
    for table, columns in _COLUMNS_ADDED.items():
        if _use_postgres():
            for name, decl in columns.items():
                conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {name} {decl}")
        else:
            existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            for name, decl in columns.items():
                if name not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    _retag_single_answer_multi(conn)
    conn.commit()


def _retag_single_answer_multi(
    conn: sqlite3.Connection | psycopg.Connection[dict[str, Any]],
) -> None:
    """Relabel `mcq_multi` rows holding a single correct answer as `mcq_single`.

    Generation used to accept a one-answer `mcq_multi` from the LLM (see
    core/qtypes._validate_mcq), and those rows render as a checkbox group with
    exactly one right option. Idempotent — a retagged row no longer matches
    the WHERE clause on the next run.
    """
    for table in ("cards", "question_bank"):
        rows = conn.execute(
            _adapt(f"SELECT id, correct FROM {table} WHERE qtype = 'mcq_multi'")
        ).fetchall()
        stale_ids = [row["id"] for row in rows if len(json.loads(row["correct"] or "[]")) < 2]
        for row_id in stale_ids:
            conn.execute(_adapt(f"UPDATE {table} SET qtype = 'mcq_single' WHERE id = ?"), (row_id,))


def _usable(conn: sqlite3.Connection | psycopg.Connection[dict[str, Any]]) -> bool:
    """Ping a Postgres connection before use (the ``pool_pre_ping`` of this singleton).

    A recreated Postgres container leaves the old socket dead; without this check the app
    would answer 503 until restarted. SQLite is a local file and cannot go stale.
    """
    if isinstance(conn, sqlite3.Connection):
        return True
    try:
        if conn.closed or conn.broken:
            return False
        if conn.info.transaction_status == psycopg.pq.TransactionStatus.INERROR:
            conn.rollback()  # an aborted transaction would reject the ping and every query
        conn.execute("SELECT 1")
    except psycopg.Error:
        return False
    return True


def get_conn() -> sqlite3.Connection | psycopg.Connection[dict[str, Any]]:
    """Return the singleton database connection, (re)opening it when missing or dead."""
    global _conn
    with _conn_lock:
        if _conn is not None and not _usable(_conn):
            logger.warning("Connexion PostgreSQL perdue : reconnexion.")
            try:
                _conn.close()
            except psycopg.Error:
                pass
            _conn = None
        if _conn is None:
            conn: sqlite3.Connection | psycopg.Connection[dict[str, Any]]
            if _use_postgres():
                conn = psycopg.connect(
                    host=settings.postgres_host,
                    port=settings.postgres_port,
                    dbname=settings.postgres_db,
                    user=settings.postgres_user,
                    password=settings.postgres_password.get_secret_value(),
                    row_factory=dict_row,
                    autocommit=False,
                )
                # Idempotent (CREATE ... IF NOT EXISTS, ADD COLUMN IF NOT EXISTS): safe to replay.
                conn.execute(_POSTGRES_SCHEMA)
                conn.commit()
            else:
                settings.db_path.parent.mkdir(parents=True, exist_ok=True)
                # timeout=30: the bank-generation background job holds writes open for
                # minutes while foreground requests share this same connection; the
                # sqlite3 default (5s) was tuned for short-lived request-only writes.
                conn = sqlite3.connect(str(settings.db_path), check_same_thread=False, timeout=30)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.executescript(_SQLITE_SCHEMA)
                conn.commit()
            _migrate(conn)
            _conn = conn  # published only once fully initialised
        return _conn


# ── App settings (persisted key/value, e.g. active LLM provider toggle) ────────


def get_setting(key: str, default: str) -> str:
    """Return the persisted value for *key*, or *default* if unset."""
    conn = get_conn()
    row = conn.execute(_adapt("SELECT value FROM app_settings WHERE key = ?"), (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    """Persist *value* for *key*, overwriting any existing entry."""
    conn = get_conn()
    conn.execute(
        _adapt(
            "INSERT INTO app_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
        ),
        (key, value),
    )
    conn.commit()


# ── Documents (relative path ↔ content hash) ─────────────────────────────────


def document_paths() -> dict[str, str]:
    """Return every known document as ``{relative path: sha256}``."""
    rows = get_conn().execute("SELECT path, sha256 FROM documents").fetchall()
    return {r["path"]: r["sha256"] for r in rows}


def document_by_sha(sha: str) -> str | None:
    """Return the path of the document with content hash *sha*, or None."""
    row = (
        get_conn().execute(_adapt("SELECT path FROM documents WHERE sha256 = ?"), (sha,)).fetchone()
    )
    return row["path"] if row else None


def add_document(path: str, sha: str) -> None:
    conn = get_conn()
    conn.execute(_adapt("INSERT INTO documents (path, sha256) VALUES (?, ?)"), (path, sha))
    conn.commit()


def delete_document(path: str) -> None:
    conn = get_conn()
    conn.execute(_adapt("DELETE FROM documents WHERE path = ?"), (path,))
    conn.execute(_adapt("DELETE FROM chapters WHERE source = ?"), (path,))
    conn.commit()


def rename_document(old: str, new: str) -> None:
    """Re-key a document and everything that names it (bank questions, SRS topics), atomically."""
    conn = get_conn()
    try:
        for sql in (
            "UPDATE documents SET path = ? WHERE path = ?",
            "UPDATE question_bank SET source = ? WHERE source = ?",
            "UPDATE revision_sheets SET source = ? WHERE source = ?",
            "UPDATE mind_maps SET source = ? WHERE source = ?",
            "UPDATE podcasts SET source = ? WHERE source = ?",
            "UPDATE chapters SET source = ? WHERE source = ?",
            "UPDATE cards SET topic = ? WHERE topic = ?",
        ):
            conn.execute(_adapt(sql), (new, old))
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# ── Card CRUD ─────────────────────────────────────────────────────────────────


def add_card(
    question: str,
    answer: str,
    topic: str,
    difficulty: str,
    qtype: str = "free",
    options: list[str] | None = None,
    correct: list[int] | None = None,
) -> int:
    """Insert a new card and return its id.

    ``qtype`` is one of ``free`` / ``mcq_single`` / ``mcq_multi`` / ``tf``.
    ``options`` / ``correct`` are only meaningful for structured types.
    """
    conn = get_conn()
    sql = (
        "INSERT INTO cards (question, answer, topic, difficulty, qtype, options, correct, due_date) "
        "VALUES (?,?,?,?,?,?,?,?)"
    )
    params = (
        question,
        answer,
        topic,
        difficulty,
        qtype,
        _dump_json(options),
        _dump_json(correct),
        date.today().isoformat(),
    )
    if _use_postgres():
        cur = conn.execute(_adapt(sql) + " RETURNING id", params)
        row = cur.fetchone()
        assert row is not None
        new_id = row["id"]
    else:
        cur = conn.execute(sql, params)
        assert isinstance(cur, sqlite3.Cursor)
        new_id = cur.lastrowid
    conn.commit()
    assert new_id is not None
    return int(new_id)


def get_due_cards(
    limit: int = 20,
    source: str | None = None,
    difficulty: str | None = None,
    qtype: str | None = None,
    subject: str | None = None,
    card_ids: list[int] | None = None,
) -> list[Card]:
    """Return cards whose due_date ≤ today, ordered by due_date ASC.

    ``source`` filters on ``topic`` — the PDF path a bank-derived card was
    stamped with at materialization time (see :func:`materialize_bank_question`);
    ``subject`` keeps the topics under that top-level folder; ``card_ids`` (e.g. a
    chapter's cards, see :func:`bank_card_pages`) keeps only those cards.
    """
    if card_ids is not None and not card_ids:
        return []
    conn = get_conn()
    today = date.today().isoformat()
    clauses = ["due_date <= ?"]
    params: list[Any] = [today]
    if card_ids:
        clauses.append(f"id IN ({','.join('?' * len(card_ids))})")
        params.extend(card_ids)
    if source:
        clauses.append("topic = ?")
        params.append(source)
    if subject:
        clause, prefix = _in_subject("topic", subject)
        clauses.append(clause)
        params.append(prefix)
    if difficulty:
        clauses.append("difficulty = ?")
        params.append(difficulty)
    if qtype:
        clauses.append("qtype = ?")
        params.append(qtype)
    where = " AND ".join(clauses)
    params.append(limit)
    rows = conn.execute(
        _adapt(f"SELECT * FROM cards WHERE {where} ORDER BY due_date LIMIT ?"),
        params,
    ).fetchall()
    return [_row_to_card(r) for r in rows]


def get_card(card_id: int) -> Card | None:
    """Return one card by id, or None."""
    conn = get_conn()
    row = conn.execute(_adapt("SELECT * FROM cards WHERE id = ?"), (card_id,)).fetchone()
    return _row_to_card(row) if row else None


def get_all_cards(topic: str | None = None, subject: str | None = None) -> list[Card]:
    """Return all cards, optionally filtered by *topic* or by *subject* (top-level folder)."""
    conn = get_conn()
    if topic:
        rows = conn.execute(_adapt("SELECT * FROM cards WHERE topic = ?"), (topic,)).fetchall()
    elif subject:
        clause, prefix = _in_subject("topic", subject)
        rows = conn.execute(
            _adapt(f"SELECT * FROM cards WHERE {clause} ORDER BY topic, id"), (prefix,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM cards ORDER BY topic, id").fetchall()
    return [_row_to_card(r) for r in rows]


def save_review(card: Card, grade: int) -> None:
    """Persist a review result and update the card's SM-2 state in one transaction."""
    conn = get_conn()
    conn.execute(_adapt("INSERT INTO reviews (card_id, grade) VALUES (?,?)"), (card.id, grade))
    conn.execute(
        _adapt(
            """UPDATE cards
               SET interval = ?, repetitions = ?, ease_factor = ?, due_date = ?
               WHERE id = ?"""
        ),
        (
            card.interval,
            card.repetitions,
            card.ease_factor,
            card.due_date.isoformat(),
            card.id,
        ),
    )
    conn.commit()


def delete_card(card_id: int) -> None:
    """Delete a card and its review history."""
    conn = get_conn()
    conn.execute(_adapt("DELETE FROM cards WHERE id = ?"), (card_id,))
    conn.commit()


# ── Question bank ─────────────────────────────────────────────────────────────


@dataclass
class BankQuestion:
    """A generated study question with its exact source references.

    ``difficulty`` is one of ``facile`` / ``moyen`` / ``difficile``.
    ``qtype`` is one of ``free`` / ``mcq_single`` / ``mcq_multi`` / ``tf``;
    ``options`` / ``correct`` are only meaningful for structured types.
    ``pages`` / ``chunk_ids`` reference where in the PDF the answer comes from —
    attached programmatically at generation time, never LLM-provided.
    """

    question: str
    answer: str
    difficulty: str
    source: str  # PDF filename
    pages: list[int]
    chunk_ids: list[str]
    qtype: str = "free"
    options: list[str] | None = None
    correct: list[int] | None = None
    id: int | None = None
    card_id: int | None = None  # set once added to the SRS deck
    embedding: list[float] | None = None  # used for semantic near-dup detection only
    last_result: int | None = None  # last self-test result: 1=juste, 0=faux, None=jamais tenté
    passage: str = ""  # 1-5 sentences of the source text backing the answer ("" on old rows)


def add_bank_questions(items: list[BankQuestion]) -> int:
    """Batch-insert bank questions; duplicates (same source+question) are skipped.

    Returns the number of rows actually inserted.
    """
    conn = get_conn()
    sql = _adapt(
        "INSERT INTO question_bank "
        "(question, answer, difficulty, qtype, options, correct, source, pages, chunk_ids, "
        "passage, embedding) VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING"
    )
    # ponytail: one round-trip per row — psycopg's Connection has no
    # executemany(), and a batch here is a handful of rows sitting behind a
    # multi-second LLM call. Use conn.cursor().executemany() (SQLite only) if
    # bulk import ever lands.
    inserted = 0
    for q in items:
        cur = conn.execute(
            sql,
            (
                q.question,
                q.answer,
                q.difficulty,
                q.qtype,
                _dump_json(q.options),
                _dump_json(q.correct),
                q.source,
                json.dumps(q.pages),
                json.dumps(q.chunk_ids),
                q.passage,
                _dump_json(q.embedding),
            ),
        )
        inserted += cur.rowcount
    conn.commit()
    return inserted


def bank_question_embeddings(source: str) -> list[list[float]]:
    """Return every stored embedding vector for *source* (skips rows with none).

    Used to compare newly generated candidates against what's already in the
    bank for semantic near-duplicate detection.
    """
    conn = get_conn()
    rows = conn.execute(
        _adapt("SELECT embedding FROM question_bank WHERE source = ? AND embedding IS NOT NULL"),
        (source,),
    ).fetchall()
    return [json.loads(r["embedding"]) for r in rows]


def _bank_where(
    source: str | None,
    difficulty: str | None,
    search: str | None = None,
    qtype: str | None = None,
    result: str | None = None,
    deck: str | None = None,
    subject: str | None = None,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if source:
        clauses.append("source = ?")
        params.append(source)
    if subject:
        clause, prefix = _in_subject("source", subject)
        clauses.append(clause)
        params.append(prefix)
    if difficulty:
        clauses.append("difficulty = ?")
        params.append(difficulty)
    if search:
        # SQLite's LIKE folds ASCII case, Postgres' doesn't (and ILIKE is
        # PG-only) — LOWER() on both sides keeps search identical across
        # backends. ponytail: ASCII-only folding, so "Ecrire" won't match
        # "écrire"; reach for unaccent/citext (PG) only if that bites.
        clauses.append("LOWER(question) LIKE ?")
        params.append(f"%{search.lower()}%")
    if qtype:
        clauses.append("qtype = ?")
        params.append(qtype)
    if result == "correct":
        clauses.append("last_result = 1")
    elif result == "incorrect":
        clauses.append("last_result = 0")
    elif result == "untried":
        clauses.append("last_result IS NULL")
    if deck == "in":
        clauses.append("card_id IS NOT NULL")
    elif deck == "out":
        clauses.append("card_id IS NULL")
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def list_bank_questions(
    source: str | None = None,
    difficulty: str | None = None,
    search: str | None = None,
    qtype: str | None = None,
    result: str | None = None,
    deck: str | None = None,
    limit: int = 20,
    offset: int = 0,
    order_asc: bool = False,
    subject: str | None = None,
) -> list[BankQuestion]:
    """Return bank questions, optionally filtered.

    Newest first by default (the bank tab); ``order_asc`` serves them in
    document order instead — used by revision to work through a PDF front-to-back.
    """
    conn = get_conn()
    where, params = _bank_where(source, difficulty, search, qtype, result, deck, subject)
    order = "ASC" if order_asc else "DESC"
    rows = conn.execute(
        _adapt(f"SELECT * FROM question_bank{where} ORDER BY id {order} LIMIT ? OFFSET ?"),
        [*params, limit, offset],
    ).fetchall()
    return [_row_to_bank_question(r) for r in rows]


def count_bank_questions(
    source: str | None = None,
    difficulty: str | None = None,
    search: str | None = None,
    qtype: str | None = None,
    result: str | None = None,
    deck: str | None = None,
    subject: str | None = None,
) -> int:
    """Return how many bank questions match the filters."""
    conn = get_conn()
    where, params = _bank_where(source, difficulty, search, qtype, result, deck, subject)
    row = conn.execute(_adapt(f"SELECT COUNT(*) AS n FROM question_bank{where}"), params).fetchone()
    assert row is not None
    return int(row["n"])


def bank_sources(subject: str | None = None) -> list[str]:
    """Return sorted distinct source paths present in the bank (of one subject, if given)."""
    conn = get_conn()
    clause = ""
    params: tuple[str, ...] = ()
    if subject:
        cond, prefix = _in_subject("source", subject)
        clause, params = f" WHERE {cond}", (prefix,)
    rows = conn.execute(
        _adapt(f"SELECT DISTINCT source FROM question_bank{clause} ORDER BY source"), params
    ).fetchall()
    return [r["source"] for r in rows]


def get_bank_question(bank_id: int) -> BankQuestion | None:
    """Return one bank question by id, or None."""
    conn = get_conn()
    row = conn.execute(_adapt("SELECT * FROM question_bank WHERE id = ?"), (bank_id,)).fetchone()
    return _row_to_bank_question(row) if row else None


def bank_card_pages(source: str) -> dict[int, int]:
    """``{card id: first cited page}`` for the deck cards made from *source*'s bank questions.

    A card belongs to the chapter holding that page (cards made outside the bank have none).
    """
    rows = (
        get_conn()
        .execute(
            _adapt(
                "SELECT card_id, pages FROM question_bank WHERE source = ? AND card_id IS NOT NULL"
            ),
            (source,),
        )
        .fetchall()
    )
    out: dict[int, int] = {}
    for r in rows:
        pages = [int(p) for p in json.loads(r["pages"]) if isinstance(p, int)]
        if pages:
            out[int(r["card_id"])] = min(pages)
    return out


def link_bank_card(bank_id: int, card_id: int) -> None:
    """Record that a bank question was copied into the SRS deck as *card_id*."""
    conn = get_conn()
    conn.execute(_adapt("UPDATE question_bank SET card_id = ? WHERE id = ?"), (card_id, bank_id))
    conn.commit()


def materialize_bank_question(q: BankQuestion) -> int:
    """Copy one bank question into the SRS deck and link it back. Returns the new card id.

    Factors out the add_card()+link_bank_card() pair used by every "add to
    révision" call site (manual add, bulk add-all, auto-add on a wrong answer,
    and revision pulling straight from the bank when nothing is due).
    """
    assert q.id is not None
    card_id = add_card(
        question=q.question,
        answer=q.answer,
        topic=q.source,
        difficulty=q.difficulty,
        qtype=q.qtype,
        options=q.options,
        correct=q.correct,
    )
    link_bank_card(q.id, card_id)
    return card_id


def record_bank_attempt(bank_id: int, correct: bool) -> None:
    """Store the latest self-test result for a bank question (1=juste, 0=faux)."""
    conn = get_conn()
    # int, not bool: psycopg adapts a Python bool to PG `boolean`, which PG
    # refuses to store in an INTEGER column.
    conn.execute(
        _adapt("UPDATE question_bank SET last_result = ? WHERE id = ?"),
        (1 if correct else 0, bank_id),
    )
    conn.commit()


def delete_bank_question(bank_id: int) -> None:
    """Remove a question from the bank (any linked SRS card is kept)."""
    conn = get_conn()
    conn.execute(_adapt("DELETE FROM question_bank WHERE id = ?"), (bank_id,))
    conn.commit()


def delete_bank_questions(bank_ids: list[int]) -> int:
    """Remove several bank questions at once (any linked SRS cards are kept).

    Returns the number of rows actually deleted.
    """
    if not bank_ids:
        return 0
    conn = get_conn()
    placeholders = ",".join("?" * len(bank_ids))
    cur = conn.execute(_adapt(f"DELETE FROM question_bank WHERE id IN ({placeholders})"), bank_ids)
    conn.commit()
    return cur.rowcount


# ── Revision sheets (chapter summaries) ────────────────────────────────────────


@dataclass
class Sheet:
    source: str
    page_from: int
    page_to: int
    content: str = ""
    id: int | None = None
    created_at: str = ""


def add_sheet(source: str, page_from: int, page_to: int, content: str) -> int:
    """Insert a revision sheet and return its id."""
    conn = get_conn()
    sql = "INSERT INTO revision_sheets (source, page_from, page_to, content) VALUES (?,?,?,?)"
    params = (source, page_from, page_to, content)
    if _use_postgres():
        row = conn.execute(_adapt(sql) + " RETURNING id", params).fetchone()
        assert row is not None
        new_id = row["id"]
    else:
        cur = conn.execute(sql, params)
        assert isinstance(cur, sqlite3.Cursor)
        new_id = cur.lastrowid
    conn.commit()
    assert new_id is not None
    return int(new_id)


def _sheet(row: Any) -> Sheet:
    return Sheet(
        source=row["source"],
        page_from=row["page_from"],
        page_to=row["page_to"],
        content=row["content"],
        id=row["id"],
        created_at=str(row["created_at"])[:16],
    )


def list_sheets(subject: str | None = None, source: str | None = None) -> list[Sheet]:
    """Every sheet, newest first, without its (large) content (of one subject, if given, or of one document)."""
    clause = ""
    params: tuple[str, ...] = ()
    if source:
        clause, params = " WHERE source = ?", (source,)
    elif subject:
        cond, prefix = _in_subject("source", subject)
        clause, params = f" WHERE {cond}", (prefix,)
    rows = (
        get_conn()
        .execute(
            _adapt(
                "SELECT id, source, page_from, page_to, '' AS content, created_at "
                f"FROM revision_sheets{clause} ORDER BY id DESC"
            ),
            params,
        )
        .fetchall()
    )
    return [_sheet(r) for r in rows]


def get_sheet(sheet_id: int) -> Sheet | None:
    row = (
        get_conn()
        .execute(_adapt("SELECT * FROM revision_sheets WHERE id = ?"), (sheet_id,))
        .fetchone()
    )
    return _sheet(row) if row else None


def delete_sheet(sheet_id: int) -> None:
    conn = get_conn()
    conn.execute(_adapt("DELETE FROM revision_sheets WHERE id = ?"), (sheet_id,))
    conn.commit()


# ── Mind maps (chapter trees, JSON in `content`) ───────────────────────────────


def add_map(source: str, page_from: int, page_to: int, content: str) -> int:
    """Insert a mind map and return its id."""
    conn = get_conn()
    sql = "INSERT INTO mind_maps (source, page_from, page_to, content) VALUES (?,?,?,?)"
    params = (source, page_from, page_to, content)
    if _use_postgres():
        row = conn.execute(_adapt(sql) + " RETURNING id", params).fetchone()
        assert row is not None
        new_id = row["id"]
    else:
        cur = conn.execute(sql, params)
        assert isinstance(cur, sqlite3.Cursor)
        new_id = cur.lastrowid
    conn.commit()
    assert new_id is not None
    return int(new_id)


def list_maps(subject: str | None = None, source: str | None = None) -> list[Sheet]:
    """Every mind map, newest first, without its content (of one subject, if given, or of one document)."""
    clause = ""
    params: tuple[str, ...] = ()
    if source:
        clause, params = " WHERE source = ?", (source,)
    elif subject:
        cond, prefix = _in_subject("source", subject)
        clause, params = f" WHERE {cond}", (prefix,)
    rows = (
        get_conn()
        .execute(
            _adapt(
                "SELECT id, source, page_from, page_to, '' AS content, created_at "
                f"FROM mind_maps{clause} ORDER BY id DESC"
            ),
            params,
        )
        .fetchall()
    )
    return [_sheet(r) for r in rows]


def get_map(map_id: int) -> Sheet | None:
    row = get_conn().execute(_adapt("SELECT * FROM mind_maps WHERE id = ?"), (map_id,)).fetchone()
    return _sheet(row) if row else None


def delete_map(map_id: int) -> None:
    conn = get_conn()
    conn.execute(_adapt("DELETE FROM mind_maps WHERE id = ?"), (map_id,))
    conn.commit()


# ── Chapters (a book's page ranges: detected once by core.chapters, then editable) ──


@dataclass
class Chapter:
    title: str
    page_from: int
    page_to: int


def list_chapters(source: str) -> list[Chapter]:
    """*source*'s stored chapters in reading order (empty if never detected)."""
    rows = (
        get_conn()
        .execute(
            _adapt("SELECT title, page_from, page_to FROM chapters WHERE source = ? ORDER BY ord"),
            (source,),
        )
        .fetchall()
    )
    return [Chapter(r["title"], int(r["page_from"]), int(r["page_to"])) for r in rows]


def set_chapters(source: str, chapters: list[Chapter]) -> None:
    """Replace *source*'s chapters, atomically."""
    conn = get_conn()
    try:
        conn.execute(_adapt("DELETE FROM chapters WHERE source = ?"), (source,))
        for i, c in enumerate(chapters):
            conn.execute(
                _adapt(
                    "INSERT INTO chapters (source, ord, title, page_from, page_to) "
                    "VALUES (?,?,?,?,?)"
                ),
                (source, i, c.title, c.page_from, c.page_to),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# ── Podcasts (two-voice audio lessons: transcript JSON + MP3/WAV bytes) ────────


@dataclass
class Podcast:
    source: str
    page_from: int
    page_to: int
    script: str = ""
    duration: float = 0.0
    id: int | None = None
    created_at: str = ""


def add_podcast(
    source: str, page_from: int, page_to: int, script: str, audio: bytes, duration: float
) -> int:
    """Insert a podcast and return its id."""
    conn = get_conn()
    sql = (
        "INSERT INTO podcasts (source, page_from, page_to, script, audio, duration) "
        "VALUES (?,?,?,?,?,?)"
    )
    params = (source, page_from, page_to, script, audio, duration)
    if _use_postgres():
        row = conn.execute(_adapt(sql) + " RETURNING id", params).fetchone()
        assert row is not None
        new_id = row["id"]
    else:
        cur = conn.execute(sql, params)
        assert isinstance(cur, sqlite3.Cursor)
        new_id = cur.lastrowid
    conn.commit()
    assert new_id is not None
    return int(new_id)


def _podcast(row: Any) -> Podcast:
    return Podcast(
        source=row["source"],
        page_from=row["page_from"],
        page_to=row["page_to"],
        script=row["script"],
        duration=float(row["duration"]),
        id=row["id"],
        created_at=str(row["created_at"])[:16],
    )


def list_podcasts(subject: str | None = None, source: str | None = None) -> list[Podcast]:
    """Every podcast, newest first, without script or audio (of one subject, if given, or of one document)."""
    clause = ""
    params: tuple[str, ...] = ()
    if source:
        clause, params = " WHERE source = ?", (source,)
    elif subject:
        cond, prefix = _in_subject("source", subject)
        clause, params = f" WHERE {cond}", (prefix,)
    rows = (
        get_conn()
        .execute(
            _adapt(
                "SELECT id, source, page_from, page_to, '' AS script, duration, created_at "
                f"FROM podcasts{clause} ORDER BY id DESC"
            ),
            params,
        )
        .fetchall()
    )
    return [_podcast(r) for r in rows]


def get_podcast(podcast_id: int) -> Podcast | None:
    """One podcast with its script, without the (large) audio."""
    row = (
        get_conn()
        .execute(
            _adapt(
                "SELECT id, source, page_from, page_to, script, duration, created_at "
                "FROM podcasts WHERE id = ?"
            ),
            (podcast_id,),
        )
        .fetchone()
    )
    return _podcast(row) if row else None


def get_podcast_audio(podcast_id: int) -> bytes | None:
    row = (
        get_conn()
        .execute(_adapt("SELECT audio FROM podcasts WHERE id = ?"), (podcast_id,))
        .fetchone()
    )
    return bytes(row["audio"]) if row else None


def get_podcast_audio_size(podcast_id: int) -> int | None:
    """Byte length of the stored audio, without reading it."""
    row = (
        get_conn()
        .execute(_adapt("SELECT length(audio) AS n FROM podcasts WHERE id = ?"), (podcast_id,))
        .fetchone()
    )
    return int(row["n"]) if row else None


def get_podcast_audio_slice(podcast_id: int, start: int, length: int) -> bytes | None:
    """``length`` bytes of the stored audio from byte ``start`` (HTTP Range requests)."""
    row = (
        get_conn()
        .execute(
            # substr() on a BLOB (SQLite) / bytea (Postgres) is byte-based and 1-indexed.
            _adapt("SELECT substr(audio, ?, ?) AS part FROM podcasts WHERE id = ?"),
            (start + 1, length, podcast_id),
        )
        .fetchone()
    )
    return bytes(row["part"]) if row else None


def delete_podcast(podcast_id: int) -> None:
    conn = get_conn()
    conn.execute(_adapt("DELETE FROM podcasts WHERE id = ?"), (podcast_id,))
    conn.commit()


# ── Stats (used by rag_bachelor.study.stats) ────────────────────────────────


def get_topic_stats_rows(subject: str | None = None) -> list[dict[str, Any]]:
    """Return one raw row per topic with the aggregates :mod:`study.stats` needs."""
    conn = get_conn()
    today = date.today().isoformat()
    clause, prefix = _in_subject("topic", subject) if subject else ("1 = 1", None)
    rows = conn.execute(
        _adapt(
            f"""
            SELECT
                topic,
                COUNT(*)                                          AS total,
                SUM(CASE WHEN due_date <= ? THEN 1 ELSE 0 END)     AS due,
                AVG(ease_factor)                                   AS avg_ease,
                AVG(interval)                                      AS avg_interval
            FROM cards
            WHERE {clause}
            GROUP BY topic
            ORDER BY topic
            """
        ),
        (today, prefix) if prefix else (today,),
    ).fetchall()
    return [
        {
            "topic": r["topic"],
            "total": r["total"],
            "due": r["due"],
            # Postgres' AVG() on an integer column returns Decimal; normalise to
            # float so callers see the same type regardless of backend.
            "avg_ease": float(r["avg_ease"]) if r["avg_ease"] is not None else None,
            "avg_interval": float(r["avg_interval"]) if r["avg_interval"] is not None else None,
        }
        for r in rows
    ]


def get_review_days(since: date, subject: str | None = None) -> list[tuple[date, int, int]]:
    """``(day, reviews, successes)`` per day since *since* (grade >= 3 is a success)."""
    clause, prefix = _in_subject("c.topic", subject) if subject else ("1 = 1", None)
    rows = (
        get_conn()
        .execute(
            _adapt(
                f"""
                SELECT substr(CAST(r.reviewed_at AS TEXT), 1, 10) AS day,
                       COUNT(*) AS n,
                       SUM(CASE WHEN r.grade >= 3 THEN 1 ELSE 0 END) AS ok
                FROM reviews r JOIN cards c ON c.id = r.card_id
                WHERE {clause} AND substr(CAST(r.reviewed_at AS TEXT), 1, 10) >= ?
                GROUP BY day ORDER BY day
                """
            ),
            (prefix, since.isoformat()) if prefix else (since.isoformat(),),
        )
        .fetchall()
    )
    return [(date.fromisoformat(r["day"]), int(r["n"]), int(r["ok"])) for r in rows]


# ── Internal helpers ──────────────────────────────────────────────────────────


def _dump_json(value: object | None) -> str | None:
    """Encode a nullable JSON column (``options`` / ``correct`` / ``embedding``)."""
    return json.dumps(value) if value is not None else None


def _load_json_col(value: str | None) -> Any:
    """Parse a nullable JSON-encoded column (``options`` / ``correct``)."""
    return json.loads(value) if value is not None else None


def _row_to_bank_question(row: sqlite3.Row | dict[str, Any]) -> BankQuestion:
    return BankQuestion(
        id=row["id"],
        question=row["question"],
        answer=row["answer"],
        difficulty=row["difficulty"],
        qtype=row["qtype"],
        options=_load_json_col(row["options"]),
        correct=_load_json_col(row["correct"]),
        source=row["source"],
        pages=json.loads(row["pages"]),
        chunk_ids=json.loads(row["chunk_ids"]),
        card_id=row["card_id"],
        last_result=row["last_result"],
        passage=row["passage"],
    )


def _row_to_card(row: sqlite3.Row | dict[str, Any]) -> Card:
    return Card(
        id=row["id"],
        question=row["question"],
        answer=row["answer"],
        topic=row["topic"],
        difficulty=row["difficulty"],
        qtype=row["qtype"],
        options=_load_json_col(row["options"]),
        correct=_load_json_col(row["correct"]),
        interval=row["interval"],
        repetitions=row["repetitions"],
        ease_factor=row["ease_factor"],
        due_date=date.fromisoformat(row["due_date"]),
    )

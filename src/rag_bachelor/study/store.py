"""Persistence layer for flashcards, review history, and the question bank.

Connects to a standalone PostgreSQL server (isolated data-tier container, e.g.
on a NAS) when ``POSTGRES_HOST`` is set, otherwise falls back to a local SQLite
file for frictionless local dev. Both backends expose the exact same function
surface below, so callers never need to know which one is active.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Any

import psycopg
from psycopg.rows import dict_row

from rag_bachelor.config import settings
from rag_bachelor.study.srs import Card

_conn: sqlite3.Connection | psycopg.Connection[dict[str, Any]] | None = None

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
    card_id     INTEGER REFERENCES cards(id) ON DELETE SET NULL,
    embedding   TEXT,
    last_result INTEGER,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(source, question)
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
    card_id     INTEGER REFERENCES cards(id) ON DELETE SET NULL,
    embedding   TEXT,
    last_result INTEGER,
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    -- A pathologically long generated question could exceed Postgres' ~2704-byte
    -- btree index row limit; SQLite has no such ceiling. Generated questions are
    -- one sentence, so this is left unguarded.
    UNIQUE(source, question)
);
"""

# Columns added to `cards` after the initial release. The CREATE TABLE IF NOT
# EXISTS above only affects brand-new databases, so existing ones (the local
# SQLite file and the NAS Postgres) need an ALTER. Postgres has ADD COLUMN IF
# NOT EXISTS; SQLite doesn't, so there we check PRAGMA table_info first.
_CARD_COLUMNS_ADDED = {
    "qtype": "TEXT NOT NULL DEFAULT 'free'",
    "options": "TEXT",
    "correct": "TEXT",
}


def _use_postgres() -> bool:
    return bool(settings.postgres_host)


def _adapt(sql: str) -> str:
    """Translate sqlite3's ``?`` placeholders to psycopg's ``%s`` when needed."""
    return sql.replace("?", "%s") if _use_postgres() else sql


def _migrate(conn: sqlite3.Connection | psycopg.Connection[dict[str, Any]]) -> None:
    """Add any missing `cards` columns. Idempotent on both backends."""
    if _use_postgres():
        for name, decl in _CARD_COLUMNS_ADDED.items():
            conn.execute(f"ALTER TABLE cards ADD COLUMN IF NOT EXISTS {name} {decl}")
    else:
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(cards)")}
        for name, decl in _CARD_COLUMNS_ADDED.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE cards ADD COLUMN {name} {decl}")
    conn.commit()


def get_conn() -> sqlite3.Connection | psycopg.Connection[dict[str, Any]]:
    """Return (and lazily open) the singleton database connection."""
    global _conn
    if _conn is None:
        if _use_postgres():
            _conn = psycopg.connect(
                host=settings.postgres_host,
                port=settings.postgres_port,
                dbname=settings.postgres_db,
                user=settings.postgres_user,
                password=settings.postgres_password.get_secret_value(),
                row_factory=dict_row,
                autocommit=False,
            )
            _conn.execute(_POSTGRES_SCHEMA)
            _conn.commit()
        else:
            settings.db_path.parent.mkdir(parents=True, exist_ok=True)
            # timeout=30: the bank-generation background job holds writes open for
            # minutes while foreground requests share this same connection; the
            # sqlite3 default (5s) was tuned for short-lived request-only writes.
            _conn = sqlite3.connect(str(settings.db_path), check_same_thread=False, timeout=30)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL;")
            _conn.executescript(_SQLITE_SCHEMA)
            _conn.commit()
        _migrate(_conn)
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
) -> list[Card]:
    """Return cards whose due_date ≤ today, ordered by due_date ASC.

    ``source`` filters on ``topic`` — the PDF filename a bank-derived card was
    stamped with at materialization time (see :func:`materialize_bank_question`).
    """
    conn = get_conn()
    today = date.today().isoformat()
    clauses = ["due_date <= ?"]
    params: list[Any] = [today]
    if source:
        clauses.append("topic = ?")
        params.append(source)
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


def get_all_cards(topic: str | None = None) -> list[Card]:
    """Return all cards, optionally filtered by *topic*."""
    conn = get_conn()
    if topic:
        rows = conn.execute(_adapt("SELECT * FROM cards WHERE topic = ?"), (topic,)).fetchall()
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


def add_bank_questions(items: list[BankQuestion]) -> int:
    """Batch-insert bank questions; duplicates (same source+question) are skipped.

    Returns the number of rows actually inserted.
    """
    conn = get_conn()
    sql = _adapt(
        "INSERT INTO question_bank "
        "(question, answer, difficulty, qtype, options, correct, source, pages, chunk_ids, embedding) "
        "VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING"
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
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if source:
        clauses.append("source = ?")
        params.append(source)
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
) -> list[BankQuestion]:
    """Return bank questions, optionally filtered.

    Newest first by default (the bank tab); ``order_asc`` serves them in
    document order instead — used by revision to work through a PDF front-to-back.
    """
    conn = get_conn()
    where, params = _bank_where(source, difficulty, search, qtype, result, deck)
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
) -> int:
    """Return how many bank questions match the filters."""
    conn = get_conn()
    where, params = _bank_where(source, difficulty, search, qtype, result, deck)
    row = conn.execute(_adapt(f"SELECT COUNT(*) AS n FROM question_bank{where}"), params).fetchone()
    assert row is not None
    return int(row["n"])


def bank_sources() -> list[str]:
    """Return sorted distinct source filenames present in the bank."""
    conn = get_conn()
    rows = conn.execute("SELECT DISTINCT source FROM question_bank ORDER BY source").fetchall()
    return [r["source"] for r in rows]


def get_bank_question(bank_id: int) -> BankQuestion | None:
    """Return one bank question by id, or None."""
    conn = get_conn()
    row = conn.execute(_adapt("SELECT * FROM question_bank WHERE id = ?"), (bank_id,)).fetchone()
    return _row_to_bank_question(row) if row else None


def link_bank_card(bank_id: int, card_id: int) -> None:
    """Record that a bank question was copied into the SRS deck as *card_id*."""
    conn = get_conn()
    conn.execute(
        _adapt("UPDATE question_bank SET card_id = ? WHERE id = ?"), (card_id, bank_id)
    )
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


# ── Stats (used by rag_bachelor.study.stats) ────────────────────────────────


def get_topic_stats_rows() -> list[dict[str, Any]]:
    """Return one raw row per topic with the aggregates :mod:`study.stats` needs."""
    conn = get_conn()
    today = date.today().isoformat()
    rows = conn.execute(
        _adapt(
            """
            SELECT
                topic,
                COUNT(*)                                          AS total,
                SUM(CASE WHEN due_date <= ? THEN 1 ELSE 0 END)     AS due,
                AVG(ease_factor)                                   AS avg_ease,
                AVG(interval)                                      AS avg_interval
            FROM cards
            GROUP BY topic
            ORDER BY topic
            """
        ),
        (today,),
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

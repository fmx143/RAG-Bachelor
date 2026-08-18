"""Persistence layer for flashcards and review history.

Connects to a standalone PostgreSQL server (isolated data-tier container, e.g.
on a NAS) when ``POSTGRES_HOST`` is set, otherwise falls back to a local SQLite
file for frictionless local dev. Both backends expose the exact same function
surface below, so callers never need to know which one is active.
"""

from __future__ import annotations

import sqlite3
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
"""


def _use_postgres() -> bool:
    return bool(settings.postgres_host)


def _adapt(sql: str) -> str:
    """Translate sqlite3's ``?`` placeholders to psycopg's ``%s`` when needed."""
    return sql.replace("?", "%s") if _use_postgres() else sql


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
            _conn = sqlite3.connect(str(settings.db_path), check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL;")
            _conn.executescript(_SQLITE_SCHEMA)
            _conn.commit()
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


def add_card(question: str, answer: str, topic: str, difficulty: str) -> int:
    """Insert a new card and return its id."""
    conn = get_conn()
    sql = "INSERT INTO cards (question, answer, topic, difficulty, due_date) VALUES (?,?,?,?,?)"
    params = (question, answer, topic, difficulty, date.today().isoformat())
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


def get_due_cards(limit: int = 20) -> list[Card]:
    """Return cards whose due_date ≤ today, ordered by due_date ASC."""
    conn = get_conn()
    today = date.today().isoformat()
    rows = conn.execute(
        _adapt("SELECT * FROM cards WHERE due_date <= ? ORDER BY due_date LIMIT ?"),
        (today, limit),
    ).fetchall()
    return [_row_to_card(r) for r in rows]


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


def _row_to_card(row: sqlite3.Row | dict[str, Any]) -> Card:
    return Card(
        id=row["id"],
        question=row["question"],
        answer=row["answer"],
        topic=row["topic"],
        difficulty=row["difficulty"],
        interval=row["interval"],
        repetitions=row["repetitions"],
        ease_factor=row["ease_factor"],
        due_date=date.fromisoformat(row["due_date"]),
    )

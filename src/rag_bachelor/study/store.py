"""SQLite persistence layer for flashcards and review history."""

from __future__ import annotations

import sqlite3
from datetime import date

from rag_bachelor.config import settings
from rag_bachelor.study.srs import Card

_conn: sqlite3.Connection | None = None

_SCHEMA = """
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
"""


def get_conn() -> sqlite3.Connection:
    """Return (and lazily open) the singleton SQLite connection."""
    global _conn
    if _conn is None:
        settings.db_path.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(settings.db_path), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL;")
        _conn.executescript(_SCHEMA)
        _conn.commit()
    return _conn


# ── Card CRUD ─────────────────────────────────────────────────────────────────


def add_card(question: str, answer: str, topic: str, difficulty: str) -> int:
    """Insert a new card and return its id."""
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO cards (question, answer, topic, difficulty, due_date) VALUES (?,?,?,?,?)",
        (question, answer, topic, difficulty, date.today().isoformat()),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


def get_due_cards(limit: int = 20) -> list[Card]:
    """Return cards whose due_date ≤ today, ordered by due_date ASC."""
    conn = get_conn()
    today = date.today().isoformat()
    rows = conn.execute(
        "SELECT * FROM cards WHERE due_date <= ? ORDER BY due_date LIMIT ?",
        (today, limit),
    ).fetchall()
    return [_row_to_card(r) for r in rows]


def get_all_cards(topic: str | None = None) -> list[Card]:
    """Return all cards, optionally filtered by *topic*."""
    conn = get_conn()
    if topic:
        rows = conn.execute("SELECT * FROM cards WHERE topic = ?", (topic,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM cards ORDER BY topic, id").fetchall()
    return [_row_to_card(r) for r in rows]


def save_review(card: Card, grade: int) -> None:
    """Persist a review result and update the card's SM-2 state in one transaction."""
    conn = get_conn()
    conn.execute("INSERT INTO reviews (card_id, grade) VALUES (?,?)", (card.id, grade))
    conn.execute(
        """UPDATE cards
           SET interval = ?, repetitions = ?, ease_factor = ?, due_date = ?
           WHERE id = ?""",
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
    conn.execute("DELETE FROM cards WHERE id = ?", (card_id,))
    conn.commit()


# ── Internal helpers ──────────────────────────────────────────────────────────


def _row_to_card(row: sqlite3.Row) -> Card:
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

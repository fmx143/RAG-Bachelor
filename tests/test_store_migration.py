"""study/store.py: _migrate() backfills `cards` columns on a pre-port schema.

The NAS's Postgres `cards` table (and any pre-existing local SQLite file)
predates `qtype`/`options`/`correct` — CREATE TABLE IF NOT EXISTS never
touches an existing table, so only an explicit ALTER TABLE brings it current.
This exercises the SQLite path only (see store.py's ponytail: note — the
Postgres path has no test container here).
"""

from __future__ import annotations

import sqlite3

from rag_bachelor.config import settings
from rag_bachelor.study import store

_OLD_SCHEMA = """
CREATE TABLE cards (
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
"""


def test_migrate_backfills_columns_and_is_idempotent() -> None:
    raw = sqlite3.connect(str(settings.db_path))
    raw.execute(_OLD_SCHEMA)
    raw.execute(
        "INSERT INTO cards (question, answer, topic, difficulty, due_date) "
        "VALUES ('Q ?', 'R.', 'Sujet', 'facile', '2026-01-01')"
    )
    raw.commit()
    raw.close()

    conn = store.get_conn()
    row = conn.execute("SELECT qtype, options, correct FROM cards").fetchone()
    assert row is not None
    assert row["qtype"] == "free"
    assert row["options"] is None
    assert row["correct"] is None

    # A second migrate() (e.g. the next process start) must not error on
    # already-present columns.
    store._migrate(conn)

    card_id = store.add_card("Q2 ?", "R2.", topic="Sujet", difficulty="moyen", qtype="mcq_single", options=["A", "B"], correct=[0])
    card = next(c for c in store.get_all_cards() if c.id == card_id)
    assert card.qtype == "mcq_single"
    assert card.options == ["A", "B"]
    assert card.correct == [0]


def test_migrate_retags_single_answer_mcq_multi_rows() -> None:
    """_migrate() must relabel pre-fix `mcq_multi` rows with only 1 correct answer."""
    conn = store.get_conn()
    conn.execute(
        "INSERT INTO question_bank (question, answer, difficulty, qtype, options, correct, "
        "source, pages, chunk_ids) VALUES "
        "('Q single ?', 'R.', 'facile', 'mcq_multi', '[\"A\",\"B\"]', '[0]', 's', '[1]', '[\"c\"]'),"
        "('Q multi ?', 'R.', 'facile', 'mcq_multi', '[\"A\",\"B\",\"C\"]', '[0, 2]', 's', '[1]', '[\"c\"]')"
    )
    conn.commit()

    store._migrate(conn)

    rows = {
        row["question"]: row["qtype"]
        for row in conn.execute("SELECT question, qtype FROM question_bank")
    }
    assert rows["Q single ?"] == "mcq_single"
    assert rows["Q multi ?"] == "mcq_multi"

    # Idempotent: a second migrate() must not error and must not touch the
    # already-correct multi row.
    store._migrate(conn)
    row = conn.execute("SELECT qtype FROM question_bank WHERE question = 'Q multi ?'").fetchone()
    assert row["qtype"] == "mcq_multi"

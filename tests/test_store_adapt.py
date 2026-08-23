"""study/store.py: _adapt() translates sqlite3 `?` placeholders to psycopg `%s`."""

from __future__ import annotations

import pytest

from rag_bachelor.config import settings
from rag_bachelor.study import store


def test_adapt_is_noop_for_sqlite() -> None:
    sql = "DELETE FROM question_bank WHERE id IN (?,?,?)"
    assert store._adapt(sql) == sql


def test_adapt_translates_placeholders_for_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "postgres_host", "db.internal")
    sql = "DELETE FROM question_bank WHERE id IN (?,?,?)"
    assert store._adapt(sql) == "DELETE FROM question_bank WHERE id IN (%s,%s,%s)"

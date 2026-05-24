"""Engine-level query helpers (SQLAlchemy Core over asyncpg, :name params).

fetch_all/fetch_val run on engine.connect() (read); execute runs on
engine.begin() (auto-commit) and returns the affected rowcount.
"""

from __future__ import annotations

from app.db import engine as db_engine


class _Result:
    def __init__(self, rows=None, scalar=None, rowcount=0):
        self._rows = rows or []
        self._scalar = scalar
        self.rowcount = rowcount

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def scalar(self):
        return self._scalar


class _Conn:
    def __init__(self, result: _Result):
        self._result = result

    async def execute(self, _stmt, _params=None):
        return self._result

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _Engine:
    def __init__(self, result: _Result):
        self._result = result

    def connect(self):
        return _Conn(self._result)

    def begin(self):
        return _Conn(self._result)


async def test_fetch_all_returns_plain_dicts(monkeypatch):
    monkeypatch.setattr(
        db_engine, "get_engine", lambda: _Engine(_Result(rows=[{"a": 1}, {"a": 2}]))
    )
    assert await db_engine.fetch_all("SELECT a FROM t") == [{"a": 1}, {"a": 2}]


async def test_fetch_val_returns_scalar(monkeypatch):
    monkeypatch.setattr(db_engine, "get_engine", lambda: _Engine(_Result(scalar=42)))
    assert await db_engine.fetch_val("SELECT count(*) FROM t") == 42


async def test_execute_returns_rowcount(monkeypatch):
    monkeypatch.setattr(db_engine, "get_engine", lambda: _Engine(_Result(rowcount=3)))
    n = await db_engine.execute("UPDATE t SET x = :x WHERE id = :id", {"x": 1, "id": 9})
    assert n == 3


async def test_execute_returning_val_returns_scalar(monkeypatch):
    # INSERT ... RETURNING id must run on begin() (commit) and surface the
    # scalar — fetch_val would run on connect() and roll the write back.
    monkeypatch.setattr(
        db_engine, "get_engine", lambda: _Engine(_Result(scalar="new-id"))
    )
    got = await db_engine.execute_returning_val(
        "INSERT INTO t (x) VALUES (:x) RETURNING id", {"x": 1}
    )
    assert got == "new-id"

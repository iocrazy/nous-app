"""AsyncpgRepository on SQLAlchemy engine — the $N→:name shim + delete_by_id.

The shim is the risk surface (all repos route through it). It must:
  - convert asyncpg $N → SQLAlchemy :pN, mapping args by position
  - space out param-adjacent casts ($1::bigint[] → :p1 ::bigint[]) so
    SQLAlchemy text() tokenizes the param correctly (it mis-parses :p1::x)
  - leave literal casts ('x'::text) untouched
"""

from __future__ import annotations

from sqlalchemy import text

from app.db.repository_base import AsyncpgRepository, _to_named


def test_to_named_basic():
    sql, params = _to_named("SELECT * FROM x WHERE id = $1", ("v",))
    assert sql == "SELECT * FROM x WHERE id = :p1"
    assert params == {"p1": "v"}
    assert "p1" in text(sql)._bindparams


def test_to_named_multiple_params():
    sql, params = _to_named("WHERE a = $1 AND b = $2", (1, 2))
    assert sql == "WHERE a = :p1 AND b = :p2"
    assert params == {"p1": 1, "p2": 2}
    assert sorted(text(sql)._bindparams) == ["p1", "p2"]


def test_to_named_array_cast_gets_spaced():
    # The critical case: $1::bigint[] must become :p1 ::bigint[] so SQLAlchemy
    # parses :p1 (it mis-tokenizes :p1::bigint[] as a param named 'p').
    sql, params = _to_named("WHERE id = ANY($1::bigint[])", ([1, 2],))
    assert sql == "WHERE id = ANY(:p1 ::bigint[])"
    assert params == {"p1": [1, 2]}
    assert "p1" in text(sql)._bindparams


def test_to_named_literal_cast_untouched():
    sql, _ = _to_named("SELECT 'x'::text, id FROM t WHERE id = $1", ("v",))
    assert "'x'::text" in sql  # literal cast not spaced (no param before ::)
    assert ":p1" in sql
    assert "p1" in text(sql)._bindparams


def test_to_named_no_args_passthrough():
    sql, params = _to_named("SELECT 1", ())
    assert sql == "SELECT 1"
    assert params == {}


async def test_delete_by_id_truthiness_from_rowcount(monkeypatch):
    """execute() now returns an int rowcount (SQLAlchemy), not the asyncpg
    status tag — delete_by_id must read truthiness from it."""
    from app.db import engine as db_engine

    class _Repo(AsyncpgRepository):
        TABLE = "things"

    async def _exec_one(sql, params):
        return 1

    monkeypatch.setattr(db_engine, "execute", _exec_one)
    assert await _Repo().delete_by_id("5") is True

    async def _exec_zero(sql, params):
        return 0

    monkeypatch.setattr(db_engine, "execute", _exec_zero)
    assert await _Repo().delete_by_id("5") is False

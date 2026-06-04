"""Repository base — SQLAlchemy Core over asyncpg (Issue #199).

Gives repository files a familiar, ergonomic API so migrating from
``await client.table("foo").select("*").eq("id", x).execute()`` chaining
to raw SQL is mechanical and reviewable.

Pattern (per repo) — note the SQL still uses asyncpg-style ``$1`` params;
the base converts them to SQLAlchemy ``:p1`` bind params transparently:

    class FooRepository(AsyncpgRepository):
        TABLE = "foos"

        async def get_by_id(self, id: str) -> dict | None:
            return await self.fetch_one("SELECT * FROM foos WHERE id = $1", id)

The base class handles:
  - The SQLAlchemy async engine (one shared pool; no httpx → no CLOSE_WAIT
    leak — see the 2026-05-22 incident / docs/db-layer-migration-199.md)
  - ``$N`` → ``:pN`` bind-param conversion (incl. ``$1::bigint[]`` casts)
  - Row → dict conversion
  - Common shapes: insert / update_by_id / fetch_one / fetch_all
  - Transactions via ``async with self.transaction() as conn:`` (conn is a
    SQLAlchemy AsyncConnection — use ``conn.execute(text(...))``)

Intentionally NOT included (use raw SQL directly):
  - Query builder DSL, soft-delete/pagination helpers, ORM relationships.
"""

from __future__ import annotations

import re
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional

from app.db import engine as db_engine

# $1, $2, ... (asyncpg positional) → :p1, :p2, ... (SQLAlchemy named).
_DOLLAR_PARAM = re.compile(r"\$(\d+)")
# A cast immediately after a bind param (":p1::bigint[]") makes SQLAlchemy's
# text() mis-tokenize the param name; a space (":p1 ::bigint[]") fixes it.
# Only touch param-adjacent "::", never "::" inside literals.
_PARAM_CAST = re.compile(r"(:p\d+)::")


def _to_named(sql: str, args: tuple) -> tuple[str, dict]:
    """Convert asyncpg ``$N`` positional SQL + args to SQLAlchemy named SQL
    + a params dict. ``$1`` → ``:p1`` bound to ``args[0]``."""
    if not args:
        return sql, {}
    named = _DOLLAR_PARAM.sub(r":p\1", sql)
    named = _PARAM_CAST.sub(r"\1 ::", named)
    params = {f"p{i + 1}": a for i, a in enumerate(args)}
    return named, params


class AsyncpgRepository:
    """Thin SQLAlchemy-over-asyncpg wrapper. Subclass + set ``TABLE`` to use
    the convenience methods, or call ``fetch_one`` / ``fetch_all`` /
    ``execute`` with raw ``$N`` SQL directly."""

    TABLE: str = ""

    # ── Type coercion at the boundary ───────────────────────────────

    @staticmethod
    def _bigint(v: Any) -> Any:
        """Coerce digit-only strings to int for BIGINT bindings. Snowflake
        IDs travel as strings through FastAPI path params; the PG int8 codec
        is strict. UUID/text columns are unaffected (str passes through)."""
        if isinstance(v, str):
            try:
                return int(v)
            except ValueError:
                return v
        return v

    @staticmethod
    def _bigint_list(vs: Any) -> list:
        """Coerce a list of str ids to ints. For ``= ANY($1)`` bindings."""
        return [AsyncpgRepository._bigint(v) for v in (vs or [])]

    # ── Low-level access ────────────────────────────────────────────

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[Any]:
        """Borrow a SQLAlchemy AsyncConnection for a multi-statement
        sequence (no surrounding transaction). Use ``transaction()`` for
        atomicity. The yielded conn uses ``await conn.execute(text(...))``."""
        eng = db_engine.get_engine()
        async with eng.connect() as conn:
            yield conn

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[Any]:
        """Wrap multiple statements in a single PG transaction (auto-commit
        on clean exit, auto-rollback on raise). Yields a SQLAlchemy
        AsyncConnection — use ``await conn.execute(text(...))``."""
        eng = db_engine.get_engine()
        async with eng.begin() as conn:
            yield conn

    async def fetch_one(self, sql: str, *args: Any) -> Optional[dict]:
        """SELECT one row → plain dict, or None when no row.

        READ ONLY. Runs on ``connect()`` (no transaction). Do NOT pass an
        ``INSERT/UPDATE/DELETE ... RETURNING`` here — it executes but SILENTLY
        ROLLS BACK on connection close (the #498 class). For writes that need a
        row back use ``db_engine.execute_returning_one``."""
        named, params = _to_named(sql, args)
        return await db_engine.fetch_one(named, params)

    async def fetch_all(self, sql: str, *args: Any) -> list[dict]:
        """SELECT many rows → list of plain dicts."""
        named, params = _to_named(sql, args)
        return await db_engine.fetch_all(named, params)

    async def fetch_value(self, sql: str, *args: Any) -> Any:
        """SELECT one column from one row (COUNT / EXISTS / scalar).

        READ ONLY. Runs on ``connect()`` (no transaction). Do NOT pass an
        ``INSERT/UPDATE/DELETE ... RETURNING`` here — it SILENTLY ROLLS BACK
        (the #498 class). For a writing RETURNING scalar use
        ``db_engine.execute_returning_val``."""
        named, params = _to_named(sql, args)
        return await db_engine.fetch_val(named, params)

    async def execute(self, sql: str, *args: Any) -> int:
        """INSERT / UPDATE / DELETE → affected row count (auto-committed).

        For INSERT/UPDATE/DELETE ... RETURNING use
        ``db_engine.execute_returning_one`` / ``execute_returning_val``
        (both committing). NEVER ``fetch_one`` / ``fetch_val`` for a write —
        those run on ``connect()`` (no transaction) and SILENTLY ROLL BACK the
        write (the #498 silent-rollback class).

        NOTE: returns an int rowcount (SQLAlchemy), NOT the asyncpg status
        tag string the previous raw-asyncpg base returned."""
        named, params = _to_named(sql, args)
        return await db_engine.execute(named, params)

    @staticmethod
    async def _conn_fetch_all(conn: Any, sql: str, *args: Any) -> list[dict]:
        """Run a ``$N`` query on an EXISTING connection (inside an
        ``acquire()`` / ``transaction()`` block) → list of plain dicts.
        Handles ``$N`` → ``:pN`` conversion. Works for SELECT and
        ``UPDATE/DELETE ... RETURNING`` (both return rows)."""
        from sqlalchemy import text

        named, params = _to_named(sql, args)
        result = await conn.execute(text(named), params)
        return [dict(r) for r in result.mappings().all()]

    # ── Convenience helpers (require self.TABLE set) ────────────────

    async def insert(self, **fields: Any) -> dict:
        """INSERT a row and return the inserted record."""
        if not self.TABLE:
            raise RuntimeError(
                f"{type(self).__name__}.TABLE must be set to use insert()"
            )
        if not fields:
            raise ValueError("insert() requires at least one field")
        cols = list(fields.keys())
        placeholders = ", ".join(f"${i + 1}" for i in range(len(cols)))
        col_list = ", ".join(f'"{c}"' for c in cols)
        sql = (
            f'INSERT INTO "{self.TABLE}" ({col_list}) '
            f"VALUES ({placeholders}) RETURNING *"
        )
        # COMMITTING path: execute_returning_one runs on eng.begin() (auto-commit).
        # NEVER self.fetch_one here — fetch_one runs on eng.connect() (no txn) and
        # SILENTLY ROLLS BACK the write (the #498 silent-rollback P0 class).
        named, params = _to_named(sql, tuple(fields.values()))
        row = await db_engine.execute_returning_one(named, params)
        if row is None:
            raise RuntimeError(f"INSERT into {self.TABLE} returned no row")
        return row

    async def update_by_id(
        self, row_id: Any, *, id_column: str = "id", **fields: Any
    ) -> Optional[dict]:
        """UPDATE one row and return its post-update record, or None when no
        row matched."""
        if not self.TABLE:
            raise RuntimeError(
                f"{type(self).__name__}.TABLE must be set to use update_by_id()"
            )
        if not fields:
            raise ValueError("update_by_id() requires at least one field")
        set_pairs = ", ".join(f'"{c}" = ${i + 1}' for i, c in enumerate(fields.keys()))
        id_placeholder = f"${len(fields) + 1}"
        sql = (
            f'UPDATE "{self.TABLE}" SET {set_pairs} '
            f'WHERE "{id_column}" = {id_placeholder} RETURNING *'
        )
        # COMMITTING path (eng.begin auto-commit). NEVER self.fetch_one — it runs
        # on eng.connect() (no txn) and SILENTLY ROLLS BACK (the #498 class).
        named, params = _to_named(sql, (*fields.values(), row_id))
        return await db_engine.execute_returning_one(named, params)

    async def get_by_id(self, row_id: Any, *, id_column: str = "id") -> Optional[dict]:
        """SELECT one row by primary key. Returns None when not found."""
        if not self.TABLE:
            raise RuntimeError(
                f"{type(self).__name__}.TABLE must be set to use get_by_id()"
            )
        sql = f'SELECT * FROM "{self.TABLE}" WHERE "{id_column}" = $1'
        return await self.fetch_one(sql, row_id)

    async def delete_by_id(self, row_id: Any, *, id_column: str = "id") -> bool:
        """DELETE one row by primary key. Returns True iff a row was deleted."""
        if not self.TABLE:
            raise RuntimeError(
                f"{type(self).__name__}.TABLE must be set to use delete_by_id()"
            )
        sql = f'DELETE FROM "{self.TABLE}" WHERE "{id_column}" = $1'
        rowcount = await self.execute(sql, row_id)
        return rowcount > 0


__all__ = ["AsyncpgRepository"]

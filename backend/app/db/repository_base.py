"""asyncpg-backed repository base — supabase-py replacement contract.

Goal: give individual repository files a familiar, ergonomic API so
the migration from ``await client.table("foo").select("*").eq("id", x).execute()``
style chaining to raw SQL is mechanical and reviewable.

Pattern (per repo):

    class FooRepository(AsyncpgRepository):
        TABLE = "foos"

        async def get_by_id(self, id: str) -> dict | None:
            return await self.fetch_one(
                "SELECT * FROM foos WHERE id = $1", id
            )

The base class handles:
  - Pool acquisition (one connection per call, returned to pool)
  - Row → dict conversion (asyncpg.Record → plain dict)
  - Common shapes: insert / update_by_id / fetch_one / fetch_all
  - Transactions via ``async with self.transaction():``

Intentionally NOT included (use raw SQL directly):
  - Query builder DSL (would re-create supabase-py's chaining trap)
  - Soft delete / pagination helpers (vary too much by table)
  - ORM-style relationship loading (yagni for our flat schemas)
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional

import asyncpg
from loguru import logger

from app.db.pg_pool import get_pool


class AsyncpgRepository:
    """Thin asyncpg wrapper. Subclass + set ``TABLE`` to use the
    convenience methods, or call ``fetch_one`` / ``fetch_all`` /
    ``execute`` with raw SQL directly."""

    TABLE: str = ""

    # ── Type coercion at the boundary ───────────────────────────────

    @staticmethod
    def _bigint(v: Any) -> Any:
        """Coerce digit-only strings to int for BIGINT bindings.

        Snowflake IDs travel as strings through FastAPI path params
        and the supabase-py-shaped legacy callers. asyncpg's int8
        codec is strict and raises ``DataError: 'str' object cannot
        be interpreted`` for str input. Wrap any user-supplied
        BIGINT id in this helper before binding.

        Verified empirically: ``WHERE bigint_col = $1`` with
        str input fails; with int input works. UUID and text
        columns are unaffected (their codecs accept str)."""
        if isinstance(v, str):
            try:
                return int(v)
            except ValueError:
                return v
        return v

    @staticmethod
    def _bigint_list(vs: Any) -> list:
        """Coerce a list of str ids to ints. For ``WHERE col = ANY($1)``
        bindings against bigint arrays."""
        return [AsyncpgRepository._bigint(v) for v in (vs or [])]

    # ── Low-level access ────────────────────────────────────────────

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[asyncpg.Connection]:
        """Borrow a connection from the pool for a multi-statement
        sequence. Always returned to the pool when the block exits.

        Use ``transaction()`` instead when you need atomicity."""
        pool = await get_pool()
        async with pool.acquire() as conn:
            yield conn

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[asyncpg.Connection]:
        """Wrap multiple statements in a single PG transaction.

        async with repo.transaction() as conn:
            await conn.execute("INSERT INTO ...")
            await conn.execute("UPDATE ...")
            # auto-commit on clean exit; auto-rollback on raise
        """
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                yield conn

    async def fetch_one(self, sql: str, *args: Any) -> Optional[dict]:
        """SELECT one row → plain dict, or None when no row."""
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(sql, *args)
        return dict(row) if row else None

    async def fetch_all(self, sql: str, *args: Any) -> list[dict]:
        """SELECT many rows → list of plain dicts."""
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *args)
        return [dict(r) for r in rows]

    async def fetch_value(self, sql: str, *args: Any) -> Any:
        """SELECT one column from one row. Useful for COUNT / EXISTS /
        scalar lookups."""
        pool = await get_pool()
        async with pool.acquire() as conn:
            return await conn.fetchval(sql, *args)

    async def execute(self, sql: str, *args: Any) -> str:
        """INSERT / UPDATE / DELETE returning the asyncpg status tag
        ("INSERT 0 1", "UPDATE 3", etc.). For INSERT ... RETURNING use
        fetch_one with a RETURNING clause instead."""
        pool = await get_pool()
        async with pool.acquire() as conn:
            return await conn.execute(sql, *args)

    # ── Convenience helpers (require self.TABLE set) ────────────────

    async def insert(self, **fields: Any) -> dict:
        """INSERT a row and return the inserted record. Mirrors what
        supabase-py's ``client.table(t).insert(row).execute().data[0]``
        used to give callers."""
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
        row = await self.fetch_one(sql, *fields.values())
        if row is None:
            raise RuntimeError(f"INSERT into {self.TABLE} returned no row")
        return row

    async def update_by_id(
        self, row_id: Any, *, id_column: str = "id", **fields: Any
    ) -> Optional[dict]:
        """UPDATE one row and return its post-update record. Returns
        None when no row matched (mirrors get_by_id semantics).

        ``id_column`` is parameterized for tables with non-``id`` PKs
        (e.g. ``parsed_media`` keys on ``platform_id``)."""
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
        return await self.fetch_one(sql, *fields.values(), row_id)

    async def get_by_id(self, row_id: Any, *, id_column: str = "id") -> Optional[dict]:
        """SELECT one row by primary key. Returns None when not found."""
        if not self.TABLE:
            raise RuntimeError(
                f"{type(self).__name__}.TABLE must be set to use get_by_id()"
            )
        sql = f'SELECT * FROM "{self.TABLE}" WHERE "{id_column}" = $1'
        return await self.fetch_one(sql, row_id)

    async def delete_by_id(self, row_id: Any, *, id_column: str = "id") -> bool:
        """DELETE one row by primary key. Returns True iff a row was
        deleted (matches PostgREST ``.delete().eq()`` truthiness)."""
        if not self.TABLE:
            raise RuntimeError(
                f"{type(self).__name__}.TABLE must be set to use delete_by_id()"
            )
        sql = f'DELETE FROM "{self.TABLE}" WHERE "{id_column}" = $1'
        status = await self.execute(sql, row_id)
        # asyncpg returns "DELETE N" — read the count off the tag
        try:
            return int(status.split()[1]) > 0
        except (IndexError, ValueError):
            return False


__all__ = ["AsyncpgRepository"]

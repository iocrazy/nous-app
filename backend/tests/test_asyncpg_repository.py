"""engine.is_configured() gate + AsyncpgRepository contract tests.

These don't require a live Supavisor. The point is:
  - engine.is_configured() reflects env state (the repo factories read it
    to choose the asyncpg variant vs the supabase-py legacy path)
  - AsyncpgRepository SQL builders match the expected shape

(Formerly test_pg_pool.py — the raw-asyncpg pg_pool module was retired in
favour of the SQLAlchemy engine, so the pg_pool lifecycle tests are gone.)
Live integration tests against a real Supavisor live in
tests/integration/test_asyncpg_repos.py.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

# ─── engine.is_configured gate ─────────────────────────────────────────


def test_is_configured_false_when_url_blank():
    """Empty SUPAVISOR_DATABASE_URL = asyncpg disabled. Repo factories
    read is_configured() to fall back to supabase-py."""
    from app.db import engine

    with patch.object(engine.settings, "SUPAVISOR_DATABASE_URL", ""):
        assert engine.is_configured() is False


def test_is_configured_true_when_url_set():
    """Any non-empty DSN flips the asyncpg path on."""
    from app.db import engine

    with patch.object(
        engine.settings,
        "SUPAVISOR_DATABASE_URL",
        "postgresql://u:p@localhost:6543/postgres",
    ):
        assert engine.is_configured() is True


# ─── AsyncpgRepository contract ────────────────────────────────────────


async def test_insert_requires_table():
    """Without TABLE set, insert() must raise — silently inserting
    into '' would mask a config bug. fail-fast is right here."""
    from app.db.repository_base import AsyncpgRepository

    repo = AsyncpgRepository()
    with pytest.raises(RuntimeError, match="TABLE must be set"):
        await repo.insert(name="x")


async def test_insert_requires_at_least_one_field():
    """insert() with no fields would generate invalid SQL
    (INSERT INTO foo () VALUES ()). Catch it before SQL roundtrip."""
    from app.db.repository_base import AsyncpgRepository

    class FooRepo(AsyncpgRepository):
        TABLE = "foos"

    repo = FooRepo()
    with pytest.raises(ValueError, match="at least one field"):
        await repo.insert()


async def test_insert_builds_parameterized_sql():
    """insert() must build $1, $2, ... placeholders (no string
    interpolation of values). This is the SQL injection guard —
    pin the pattern so a future "let me concat the values" diff
    breaks loudly."""
    from app.db.repository_base import AsyncpgRepository

    class FooRepo(AsyncpgRepository):
        TABLE = "foos"

    captured_sql = []
    captured_args = []

    async def fake_fetch_one(self, sql, *args):
        captured_sql.append(sql)
        captured_args.append(args)
        return {"id": 1, "name": "x", "kind": "y"}

    with patch.object(AsyncpgRepository, "fetch_one", fake_fetch_one):
        repo = FooRepo()
        result = await repo.insert(name="x", kind="y")

    assert result == {"id": 1, "name": "x", "kind": "y"}
    assert "$1" in captured_sql[0] and "$2" in captured_sql[0]
    assert "INSERT INTO" in captured_sql[0]
    assert "RETURNING *" in captured_sql[0]
    assert captured_args[0] == ("x", "y")


async def test_update_by_id_uses_id_column_param():
    """update_by_id supports non-'id' PKs (parsed_media keys on
    platform_id, etc). Pin the SQL shape so the parameterization
    stays correct."""
    from app.db.repository_base import AsyncpgRepository

    class MediaRepo(AsyncpgRepository):
        TABLE = "parsed_media"

    captured_sql = []
    captured_args = []

    async def fake_fetch_one(self, sql, *args):
        captured_sql.append(sql)
        captured_args.append(args)
        return {"platform_id": "abc", "title": "new"}

    with patch.object(AsyncpgRepository, "fetch_one", fake_fetch_one):
        repo = MediaRepo()
        result = await repo.update_by_id("abc", id_column="platform_id", title="new")

    assert result["title"] == "new"
    assert '"platform_id" = $2' in captured_sql[0]
    assert '"title" = $1' in captured_sql[0]
    # Args order: SET values first, then ID — pin the convention
    assert captured_args[0] == ("new", "abc")


async def test_delete_by_id_returns_bool_from_rowcount():
    """After the SQLAlchemy-engine migration, execute() returns the int
    affected-row count (not the asyncpg 'DELETE N' status tag). delete_by_id
    must return truthy iff a row was deleted so callers can
    `if await repo.delete_by_id(x):` cleanly."""
    from app.db.repository_base import AsyncpgRepository

    class FooRepo(AsyncpgRepository):
        TABLE = "foos"

    async def fake_execute_one(self, sql, *args):
        return 1

    async def fake_execute_zero(self, sql, *args):
        return 0

    repo = FooRepo()
    with patch.object(AsyncpgRepository, "execute", fake_execute_one):
        assert await repo.delete_by_id(1) is True
    with patch.object(AsyncpgRepository, "execute", fake_execute_zero):
        assert await repo.delete_by_id(1) is False

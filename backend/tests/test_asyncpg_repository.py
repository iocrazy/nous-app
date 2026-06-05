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
    """insert() must build parameterized placeholders (no string
    interpolation of values). This is the SQL injection guard —
    pin the pattern so a future "let me concat the values" diff
    breaks loudly. It must also route through the COMMITTING
    ``execute_returning_one`` (eng.begin), NOT the non-committing
    ``fetch_one`` (eng.connect → silent rollback, the #498 class)."""
    from app.db import engine as db_engine
    from app.db.repository_base import AsyncpgRepository

    class FooRepo(AsyncpgRepository):
        TABLE = "foos"

    captured_sql = []
    captured_params = []

    async def fake_execute_returning_one(sql, params=None):
        captured_sql.append(sql)
        captured_params.append(params)
        return {"id": 1, "name": "x", "kind": "y"}

    # fetch_one (non-committing) must NOT be touched — fail loudly if it is.
    async def forbidden_fetch_one(self, sql, *args):  # pragma: no cover
        raise AssertionError("insert() routed a write through non-committing fetch_one")

    with (
        patch.object(db_engine, "execute_returning_one", fake_execute_returning_one),
        patch.object(AsyncpgRepository, "fetch_one", forbidden_fetch_one),
    ):
        repo = FooRepo()
        result = await repo.insert(name="x", kind="y")

    assert result == {"id": 1, "name": "x", "kind": "y"}
    # $N → :pN conversion happens before the committing helper.
    assert ":p1" in captured_sql[0] and ":p2" in captured_sql[0]
    assert "INSERT INTO" in captured_sql[0]
    assert "RETURNING *" in captured_sql[0]
    # Values bound by name, in order — never interpolated into the SQL text.
    assert captured_params[0] == {"p1": "x", "p2": "y"}


async def test_update_by_id_uses_id_column_param():
    """update_by_id supports non-'id' PKs (parsed_media keys on
    platform_id, etc). Pin the SQL shape so the parameterization
    stays correct, and confirm it routes through the COMMITTING
    ``execute_returning_one`` (not the non-committing ``fetch_one``)."""
    from app.db import engine as db_engine
    from app.db.repository_base import AsyncpgRepository

    class MediaRepo(AsyncpgRepository):
        TABLE = "parsed_media"

    captured_sql = []
    captured_params = []

    async def fake_execute_returning_one(sql, params=None):
        captured_sql.append(sql)
        captured_params.append(params)
        return {"platform_id": "abc", "title": "new"}

    async def forbidden_fetch_one(self, sql, *args):  # pragma: no cover
        raise AssertionError(
            "update_by_id() routed a write through non-committing fetch_one"
        )

    with (
        patch.object(db_engine, "execute_returning_one", fake_execute_returning_one),
        patch.object(AsyncpgRepository, "fetch_one", forbidden_fetch_one),
    ):
        repo = MediaRepo()
        result = await repo.update_by_id("abc", id_column="platform_id", title="new")

    assert result["title"] == "new"
    # $N → :pN: SET value is $1→:p1, the id predicate is $2→:p2.
    assert '"platform_id" = :p2' in captured_sql[0]
    assert '"title" = :p1' in captured_sql[0]
    # Params order: SET values first, then ID — pin the convention.
    assert captured_params[0] == {"p1": "new", "p2": "abc"}


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

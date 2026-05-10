"""Phase 1 foundation tests — pg_pool + AsyncpgRepository contract.

These don't require a live Supavisor. The point is:
  - is_configured() reflects env state
  - get_pool() raises cleanly when not configured
  - AsyncpgRepository SQL builders match the expected shape
  - close_pool() is idempotent

Live integration tests against a real Supavisor are deferred to Phase 2
(pilot repo) where the pattern is exercised end-to-end on one table.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

# ─── pg_pool ───────────────────────────────────────────────────────────


def test_is_configured_false_when_url_blank(monkeypatch):
    """Empty SUPAVISOR_DATABASE_URL = feature disabled. Repository
    code reads is_configured() to decide whether to take the asyncpg
    fast path or fall back to supabase-py during migration."""
    from app.db import pg_pool

    with patch.object(pg_pool.settings, "SUPAVISOR_DATABASE_URL", ""):
        assert pg_pool.is_configured() is False


def test_is_configured_true_when_url_set(monkeypatch):
    """Any non-empty DSN flips the feature on. We trust the env to
    contain a valid PG DSN — pool creation handles invalid input."""
    from app.db import pg_pool

    with patch.object(
        pg_pool.settings,
        "SUPAVISOR_DATABASE_URL",
        "postgresql://u:p@localhost:6543/postgres",
    ):
        assert pg_pool.is_configured() is True


async def test_get_pool_raises_when_not_configured(monkeypatch):
    """Calling get_pool() with an unset DSN must raise RuntimeError so
    the caller (repository) can fall back to supabase-py instead of
    hanging or silently using a wrong default."""
    from app.db import pg_pool

    with patch.object(pg_pool.settings, "SUPAVISOR_DATABASE_URL", ""):
        # Force the singleton back to None so previous tests don't
        # leak a real pool into this one.
        pg_pool._pool = None
        with pytest.raises(RuntimeError, match="not configured"):
            await pg_pool.get_pool()


async def test_close_pool_is_idempotent_when_no_pool():
    """Lifespan shutdown calls close_pool() unconditionally. It must
    not raise when there's nothing to close — typical case in dev /
    test environments where SUPAVISOR_DATABASE_URL is unset."""
    from app.db import pg_pool

    pg_pool._pool = None
    await pg_pool.close_pool()  # must not raise


async def test_health_check_false_when_not_configured(monkeypatch):
    """Liveness probe returns False (not raise) on unconfigured —
    monitoring / startup hooks check the bool, no try/except needed."""
    from app.db import pg_pool

    with patch.object(pg_pool.settings, "SUPAVISOR_DATABASE_URL", ""):
        pg_pool._pool = None
        assert await pg_pool.health_check() is False


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


async def test_delete_by_id_returns_bool_from_status_tag():
    """asyncpg returns 'DELETE N' as the status string. The helper
    must parse N to a bool (truthy iff anything got deleted) so
    callers can `if await repo.delete_by_id(x):` cleanly."""
    from app.db.repository_base import AsyncpgRepository

    class FooRepo(AsyncpgRepository):
        TABLE = "foos"

    async def fake_execute_one(self, sql, *args):
        return "DELETE 1"

    async def fake_execute_zero(self, sql, *args):
        return "DELETE 0"

    repo = FooRepo()
    with patch.object(AsyncpgRepository, "execute", fake_execute_one):
        assert await repo.delete_by_id(1) is True
    with patch.object(AsyncpgRepository, "execute", fake_execute_zero):
        assert await repo.delete_by_id(1) is False

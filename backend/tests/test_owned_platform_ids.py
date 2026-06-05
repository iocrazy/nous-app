"""Unit tests for ``get_owned_platform_ids`` on both repo tracks.

The method answers: of the given vids (parsed_media.platform_id), which
ones has this user already downloaded (a resources row with file_path
set)? Used by the Soda playlist endpoint to mark already-owned tracks so
the user only re-downloads new ones.

The must-have here is the empty-input short-circuit (no DB hit) on both
the ORM (prod) path and the supabase-py (fallback) path. Live JOIN
behaviour is covered by the integration suite (gated on a real DB).
"""

from __future__ import annotations

import asyncio


def test_orm_empty_input_no_db_hit():
    """Empty platform_ids → set() WITHOUT opening a session."""
    from app.repositories import resources_repository_orm as orm_mod
    from app.repositories.resources_repository_orm import ResourcesRepositoryOrm

    repo = ResourcesRepositoryOrm()

    def _boom(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("read_scope must not be opened for empty input")

    # Patch the module-level read_scope the repo uses; the short-circuit
    # must return before any session is opened.
    original = orm_mod.read_scope
    orm_mod.read_scope = _boom  # type: ignore[assignment]
    try:
        result = asyncio.run(repo.get_owned_platform_ids([], "user-1"))
    finally:
        orm_mod.read_scope = original  # type: ignore[assignment]
    assert result == set()


def test_supabase_empty_input_no_db_hit():
    """Empty platform_ids → set() WITHOUT touching the DB."""
    from app.repositories.resources_repository import ResourcesRepository

    repo = ResourcesRepository()

    async def _boom(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("_get_client must not be called for empty input")

    repo._get_client = _boom  # type: ignore[assignment]
    result = asyncio.run(repo.get_owned_platform_ids([], "user-1"))
    assert result == set()


def test_orm_returns_subset_from_rows():
    """ORM path returns exactly the platform_ids the query yields, and binds
    the AMBIENT scope (A3: ``scope_user_id`` via scoped_sql, NOT the legacy
    ``creator_id``) + the vid list as named params on the file_path-gated SQL.

    The method now requires an ambient scope (scoped_sql fail-closes otherwise),
    so the call is wrapped in ``request_scope``."""
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock, MagicMock

    from app.db.scope import Scope, request_scope
    from app.repositories import resources_repository_orm as orm_mod
    from app.repositories.resources_repository_orm import ResourcesRepositoryOrm

    repo = ResourcesRepositoryOrm()
    captured = {}

    class _Mappings:
        def all(self):
            return [{"platform_id": "vid_a"}, {"platform_id": "vid_c"}]

    class _Result:
        def mappings(self):
            return _Mappings()

    fake_session = MagicMock()

    async def _execute(stmt, params):
        captured["sql"] = str(stmt)
        captured["params"] = params
        return _Result()

    fake_session.execute = AsyncMock(side_effect=_execute)

    @asynccontextmanager
    async def _fake_read_scope():
        yield fake_session

    async def _run():
        async with request_scope(Scope(user_id="user-9")):
            return await repo.get_owned_platform_ids(
                ["vid_a", "vid_b", "vid_c"], "user-9"
            )

    original = orm_mod.read_scope
    orm_mod.read_scope = _fake_read_scope  # type: ignore[assignment]
    try:
        result = asyncio.run(_run())
    finally:
        orm_mod.read_scope = original  # type: ignore[assignment]

    assert result == {"vid_a", "vid_c"}
    # A3: the tenant value is now bound under the ambient-scope param, AS-IS.
    assert captured["params"]["scope_user_id"] == "user-9"
    assert "creator_id" not in captured["params"], "legacy :creator_id bind lingered"
    assert captured["params"]["pids"] == ["vid_a", "vid_b", "vid_c"]
    assert "file_path IS NOT NULL" in captured["sql"]


def test_supabase_returns_subset_chunked():
    """supabase-py path collects nested platform_id across chunks."""
    from app.repositories.resources_repository import ResourcesRepository

    repo = ResourcesRepository()

    # Build a chainable fake matching the postgrest builder surface.
    class _Builder:
        def __init__(self, rows):
            self._rows = rows

        def select(self, *a, **k):
            return self

        def eq(self, *a, **k):
            return self

        @property
        def not_(self):
            return self

        def is_(self, *a, **k):
            return self

        def in_(self, *a, **k):
            return self

        async def execute(self):
            class _R:
                data = self._rows

            return _R()

    class _Table:
        def __init__(self, rows):
            self._rows = rows

        def table(self, name):
            return _Builder(self._rows)

    rows = [
        {"media_id": 1, "parsed_media": {"platform_id": "vid_a"}},
        {"media_id": 2, "parsed_media": {"platform_id": "vid_b"}},
    ]

    async def _fake_client():
        return _Table(rows)

    repo._get_client = _fake_client  # type: ignore[assignment]
    result = asyncio.run(
        repo.get_owned_platform_ids(["vid_a", "vid_b", "vid_z"], "user-9")
    )
    assert result == {"vid_a", "vid_b"}

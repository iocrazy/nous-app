"""Unit tests for ``get_owned_platform_ids`` on the ORM-backed repo.

The method answers: of the given vids (parsed_media.platform_id), which
ones has this user already downloaded (a resources row with file_path
set)? Used by the Soda playlist endpoint to mark already-owned tracks so
the user only re-downloads new ones.

Post-collapse there is ONE implementation (the ORM path is now
``ResourcesRepository``). The must-have here is the empty-input
short-circuit (no DB hit) and the scoped-SQL binding (A3: ``scope_user_id``
via scoped_sql, NOT the legacy ``creator_id`` bind). Live JOIN behaviour is
covered by the integration suite (gated on a real DB).
"""

from __future__ import annotations

import asyncio


def test_empty_input_no_db_hit():
    """Empty platform_ids → set() WITHOUT opening a session."""
    from app.repositories import resources_repository as repo_mod
    from app.repositories.resources_repository import ResourcesRepository

    repo = ResourcesRepository()

    def _boom(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("read_scope must not be opened for empty input")

    # Patch the module-level read_scope the repo uses; the short-circuit
    # must return before any session is opened.
    original = repo_mod.read_scope
    repo_mod.read_scope = _boom  # type: ignore[assignment]
    try:
        result = asyncio.run(repo.get_owned_platform_ids([], "user-1"))
    finally:
        repo_mod.read_scope = original  # type: ignore[assignment]
    assert result == set()


def test_returns_subset_from_rows():
    """Returns exactly the platform_ids the query yields, and binds the
    AMBIENT scope (A3: ``scope_user_id`` via scoped_sql, NOT the legacy
    ``creator_id``) + the vid list as named params on the file_path-gated SQL.

    The method requires an ambient scope (scoped_sql fail-closes otherwise),
    so the call is wrapped in ``request_scope``."""
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock, MagicMock

    from app.db.scope import Scope, request_scope
    from app.repositories import resources_repository as repo_mod
    from app.repositories.resources_repository import ResourcesRepository

    repo = ResourcesRepository()
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

    original = repo_mod.read_scope
    repo_mod.read_scope = _fake_read_scope  # type: ignore[assignment]
    try:
        result = asyncio.run(_run())
    finally:
        repo_mod.read_scope = original  # type: ignore[assignment]

    assert result == {"vid_a", "vid_c"}
    # A3: the tenant value is now bound under the ambient-scope param, AS-IS.
    assert captured["params"]["scope_user_id"] == "user-9"
    assert "creator_id" not in captured["params"], "legacy :creator_id bind lingered"
    assert captured["params"]["pids"] == ["vid_a", "vid_b", "vid_c"]
    assert "file_path IS NOT NULL" in captured["sql"]

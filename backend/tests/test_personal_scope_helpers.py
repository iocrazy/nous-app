"""Unit tests for the helpers introduced when unifying personal-scope
handling after Spec 1 PR-C.

  - ``resources_service._resolve_personal_team_id(user_id)`` looks up
    the user's personal team snowflake.
  - ``temp_ttl_settings._resolve_personal_user_id(scope_id)`` accepts
    either a UUID (legacy UI path) or a snowflake (post-PR-C
    sweeper/iterator path) and returns the UUID needed for the
    user_settings lookup.

``_resolve_personal_team_id`` still delegates to ``app.db.engine.fetch_one``.
``_resolve_personal_user_id`` was migrated to the SQLAlchemy ORM (Phase B2
Task 2) — its DB-touching tests patch ``app.db.session.read_scope`` instead.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from app.services.library.resources_service import _resolve_personal_team_id
from app.services.library.temp_ttl_settings import _resolve_personal_user_id

USER_UUID = "8e1584e3-9c29-4a5b-90fe-125b74259f7f"
TEAM_SNOWFLAKE = "310812366953241"


class _FakeScopeSession:
    """Stand-in for the ORM AsyncSession — only ``scalar()`` is used by
    ``_resolve_personal_user_id``'s teams lookup (Phase B2 Task 2 ORM
    rewrite: ``select(cast(Teams.owner_id, String))...`` returns the uid
    directly, not a ``{"uid": ...}`` row mapping)."""

    def __init__(self, scalar_value):
        self._scalar_value = scalar_value

    async def scalar(self, stmt):
        return self._scalar_value


def _fake_read_scope(scalar_value):
    @asynccontextmanager
    async def _read_scope():
        yield _FakeScopeSession(scalar_value)

    return _read_scope


@pytest.mark.asyncio
async def test_resolve_personal_team_id_returns_snowflake():
    with patch(
        "app.db.engine.fetch_one",
        new=AsyncMock(return_value={"id": TEAM_SNOWFLAKE}),
    ):
        result = await _resolve_personal_team_id(USER_UUID)
    assert result == TEAM_SNOWFLAKE


@pytest.mark.asyncio
async def test_resolve_personal_team_id_raises_when_missing():
    with patch("app.db.engine.fetch_one", new=AsyncMock(return_value=None)):
        with pytest.raises(ValueError, match=USER_UUID):
            await _resolve_personal_team_id(USER_UUID)


@pytest.mark.asyncio
async def test_resolve_personal_user_id_passes_uuid_through():
    """A UUID-shaped scope_id (legacy UI path) should not hit the DB."""
    with patch("app.db.engine.fetch_one", new=AsyncMock()) as m:
        result = await _resolve_personal_user_id(USER_UUID)
    assert result == USER_UUID
    m.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_personal_user_id_translates_snowflake():
    """A digit-only scope_id (post-PR-C path) is translated via teams."""
    with patch("app.db.session.read_scope", new=_fake_read_scope(USER_UUID)):
        result = await _resolve_personal_user_id(TEAM_SNOWFLAKE)
    assert result == USER_UUID


@pytest.mark.asyncio
async def test_resolve_personal_user_id_returns_none_when_no_personal_team():
    with patch("app.db.session.read_scope", new=_fake_read_scope(None)):
        result = await _resolve_personal_user_id(TEAM_SNOWFLAKE)
    assert result is None


@pytest.mark.asyncio
async def test_resolve_personal_user_id_empty_string_returned_as_is():
    """Defensive: empty string shouldn't trigger a DB lookup."""
    with patch("app.db.engine.fetch_one", new=AsyncMock()) as m:
        result = await _resolve_personal_user_id("")
    assert result == ""
    m.assert_not_called()

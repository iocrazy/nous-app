"""Unit tests for the helpers introduced when unifying personal-scope
handling after Spec 1 PR-C.

  - ``resources_service._resolve_personal_team_id(user_id)`` looks up
    the user's personal team snowflake.
  - ``temp_ttl_settings._resolve_personal_user_id(scope_id)`` accepts
    either a UUID (legacy UI path) or a snowflake (post-PR-C
    sweeper/iterator path) and returns the UUID needed for the
    user_settings lookup.

Both delegate to ``app.db.engine.fetch_one``, so the tests patch that
to keep them DB-free.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.library.resources_service import _resolve_personal_team_id
from app.services.library.temp_ttl_settings import _resolve_personal_user_id

USER_UUID = "8e1584e3-9c29-4a5b-90fe-125b74259f7f"
TEAM_SNOWFLAKE = "310812366953241"


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
    with patch(
        "app.db.engine.fetch_one",
        new=AsyncMock(return_value={"uid": USER_UUID}),
    ):
        result = await _resolve_personal_user_id(TEAM_SNOWFLAKE)
    assert result == USER_UUID


@pytest.mark.asyncio
async def test_resolve_personal_user_id_returns_none_when_no_personal_team():
    with patch("app.db.engine.fetch_one", new=AsyncMock(return_value=None)):
        result = await _resolve_personal_user_id(TEAM_SNOWFLAKE)
    assert result is None


@pytest.mark.asyncio
async def test_resolve_personal_user_id_empty_string_returned_as_is():
    """Defensive: empty string shouldn't trigger a DB lookup."""
    with patch("app.db.engine.fetch_one", new=AsyncMock()) as m:
        result = await _resolve_personal_user_id("")
    assert result == ""
    m.assert_not_called()

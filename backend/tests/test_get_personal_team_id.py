"""Unit tests for ``TeamRepository.get_personal_team_id`` — the shared
personal-workspace scope resolver.

Reads go through ``read_scope()``; these mock it with a fake session so the
str-coercion (Snowflake-safe) + None-on-missing contract is asserted without a
live DB. The DSN-gated integration suite exercises the real round-trip.
"""

from __future__ import annotations

from typing import Any

import pytest

import app.repositories.team_repository as mod
from app.repositories.team_repository import TeamRepository

USER_UUID = "8e1584e3-9c29-4a5b-90fe-125b74259f7f"
TEAM_SNOWFLAKE = 310812366953241


class _FakeSession:
    def __init__(self, scalar_value: Any) -> None:
        self._scalar_value = scalar_value

    async def scalar(self, stmt: Any, binds: dict[str, Any] | None = None) -> Any:
        return self._scalar_value


class _ScopeCM:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, *exc: Any) -> bool:
        return False


@pytest.mark.asyncio
async def test_returns_snowflake_as_string(monkeypatch):
    monkeypatch.setattr(
        mod, "read_scope", lambda: _ScopeCM(_FakeSession(TEAM_SNOWFLAKE))
    )
    result = await TeamRepository().get_personal_team_id(USER_UUID)
    # Snowflake bigint coerced to a str for Snowflake-safe transport.
    assert result == str(TEAM_SNOWFLAKE)
    assert isinstance(result, str)


@pytest.mark.asyncio
async def test_returns_none_when_no_personal_team(monkeypatch):
    monkeypatch.setattr(mod, "read_scope", lambda: _ScopeCM(_FakeSession(None)))
    result = await TeamRepository().get_personal_team_id(USER_UUID)
    assert result is None

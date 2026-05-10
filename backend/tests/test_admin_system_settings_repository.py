"""Unit tests for SystemSettingsRepository + AdminTablePreferencesRepository."""

from __future__ import annotations

from typing import Any

import pytest

from app.repositories.admin.system_settings_repository import (
    SystemSettingsRepository,
)
from app.repositories.admin.table_preferences_repository import (
    AdminTablePreferencesRepository,
)


class _FakeQuery:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self._data: Any = []

    def __getattr__(self, name: str):
        def _capture(*args: Any, **kwargs: Any) -> "_FakeQuery":
            self.calls.append((name, args, kwargs))
            return self

        return _capture

    async def execute(self) -> Any:
        class _R:
            data = self._data

        return _R()


class _FakeClient:
    def __init__(self, query: _FakeQuery) -> None:
        self._query = query

    def table(self, name: str) -> _FakeQuery:
        self._query.calls.append(("table", (name,), {}))
        return self._query


@pytest.fixture
def fake_query() -> _FakeQuery:
    return _FakeQuery()


# ─── SystemSettingsRepository ──────────────────────────────────────


@pytest.fixture
def settings_repo(fake_query: _FakeQuery) -> SystemSettingsRepository:
    r = SystemSettingsRepository()

    async def _client():
        return _FakeClient(fake_query)

    r._client = _client  # type: ignore[method-assign]
    return r


@pytest.mark.asyncio
async def test_list_non_transcode_excludes_transcode_prefix(
    settings_repo: SystemSettingsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [
        {"key": "transcode_enabled", "value": True},
        {"key": "transcode_tiers", "value": "720p"},
        {"key": "something_else", "value": "ok"},
        {"key": "another_setting", "value": 42},
    ]
    rows = await settings_repo.list_non_transcode()
    keys = [r["key"] for r in rows]
    assert "something_else" in keys
    assert "another_setting" in keys
    assert "transcode_enabled" not in keys
    assert "transcode_tiers" not in keys


@pytest.mark.asyncio
async def test_exists_true_when_data_present(
    settings_repo: SystemSettingsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = {"key": "x"}
    assert await settings_repo.exists("x") is True


@pytest.mark.asyncio
async def test_exists_false_on_none(
    settings_repo: SystemSettingsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = None
    assert await settings_repo.exists("x") is False


@pytest.mark.asyncio
async def test_exists_false_on_exception(
    settings_repo: SystemSettingsRepository, fake_query: _FakeQuery
) -> None:
    async def _raises():
        raise RuntimeError("boom")

    fake_query.execute = _raises  # type: ignore[assignment]

    assert await settings_repo.exists("x") is False


@pytest.mark.asyncio
async def test_update_returns_first_row(
    settings_repo: SystemSettingsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [{"key": "x", "value": "v", "updated_by": "admin"}]
    row = await settings_repo.update("x", "v", "admin")
    assert row == {"key": "x", "value": "v", "updated_by": "admin"}

    update = next(c for c in fake_query.calls if c[0] == "update")
    assert update[1] == ({"value": "v", "updated_by": "admin"},)


@pytest.mark.asyncio
async def test_update_returns_none_on_empty(
    settings_repo: SystemSettingsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    row = await settings_repo.update("x", "v", "admin")
    assert row is None


# ─── AdminTablePreferencesRepository ───────────────────────────────


@pytest.fixture
def prefs_repo(fake_query: _FakeQuery) -> AdminTablePreferencesRepository:
    r = AdminTablePreferencesRepository()

    async def _client():
        return _FakeClient(fake_query)

    r._client = _client  # type: ignore[method-assign]
    return r


@pytest.mark.asyncio
async def test_get_filters_user_and_table_key(
    prefs_repo: AdminTablePreferencesRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [
        {"table_key": "users", "filters": [], "sorts": []},
    ]
    row = await prefs_repo.get("user-1", "users")
    assert row["table_key"] == "users"

    eq_values = [c[1] for c in fake_query.calls if c[0] == "eq"]
    assert ("user_id", "user-1") in eq_values
    assert ("table_key", "users") in eq_values


@pytest.mark.asyncio
async def test_get_returns_none_on_empty(
    prefs_repo: AdminTablePreferencesRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    row = await prefs_repo.get("user-1", "users")
    assert row is None


@pytest.mark.asyncio
async def test_upsert_uses_on_conflict(
    prefs_repo: AdminTablePreferencesRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [{"table_key": "users"}]
    await prefs_repo.upsert(
        user_id="user-1",
        table_key="users",
        filters=[{"field": "status"}],
        sorts=[{"field": "created_at"}],
        visible_columns=["id", "email"],
        column_order=["id", "email"],
    )

    upsert = next(c for c in fake_query.calls if c[0] == "upsert")
    args, kwargs = upsert[1], upsert[2]
    assert args[0]["user_id"] == "user-1"
    assert args[0]["table_key"] == "users"
    assert args[0]["filters"] == [{"field": "status"}]
    assert kwargs == {"on_conflict": "user_id,table_key"}


@pytest.mark.asyncio
async def test_delete_targets_user_and_table_key(
    prefs_repo: AdminTablePreferencesRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    await prefs_repo.delete("user-1", "users")

    eq_values = [c[1] for c in fake_query.calls if c[0] == "eq"]
    assert ("user_id", "user-1") in eq_values
    assert ("table_key", "users") in eq_values

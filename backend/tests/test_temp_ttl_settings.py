from unittest.mock import AsyncMock

import pytest

from app.services.library import temp_ttl_settings as m


@pytest.mark.asyncio
async def test_get_personal_ttl_reads_user_settings(monkeypatch):
    monkeypatch.setattr(
        m,
        "_fetch_settings_json",
        AsyncMock(return_value={"chat_temp_ttl_days": 7}),
    )
    assert await m.get_chat_temp_ttl_days("personal", "u1") == 7


@pytest.mark.asyncio
async def test_get_team_ttl_reads_teams_settings(monkeypatch):
    monkeypatch.setattr(
        m,
        "_fetch_settings_json",
        AsyncMock(return_value={"chat_temp_ttl_days": 14}),
    )
    assert await m.get_chat_temp_ttl_days("team", "42") == 14


@pytest.mark.asyncio
async def test_get_ttl_default_when_missing(monkeypatch):
    """No row OR no key → default 30 days."""
    monkeypatch.setattr(m, "_fetch_settings_json", AsyncMock(return_value=None))
    assert await m.get_chat_temp_ttl_days("personal", "u1") == m.DEFAULT_TTL_DAYS


@pytest.mark.asyncio
async def test_get_ttl_default_when_key_missing(monkeypatch):
    """Row exists but chat_temp_ttl_days key absent → default."""
    monkeypatch.setattr(m, "_fetch_settings_json", AsyncMock(return_value={}))
    assert await m.get_chat_temp_ttl_days("personal", "u1") == m.DEFAULT_TTL_DAYS


@pytest.mark.asyncio
async def test_get_ttl_never_when_negative(monkeypatch):
    """-1 means 'never expire' → return None so sweeper skips."""
    monkeypatch.setattr(
        m,
        "_fetch_settings_json",
        AsyncMock(return_value={"chat_temp_ttl_days": -1}),
    )
    assert await m.get_chat_temp_ttl_days("personal", "u1") is None


@pytest.mark.asyncio
async def test_set_personal_ttl_upserts_user_settings(monkeypatch):
    fake_execute = AsyncMock()
    monkeypatch.setattr(m, "_upsert_settings_key", fake_execute)
    await m.set_chat_temp_ttl_days("personal", "u1", 14)
    fake_execute.assert_awaited_once_with("personal", "u1", "chat_temp_ttl_days", 14)


@pytest.mark.asyncio
async def test_set_ttl_invalid_value_raises():
    """Only allow positive ints or -1."""
    with pytest.raises(ValueError):
        await m.set_chat_temp_ttl_days("personal", "u1", 0)
    with pytest.raises(ValueError):
        await m.set_chat_temp_ttl_days("personal", "u1", -2)


@pytest.mark.asyncio
async def test_invalid_scope_type_raises():
    """Any function entering with wrong scope must reject — picked one path."""
    with pytest.raises(ValueError):
        await m.get_chat_temp_ttl_days("project", "x")


def test_upsert_sql_bind_params_are_correct():
    """Guard against SQLAlchemy bind-param corruption (e.g. ::cast eating the colon)."""
    from sqlalchemy import text

    # Re-create what _upsert_settings_key builds, for both branches.
    personal_sql = (
        "INSERT INTO public.user_settings (user_id, settings_json) "
        "VALUES (:scope_id, jsonb_build_object(:key, to_jsonb(CAST(:value AS int)))) "
        "ON CONFLICT (user_id) DO UPDATE SET "
        "settings_json = COALESCE(public.user_settings.settings_json, '{}'::jsonb) "
        "|| jsonb_build_object(:key, to_jsonb(CAST(:value AS int))), "
        "updated_at = NOW()"
    )
    team_sql = (
        "UPDATE public.teams SET settings_json = "
        "COALESCE(settings_json, '{}'::jsonb) "
        "|| jsonb_build_object(:key, to_jsonb(CAST(:value AS int))) "
        "WHERE id = :scope_id"
    )
    for sql in (personal_sql, team_sql):
        bp = set(text(sql)._bindparams.keys())
        assert bp == {"scope_id", "key", "value"}, f"unexpected bind params: {bp}"

import importlib
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

r = importlib.import_module("app.api.temp_ttl_router")


class _Auth:
    def __init__(self, user_id="u1"):
        self.user_id = user_id


@pytest.mark.asyncio
async def test_get_temp_ttl_returns_default_when_unset(monkeypatch):
    monkeypatch.setattr(r, "verify_scope_access", AsyncMock())  # noop pass
    monkeypatch.setattr(r, "get_chat_temp_ttl_days", AsyncMock(return_value=30))
    out = await r.get_temp_ttl(auth=_Auth(), scope_type="personal", scope_id="u1")
    assert out == {"ttl_days": 30}


@pytest.mark.asyncio
async def test_get_temp_ttl_returns_minus_one_for_never(monkeypatch):
    monkeypatch.setattr(r, "verify_scope_access", AsyncMock())
    monkeypatch.setattr(r, "get_chat_temp_ttl_days", AsyncMock(return_value=None))
    out = await r.get_temp_ttl(auth=_Auth(), scope_type="personal", scope_id="u1")
    assert out == {"ttl_days": -1}


@pytest.mark.asyncio
async def test_put_temp_ttl_writes(monkeypatch):
    monkeypatch.setattr(r, "verify_scope_access", AsyncMock())
    monkeypatch.setattr(r, "_verify_team_owner", AsyncMock())  # noop pass
    set_mock = AsyncMock()
    monkeypatch.setattr(r, "set_chat_temp_ttl_days", set_mock)
    out = await r.put_temp_ttl(
        auth=_Auth(),
        payload=r.TempTtlUpdate(scope_type="team", scope_id="42", ttl_days=7),
    )
    assert out == {"ttl_days": 7}
    set_mock.assert_awaited_once_with("team", "42", 7)


@pytest.mark.asyncio
async def test_put_team_ttl_rejects_non_owner(monkeypatch):
    """Non-owner team members cannot change the team chat TTL."""
    monkeypatch.setattr(r, "verify_scope_access", AsyncMock())

    async def _not_owner(*a, **kw):
        raise HTTPException(
            status_code=403,
            detail="Only the team owner can change team chat TTL",
        )

    monkeypatch.setattr(r, "_verify_team_owner", _not_owner)
    monkeypatch.setattr(r, "set_chat_temp_ttl_days", AsyncMock())
    with pytest.raises(HTTPException) as e:
        await r.put_temp_ttl(
            auth=_Auth(),
            payload=r.TempTtlUpdate(scope_type="team", scope_id="42", ttl_days=7),
        )
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_put_temp_ttl_scope_access_denied(monkeypatch):
    async def _denied(*a, **kw):
        raise HTTPException(status_code=403, detail="forbidden")

    monkeypatch.setattr(r, "verify_scope_access", _denied)
    with pytest.raises(HTTPException) as e:
        await r.put_temp_ttl(
            auth=_Auth(),
            payload=r.TempTtlUpdate(scope_type="team", scope_id="42", ttl_days=7),
        )
    assert e.value.status_code == 403


def _fake_read_scope_returning(role):
    class _FakeResult:
        def scalar(self):
            return role

    class _FakeSession:
        async def execute(self, stmt):
            return _FakeResult()

    @asynccontextmanager
    async def _scope():
        yield _FakeSession()

    return _scope


@pytest.mark.asyncio
async def test_verify_team_owner_allows_owner(monkeypatch):
    """Phase B5 Task 1: _verify_team_owner's team_members.role lookup —
    migrated from raw db_engine.fetch_one to the ORM. Exercises the real
    function (not mocked away, unlike the other tests in this file)."""
    import app.db.session as db_session

    # _verify_team_owner does a function-local ``from app.db.session import
    # read_scope`` (deferred import, per-call) — patch the source attribute
    # so it resolves at call time, not a (nonexistent) module-level name on
    # the router module.
    monkeypatch.setattr(db_session, "read_scope", _fake_read_scope_returning("owner"))
    await r._verify_team_owner(_Auth(), "42")  # must not raise


@pytest.mark.asyncio
async def test_verify_team_owner_rejects_member_role(monkeypatch):
    import app.db.session as db_session

    monkeypatch.setattr(db_session, "read_scope", _fake_read_scope_returning("member"))
    with pytest.raises(HTTPException) as e:
        await r._verify_team_owner(_Auth(), "42")
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_verify_team_owner_rejects_when_no_membership_row(monkeypatch):
    """No matching team_members row (scalar() → None) is refused identically
    to a non-owner role, not a 500."""
    import app.db.session as db_session

    monkeypatch.setattr(db_session, "read_scope", _fake_read_scope_returning(None))
    with pytest.raises(HTTPException) as e:
        await r._verify_team_owner(_Auth(), "42")
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_put_temp_ttl_rejects_zero_and_invalid_negative(monkeypatch):
    """API-level guard for ttl_days=0 / ttl_days<-1."""
    monkeypatch.setattr(r, "verify_scope_access", AsyncMock())
    monkeypatch.setattr(r, "set_chat_temp_ttl_days", AsyncMock())
    for bad in (0, -5):
        with pytest.raises(HTTPException) as e:
            await r.put_temp_ttl(
                auth=_Auth(),
                payload=r.TempTtlUpdate(
                    scope_type="personal", scope_id="u1", ttl_days=bad
                ),
            )
        assert e.value.status_code == 400

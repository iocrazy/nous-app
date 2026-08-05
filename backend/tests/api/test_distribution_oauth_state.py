"""Unit tests for the distribution OAuth-state helpers (Phase B5 Task 1).

``_save_oauth_state``/``_pop_oauth_state`` were migrated from a broken
``accounts_repo.execute(...)`` call and a raw ``db_engine.execute_returning_one
(...)`` DELETE...RETURNING, to the SQLAlchemy ORM (``app.db.session.write_scope``
+ the ``DistributionOauthStates`` model).

``accounts_repo.execute(...)`` was never a real method, not something a later
migration removed: ``git log --follow --reverse`` shows ``_save_oauth_state``
was introduced in the very same commit (f137bf0) that created
``SocialAccountsRepository`` — and that class was ORM-only (read_scope/
write_scope) from its first line, with no generic ``.execute()`` passthrough
ever defined on it. The call was dead code from day one, silently orphaning
this INSERT — no test caught it because the only prior coverage
(test_distribution_flag_gate.py) mocked ``_save_oauth_state`` wholesale rather
than exercising the real call. Tests here patch ``write_scope`` and assert
against the COMPILED statement.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql

import app.api.distribution_router as dr


def _compile(stmt: Any) -> tuple[str, dict[str, Any]]:
    compiled = stmt.compile(dialect=postgresql.dialect())
    return str(compiled), dict(compiled.params)


class _FakeResult:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self._rows = rows if rows is not None else []

    def mappings(self) -> "_FakeResult":
        return self

    def first(self) -> Any:
        return self._rows[0] if self._rows else None


class _RecordingSession:
    def __init__(self, result: _FakeResult | None = None) -> None:
        self.calls: list[Any] = []
        self._result = result or _FakeResult()

    async def execute(self, stmt: Any) -> _FakeResult:
        self.calls.append(stmt)
        return self._result


def _patch_write_scope(monkeypatch: pytest.MonkeyPatch, result=None):
    session = _RecordingSession(result)

    @asynccontextmanager
    async def fake_scope():
        yield session

    monkeypatch.setattr(dr, "write_scope", fake_scope)
    return session


@pytest.mark.asyncio
async def test_save_oauth_state_inserts_row(monkeypatch):
    session = _patch_write_scope(monkeypatch)
    uid = "11111111-1111-1111-1111-111111111111"

    await dr._save_oauth_state("state-abc", uid, "douyin", "user", uid)

    assert len(session.calls) == 1
    sql, binds = _compile(session.calls[0])
    assert "INSERT INTO public.distribution_oauth_states" in sql
    assert binds["state"] == "state-abc"
    assert binds["user_id"] == uid
    assert binds["platform"] == "douyin"
    assert binds["scope_type"] == "user"
    assert binds["scope_id"] == uid


@pytest.mark.asyncio
async def test_pop_oauth_state_deletes_and_returns_row(monkeypatch):
    now = datetime.now(timezone.utc)
    row = {
        "state": "state-abc",
        "user_id": UUID("11111111-1111-1111-1111-111111111111"),
        "platform": "douyin",
        "scope_type": "user",
        "scope_id": "11111111-1111-1111-1111-111111111111",
        "created_at": now,
    }
    session = _patch_write_scope(monkeypatch, _FakeResult([row]))

    result = await dr._pop_oauth_state("state-abc")
    assert result == row

    sql, binds = _compile(session.calls[0])
    assert "DELETE FROM public.distribution_oauth_states" in sql
    assert "RETURNING public.distribution_oauth_states.state" in sql
    assert binds["state_1"] == "state-abc"
    # 10-minute freshness window bound as an app-side cutoff timestamp, not a
    # server-side INTERVAL literal (see the function's docstring).
    cutoff = binds["created_at_1"]
    assert datetime.now(timezone.utc) - cutoff >= timedelta(minutes=10)
    assert datetime.now(timezone.utc) - cutoff < timedelta(minutes=10, seconds=5)


@pytest.mark.asyncio
async def test_pop_oauth_state_returns_none_when_not_found(monkeypatch):
    _patch_write_scope(monkeypatch, _FakeResult([]))
    result = await dr._pop_oauth_state("missing-state")
    assert result is None

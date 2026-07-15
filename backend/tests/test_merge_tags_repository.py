"""merge_tags contract on the ORM transport.

The repo method used to call the proc via supabase-py ``client.rpc``; it now
issues ``SELECT merge_tags(...)`` as ``text()`` on the committing
``write_scope()`` session (same SECURITY DEFINER function, migration 267).
These tests stub only the session boundary — the statement/params are real.
"""

from contextlib import asynccontextmanager

import pytest

from app.repositories.tags_repository import TagsRepository


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class _FakeSession:
    def __init__(self, value):
        self.calls = []
        self._value = value

    async def execute(self, stmt, params=None):
        self.calls.append((stmt, params))
        return _FakeResult(self._value)


def _patch_write_scope(monkeypatch, session):
    from app.repositories import tags_repository as mod

    @asynccontextmanager
    async def _scope():
        yield session

    monkeypatch.setattr(mod, "write_scope", _scope)


@pytest.mark.asyncio
async def test_merge_tags_calls_proc_and_returns_count(monkeypatch):
    session = _FakeSession(value=12)
    _patch_write_scope(monkeypatch, session)

    count = await TagsRepository().merge_tags("100", ["200", "300"], "user-uuid")

    assert count == 12
    assert len(session.calls) == 1
    stmt, params = session.calls[0]
    assert "merge_tags" in str(stmt)
    assert params == {
        "p_target": "100",
        "p_sources": ["200", "300"],
        "p_user": "user-uuid",
    }


@pytest.mark.asyncio
async def test_merge_tags_coerces_scalar_to_int(monkeypatch):
    _patch_write_scope(monkeypatch, _FakeSession(value=7))
    assert await TagsRepository().merge_tags("1", ["2"], "u") == 7


@pytest.mark.asyncio
async def test_merge_tags_none_scalar_returns_zero(monkeypatch):
    _patch_write_scope(monkeypatch, _FakeSession(value=None))
    assert await TagsRepository().merge_tags("1", ["2"], "u") == 0

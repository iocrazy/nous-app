"""Unit tests for ``maybe_unit_of_work`` — the conditional unit-of-work gate.

Verifies the application-site contract WITHOUT a real DB (the real atomicity /
rollback behaviour of ``unit_of_work`` itself is covered by the integration
tests in ``test_orm_session.py``):

  * enabled=False → no session/engine is touched (zero overhead), yields None,
    and NO ambient session is set (repo writes would commit per-method = legacy).
  * enabled=True  → a real ``unit_of_work()`` is opened and yielded.
"""

from __future__ import annotations

import pytest

import app.db.session as session_mod
from app.db.session import maybe_unit_of_work


@pytest.mark.asyncio
async def test_disabled_opens_no_session(monkeypatch):
    """enabled=False must NOT call get_sessionmaker/get_engine and yields None."""

    def _boom(*a, **k):
        raise AssertionError("maybe_unit_of_work(False) must not open a session/engine")

    # If the disabled branch ever touched the engine, this would raise.
    monkeypatch.setattr(session_mod, "get_sessionmaker", _boom)

    async with maybe_unit_of_work(False) as session:
        assert session is None
        # No ambient unit-of-work is set → repo write_scope() would self-commit.
        assert session_mod._request_session.get() is None


@pytest.mark.asyncio
async def test_disabled_does_not_set_ambient_session(monkeypatch):
    """Inside a disabled gate, the ambient session ContextVar stays None before,
    during, and after — so nothing accidentally joins a (non-existent) UoW."""
    assert session_mod._request_session.get() is None
    async with maybe_unit_of_work(False):
        assert session_mod._request_session.get() is None
    assert session_mod._request_session.get() is None


@pytest.mark.asyncio
async def test_enabled_enters_unit_of_work(monkeypatch):
    """enabled=True must open the real ``unit_of_work()`` and yield its session."""
    from contextlib import asynccontextmanager

    sentinel = object()
    entered = {"in": False, "out": False}

    @asynccontextmanager
    async def _fake_uow():
        entered["in"] = True
        try:
            yield sentinel
        finally:
            entered["out"] = True

    # maybe_unit_of_work looks up ``unit_of_work`` at call time on the module.
    monkeypatch.setattr(session_mod, "unit_of_work", _fake_uow)

    async with maybe_unit_of_work(True) as session:
        assert session is sentinel
        assert entered["in"] is True
        assert entered["out"] is False

    assert entered["out"] is True

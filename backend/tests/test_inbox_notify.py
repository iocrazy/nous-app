"""Unit tests for the inbox producer helper ``notify`` (W3d).

``notify`` reads through ``read_scope()`` (the 10-minute dedupe probe) and writes
through ``write_scope()`` (the insert). These tests mock both scopes with a fake
session that captures each ``(compiled sql, binds)`` and returns configured
results — so the dedupe short-circuit, the insert shape, and the best-effort
swallow are all asserted WITHOUT a live database.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

import app.services.notifications as mod


def _compile(stmt: Any) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def scalar(self) -> Any:
        return self._rows[0] if self._rows else None


class _FakeSession:
    def __init__(self) -> None:
        self.sqls: list[str] = []
        self._queue: list[list[Any]] = []

    def queue(self, rows: list[Any]) -> "_FakeSession":
        self._queue.append(rows)
        return self

    async def execute(self, stmt: Any, params: Any = None) -> _FakeResult:
        self.sqls.append(_compile(stmt))
        return _FakeResult(self._queue.pop(0) if self._queue else [])


class _ScopeCM:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, *exc: Any) -> bool:
        return False


@pytest.fixture
def fake_session(monkeypatch: pytest.MonkeyPatch) -> _FakeSession:
    session = _FakeSession()
    monkeypatch.setattr(mod, "read_scope", lambda: _ScopeCM(session))
    monkeypatch.setattr(mod, "write_scope", lambda: _ScopeCM(session))
    return session


@pytest.mark.asyncio
async def test_notify_inserts_when_not_duplicate(fake_session: _FakeSession) -> None:
    # dedupe probe → empty (no dup); insert RETURNING id → 42.
    fake_session.queue([]).queue([42])
    new_id = await mod.notify(
        "user-1",
        "generation_result",
        "Download ready",
        severity="success",
        link_kind="resource",
        link_id="900",
    )
    assert new_id == 42
    # Two statements: the SELECT probe, then the INSERT.
    assert len(fake_session.sqls) == 2
    assert fake_session.sqls[0].upper().startswith("SELECT")
    assert "INSERT INTO" in fake_session.sqls[1].upper()
    assert "RETURNING" in fake_session.sqls[1].upper()


@pytest.mark.asyncio
async def test_notify_deduped_within_window(fake_session: _FakeSession) -> None:
    # dedupe probe finds an identical recent row → skip the insert entirely.
    fake_session.queue([1])
    new_id = await mod.notify(
        "user-1",
        "publish_result",
        "Publish complete",
        link_kind="publish_batch",
        link_id="77",
    )
    assert new_id is None
    # Only the SELECT probe ran; no INSERT.
    assert len(fake_session.sqls) == 1
    assert fake_session.sqls[0].upper().startswith("SELECT")


@pytest.mark.asyncio
async def test_notify_dedupe_handles_null_link(fake_session: _FakeSession) -> None:
    # A link-less notification must probe with IS NULL (not = NULL) — and still
    # insert when no dup is found.
    fake_session.queue([]).queue([7])
    new_id = await mod.notify("user-1", "autopilot_output", "Routine fired")
    assert new_id == 7
    assert "IS NULL" in fake_session.sqls[0].upper()


@pytest.mark.asyncio
async def test_notify_skips_empty_user(fake_session: _FakeSession) -> None:
    new_id = await mod.notify("", "generation_result", "x")
    assert new_id is None
    assert fake_session.sqls == []  # never touched the DB


@pytest.mark.asyncio
async def test_notify_swallows_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    # Any DB failure must be swallowed (best-effort) — producers never break.
    def _boom() -> Any:
        raise RuntimeError("db down")

    monkeypatch.setattr(mod, "read_scope", lambda: _boom())
    new_id = await mod.notify("user-1", "generation_result", "x")
    assert new_id is None

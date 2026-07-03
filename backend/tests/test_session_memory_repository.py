"""B2 — SessionMemoryRepository unit tests (ORM 2.0, mocked session).

Post-rollout the repo is the SQLAlchemy 2.0 implementation — reads go through
``read_scope()`` (``select``) and writes through ``write_scope()``
(``pg_insert ... on_conflict_do_update`` / ``delete ... returning``). These
tests mock those scopes with a fake session that returns a scripted ORM row
object (so ``_orm_obj_to_dict`` → ``_row_to_obj`` yields a real
``SessionMemoryRow``), covering load/upsert/delete behaviour + error swallowing
WITHOUT a live DB. The DSN-gated integration suite in
``tests/integration/test_session_memory_repository_orm.py`` exercises the real
round-trip.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

import pytest

import app.repositories.session_memory_repository as mod
from app.repositories.session_memory_repository import (
    SessionMemoryRepository,
    SessionMemoryRow,
)


class _FakeRow:
    """A stand-in ai_session_memory ORM row. ``_orm_obj_to_dict`` reads mapped
    attributes off it by name, so exposing the DB columns as attributes is
    enough for parity."""

    def __init__(self, **cols: Any) -> None:
        defaults = {
            "session_id": 0,
            "body_md": "",
            "sections_json": {},
            "version": 1,
            "last_updated_at": None,
            "tokens_at_last_update": 0,
            "tool_calls_at_last_update": 0,
            "turns_at_last_update": 0,
        }
        for key, val in {**defaults, **cols}.items():
            setattr(self, key, val)


class _FakeScalars:
    def __init__(self, first: Optional[_FakeRow]) -> None:
        self._first = first

    def first(self) -> Optional[_FakeRow]:
        return self._first


class _FakeResult:
    def __init__(self, first: Optional[_FakeRow]) -> None:
        self._first = first

    def scalars(self) -> _FakeScalars:
        return _FakeScalars(self._first)


class _FakeSession:
    """Returns a scripted row per execute() call (sequenced), and records the
    compiled SQL of every statement it runs."""

    def __init__(self, rows: list[Optional[_FakeRow]]) -> None:
        self._rows = list(rows)
        self.sql: list[str] = []

    async def execute(self, stmt) -> _FakeResult:
        self.sql.append(str(stmt.compile()))
        row = self._rows.pop(0) if self._rows else None
        return _FakeResult(row)


class _ScopeCM:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, *exc) -> bool:
        return False


def _patch_scopes(monkeypatch, session: _FakeSession) -> None:
    """Route both read_scope and write_scope to the same fake session."""
    monkeypatch.setattr(mod, "read_scope", lambda: _ScopeCM(session))
    monkeypatch.setattr(mod, "write_scope", lambda: _ScopeCM(session))


@pytest.fixture
def repo():
    return SessionMemoryRepository()


_SESSION = str(uuid4())
_SESSION_BIGINT = 1234567890123456789


# ─── load ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_load_returns_none_when_missing(repo, monkeypatch):
    session = _FakeSession([None])
    _patch_scopes(monkeypatch, session)
    out = await repo.load(_SESSION_BIGINT)
    assert out is None


@pytest.mark.asyncio
async def test_load_parses_existing_row(repo, monkeypatch):
    row = _FakeRow(
        session_id=_SESSION_BIGINT,
        body_md="# Title\nbody...",
        sections_json={"title": "Title"},
        version=5,
        last_updated_at=datetime(2026, 5, 3, 12, 0, tzinfo=timezone.utc),
        tokens_at_last_update=10000,
        tool_calls_at_last_update=7,
        turns_at_last_update=12,
    )
    session = _FakeSession([row])
    _patch_scopes(monkeypatch, session)
    out = await repo.load(_SESSION_BIGINT)
    assert isinstance(out, SessionMemoryRow)
    # session_id str()'d back out (bigint → str by _row_to_obj).
    assert out.session_id == str(_SESSION_BIGINT)
    assert out.body_md == "# Title\nbody..."
    assert out.version == 5
    assert out.tokens_at_last_update == 10000
    assert out.last_updated_at is not None
    assert session.sql[0].startswith("SELECT")


@pytest.mark.asyncio
async def test_load_swallows_errors(repo, monkeypatch):
    """DB error in load → None (best-effort, never crashes caller)."""

    def _broken_scope():
        raise RuntimeError("db down")

    monkeypatch.setattr(mod, "read_scope", _broken_scope)
    out = await repo.load(_SESSION_BIGINT)
    assert out is None


# ─── upsert ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upsert_inserts_when_no_existing(repo, monkeypatch):
    """No prior row → version starts at 1."""
    inserted = _FakeRow(
        session_id=_SESSION_BIGINT,
        body_md="new body",
        version=1,
        tokens_at_last_update=100,
        turns_at_last_update=1,
    )
    # 1st execute (load) → None; 2nd execute (upsert returning) → inserted row.
    session = _FakeSession([None, inserted])
    _patch_scopes(monkeypatch, session)

    out = await repo.upsert(
        _SESSION_BIGINT,
        body_md="new body",
        sections_json={},
        tokens_at_update=100,
        tool_calls_at_update=0,
        turns_at_update=1,
    )
    assert out is not None
    assert out.version == 1
    assert "INSERT INTO public.ai_session_memory" in session.sql[1]
    assert "ON CONFLICT" in session.sql[1]


@pytest.mark.asyncio
async def test_upsert_bumps_version(repo, monkeypatch):
    """Existing row v=3 → new version 4."""
    existing = _FakeRow(session_id=_SESSION_BIGINT, body_md="old", version=3)
    upserted = _FakeRow(session_id=_SESSION_BIGINT, body_md="updated", version=4)
    # 1st execute (load) → existing; 2nd execute (upsert) → upserted row.
    session = _FakeSession([existing, upserted])
    _patch_scopes(monkeypatch, session)

    out = await repo.upsert(
        _SESSION_BIGINT,
        body_md="updated",
        sections_json={},
        tokens_at_update=200,
        tool_calls_at_update=2,
        turns_at_update=5,
    )
    assert out is not None
    assert out.version == 4


@pytest.mark.asyncio
async def test_upsert_swallows_errors(repo, monkeypatch):
    """DB error in upsert → None, never raises."""

    def _broken_scope():
        raise RuntimeError("nope")

    # load's read_scope raises → existing=None path is inside the try, so the
    # whole upsert swallows and returns None.
    monkeypatch.setattr(mod, "read_scope", _broken_scope)
    monkeypatch.setattr(mod, "write_scope", _broken_scope)
    out = await repo.upsert(
        _SESSION_BIGINT,
        body_md="x",
        sections_json={},
        tokens_at_update=0,
        tool_calls_at_update=0,
        turns_at_update=0,
    )
    assert out is None


# ─── delete ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_returns_true_on_success(repo, monkeypatch):
    # delete's RETURNING session_id yields the removed key → truthy.
    session = _FakeSession([_FakeRow(session_id=_SESSION_BIGINT)])
    _patch_scopes(monkeypatch, session)
    assert await repo.delete(_SESSION_BIGINT) is True
    assert session.sql[0].startswith("DELETE FROM public.ai_session_memory")


@pytest.mark.asyncio
async def test_delete_returns_false_when_missing(repo, monkeypatch):
    # No row returned by RETURNING → nothing deleted.
    session = _FakeSession([None])
    _patch_scopes(monkeypatch, session)
    assert await repo.delete(_SESSION_BIGINT) is False


@pytest.mark.asyncio
async def test_delete_swallows_errors(repo, monkeypatch):
    def _broken_scope():
        raise RuntimeError("nope")

    monkeypatch.setattr(mod, "write_scope", _broken_scope)
    assert await repo.delete(_SESSION_BIGINT) is False

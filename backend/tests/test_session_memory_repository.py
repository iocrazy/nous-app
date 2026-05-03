"""B2 — SessionMemoryRepository unit tests (mock Supabase)."""
from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from app.repositories.session_memory_repository import (
    SessionMemoryRepository,
    SessionMemoryRow,
)


class _FakeQuery:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self._data: Any = None

    def __getattr__(self, name):
        def _capture(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return self

        return _capture

    async def execute(self):
        class _R:
            data = self._data

        return _R()


class _FakeClient:
    def __init__(self, query):
        self._q = query

    def table(self, name):
        self._q.calls.append(("table", (name,), {}))
        return self._q


@pytest.fixture
def fake_query():
    return _FakeQuery()


@pytest.fixture
def repo(fake_query):
    r = SessionMemoryRepository()

    async def _get_client():
        return _FakeClient(fake_query)

    r._get_client = _get_client  # type: ignore[method-assign]
    return r


_SESSION = str(uuid4())


# ─── load ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_load_returns_none_when_missing(repo, fake_query):
    fake_query._data = None
    out = await repo.load(_SESSION)
    assert out is None


@pytest.mark.asyncio
async def test_load_parses_existing_row(repo, fake_query):
    fake_query._data = {
        "session_id": _SESSION,
        "body_md": "# Title\nbody...",
        "sections_json": {"title": "Title"},
        "version": 5,
        "last_updated_at": "2026-05-03T12:00:00+00:00",
        "tokens_at_last_update": 10000,
        "tool_calls_at_last_update": 7,
        "turns_at_last_update": 12,
    }
    out = await repo.load(_SESSION)
    assert isinstance(out, SessionMemoryRow)
    assert out.body_md == "# Title\nbody..."
    assert out.version == 5
    assert out.tokens_at_last_update == 10000
    assert out.last_updated_at is not None


@pytest.mark.asyncio
async def test_load_swallows_errors(repo):
    """DB error in load → None (best-effort, never crashes caller)."""
    async def _broken_client():
        raise RuntimeError("supabase down")

    repo._get_client = _broken_client  # type: ignore[method-assign]
    out = await repo.load(_SESSION)
    assert out is None


# ─── upsert ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upsert_inserts_when_no_existing(repo, fake_query):
    """No prior row → version starts at 1."""
    inserted_data = [
        {
            "session_id": _SESSION,
            "body_md": "new body",
            "sections_json": {},
            "version": 1,
            "tokens_at_last_update": 100,
            "tool_calls_at_last_update": 0,
            "turns_at_last_update": 1,
        }
    ]
    # First call (load) returns None; second call (upsert) returns inserted
    call_count = {"n": 0}

    async def _execute_seq(self):
        call_count["n"] += 1
        class _R:
            data = inserted_data if call_count["n"] >= 2 else None
        return _R()

    # Patch fake_query to alternate
    fake_query.execute = _execute_seq.__get__(fake_query)

    out = await repo.upsert(
        _SESSION,
        body_md="new body",
        sections_json={},
        tokens_at_update=100,
        tool_calls_at_update=0,
        turns_at_update=1,
    )
    assert out is not None
    assert out.version == 1


@pytest.mark.asyncio
async def test_upsert_bumps_version(repo, fake_query):
    """Existing row v=3 → new version 4."""
    existing = {
        "session_id": _SESSION,
        "body_md": "old",
        "version": 3,
        "tokens_at_last_update": 0,
        "tool_calls_at_last_update": 0,
        "turns_at_last_update": 0,
    }
    upserted = [
        {**existing, "body_md": "updated", "version": 4}
    ]

    call_count = {"n": 0}

    async def _execute_seq(self):
        call_count["n"] += 1
        class _R:
            data = upserted if call_count["n"] >= 2 else existing
        return _R()

    fake_query.execute = _execute_seq.__get__(fake_query)

    out = await repo.upsert(
        _SESSION,
        body_md="updated",
        sections_json={},
        tokens_at_update=200,
        tool_calls_at_update=2,
        turns_at_update=5,
    )
    assert out is not None
    assert out.version == 4


@pytest.mark.asyncio
async def test_upsert_swallows_errors(repo):
    """DB error in upsert → None, never raises."""
    async def _broken():
        raise RuntimeError("nope")

    repo._get_client = _broken  # type: ignore[method-assign]
    out = await repo.upsert(
        _SESSION,
        body_md="x",
        sections_json={},
        tokens_at_update=0,
        tool_calls_at_update=0,
        turns_at_update=0,
    )
    assert out is None


# ─── delete ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_returns_true_on_success(repo, fake_query):
    fake_query._data = [{"session_id": _SESSION}]
    assert await repo.delete(_SESSION) is True


@pytest.mark.asyncio
async def test_delete_returns_false_when_missing(repo, fake_query):
    fake_query._data = []
    assert await repo.delete(_SESSION) is False


@pytest.mark.asyncio
async def test_delete_swallows_errors(repo):
    async def _broken():
        raise RuntimeError("nope")

    repo._get_client = _broken  # type: ignore[method-assign]
    assert await repo.delete(_SESSION) is False

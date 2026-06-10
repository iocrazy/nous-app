"""Scale guards for the batch sweepers in ResourcesRepository.

``get_expired_trashed_resources`` and ``get_untranscoded_video_versions`` used to
issue unbounded SELECTs that PostgREST silently truncated at 1000 rows (and whose
ORM twins fetched the whole set into RAM). They must now apply a deterministic
ORDER BY + an explicit LIMIT so a backlog drains over successive sweeper runs
instead of being clipped.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.repositories.resources_repository import (
    EXPIRED_TRASH_BATCH,
    UNTRANSCODED_BATCH,
    ResourcesRepository,
)


class _FakeQuery:
    """Chainable query stub that records every method call."""

    def __init__(self, data: Any = None) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self._data = data if data is not None else []

    def __getattr__(self, name: str):
        # supabase-py exposes `.not_` as a chained namespace (`q.not_.is_(...)`);
        # return self so the trailing call still records + chains.
        if name == "not_":
            return self

        def _capture(*args: Any, **kwargs: Any) -> "_FakeQuery":
            self.calls.append((name, args, kwargs))
            return self

        return _capture

    async def execute(self) -> Any:
        return type("_R", (), {"data": self._data})()

    def order_call(self) -> tuple[Any, ...] | None:
        return next((c for c in self.calls if c[0] == "order"), None)

    def limit_call(self) -> tuple[Any, ...] | None:
        return next((c for c in self.calls if c[0] == "limit"), None)


def _repo_with(query: _FakeQuery) -> ResourcesRepository:
    repo = ResourcesRepository()

    class _Client:
        def table(self, _name: str) -> _FakeQuery:
            return query

    async def _get_client() -> Any:
        return _Client()

    repo._get_client = _get_client  # type: ignore[method-assign]
    return repo


# ─── get_expired_trashed_resources ──────────────────────────────────


@pytest.mark.asyncio
async def test_expired_trash_default_is_bounded_and_ordered() -> None:
    q = _FakeQuery()
    repo = _repo_with(q)

    await repo.get_expired_trashed_resources()

    order = q.order_call()
    assert order is not None
    assert order[1] == ("trashed_at",)
    assert order[2] == {"desc": False}  # oldest-trashed first

    limit = q.limit_call()
    assert limit is not None
    assert limit[1] == (EXPIRED_TRASH_BATCH,)


@pytest.mark.asyncio
async def test_expired_trash_respects_custom_limit() -> None:
    q = _FakeQuery()
    repo = _repo_with(q)

    await repo.get_expired_trashed_resources(older_than_days=15, limit=100)

    assert q.limit_call()[1] == (100,)


# ─── get_untranscoded_video_versions ────────────────────────────────


@pytest.mark.asyncio
async def test_untranscoded_default_is_bounded_and_ordered() -> None:
    q = _FakeQuery()
    repo = _repo_with(q)

    await repo.get_untranscoded_video_versions()

    order = q.order_call()
    assert order is not None
    assert order[1] == ("id",)
    assert order[2] == {"desc": False}

    limit = q.limit_call()
    assert limit is not None
    assert limit[1] == (UNTRANSCODED_BATCH,)


@pytest.mark.asyncio
async def test_untranscoded_respects_custom_limit() -> None:
    q = _FakeQuery()
    repo = _repo_with(q)

    await repo.get_untranscoded_video_versions(limit=25)

    assert q.limit_call()[1] == (25,)

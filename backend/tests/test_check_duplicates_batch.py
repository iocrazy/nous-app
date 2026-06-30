"""Tests for the batch check-duplicates endpoint (Task 1 – bulk import).

Coverage:
- find_by_hashes: issues one PostgREST .in_ query; maps hash→first row
- POST /check-duplicates: one result per input item, order preserved
- duplicate=True only when hash exists AND file_size_bytes == item.file_size
- duplicate=False when hash exists but size differs (collision guard)
- duplicate=False when hash is absent
- >200 items → HTTP 422
- creator_id binding (per-user; never cross-user)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.repositories.resources_repository import ResourcesRepository

# ─── Repository helpers ────────────────────────────────────────────────────────


class _FakeQuery:
    """Chainable query stub that records every chained call."""

    def __init__(self, data: list[dict] | None = None) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self._data = data if data is not None else []

    def __getattr__(self, name: str):
        def _capture(*args: Any, **kwargs: Any) -> "_FakeQuery":
            self.calls.append((name, args, kwargs))
            return self

        return _capture

    async def execute(self) -> Any:
        return type("_R", (), {"data": self._data})()

    def call_args(self, method: str) -> tuple[Any, ...] | None:
        found = next((c for c in self.calls if c[0] == method), None)
        return found[1] if found else None

    def kwarg(self, method: str, key: str) -> Any:
        found = next((c for c in self.calls if c[0] == method), None)
        return found[2].get(key) if found else None


def _repo_with_query(query: _FakeQuery) -> ResourcesRepository:
    repo = ResourcesRepository()

    class _Client:
        def table(self, _name: str) -> _FakeQuery:
            return query

    async def _get_client() -> Any:
        return _Client()

    repo._get_client = _get_client  # type: ignore[method-assign]
    return repo


# ─── find_by_hashes tests ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_find_by_hashes_uses_in_query() -> None:
    """find_by_hashes must use .in_("file_hash", ...) not individual .eq calls."""
    hashes = ["a" * 64, "b" * 64]
    q = _FakeQuery(data=[])
    repo = _repo_with_query(q)

    await repo.find_by_hashes(hashes, "user-1")

    in_args = q.call_args("in_")
    assert in_args is not None, "Expected .in_() to be called"
    assert in_args[0] == "file_hash"
    assert list(in_args[1]) == hashes


@pytest.mark.asyncio
async def test_find_by_hashes_filters_by_creator_id() -> None:
    """find_by_hashes must bind creator_id so data never crosses user boundaries."""
    q = _FakeQuery(data=[])
    repo = _repo_with_query(q)

    await repo.find_by_hashes(["a" * 64], "creator-99")

    # Look for an eq call with "creator_id"
    eq_calls = [c for c in q.calls if c[0] == "eq" and c[1][0] == "creator_id"]
    assert eq_calls, "Expected .eq('creator_id', ...) to filter by user"
    assert eq_calls[0][1][1] == "creator-99"


@pytest.mark.asyncio
async def test_find_by_hashes_filters_is_trashed_false() -> None:
    """find_by_hashes must exclude trashed resources."""
    q = _FakeQuery(data=[])
    repo = _repo_with_query(q)

    await repo.find_by_hashes(["a" * 64], "u1")

    eq_calls = [c for c in q.calls if c[0] == "eq" and c[1][0] == "is_trashed"]
    assert eq_calls, "Expected .eq('is_trashed', False)"
    assert eq_calls[0][1][1] is False


@pytest.mark.asyncio
async def test_find_by_hashes_returns_hash_keyed_dict() -> None:
    """Return value is {file_hash: first_matching_row}."""
    hash_a = "a" * 64
    hash_b = "b" * 64
    row_a = {"file_hash": hash_a, "id": "1", "file_size_bytes": 100}
    row_b = {"file_hash": hash_b, "id": "2", "file_size_bytes": 200}

    q = _FakeQuery(data=[row_a, row_b])
    repo = _repo_with_query(q)

    result = await repo.find_by_hashes([hash_a, hash_b], "u1")

    assert result == {hash_a: row_a, hash_b: row_b}


@pytest.mark.asyncio
async def test_find_by_hashes_first_row_per_hash_wins() -> None:
    """When multiple rows share a hash, only the first is kept in the map."""
    h = "c" * 64
    row1 = {"file_hash": h, "id": "first", "file_size_bytes": 42}
    row2 = {"file_hash": h, "id": "second", "file_size_bytes": 42}

    q = _FakeQuery(data=[row1, row2])
    repo = _repo_with_query(q)

    result = await repo.find_by_hashes([h], "u1")

    assert result[h]["id"] == "first"


@pytest.mark.asyncio
async def test_find_by_hashes_returns_empty_dict_on_error() -> None:
    """find_by_hashes must never raise; return {} on any DB error."""
    repo = ResourcesRepository()

    async def _bad_client() -> Any:
        raise RuntimeError("DB exploded")

    repo._get_client = _bad_client  # type: ignore[method-assign]

    result = await repo.find_by_hashes(["a" * 64], "u1")

    assert result == {}


# ─── Endpoint tests ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_check_duplicates_exact_match_is_duplicate() -> None:
    """Hash + matching file_size → duplicate=True, existing populated."""
    from app.api.resources_upload_router import check_duplicates_batch
    from app.schemas.resources_batch import CheckDuplicatesItem, CheckDuplicatesRequest

    h = "d" * 64
    row = {"file_hash": h, "id": "res-1", "file_size_bytes": 999}

    auth = MagicMock(user_id="u1")
    body = CheckDuplicatesRequest(
        items=[CheckDuplicatesItem(file_hash=h, file_size=999)]
    )

    with patch("app.api.resources_upload_router.ResourcesRepository") as MockRepo:
        MockRepo.return_value.find_by_hashes = AsyncMock(return_value={h: row})
        resp = await check_duplicates_batch(body, auth)

    assert len(resp.results) == 1
    assert resp.results[0].file_hash == h
    assert resp.results[0].duplicate is True
    assert resp.results[0].existing == row


@pytest.mark.asyncio
async def test_check_duplicates_size_mismatch_is_not_duplicate() -> None:
    """Hash matches but file_size differs → collision guard → duplicate=False."""
    from app.api.resources_upload_router import check_duplicates_batch
    from app.schemas.resources_batch import CheckDuplicatesItem, CheckDuplicatesRequest

    h = "e" * 64
    row = {"file_hash": h, "id": "res-2", "file_size_bytes": 500}

    auth = MagicMock(user_id="u1")
    body = CheckDuplicatesRequest(
        items=[CheckDuplicatesItem(file_hash=h, file_size=501)]  # different size
    )

    with patch("app.api.resources_upload_router.ResourcesRepository") as MockRepo:
        MockRepo.return_value.find_by_hashes = AsyncMock(return_value={h: row})
        resp = await check_duplicates_batch(body, auth)

    assert resp.results[0].duplicate is False
    assert resp.results[0].existing is None


@pytest.mark.asyncio
async def test_check_duplicates_absent_hash_is_not_duplicate() -> None:
    """Hash not found in DB → duplicate=False, existing=None."""
    from app.api.resources_upload_router import check_duplicates_batch
    from app.schemas.resources_batch import CheckDuplicatesItem, CheckDuplicatesRequest

    h = "f" * 64
    auth = MagicMock(user_id="u1")
    body = CheckDuplicatesRequest(
        items=[CheckDuplicatesItem(file_hash=h, file_size=100)]
    )

    with patch("app.api.resources_upload_router.ResourcesRepository") as MockRepo:
        MockRepo.return_value.find_by_hashes = AsyncMock(return_value={})
        resp = await check_duplicates_batch(body, auth)

    assert resp.results[0].duplicate is False
    assert resp.results[0].existing is None


@pytest.mark.asyncio
async def test_check_duplicates_order_preserved() -> None:
    """Results list must match input order exactly."""
    from app.api.resources_upload_router import check_duplicates_batch
    from app.schemas.resources_batch import CheckDuplicatesItem, CheckDuplicatesRequest

    h1, h2, h3 = "1" * 64, "2" * 64, "3" * 64
    # h1 → duplicate, h2 → absent, h3 → size mismatch
    db_map = {
        h1: {"file_hash": h1, "file_size_bytes": 10},
        h3: {"file_hash": h3, "file_size_bytes": 30},
    }
    auth = MagicMock(user_id="u1")
    body = CheckDuplicatesRequest(
        items=[
            CheckDuplicatesItem(file_hash=h1, file_size=10),  # exact match
            CheckDuplicatesItem(file_hash=h2, file_size=20),  # absent
            CheckDuplicatesItem(file_hash=h3, file_size=999),  # size mismatch
        ]
    )

    with patch("app.api.resources_upload_router.ResourcesRepository") as MockRepo:
        MockRepo.return_value.find_by_hashes = AsyncMock(return_value=db_map)
        resp = await check_duplicates_batch(body, auth)

    assert [r.file_hash for r in resp.results] == [h1, h2, h3]
    assert [r.duplicate for r in resp.results] == [True, False, False]


@pytest.mark.asyncio
async def test_check_duplicates_over_200_returns_422() -> None:
    """Sending > 200 items must return HTTP 422 immediately."""
    from fastapi import HTTPException

    from app.api.resources_upload_router import check_duplicates_batch
    from app.schemas.resources_batch import CheckDuplicatesItem, CheckDuplicatesRequest

    items = [CheckDuplicatesItem(file_hash="a" * 64, file_size=1) for _ in range(201)]
    auth = MagicMock(user_id="u1")
    body = CheckDuplicatesRequest(items=items)

    with pytest.raises(HTTPException) as exc_info:
        await check_duplicates_batch(body, auth)

    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_check_duplicates_passes_creator_id_to_repo() -> None:
    """The endpoint must pass auth.user_id as creator_id; never another user's id."""
    from app.api.resources_upload_router import check_duplicates_batch
    from app.schemas.resources_batch import CheckDuplicatesItem, CheckDuplicatesRequest

    h = "9" * 64
    auth = MagicMock(user_id="owner-only")
    body = CheckDuplicatesRequest(items=[CheckDuplicatesItem(file_hash=h, file_size=1)])

    with patch("app.api.resources_upload_router.ResourcesRepository") as MockRepo:
        mock_repo = MockRepo.return_value
        mock_repo.find_by_hashes = AsyncMock(return_value={})
        await check_duplicates_batch(body, auth)

    mock_repo.find_by_hashes.assert_awaited_once()
    call_args = mock_repo.find_by_hashes.await_args
    # Second positional arg (or kwarg creator_id) must be the auth user's id
    args, kwargs = call_args
    creator_id = kwargs.get("creator_id") or (args[1] if len(args) > 1 else None)
    assert creator_id == "owner-only"


@pytest.mark.asyncio
async def test_check_duplicates_empty_list_returns_empty_results() -> None:
    """Empty items list → empty results list (no error)."""
    from app.api.resources_upload_router import check_duplicates_batch
    from app.schemas.resources_batch import CheckDuplicatesRequest

    auth = MagicMock(user_id="u1")
    body = CheckDuplicatesRequest(items=[])

    with patch("app.api.resources_upload_router.ResourcesRepository") as MockRepo:
        MockRepo.return_value.find_by_hashes = AsyncMock(return_value={})
        resp = await check_duplicates_batch(body, auth)

    assert resp.results == []

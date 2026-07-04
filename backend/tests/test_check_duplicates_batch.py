"""Tests for the batch check-duplicates endpoint (Task 1 – bulk import).

Coverage:
- find_by_hashes: issues one ORM ``file_hash IN (...)`` query; maps hash→first
  row (projected to the exact legacy column set)
- POST /check-duplicates: one result per input item, order preserved
- duplicate=True only when hash exists AND file_size_bytes == item.file_size
- duplicate=False when hash exists but size differs (collision guard)
- duplicate=False when hash is absent
- >100 items → HTTP 422
- creator_id binding (per-user; never cross-user)
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql

from app.models import Resources
from app.repositories import resources_repository as repo_mod
from app.repositories.resources_repository import ResourcesRepository

# ─── Repository scope-mock plumbing ────────────────────────────────────────────


class _ScalarResult:
    def __init__(self, objs: list[Any]) -> None:
        self._objs = objs

    def all(self) -> list[Any]:
        return list(self._objs)


class _ExecResult:
    def __init__(self, objs: list[Any]) -> None:
        self._objs = objs

    def scalars(self) -> _ScalarResult:
        return _ScalarResult(self._objs)


class _CapSession:
    """Records executed ORM statements and hands back queued ORM objects."""

    def __init__(self, objs: list[Any] | None = None) -> None:
        self.statements: list[Any] = []
        self._objs = list(objs or [])

    async def execute(self, stmt: Any, params: Any = None) -> _ExecResult:
        self.statements.append(stmt)
        return _ExecResult(self._objs)


@asynccontextmanager
async def _fake_scope(session: _CapSession):
    yield session


def _compiled(session: _CapSession):
    """(sql_text, bind_values) for the single captured statement."""
    assert session.statements, "expected a query to be executed"
    compiled = session.statements[-1].compile(dialect=postgresql.dialect())
    return str(compiled), compiled.params


def _res(**kw: Any) -> Resources:
    """A transient Resources ORM instance for the projected read path."""
    return Resources(source_type="web", filename=kw.pop("filename", "f.mp4"), **kw)


# ─── find_by_hashes tests ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_find_by_hashes_uses_in_query(monkeypatch) -> None:
    """find_by_hashes filters ``file_hash IN (...)`` with the given hashes."""
    hashes = ["a" * 64, "b" * 64]
    session = _CapSession(objs=[])
    monkeypatch.setattr(repo_mod, "read_scope", lambda: _fake_scope(session))
    repo = ResourcesRepository()

    await repo.find_by_hashes(hashes, "user-1")

    sql, binds = _compiled(session)
    assert "file_hash IN" in sql
    # The IN list is a single POSTCOMPILE bind holding all hashes.
    flat = [x for v in binds.values() for x in (v if isinstance(v, list) else [v])]
    for h in hashes:
        assert h in flat


@pytest.mark.asyncio
async def test_find_by_hashes_filters_by_creator_id(monkeypatch) -> None:
    """find_by_hashes must bind creator_id so data never crosses user boundaries."""
    session = _CapSession(objs=[])
    monkeypatch.setattr(repo_mod, "read_scope", lambda: _fake_scope(session))
    repo = ResourcesRepository()

    await repo.find_by_hashes(["a" * 64], "creator-99")

    sql, binds = _compiled(session)
    assert "creator_id" in sql
    assert "creator-99" in binds.values()


@pytest.mark.asyncio
async def test_find_by_hashes_filters_is_trashed_false(monkeypatch) -> None:
    """find_by_hashes must exclude trashed resources."""
    session = _CapSession(objs=[])
    monkeypatch.setattr(repo_mod, "read_scope", lambda: _fake_scope(session))
    repo = ResourcesRepository()

    await repo.find_by_hashes(["a" * 64], "u1")

    sql, _binds = _compiled(session)
    # ``is_(False)`` renders as a literal ``IS false`` (not a bind param).
    assert "is_trashed IS false" in sql


@pytest.mark.asyncio
async def test_find_by_hashes_returns_hash_keyed_dict(monkeypatch) -> None:
    """Return value is {file_hash: first_matching_row} projected to the legacy
    column set (id → int, no extra keys leak)."""
    hash_a = "a" * 64
    hash_b = "b" * 64
    objs = [
        _res(id=1, file_hash=hash_a, file_size_bytes=100),
        _res(id=2, file_hash=hash_b, file_size_bytes=200),
    ]
    session = _CapSession(objs=objs)
    monkeypatch.setattr(repo_mod, "read_scope", lambda: _fake_scope(session))
    repo = ResourcesRepository()

    result = await repo.find_by_hashes([hash_a, hash_b], "u1")

    assert set(result.keys()) == {hash_a, hash_b}
    assert result[hash_a]["id"] == 1
    assert result[hash_a]["file_size_bytes"] == 100
    # Exactly the legacy projection — no extra columns from the full entity read.
    assert set(result[hash_a].keys()) == set(repo_mod._FIND_BY_HASH_COLS)


@pytest.mark.asyncio
async def test_find_by_hashes_first_row_per_hash_wins(monkeypatch) -> None:
    """When multiple rows share a hash, only the first is kept in the map."""
    h = "c" * 64
    objs = [
        _res(id=111, file_hash=h, file_size_bytes=42),
        _res(id=222, file_hash=h, file_size_bytes=42),
    ]
    session = _CapSession(objs=objs)
    monkeypatch.setattr(repo_mod, "read_scope", lambda: _fake_scope(session))
    repo = ResourcesRepository()

    result = await repo.find_by_hashes([h], "u1")

    assert result[h]["id"] == 111


@pytest.mark.asyncio
async def test_find_by_hashes_returns_empty_dict_on_error(monkeypatch) -> None:
    """find_by_hashes must never raise; return {} on any DB error."""

    def _boom():
        raise RuntimeError("DB exploded")

    monkeypatch.setattr(repo_mod, "read_scope", _boom)
    repo = ResourcesRepository()

    result = await repo.find_by_hashes(["a" * 64], "u1")

    assert result == {}


@pytest.mark.asyncio
async def test_find_by_hashes_empty_input_short_circuits(monkeypatch) -> None:
    """Empty hash list → {} without opening a session."""
    session = _CapSession(objs=[])
    monkeypatch.setattr(repo_mod, "read_scope", lambda: _fake_scope(session))
    repo = ResourcesRepository()

    result = await repo.find_by_hashes([], "u1")

    assert result == {}
    assert session.statements == []


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
async def test_check_duplicates_over_100_returns_422() -> None:
    """Sending > 100 items must return HTTP 422 immediately."""
    from fastapi import HTTPException

    from app.api.resources_upload_router import check_duplicates_batch
    from app.schemas.resources_batch import CheckDuplicatesItem, CheckDuplicatesRequest

    items = [CheckDuplicatesItem(file_hash="a" * 64, file_size=1) for _ in range(101)]
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

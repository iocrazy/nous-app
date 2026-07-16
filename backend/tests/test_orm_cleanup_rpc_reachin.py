"""ORM cleanup: the last supabase-py surfaces outside the auth bucket.

- ``TagsRepository.merge_tags`` — was ``client.rpc("merge_tags", ...)``; now a
  ``text("SELECT merge_tags(...)")`` call on the committing ``write_scope()``
  session (same SECURITY DEFINER proc, same transport as everything else).
- ``StoryboardAssetRepository.get_by_id`` — new ORM read replacing the
  ``StoryboardService.get_asset`` reach-in that used the repo's raw
  supabase-py client (the last such reach-in).

The GoTrue ``auth.admin`` methods on projects_repository are the ONE
deliberate supabase-py remnant (auth API, not PostgREST) — asserted here so
the exception bucket stays exactly one bucket.

Style: stub only the session boundary; real statement construction runs.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest


class _FakeScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class _FakeRpcSession:
    def __init__(self, value):
        self.calls = []
        self._value = value

    async def execute(self, stmt, params=None):
        self.calls.append((stmt, params))
        return _FakeScalarResult(self._value)


def _cm(session):
    @asynccontextmanager
    async def _scope():
        yield session

    return _scope


# ── tags.merge_tags → text() on write_scope ────────────────────────────────


@pytest.mark.asyncio
async def test_merge_tags_calls_sql_function_in_write_scope(monkeypatch):
    from app.repositories import tags_repository as mod
    from app.repositories.tags_repository import TagsRepository

    session = _FakeRpcSession(value=7)
    monkeypatch.setattr(mod, "write_scope", _cm(session))

    out = await TagsRepository().merge_tags("101", ["202", 303], "user-uuid-1")

    assert out == 7
    assert len(session.calls) == 1
    stmt, params = session.calls[0]
    sql = str(stmt)
    assert "merge_tags" in sql
    assert params == {
        "p_target": "101",
        "p_sources": ["202", "303"],
        "p_user": "user-uuid-1",
    }


@pytest.mark.asyncio
async def test_merge_tags_none_result_returns_zero(monkeypatch):
    from app.repositories import tags_repository as mod
    from app.repositories.tags_repository import TagsRepository

    monkeypatch.setattr(mod, "write_scope", _cm(_FakeRpcSession(value=None)))
    assert await TagsRepository().merge_tags("1", ["2"], "u") == 0


# ── storyboard asset get_by_id (replaces the service reach-in) ─────────────


class _FakeRowResult:
    def __init__(self, obj):
        self._obj = obj

    def scalars(self):
        return self

    def first(self):
        return self._obj


class _FakeReadSession:
    def __init__(self, obj):
        self.statements = []
        self._obj = obj

    async def execute(self, stmt, params=None):
        self.statements.append(stmt)
        return _FakeRowResult(self._obj)


@pytest.mark.asyncio
async def test_asset_get_by_id_selects_by_bigint_id(monkeypatch):
    from app.repositories import storyboard_repository as mod
    from app.repositories.storyboard_repository import StoryboardAssetRepository

    session = _FakeReadSession(obj=None)
    monkeypatch.setattr(mod, "read_scope", _cm(session))

    out = await StoryboardAssetRepository().get_by_id("123456789012345678")

    assert out is None
    assert len(session.statements) == 1
    stmt = session.statements[0]
    # mig 348 rename-deprecated the storyboard tables; the model (and so the
    # emitted SQL) names the real table, not the pre-348 one.
    assert "FROM public.zzz_deprecated_storyboard_assets" in str(stmt)
    # str snowflake id must be bigint-coerced before binding.
    assert 123456789012345678 in stmt.compile().params.values()


@pytest.mark.asyncio
async def test_service_get_asset_delegates_to_repo(monkeypatch):
    from app.services.storyboard.storyboard_service import StoryboardService

    class _StubAssetRepo:
        def __init__(self):
            self.seen = []

        async def get_by_id(self, asset_id):
            self.seen.append(asset_id)
            return {"id": "9", "file_path": "/x.png"}

    service = object.__new__(StoryboardService)  # skip heavy __init__
    service.asset_repo = _StubAssetRepo()

    out = await service.get_asset("9")

    assert out == {"id": "9", "file_path": "/x.png"}
    assert service.asset_repo.seen == ["9"]


# ── the supabase-py surface is retired outside the auth bucket ─────────────


def test_tags_and_storyboard_repos_have_no_supabase_client():
    # get_async_supabase_admin is the ONLY way these modules could obtain a
    # supabase-py client — its absence proves the REST surface is gone.
    # (No `client.table` substring check: storyboard's module docstring
    # legitimately mentions the pattern in prose.)
    import inspect

    from app.repositories import storyboard_repository, tags_repository

    for mod in (tags_repository, storyboard_repository):
        src = inspect.getsource(mod)
        assert "get_async_supabase_admin" not in src, mod.__name__


def test_projects_repo_supabase_surface_is_auth_admin_only():
    """projects_repository keeps supabase-py ONLY for GoTrue auth.admin
    (auth.users is Supabase-managed — not reachable via the DB engine)."""
    import inspect

    from app.repositories import projects_repository

    src = inspect.getsource(projects_repository)
    assert "client.auth.admin" in src  # the deliberate exception bucket
    assert "client.table" not in src  # no PostgREST data path left

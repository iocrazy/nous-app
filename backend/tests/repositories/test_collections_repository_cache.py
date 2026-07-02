"""Smart-collection cache column drift regression.

The `smart_collections` table has a `cached_video_ids` column (ARRAY bigint);
there is no `cached_media_ids` column. update_cache() must write the real
column, and the service must read it back from the same key so the 5-minute
cache fast-path triggers. (Historically the phantom `cached_media_ids` key
500'd at PostgREST and broke the fast-path; the column drift is now
structurally impossible since update_cache targets the mapped ORM attribute.)
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from app.repositories import collections_repository as repo_mod
from app.repositories.collections_repository import CollectionsRepository
from app.services.library.collections_service import CollectionsService


@pytest.mark.asyncio
async def test_update_cache_writes_cached_video_ids_not_phantom(monkeypatch):
    """update_cache() must SET the real cached_video_ids column, never the
    phantom cached_media_ids. The ORM body issues an UPDATE via write_scope();
    we mock the session to capture the emitted statement and its bind params."""
    repo = CollectionsRepository()
    captured: dict = {}

    class _FakeScalars:
        def first(self):
            return None

    class _FakeResult:
        def scalars(self):
            return _FakeScalars()

    class _FakeSession:
        async def execute(self, stmt):
            captured["stmt"] = stmt
            return _FakeResult()

    @asynccontextmanager
    async def _fake_write_scope():
        yield _FakeSession()

    # update_cache imports write_scope at module scope, so patch it there.
    monkeypatch.setattr(repo_mod, "write_scope", _fake_write_scope)

    await repo.update_cache("12345", media_ids=[10, 20, 30], count=3)

    stmt = captured["stmt"]
    sql = str(stmt)
    # The real column is cached_video_ids; the phantom one must be gone.
    assert "cached_video_ids" in sql
    assert "cached_media_ids" not in sql
    # Bind params carry the ARRAY(BigInteger) value + companion cache columns.
    params = stmt.compile().params
    assert params["cached_video_ids"] == [10, 20, 30]
    assert params["cached_count"] == 3
    assert "cached_at" in params


@pytest.mark.asyncio
async def test_service_cache_fastpath_reads_cached_video_ids():
    """A fresh cache keyed on cached_video_ids must short-circuit rule eval."""
    from datetime import datetime

    service = CollectionsService()

    collection = {
        "id": "c1",
        "rules": {},
        "cached_at": datetime.utcnow().isoformat(),
        "cached_video_ids": [101, 202],
        "cached_count": 2,
        "sort_by": "created_at",
        "sort_order": "desc",
    }

    service.repo.get_collection_by_id = AsyncMock(return_value=collection)
    # If the fast-path works, neither rule eval nor cache write should run.
    service._evaluate_rules = AsyncMock(
        side_effect=AssertionError("fast-path missed: rules were re-evaluated")
    )
    service.repo.update_cache = AsyncMock(
        side_effect=AssertionError("fast-path missed: cache was rewritten")
    )
    service._fetch_media_by_ids = AsyncMock(return_value=[{"id": 101}, {"id": 202}])

    media_list, total = await service.get_collection_media(
        "c1", user_id="u1", page=1, page_size=20, use_cache=True
    )

    assert total == 2
    service._fetch_media_by_ids.assert_awaited_once()
    # The IDs handed to the fetch came straight from cached_video_ids.
    page_ids = service._fetch_media_by_ids.call_args.args[0]
    assert page_ids == [101, 202]

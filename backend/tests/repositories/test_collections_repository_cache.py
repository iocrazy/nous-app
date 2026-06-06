"""Smart-collection cache column drift regression.

The `smart_collections` table has a `cached_video_ids` column (ARRAY bigint);
there is no `cached_media_ids` column. update_cache() previously wrote the
phantom key, which 500'd at PostgREST, and the service read it back from the
same phantom key, so the 5-minute cache fast-path never triggered.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.repositories.collections_repository import CollectionsRepository
from app.services.library.collections_service import CollectionsService


@pytest.mark.asyncio
async def test_update_cache_writes_cached_video_ids_not_phantom():
    repo = CollectionsRepository()
    captured = {}

    # Mock the Supabase query builder chain:
    # table.update(payload).eq("id", id).execute()
    builder = MagicMock()

    def _update(payload):
        captured["payload"] = payload
        return builder

    builder.update.side_effect = _update
    builder.eq.return_value = builder
    builder.execute = AsyncMock(return_value=MagicMock(data=[{"id": "c1"}]))

    with patch.object(repo, "_get_table", AsyncMock(return_value=builder)):
        await repo.update_cache("c1", media_ids=[10, 20, 30], count=3)

    payload = captured["payload"]
    # The real column is cached_video_ids; the phantom one must be gone.
    assert "cached_video_ids" in payload
    assert "cached_media_ids" not in payload
    # Value shape matches ARRAY(BigInteger): a list of int ids.
    assert payload["cached_video_ids"] == [10, 20, 30]
    # Companion cache columns preserved.
    assert payload["cached_count"] == 3
    assert "cached_at" in payload


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

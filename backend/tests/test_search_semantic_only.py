"""SearchService.semantic_only: the vector leg alone, for callers that asked
for meaning matches only (the LibrarySearch tool with layers=["semantic"])."""

from __future__ import annotations

from typing import Any

import pytest

from app.services.library.search_service import SearchResult, SearchService

pytestmark = pytest.mark.unit


def _hit(mid: int) -> SearchResult:
    return SearchResult(
        media_id=mid,
        platform_id=f"p{mid}",
        title="t",
        description=None,
        cover_url=None,
        similarity=0.7,
        layer="semantic",
    )


async def test_runs_only_the_vector_leg_and_reports_its_outcome(monkeypatch):
    svc = SearchService()
    seen: dict[str, Any] = {}

    async def _vec(query, user_id, limit, threshold):
        seen.update(query=query, user_id=user_id, limit=limit, threshold=threshold)
        return [_hit(1), _hit(2)], "ok"

    async def _text(**kw):
        raise AssertionError("text leg must not run")

    monkeypatch.setattr(svc, "_vector_hits", _vec)
    monkeypatch.setattr(svc, "search_user_media_text", _text)

    resp = await svc.semantic_only(" rain ", user_id="u-1", limit=2, threshold=0.4)

    assert seen == {"query": "rain", "user_id": "u-1", "limit": 2, "threshold": 0.4}
    assert [r.media_id for r in resp.results] == [1, 2]
    assert resp.vector_leg == "ok"
    assert resp.legs == {"semantic": 2}
    assert resp.search_type == "semantic"


async def test_degraded_leg_is_reported_not_hidden(monkeypatch):
    svc = SearchService()

    async def _vec(query, user_id, limit, threshold):
        return [], "timeout"

    monkeypatch.setattr(svc, "_vector_hits", _vec)
    resp = await svc.semantic_only("rain", user_id="u-1", limit=5)
    assert resp.results == [] and resp.vector_leg == "timeout"


@pytest.mark.parametrize(
    "query, user_id, outcome",
    [("rain", None, "skipped_no_scope"), ("  ", "u-1", "skipped_no_query")],
)
async def test_nothing_to_search(query, user_id, outcome):
    resp = await SearchService().semantic_only(query, user_id=user_id, limit=5)
    assert resp.results == [] and resp.vector_leg == outcome

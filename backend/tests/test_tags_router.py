"""Tests for the tags router promote endpoint + origin field exposure.

Covers Task 1.5 (Unified Tags PR-1):
- PUT /api/v1/tags/{id} accepts ``origin: "curated"`` to promote a shadow
  (origin='note') tag into the curated pool — the only direction the API allows.
- ``origin: "note"`` is rejected at request validation (Pydantic Literal) → 422.
- ``origin`` is surfaced on the tag response so list/get expose it (PR-3 frontend
  consumes it).

Convention: build a FastAPI app around just the tags router, override the auth
dependency, and patch ``get_tags_repository`` with an AsyncMock repo — matching
``test_ai_library_admin_routes.py``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.tags_router import router
from app.core.deps import get_auth

USER_ID = "11111111-1111-1111-1111-111111111111"


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    return app


@pytest.fixture
def client() -> TestClient:
    app = _app()

    async def _auth():
        class _FakeAuth:
            user_id = USER_ID
            email = "u@example.com"

        return _FakeAuth()

    app.dependency_overrides[get_auth] = _auth
    c = TestClient(app)
    yield c
    app.dependency_overrides.clear()


def _user_tag(origin: str = "note") -> dict:
    return {
        "id": 1,
        "name": "ai",
        "type": "user",
        "user_id": USER_ID,
        "created_at": "2026-01-01T00:00:00",
        "origin": origin,
    }


def test_promote_shadow_tag(client: TestClient):
    """PUT with origin='curated' promotes and passes origin through to update_tag."""
    mock_repo = AsyncMock()
    mock_repo.get_tag_by_id.return_value = _user_tag(origin="note")
    mock_repo.update_tag.return_value = _user_tag(origin="curated")

    with patch("app.api.tags_router.get_tags_repository", return_value=mock_repo):
        resp = client.put("/api/v1/tags/1", json={"origin": "curated"})

    assert resp.status_code == 200
    assert mock_repo.update_tag.call_args.kwargs["origin"] == "curated"
    assert resp.json()["origin"] == "curated"


def test_demote_to_note_rejected(client: TestClient):
    """origin='note' is not a settable value — Pydantic Literal rejects it (422)."""
    resp = client.put("/api/v1/tags/1", json={"origin": "note"})
    assert resp.status_code == 422


def test_get_tag_exposes_origin(client: TestClient):
    """GET surfaces the tag's origin so the frontend can badge shadow tags."""
    mock_repo = AsyncMock()
    mock_repo.get_tag_by_id.return_value = _user_tag(origin="note")

    with patch("app.api.tags_router.get_tags_repository", return_value=mock_repo):
        resp = client.get("/api/v1/tags/1")

    assert resp.status_code == 200
    assert resp.json()["origin"] == "note"


def test_list_tags_defaults_origin_curated(client: TestClient):
    """A tag row missing ``origin`` serializes with the curated default."""
    mock_repo = AsyncMock()
    mock_repo.get_all_tags.return_value = [
        {
            "id": 2,
            "name": "design",
            "type": "user",
            "user_id": USER_ID,
            "created_at": "2026-01-01T00:00:00",
        }
    ]

    with patch("app.api.tags_router.get_tags_repository", return_value=mock_repo):
        resp = client.get("/api/v1/tags")

    assert resp.status_code == 200
    assert resp.json()["tags"][0]["origin"] == "curated"


# ── Task 2.1: /tags/statistics cross-domain usage (resources + notes + hotspots) ──


def _patch_statistics_sources(tags_repo, note_repo, hot_repo):
    """Patch the three factories the statistics endpoint fans out to."""
    return (
        patch("app.api.tags_router.get_tags_repository", return_value=tags_repo),
        patch("app.api.tags_router.get_note_tags_repository", return_value=note_repo),
        patch("app.api.tags_router.get_hotspots_repository", return_value=hot_repo),
    )


def test_statistics_cross_domain(client: TestClient):
    """count stays = resources; notes/hotspots decorate each item, hotspot words
    matched by lower(name)/lower(name_zh)."""
    tags_repo = AsyncMock()
    tags_repo.get_tag_counts.return_value = [
        {
            "id": 1,
            "name": "copywriting",
            "name_zh": "文案",
            "color": None,
            "icon": None,
            "type": "user",
            "count": 3,
        },
    ]
    note_repo = AsyncMock()
    note_repo.counts_for_user.return_value = {1: 4}
    hot_repo = AsyncMock()
    hot_repo.recent_tag_word_counts.return_value = {"文案": 2}

    p1, p2, p3 = _patch_statistics_sources(tags_repo, note_repo, hot_repo)
    with p1, p2, p3:
        resp = client.get("/api/v1/tags/statistics")

    assert resp.status_code == 200
    item = resp.json()["top_tags"][0]
    assert (item["count"], item["notes"], item["hotspots"]) == (3, 4, 2)
    # count (resources) semantics unchanged; note counts keyed by tag id.
    note_repo.counts_for_user.assert_awaited_once_with(USER_ID)


def test_statistics_backfills_missing_name_zh(client: TestClient):
    """The RPC/fallback omit name_zh — the router backfills it in one in-list
    query so Chinese hotspot words still match."""
    tags_repo = AsyncMock()
    tags_repo.get_tag_counts.return_value = [
        # No name_zh key — mirrors get_user_tag_counts / fallback shape.
        {
            "id": 1,
            "name": "copywriting",
            "color": None,
            "icon": None,
            "type": "user",
            "count": 3,
        },
    ]
    tags_repo.get_name_zh_map.return_value = {1: "文案"}
    note_repo = AsyncMock()
    note_repo.counts_for_user.return_value = {}
    hot_repo = AsyncMock()
    hot_repo.recent_tag_word_counts.return_value = {"文案": 7}

    p1, p2, p3 = _patch_statistics_sources(tags_repo, note_repo, hot_repo)
    with p1, p2, p3:
        resp = client.get("/api/v1/tags/statistics")

    assert resp.status_code == 200
    item = resp.json()["top_tags"][0]
    # matched via the backfilled name_zh
    assert item["hotspots"] == 7
    tags_repo.get_name_zh_map.assert_awaited_once_with([1])


def test_statistics_no_backfill_when_name_zh_present(client: TestClient):
    """When name_zh is already on every item, no extra query fires (no N+1)."""
    tags_repo = AsyncMock()
    tags_repo.get_tag_counts.return_value = [
        {
            "id": 1,
            "name": "ai",
            "name_zh": None,
            "color": None,
            "icon": None,
            "type": "user",
            "count": 1,
        },
    ]
    note_repo = AsyncMock()
    note_repo.counts_for_user.return_value = {}
    hot_repo = AsyncMock()
    hot_repo.recent_tag_word_counts.return_value = {"ai": 9}

    p1, p2, p3 = _patch_statistics_sources(tags_repo, note_repo, hot_repo)
    with p1, p2, p3:
        resp = client.get("/api/v1/tags/statistics")

    assert resp.status_code == 200
    item = resp.json()["top_tags"][0]
    assert item["hotspots"] == 9
    tags_repo.get_name_zh_map.assert_not_awaited()

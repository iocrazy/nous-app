"""get_analysis_by_resource — the visual-analysis READ endpoint backing the
Task Center's vision result card.

Exercised by calling the endpoint coroutine directly with monkeypatched
resolve + repo (no app / DB), mirroring the soda endpoint test style. Pins:
  - resolves resource -> media and reads analysis by media id
  - 200 + null fields (not 404) when not analyzed yet
  - 403 on ownership mismatch
"""

from __future__ import annotations

import asyncio
import types

import pytest

from app.api import ai_router


def _auth(user_id: str = "u1"):
    return types.SimpleNamespace(user_id=user_id)


def _patch_resolve(monkeypatch, *, creator_id: str = "u1", media_id: int = 555):
    async def _fake_resolve(resource_id: str):
        resource = {"id": resource_id, "creator_id": creator_id, "media_id": media_id}
        media = {"id": media_id, "platform_id": "pf-1"}
        return resource, "pf-1", media

    monkeypatch.setattr(ai_router, "_resolve_resource_to_platform_id", _fake_resolve)


def _patch_repo(monkeypatch, analysis):
    class _FakeRepo:
        async def get_analysis(self, key):
            _FakeRepo.seen_key = key
            return analysis

    monkeypatch.setattr(ai_router, "get_analysis_repository", lambda: _FakeRepo())
    return _FakeRepo


def test_returns_analysis_mapped(monkeypatch):
    _patch_resolve(monkeypatch, media_id=555)
    repo = _patch_repo(
        monkeypatch,
        {
            "analysis_level": "L1",
            "visual_description": "A cat on a sofa",
            "detected_objects": ["cat", "sofa"],
            "detected_scenes": ["living room"],
            "detected_people": [],
            "detected_text": None,
            "analysis_model": "qwen-vl",
            "analysis_cost": 0.3,
            "analyzed_at": "2026-06-02T10:00:00Z",
        },
    )
    resp = asyncio.run(ai_router.get_analysis_by_resource("r-1", _auth()))
    assert resp.media_id == "555"
    assert resp.visual_description == "A cat on a sofa"
    assert resp.detected_objects == ["cat", "sofa"]
    assert resp.analysis_model == "qwen-vl"
    # analysis row is keyed by the linked media id, not the resource id
    assert repo.seen_key == 555


def test_not_analyzed_returns_200_with_nulls(monkeypatch):
    _patch_resolve(monkeypatch, media_id=42)
    _patch_repo(monkeypatch, None)
    resp = asyncio.run(ai_router.get_analysis_by_resource("r-2", _auth()))
    assert resp.media_id == "42"
    assert resp.visual_description is None
    assert resp.detected_objects is None


def test_ownership_mismatch_raises_403(monkeypatch):
    from fastapi import HTTPException

    _patch_resolve(monkeypatch, creator_id="someone-else")
    _patch_repo(monkeypatch, {"visual_description": "secret"})
    with pytest.raises(HTTPException) as exc:
        asyncio.run(ai_router.get_analysis_by_resource("r-3", _auth("u1")))
    assert exc.value.status_code == 403

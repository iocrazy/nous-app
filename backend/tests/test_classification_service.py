"""Unit tests for ClassificationService.auto_tag_media.

Covers the prod-bug fix: ``auto_tag_media`` formerly called the nonexistent
``repo.add_tag_to_media`` (an AttributeError silently swallowed upstream). It now
resolves media_id → resource_id ONCE and calls ``repo.add_tag_to_resource``.
"""

from __future__ import annotations

from typing import Any, List, Optional

import pytest

from app.services.ai.visual.classification_service import ClassificationService


class _FakeTagsRepo:
    """Minimal fake mirroring the tags-repo surface auto_tag_media touches."""

    def __init__(
        self,
        resource_id: Optional[str],
        tags_by_name: dict[str, dict],
    ) -> None:
        self._resource_id = resource_id
        self._tags_by_name = tags_by_name
        self.resolve_calls: List[str] = []
        self.add_resource_calls: List[dict[str, Any]] = []

    async def resolve_media_id_to_resource_id(self, media_id: str) -> Optional[str]:
        self.resolve_calls.append(media_id)
        return self._resource_id

    async def get_tag_by_name(self, name: str) -> Optional[dict]:
        return self._tags_by_name.get(name)

    async def add_tag_to_resource(
        self,
        resource_id: str,
        tag_id: str,
        confidence: Optional[float] = None,
        source: str = "manual",
    ) -> dict:
        self.add_resource_calls.append(
            {
                "resource_id": resource_id,
                "tag_id": tag_id,
                "confidence": confidence,
                "source": source,
            }
        )
        return {"resource_id": resource_id, "tag_id": tag_id}


@pytest.mark.asyncio
async def test_auto_tag_media_uses_resource_id(monkeypatch) -> None:
    """High-confidence single category → resolves once, tags the resource."""
    repo = _FakeTagsRepo(
        resource_id="999",
        tags_by_name={"Food": {"id": "11", "name": "Food"}},
    )
    monkeypatch.setattr(
        "app.services.ai.visual.classification_service.get_tags_repository",
        lambda: repo,
    )

    added = await ClassificationService.auto_tag_media(
        media_id=123,
        title="美食做饭菜谱 cooking recipe kitchen",
    )

    assert len(added) == 1
    assert added[0]["tag"]["name"] == "Food"
    # Resolved exactly once (not per-tag).
    assert repo.resolve_calls == ["123"]
    assert len(repo.add_resource_calls) == 1
    call = repo.add_resource_calls[0]
    assert call["resource_id"] == "999"
    assert call["tag_id"] == "11"
    assert call["source"] == "auto"


@pytest.mark.asyncio
async def test_auto_tag_media_skips_when_no_resource(monkeypatch) -> None:
    """No resource for the media yet → skip tagging, return empty, no tag calls."""
    repo = _FakeTagsRepo(
        resource_id=None,
        tags_by_name={"Food": {"id": "11", "name": "Food"}},
    )
    monkeypatch.setattr(
        "app.services.ai.visual.classification_service.get_tags_repository",
        lambda: repo,
    )

    added = await ClassificationService.auto_tag_media(
        media_id=123,
        title="美食做饭菜谱",
    )

    assert added == []
    assert repo.resolve_calls == ["123"]
    assert repo.add_resource_calls == []

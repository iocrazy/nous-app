"""Unit tests for ReviewService permission + validation logic."""

from __future__ import annotations

from typing import Any, Optional

import pytest

from app.services.review_service import ReviewService


class _FakeRepo:
    def __init__(self) -> None:
        self.comments: dict[str, dict[str, Any]] = {}
        self.replies: dict[str, list[dict[str, Any]]] = {}
        self.annotations: dict[str, list[dict[str, Any]]] = {}
        self.review_statuses: list[dict[str, Any]] = []
        self.created_comments: list[dict[str, Any]] = []
        self.updated_comments: list[tuple[str, dict[str, Any]]] = []
        self.deleted_comments: list[str] = []
        self.upserts: list[dict[str, Any]] = []

    async def create_comment(self, data: dict[str, Any]) -> dict[str, Any]:
        self.created_comments.append(data)
        cid = f"c-{len(self.created_comments)}"
        row = {"id": cid, **data}
        self.comments[cid] = row
        return row

    async def create_annotations_batch(
        self, records: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        return [{"id": f"a-{i}", **r} for i, r in enumerate(records)]

    async def get_comments_by_resource(
        self,
        resource_id: str,
        version_id: Optional[str],
        status: Optional[str],
    ) -> list[dict[str, Any]]:
        return [
            c for c in self.comments.values()
            if c.get("resource_id") == resource_id
        ]

    async def get_replies(self, comment_id: str) -> list[dict[str, Any]]:
        return self.replies.get(comment_id, [])

    async def get_annotations_by_comment(
        self, comment_id: str
    ) -> list[dict[str, Any]]:
        return self.annotations.get(comment_id, [])

    async def get_comment_by_id(
        self, comment_id: str
    ) -> Optional[dict[str, Any]]:
        return self.comments.get(comment_id)

    async def update_comment(
        self, comment_id: str, updates: dict[str, Any]
    ) -> dict[str, Any]:
        self.updated_comments.append((comment_id, updates))
        self.comments[comment_id] = {**self.comments[comment_id], **updates}
        return self.comments[comment_id]

    async def delete_comment(self, comment_id: str) -> bool:
        self.deleted_comments.append(comment_id)
        self.comments.pop(comment_id, None)
        return True

    async def get_comment_count(
        self, resource_id: str, version_id: Optional[str]
    ) -> int:
        return len([
            c for c in self.comments.values()
            if c.get("resource_id") == resource_id
        ])

    async def upsert_review_status(
        self, data: dict[str, Any]
    ) -> dict[str, Any]:
        self.upserts.append(data)
        return {"id": "rs-1", **data}

    async def get_review_statuses(
        self, resource_id: str, version_id: Optional[str]
    ) -> list[dict[str, Any]]:
        return [
            s for s in self.review_statuses
            if s.get("resource_id") == resource_id
        ]


@pytest.fixture
def service() -> tuple[ReviewService, _FakeRepo]:
    svc = ReviewService()
    fake = _FakeRepo()
    svc.repo = fake  # type: ignore[assignment]
    return svc, fake


# ─── create_comment ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_comment_minimal(
    service: tuple[ReviewService, _FakeRepo],
) -> None:
    svc, fake = service
    result = await svc.create_comment(
        resource_id="r-1", author_id="u-1", content="Hi"
    )
    assert result["resource_id"] == "r-1"
    assert result["annotations"] == []


@pytest.mark.asyncio
async def test_create_comment_with_annotations(
    service: tuple[ReviewService, _FakeRepo],
) -> None:
    svc, _ = service
    result = await svc.create_comment(
        resource_id="r-1",
        author_id="u-1",
        content="Draw here",
        annotations=[
            {"tool_type": "rect", "data": {"x": 0, "y": 0}},
            {"tool_type": "arrow", "data": {"start": [0, 0]}},
        ],
    )
    assert len(result["annotations"]) == 2
    assert result["annotations"][0]["tool_type"] == "rect"


@pytest.mark.asyncio
async def test_create_comment_skips_none_optionals(
    service: tuple[ReviewService, _FakeRepo],
) -> None:
    svc, fake = service
    await svc.create_comment(
        resource_id="r-1",
        author_id="u-1",
        content="x",
        version_id=None,
        timecode=None,
        frame_number=None,
    )
    data = fake.created_comments[0]
    assert "version_id" not in data
    assert "timecode" not in data


@pytest.mark.asyncio
async def test_create_comment_preserves_zero_timecode(
    service: tuple[ReviewService, _FakeRepo],
) -> None:
    svc, fake = service
    # 0.0 is a valid timecode — must NOT be dropped as falsy
    await svc.create_comment(
        resource_id="r-1", author_id="u-1", content="x", timecode=0.0
    )
    assert "timecode" in fake.created_comments[0]


# ─── update_comment ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_comment_requires_author(
    service: tuple[ReviewService, _FakeRepo],
) -> None:
    svc, fake = service
    fake.comments["c-1"] = {"id": "c-1", "author_id": "u-owner", "content": "orig"}
    with pytest.raises(PermissionError):
        await svc.update_comment("c-1", "u-other", {"content": "x"})


@pytest.mark.asyncio
async def test_update_comment_raises_when_missing(
    service: tuple[ReviewService, _FakeRepo],
) -> None:
    svc, _ = service
    with pytest.raises(ValueError):
        await svc.update_comment("missing", "u-1", {"content": "x"})


@pytest.mark.asyncio
async def test_update_comment_filters_fields(
    service: tuple[ReviewService, _FakeRepo],
) -> None:
    svc, fake = service
    fake.comments["c-1"] = {"id": "c-1", "author_id": "u-1", "content": "orig"}
    await svc.update_comment(
        "c-1",
        "u-1",
        {"content": "new", "author_id": "u-99", "secret_field": "x"},
    )
    _, updates = fake.updated_comments[0]
    assert updates == {"content": "new"}


@pytest.mark.asyncio
async def test_update_comment_no_allowed_fields_returns_existing(
    service: tuple[ReviewService, _FakeRepo],
) -> None:
    svc, fake = service
    fake.comments["c-1"] = {"id": "c-1", "author_id": "u-1", "content": "orig"}
    result = await svc.update_comment("c-1", "u-1", {"random": "x"})
    assert result["content"] == "orig"
    assert fake.updated_comments == []


# ─── resolve/reopen ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_comment_sets_status_resolved(
    service: tuple[ReviewService, _FakeRepo],
) -> None:
    svc, fake = service
    fake.comments["c-1"] = {"id": "c-1", "author_id": "u-1", "content": "x", "status": "open"}
    await svc.resolve_comment("c-1", "u-other")  # any user
    _, updates = fake.updated_comments[0]
    assert updates == {"status": "resolved"}


@pytest.mark.asyncio
async def test_reopen_comment_sets_status_open(
    service: tuple[ReviewService, _FakeRepo],
) -> None:
    svc, fake = service
    fake.comments["c-1"] = {"id": "c-1", "author_id": "u-1", "content": "x", "status": "resolved"}
    await svc.reopen_comment("c-1", "u-other")
    _, updates = fake.updated_comments[0]
    assert updates == {"status": "open"}


# ─── delete_comment ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_comment_requires_author(
    service: tuple[ReviewService, _FakeRepo],
) -> None:
    svc, fake = service
    fake.comments["c-1"] = {"id": "c-1", "author_id": "u-1", "content": "x"}
    with pytest.raises(PermissionError):
        await svc.delete_comment("c-1", "u-other")


@pytest.mark.asyncio
async def test_delete_comment_by_author_succeeds(
    service: tuple[ReviewService, _FakeRepo],
) -> None:
    svc, fake = service
    fake.comments["c-1"] = {"id": "c-1", "author_id": "u-1", "content": "x"}
    result = await svc.delete_comment("c-1", "u-1")
    assert result is True
    assert fake.deleted_comments == ["c-1"]


# ─── review status ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_review_status_rejects_invalid_status(
    service: tuple[ReviewService, _FakeRepo],
) -> None:
    svc, _ = service
    with pytest.raises(ValueError, match="Invalid status"):
        await svc.set_review_status(
            resource_id="r-1", reviewer_id="u-1", status="bogus"
        )


@pytest.mark.asyncio
async def test_set_review_status_accepts_all_valid_statuses(
    service: tuple[ReviewService, _FakeRepo],
) -> None:
    svc, fake = service
    for status in ("pending", "approved", "needs_changes", "rejected"):
        await svc.set_review_status(
            resource_id="r-1", reviewer_id="u-1", status=status
        )
    assert len(fake.upserts) == 4


@pytest.mark.asyncio
async def test_set_review_status_includes_version_and_comment(
    service: tuple[ReviewService, _FakeRepo],
) -> None:
    svc, fake = service
    await svc.set_review_status(
        resource_id="r-1",
        reviewer_id="u-1",
        status="approved",
        version_id="v-1",
        comment="LGTM",
    )
    data = fake.upserts[0]
    assert data["version_id"] == "v-1"
    assert data["comment"] == "LGTM"

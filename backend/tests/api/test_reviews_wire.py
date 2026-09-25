"""``/api/v1/reviews``: wire parity after the routes gained response models (P7).

Rows are built by the repository's own projection (``_comment_row`` /
``_annotation_row`` / ``_status_row``) over ORM instances carrying every
column (``sample_orm``), so each key and value type is what production
emits. Each route runs over real HTTP and its body must equal
``jsonable_encoder`` of the dict the handler returns
(``tests/api/wire_parity.py``). Who may call them is
``tests/api/test_reviews_access.py``.
"""

from __future__ import annotations

import sys
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import app.api.reviews_access as access
import app.repositories.review_repository as repo_mod
import app.services.library.review_service as review_service
from app.core.deps import AuthContext, get_auth
from app.main import app
from app.models import ReviewAnnotations, ReviewComments, ReviewStatus
from tests.api.wire_parity import assert_wire_unchanged, column_names, sample_orm

r = sys.modules["app.api.reviews_router"]

pytestmark = pytest.mark.unit

USER = "00000000-0000-0000-0000-000000000042"
NULLABLE = {"timecode", "frame_number", "parent_id", "version_id"}


def _ctx() -> AuthContext:
    return AuthContext(user_id=USER, auth_type="jwt")


def _comment(**overrides: Any) -> dict[str, Any]:
    return repo_mod._comment_row(sample_orm(ReviewComments, **overrides))


def _annotation() -> dict[str, Any]:
    return repo_mod._annotation_row(sample_orm(ReviewAnnotations))


def _status(**overrides: Any) -> dict[str, Any]:
    return repo_mod._status_row(sample_orm(ReviewStatus, **overrides))


class _Repo:
    """Hands out fresh copies: the service decorates rows in place."""

    def __init__(self) -> None:
        self.comment = _comment()
        self.updated: dict[str, Any] | None = _comment(status="resolved")
        self.top: list[dict[str, Any]] = [_comment(), _comment(timecode=None)]
        self.replies: list[dict[str, Any]] = [_comment(frame_number=None)]
        self.status = _status()
        self.statuses: list[dict[str, Any]] = [_status(), _status(comment=None)]

    async def get_comment_by_id(self, comment_id):
        return dict(self.comment)

    async def create_comment(self, data):
        return dict(self.comment)

    async def create_annotations_batch(self, rows):
        return [_annotation() for _ in rows]

    async def get_comments_by_resource(self, resource_id, version_id, status):
        return [dict(c) for c in self.top]

    async def get_replies(self, parent_id):
        return [dict(c) for c in self.replies]

    async def get_annotations_by_comment(self, comment_id):
        return [_annotation()]

    async def update_comment(self, comment_id, data):
        return dict(self.updated) if self.updated else None

    async def delete_comment(self, comment_id):
        return True

    async def upsert_review_status(self, data):
        return dict(self.status)

    async def get_review_statuses(self, resource_id, version_id):
        return [dict(s) for s in self.statuses]


@pytest.fixture
def repo(monkeypatch) -> _Repo:
    fake = _Repo()

    async def _fake_auth() -> AuthContext:
        return _ctx()

    async def _allow(*_args: Any) -> bool:
        return True

    async def _same_resource(version_id: int):
        return fake.comment["resource_id"]

    app.dependency_overrides[get_auth] = _fake_auth
    monkeypatch.setattr(access, "get_review_repository", lambda: fake)
    monkeypatch.setattr(review_service, "get_review_repository", lambda: fake)
    monkeypatch.setattr(access, "caller_can_read_resource", _allow)
    monkeypatch.setattr(access, "_version_resource_id", _same_resource)
    # The caller authored it (delete is author-only); as a parent it sits on
    # the same resource.
    fake.comment["author_id"] = USER
    yield fake
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def test_sample_rows_carry_every_column() -> None:
    assert set(_comment()) == column_names(ReviewComments)
    assert set(_annotation()) == column_names(ReviewAnnotations)
    assert set(_status()) == column_names(ReviewStatus)


# ─── comments ──────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("with_annotations", [True, False])
async def test_create_comment(client, repo, with_annotations) -> None:
    rid = str(repo.comment["resource_id"])
    payload: dict[str, Any] = {
        "resource_id": rid,
        "content": "hi",
        "version_id": "11",
        "parent_id": "12",
        "timecode": 1.5,
    }
    if with_annotations:
        payload["annotations"] = [{"tool_type": "rect", "data": {"x": 1}}]
    body = r.CreateReviewCommentRequest(**payload)
    raw = await r.create_comment(body, _ctx())
    assert_wire_unchanged(
        await client.post("/api/v1/reviews/comments", json=payload), raw
    )
    assert bool(raw["data"]["annotations"]) is with_annotations


@pytest.mark.asyncio
async def test_list_comments(client, repo) -> None:
    rid = str(repo.comment["resource_id"])
    raw = await r.list_comments(_ctx(), resource_id=rid, version_id=None, status=None)
    response = await client.get("/api/v1/reviews/comments", params={"resource_id": rid})
    assert_wire_unchanged(response, raw)
    thread = response.json()["data"][0]
    assert set(thread) == column_names(ReviewComments) | {"annotations", "replies"}
    assert set(thread["replies"][0]) == column_names(ReviewComments) | {"annotations"}


@pytest.mark.asyncio
async def test_list_comments_nullable_columns(client, repo) -> None:
    repo.top = [_comment(**{k: None for k in NULLABLE})]
    repo.replies = []
    rid = str(repo.comment["resource_id"])
    raw = await r.list_comments(_ctx(), resource_id=rid, version_id=None, status=None)
    assert_wire_unchanged(
        await client.get("/api/v1/reviews/comments", params={"resource_id": rid}), raw
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("verb", ["resolve", "reopen"])
async def test_resolve_reopen(client, repo, verb) -> None:
    cid = str(repo.comment["id"])
    raw = await getattr(r, f"{verb}_comment")(cid, _ctx())
    assert_wire_unchanged(
        await client.post(f"/api/v1/reviews/comments/{cid}/{verb}"), raw
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("verb", ["resolve", "reopen"])
async def test_resolve_reopen_row_gone_is_typed_404(client, repo, verb) -> None:
    """The row went away between the guard and the write: used to be
    ``200 {"data": null}``."""
    repo.updated = None
    response = await client.post(
        f"/api/v1/reviews/comments/{repo.comment['id']}/{verb}"
    )
    assert response.status_code == 404, response.text
    assert response.json()["details"]["code"] == "not_found_or_out_of_scope"


@pytest.mark.asyncio
async def test_delete_comment(client, repo) -> None:
    cid = str(repo.comment["id"])
    raw = await r.delete_comment(cid, _ctx())
    assert_wire_unchanged(await client.delete(f"/api/v1/reviews/comments/{cid}"), raw)
    assert raw == {"success": True}


# ─── review status ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_review_status(client, repo) -> None:
    payload = {
        "resource_id": str(repo.status["resource_id"]),
        "status": "approved",
        "version_id": "11",
        "comment": "LGTM",
    }
    raw = await r.set_review_status(r.SetReviewStatusRequest(**payload), _ctx())
    assert_wire_unchanged(
        await client.post("/api/v1/reviews/status", json=payload), raw
    )


@pytest.mark.asyncio
async def test_get_review_statuses(client, repo) -> None:
    repo.statuses.append(_status(version_id=None, comment=None))
    rid = str(repo.status["resource_id"])
    raw = await r.get_review_statuses(_ctx(), resource_id=rid, version_id=None)
    assert_wire_unchanged(
        await client.get("/api/v1/reviews/status", params={"resource_id": rid}), raw
    )

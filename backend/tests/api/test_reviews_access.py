"""``/api/v1/reviews``: only resources the caller may read.

Every route used to act on any ``resource_id`` / ``comment_id``: any signed-in
user could list, post, resolve, reopen and read or set the review status on
someone else's resource. Each case below is paired: the same request on a
resource the caller can read goes through, and on one it cannot it is the
typed 404 ``not_found_or_out_of_scope`` with nothing written.

The read rule itself (creator, or member of a filing team) is
``caller_can_read_resource``'s, tested with it; here it is scripted.
"""

from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import app.api.reviews_access as access
import app.services.library.review_service as review_service
from app.core.deps import AuthContext, get_auth
from app.main import app

pytestmark = pytest.mark.unit

USER = "00000000-0000-0000-0000-000000000042"
OTHER = "00000000-0000-0000-0000-000000000099"
MINE = 7_300_000_000_000_000_001
THEIRS = 7_300_000_000_000_000_002
MY_VERSION = 7_300_000_000_000_000_011
THEIR_VERSION = 7_300_000_000_000_000_012
MY_COMMENT = 7_300_000_000_000_000_021
THEIR_COMMENT = 7_300_000_000_000_000_022


def _comment(cid: int, resource_id: int, author: str) -> dict[str, Any]:
    return {
        "id": cid,
        "resource_id": resource_id,
        "author_id": author,
        "content": "x",
        "status": "open",
        "created_at": "2026-09-24T01:02:03.456789+00:00",
        "updated_at": "2026-09-24T01:02:03.456789+00:00",
        "timecode": None,
        "frame_number": None,
        "parent_id": None,
        "version_id": None,
    }


class _Repo:
    def __init__(self) -> None:
        self.comments = {
            MY_COMMENT: _comment(MY_COMMENT, MINE, USER),
            THEIR_COMMENT: _comment(THEIR_COMMENT, THEIRS, USER),
        }
        self.writes: list[str] = []

    async def get_comment_by_id(self, comment_id: str):
        row = self.comments.get(int(comment_id))
        return dict(row) if row else None

    async def create_comment(self, data):
        self.writes.append("create_comment")
        return {**_comment(1, int(data["resource_id"]), data["author_id"])}

    async def create_annotations_batch(self, rows):
        self.writes.append("create_annotations")
        return []

    async def get_comments_by_resource(self, resource_id, version_id, status):
        return []

    async def get_replies(self, parent_id):
        return []

    async def get_annotations_by_comment(self, comment_id):
        return []

    async def update_comment(self, comment_id, data):
        self.writes.append("update_comment")
        return {**self.comments[int(comment_id)], **data}

    async def delete_comment(self, comment_id):
        self.writes.append("delete_comment")
        return True

    async def upsert_review_status(self, data):
        self.writes.append("upsert_review_status")
        return {
            "id": 5,
            "resource_id": int(data["resource_id"]),
            "reviewer_id": data["reviewer_id"],
            "status": data["status"],
            "created_at": "2026-09-24T01:02:03.456789+00:00",
            "updated_at": "2026-09-24T01:02:03.456789+00:00",
            "comment": None,
            "version_id": None,
        }

    async def get_review_statuses(self, resource_id, version_id):
        return []


@pytest.fixture
def repo(monkeypatch) -> _Repo:
    fake = _Repo()

    async def _fake_auth() -> AuthContext:
        return AuthContext(user_id=USER, auth_type="jwt")

    async def _can_read(resource_id: Any, user_id: str | None) -> bool:
        return user_id == USER and str(resource_id) == str(MINE)

    async def _version_resource_id(version_id: int):
        return {MY_VERSION: MINE, THEIR_VERSION: THEIRS}.get(version_id)

    app.dependency_overrides[get_auth] = _fake_auth
    monkeypatch.setattr(access, "get_review_repository", lambda: fake)
    monkeypatch.setattr(review_service, "get_review_repository", lambda: fake)
    monkeypatch.setattr(access, "caller_can_read_resource", _can_read)
    monkeypatch.setattr(access, "_version_resource_id", _version_resource_id)
    yield fake
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _assert_scoped_404(response) -> None:
    assert response.status_code == 404, response.text
    assert response.json()["details"]["code"] == "not_found_or_out_of_scope"


# ─── resource_id routes ────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/v1/reviews/comments", "/api/v1/reviews/status"])
async def test_reads_only_readable_resources(client, repo, path) -> None:
    ok = await client.get(path, params={"resource_id": str(MINE)})
    assert ok.status_code == 200, ok.text
    _assert_scoped_404(await client.get(path, params={"resource_id": str(THEIRS)}))
    # Not a number: used to be a 500 from int().
    _assert_scoped_404(await client.get(path, params={"resource_id": "abc"}))


@pytest.mark.asyncio
async def test_create_comment_only_on_readable_resource(client, repo) -> None:
    body = {"resource_id": str(THEIRS), "content": "hi"}
    _assert_scoped_404(await client.post("/api/v1/reviews/comments", json=body))
    assert repo.writes == []

    body["resource_id"] = str(MINE)
    ok = await client.post("/api/v1/reviews/comments", json=body)
    assert ok.status_code == 200, ok.text
    assert repo.writes == ["create_comment"]


@pytest.mark.asyncio
async def test_create_comment_version_and_parent_must_be_on_resource(
    client, repo
) -> None:
    base = {"resource_id": str(MINE), "content": "hi"}
    for extra in (
        {"version_id": str(THEIR_VERSION)},
        {"version_id": "999"},
        {"parent_id": str(THEIR_COMMENT)},
        {"parent_id": "999"},
    ):
        _assert_scoped_404(
            await client.post("/api/v1/reviews/comments", json={**base, **extra})
        )
    assert repo.writes == []

    ok = await client.post(
        "/api/v1/reviews/comments",
        json={**base, "version_id": str(MY_VERSION), "parent_id": str(MY_COMMENT)},
    )
    assert ok.status_code == 200, ok.text
    assert repo.writes == ["create_comment"]


@pytest.mark.asyncio
async def test_set_status_only_on_readable_resource(client, repo) -> None:
    body = {"resource_id": str(THEIRS), "status": "approved"}
    _assert_scoped_404(await client.post("/api/v1/reviews/status", json=body))
    _assert_scoped_404(
        await client.post(
            "/api/v1/reviews/status",
            json={
                "resource_id": str(MINE),
                "status": "approved",
                "version_id": str(THEIR_VERSION),
            },
        )
    )
    assert repo.writes == []

    ok = await client.post(
        "/api/v1/reviews/status",
        json={
            "resource_id": str(MINE),
            "status": "approved",
            "version_id": str(MY_VERSION),
        },
    )
    assert ok.status_code == 200, ok.text
    assert repo.writes == ["upsert_review_status"]


# ─── comment_id routes ─────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,suffix,write",
    [
        ("POST", "/resolve", "update_comment"),
        ("POST", "/reopen", "update_comment"),
        ("DELETE", "", "delete_comment"),
    ],
)
async def test_comment_routes_only_on_readable_resource(
    client, repo, method, suffix, write
) -> None:
    # THEIR_COMMENT is even authored by the caller: authorship does not
    # outlive losing read access to the resource.
    for cid in (str(THEIR_COMMENT), "999", "abc"):
        _assert_scoped_404(
            await client.request(method, f"/api/v1/reviews/comments/{cid}{suffix}")
        )
    assert repo.writes == []

    ok = await client.request(method, f"/api/v1/reviews/comments/{MY_COMMENT}{suffix}")
    assert ok.status_code == 200, ok.text
    assert repo.writes == [write]

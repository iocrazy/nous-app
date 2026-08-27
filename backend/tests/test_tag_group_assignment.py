"""Tag ↔ group wiring: reorder routing, BIGINT coercion, explicit-null clear.

Three defects reproduced in prod on 2026-08-26 (Settings → Tags modal):

1. ``PUT /tags/groups/reorder`` was declared AFTER ``PUT /tags/groups/{group_id}``
   so Starlette matched the rename route first (``group_id="reorder"``) and the
   request died at body validation → 422 ``["body","name"] missing``. Dragging a
   group to reorder it always snapped back.
2. ``TagsRepository.update_tag`` passed ``group_id`` straight through as ``str``
   into a BIGINT column → asyncpg ``DataError: 'str' object cannot be
   interpreted as an integer`` → 500. Broke BOTH the drag-tag-onto-group path
   and the edit dialog's group selector. ``create_tag`` (and the admin repo)
   already coerced — this one was the outlier.
3. ``update_tag`` dropped every ``None`` value, so an explicit ``group_id: null``
   ("move to Uncategorized") became an empty update: HTTP 200, row unchanged,
   frontend optimistically showing a move that never happened. A silent no-op.

Style follows test_tags_router.py (router + AsyncMock repo) and
test_tag_idempotent_writes.py (boundary-stubbed session, real statements).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.tags_router import router
from app.core.deps import get_auth

USER_ID = "11111111-1111-1111-1111-111111111111"
GROUP_ID = "283891237134919"
TAG_ID = "342748518429419"


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


def _user_tag(**over) -> dict:
    row = {
        "id": int(TAG_ID),
        "name": "Lora",
        "type": "user",
        "user_id": USER_ID,
        "created_at": "2026-01-01T00:00:00",
        "origin": "curated",
    }
    row.update(over)
    return row


# ── 1. route ordering ──────────────────────────────────────────────────────


class _RecordingSession:
    """Collects executed statements; returns nothing (reorder ignores rows)."""

    def __init__(self) -> None:
        self.statements: list = []

    async def execute(self, stmt, *a, **kw):
        self.statements.append(stmt)
        return None


def test_reorder_route_is_not_shadowed_by_rename(client, monkeypatch):
    """PUT /tags/groups/reorder must reach reorder_tag_groups, not the rename
    route with group_id='reorder'."""
    session = _RecordingSession()

    @asynccontextmanager
    async def _scope():
        yield session

    monkeypatch.setattr("app.db.session.write_scope", _scope)

    res = client.put(
        "/api/v1/tags/groups/reorder",
        json={"group_ids": ["283891237134915", "283891237134916"]},
    )

    # The rename route's signature is what produced the prod 422 — a body
    # validation error naming ``name``. Assert on that shape explicitly so a
    # future re-ordering of the decorators fails loudly here.
    assert res.status_code == 200, res.text
    assert res.json() == {"success": True}
    assert len(session.statements) == 2


# ── 2. BIGINT coercion ─────────────────────────────────────────────────────


class _OneRowSession:
    def __init__(self, row) -> None:
        self._row = row
        self.statements: list = []

    async def execute(self, stmt, *a, **kw):
        self.statements.append(stmt)
        return _Result(self._row)

    async def flush(self):
        return None


class _Result:
    def __init__(self, row) -> None:
        self._row = row

    def scalars(self):
        return self

    def first(self):
        return self._row


@pytest.mark.asyncio
async def test_update_tag_coerces_group_id_to_bigint(monkeypatch):
    """A str group_id from JSON must be bound as int — asyncpg rejects str for
    a BIGINT parameter (prod DataError)."""
    from app.models import Tags
    from app.repositories.tags_repository import TagsRepository

    row = Tags(id=int(TAG_ID), name="Lora", type="user", user_id=USER_ID)
    session = _OneRowSession(row)

    @asynccontextmanager
    async def _scope():
        yield session

    monkeypatch.setattr("app.repositories.tags_repository.write_scope", _scope)

    await TagsRepository().update_tag(tag_id=TAG_ID, user_id=USER_ID, group_id=GROUP_ID)

    stmt = session.statements[0]
    bound = stmt.compile().params
    assert bound["group_id"] == int(GROUP_ID)
    assert isinstance(bound["group_id"], int)


@pytest.mark.asyncio
async def test_update_tag_clears_group_on_explicit_null(monkeypatch):
    """group_id=None must WRITE NULL (move to Uncategorized), not be filtered
    out into an empty no-op update."""
    from app.models import Tags
    from app.repositories.tags_repository import TagsRepository

    row = Tags(id=int(TAG_ID), name="Lora", type="user", user_id=USER_ID)
    session = _OneRowSession(row)

    @asynccontextmanager
    async def _scope():
        yield session

    monkeypatch.setattr("app.repositories.tags_repository.write_scope", _scope)

    await TagsRepository().update_tag(tag_id=TAG_ID, user_id=USER_ID, group_id=None)

    assert session.statements, "explicit null was dropped — update never ran"
    bound = session.statements[0].compile().params
    assert bound["group_id"] is None


@pytest.mark.asyncio
async def test_update_tag_still_ignores_untouched_none_fields(monkeypatch):
    """Fields the caller never mentioned stay untouched — only the keys that
    were actually passed reach the UPDATE."""
    from app.models import Tags
    from app.repositories.tags_repository import TagsRepository

    row = Tags(id=int(TAG_ID), name="Lora", type="user", user_id=USER_ID)
    session = _OneRowSession(row)

    @asynccontextmanager
    async def _scope():
        yield session

    monkeypatch.setattr("app.repositories.tags_repository.write_scope", _scope)

    await TagsRepository().update_tag(
        tag_id=TAG_ID, user_id=USER_ID, color="#ff0000", name=None, icon=None
    )

    bound = session.statements[0].compile().params
    assert bound["color"] == "#ff0000"
    assert "name" not in bound
    assert "icon" not in bound


# ── 3. router passes group_id only when the client sent it ─────────────────


def test_put_tag_omitting_group_id_leaves_group_untouched(client):
    """A rename-only PUT must not clear the tag's group."""
    repo = AsyncMock()
    repo.get_tag_by_id.return_value = _user_tag()
    repo.update_tag.return_value = _user_tag(name="Lora2")

    with patch("app.api.tags_router.get_tags_repository", return_value=repo):
        res = client.put(f"/api/v1/tags/{TAG_ID}", json={"name": "Lora2"})

    assert res.status_code == 200, res.text
    assert "group_id" not in repo.update_tag.await_args.kwargs


def test_put_tag_with_explicit_null_group_id_forwards_the_clear(client):
    """Dragging a tag onto "Uncategorized" sends group_id: null — it has to
    reach the repo, not be indistinguishable from "field omitted"."""
    repo = AsyncMock()
    repo.get_tag_by_id.return_value = _user_tag()
    repo.update_tag.return_value = _user_tag(group_id=None)

    with patch("app.api.tags_router.get_tags_repository", return_value=repo):
        res = client.put(f"/api/v1/tags/{TAG_ID}", json={"group_id": None})

    assert res.status_code == 200, res.text
    kwargs = repo.update_tag.await_args.kwargs
    assert "group_id" in kwargs
    assert kwargs["group_id"] is None


def test_put_tag_with_group_id_forwards_it(client):
    repo = AsyncMock()
    repo.get_tag_by_id.return_value = _user_tag()
    repo.update_tag.return_value = _user_tag(group_id=int(GROUP_ID))

    with patch("app.api.tags_router.get_tags_repository", return_value=repo):
        res = client.put(f"/api/v1/tags/{TAG_ID}", json={"group_id": GROUP_ID})

    assert res.status_code == 200, res.text
    assert repo.update_tag.await_args.kwargs["group_id"] == GROUP_ID


# ── 4. create straight into a group (one call, no POST-then-PUT dance) ─────


def test_create_tag_forwards_group_id(client):
    """The extension's "create tag in group X" needs one round trip; the
    POST-then-PUT workaround it used swallowed the PUT's 500 silently."""
    repo = AsyncMock()
    repo.get_tag_by_name.return_value = None
    repo.create_tag.return_value = _user_tag(name="ComfyUI", group_id=int(GROUP_ID))

    with patch("app.api.tags_router.get_tags_repository", return_value=repo):
        res = client.post(
            "/api/v1/tags", json={"name": "ComfyUI", "group_id": GROUP_ID}
        )

    assert res.status_code == 201, res.text
    assert repo.create_tag.await_args.kwargs["group_id"] == GROUP_ID


def test_put_tag_rejects_non_numeric_group_id(client):
    """A garbage group_id must fail validation, not reach asyncpg (where it
    surfaced as an opaque 500)."""
    repo = AsyncMock()
    repo.get_tag_by_id.return_value = _user_tag()

    with patch("app.api.tags_router.get_tags_repository", return_value=repo):
        res = client.put(f"/api/v1/tags/{TAG_ID}", json={"group_id": "reorder"})

    assert res.status_code == 422, res.text
    repo.update_tag.assert_not_awaited()


def test_create_tag_rejects_non_numeric_group_id(client):
    repo = AsyncMock()
    repo.get_tag_by_name.return_value = None

    with patch("app.api.tags_router.get_tags_repository", return_value=repo):
        res = client.post("/api/v1/tags", json={"name": "X", "group_id": "abc"})

    assert res.status_code == 422, res.text
    repo.create_tag.assert_not_awaited()

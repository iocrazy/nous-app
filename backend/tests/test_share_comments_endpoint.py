"""Tests for the share-comments endpoints rebuilt on review_comments.resource_id.

review_comments was dropped+recreated in mig 062 keyed on resource_id (no more
share_id / file_id / timestamp_seconds / visibility). These tests pin the new
query/insert shape (now on the SQLAlchemy ORM session boundary):

- GET resolves the share, then queries review_comments by share.resource_id.
- POST inserts {resource_id, author_id, content, timecode}, letting DB defaults
  fill id/status/created_at.
- A share without a resource_id is rejected (400).
- Anonymous POST is rejected (401) because author_id is NOT NULL.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from app.api.shares_router import (
    ShareCommentCreate,
    create_share_comment,
    get_share_comments,
)

# ─── helpers ────────────────────────────────────────────────────────────


def _make_auth(user_id=None):
    auth = MagicMock()
    auth.user_id = user_id or uuid4()
    return auth


class _Result:
    """Stands in for a SQLAlchemy Result; returns the queued value for
    scalar()/mappings().first()/mappings().all()."""

    def __init__(self, value):
        self._v = value

    def scalar(self):
        return self._v

    def mappings(self):
        return self

    def first(self):
        return self._v

    def all(self):
        return self._v


class _Session:
    def __init__(self, results):
        self._results = list(results)
        self.statements = []

    async def execute(self, stmt, params=None):
        self.statements.append(stmt)
        return _Result(self._results.pop(0) if self._results else None)


def _patch_scopes(monkeypatch, results):
    """Patch read_scope/write_scope to yield one session that returns
    ``results`` in call order. Returns the session for statement assertions."""
    session = _Session(results)

    @asynccontextmanager
    async def _scope():
        yield session

    import app.db.session as dbs

    monkeypatch.setattr(dbs, "read_scope", _scope)
    monkeypatch.setattr(dbs, "write_scope", _scope)
    return session


def _bound(stmt) -> dict:
    return stmt.compile(dialect=postgresql.dialect()).params


# ─── GET ────────────────────────────────────────────────────────────────


async def test_get_resolves_share_then_queries_by_resource_id(monkeypatch):
    share = {"id": 1, "status": "active", "share_type": "review", "resource_id": 99}
    comments = [
        {
            "id": 7,
            "content": "looks good",
            "timecode": 12.5,
            "frame_number": 300,
            "status": "open",
            "author_id": str(uuid4()),
            "parent_id": None,
            "created_at": "2024-01-01T00:00:00+00:00",
        }
    ]
    session = _patch_scopes(monkeypatch, [share, comments])

    result = await get_share_comments("abc123", auth=None)

    assert result["success"] is True
    assert result["data"][0]["timecode"] == 12.5
    # comments queried by the share's resource_id (bound value 99)
    assert 99 in _bound(session.statements[1]).values()


async def test_get_rejects_share_without_resource_id(monkeypatch):
    share = {"id": 1, "status": "active", "share_type": "review", "resource_id": None}
    _patch_scopes(monkeypatch, [share])

    with pytest.raises(HTTPException) as exc:
        await get_share_comments("abc123", auth=None)

    assert exc.value.status_code == 400


async def test_get_404_when_share_missing(monkeypatch):
    _patch_scopes(monkeypatch, [None])

    with pytest.raises(HTTPException) as exc:
        await get_share_comments("nope", auth=None)

    assert exc.value.status_code == 404


# ─── POST ───────────────────────────────────────────────────────────────


async def test_post_inserts_resource_id_author_id_content_timecode(monkeypatch):
    user_id = uuid4()
    share = {"id": 1, "status": "active", "share_type": "review", "resource_id": 99}
    inserted = {"id": 7, "resource_id": 99, "content": "hi"}
    session = _patch_scopes(monkeypatch, [share, inserted])

    body = ShareCommentCreate(content="hi", timecode=5.0)

    result = await create_share_comment("abc123", body, auth=_make_auth(user_id))

    assert result["success"] is True
    # The insert binds exactly the four mig-062 columns. review_comments has no
    # share_id/file_id/timestamp_seconds/visibility columns, so a stray key
    # could not compile — the shape is structurally enforced.
    params = _bound(session.statements[1])
    assert params["resource_id"] == 99
    assert params["author_id"] == user_id
    assert params["content"] == "hi"
    assert params["timecode"] == 5.0


async def test_post_rejects_share_without_resource_id(monkeypatch):
    share = {"id": 1, "status": "active", "share_type": "review", "resource_id": None}
    _patch_scopes(monkeypatch, [share])

    body = ShareCommentCreate(content="hi", timecode=None)

    with pytest.raises(HTTPException) as exc:
        await create_share_comment("abc123", body, auth=_make_auth())

    assert exc.value.status_code == 400


async def test_post_rejects_anonymous(monkeypatch):
    share = {"id": 1, "status": "active", "share_type": "review", "resource_id": 99}
    _patch_scopes(monkeypatch, [share])

    body = ShareCommentCreate(content="hi", timecode=None)

    with pytest.raises(HTTPException) as exc:
        await create_share_comment("abc123", body, auth=None)

    assert exc.value.status_code == 401


def test_schema_drops_visibility():
    """ShareCommentCreate no longer accepts/declares a visibility field."""
    assert "visibility" not in ShareCommentCreate.model_fields

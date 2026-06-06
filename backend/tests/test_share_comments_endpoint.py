"""Tests for the share-comments endpoints rebuilt on review_comments.resource_id.

review_comments was dropped+recreated in mig 062 keyed on resource_id (no more
share_id / file_id / timestamp_seconds / visibility). These tests pin the new
query/insert shape:

- GET resolves the share, then queries review_comments by share.resource_id
  selecting the mig-062 columns (timecode, frame_number, status, parent_id).
- POST inserts {resource_id, author_id, content, timecode}, letting DB defaults
  fill id/status/created_at.
- A share without a resource_id is rejected (400).
- Anonymous POST is rejected (401) because author_id is NOT NULL.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

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


def _builder(data):
    """Fluent builder whose .execute() resolves to a result with .data."""
    b = MagicMock()
    b.select.return_value = b
    b.eq.return_value = b
    b.order.return_value = b
    b.insert.return_value = b
    b.execute = AsyncMock(return_value=MagicMock(data=data))
    return b


def _client(tables: dict):
    """Build a supabase-admin-like client. tables maps table name -> builder."""
    sb = MagicMock()
    sb.table.side_effect = lambda name: tables[name]
    return sb


# ─── GET ────────────────────────────────────────────────────────────────


async def test_get_resolves_share_then_queries_by_resource_id():
    share_builder = _builder(
        [{"id": 1, "status": "active", "share_type": "review", "resource_id": 99}]
    )
    comments_builder = _builder(
        [
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
    )
    sb = _client({"shares": share_builder, "review_comments": comments_builder})

    with patch(
        "app.api.shares_router.get_async_supabase_admin",
        AsyncMock(return_value=sb),
    ):
        result = await get_share_comments("abc123", auth=None)

    # share lookup selects resource_id
    share_builder.select.assert_called_once()
    assert "resource_id" in share_builder.select.call_args.args[0]
    # comments queried by resource_id, mig-062 columns only
    selected = comments_builder.select.call_args.args[0]
    assert "timecode" in selected
    assert "frame_number" in selected
    assert "parent_id" in selected
    assert "timestamp_seconds" not in selected
    assert "visibility" not in selected
    assert "share_id" not in selected
    comments_builder.eq.assert_called_once_with("resource_id", 99)

    assert result["success"] is True
    assert result["data"][0]["timecode"] == 12.5


async def test_get_rejects_share_without_resource_id():
    share_builder = _builder(
        [{"id": 1, "status": "active", "share_type": "review", "resource_id": None}]
    )
    sb = _client({"shares": share_builder})

    with patch(
        "app.api.shares_router.get_async_supabase_admin",
        AsyncMock(return_value=sb),
    ):
        with pytest.raises(HTTPException) as exc:
            await get_share_comments("abc123", auth=None)

    assert exc.value.status_code == 400


async def test_get_404_when_share_missing():
    share_builder = _builder([])
    sb = _client({"shares": share_builder})

    with patch(
        "app.api.shares_router.get_async_supabase_admin",
        AsyncMock(return_value=sb),
    ):
        with pytest.raises(HTTPException) as exc:
            await get_share_comments("nope", auth=None)

    assert exc.value.status_code == 404


# ─── POST ───────────────────────────────────────────────────────────────


async def test_post_inserts_resource_id_author_id_content_timecode():
    user_id = uuid4()
    share_builder = _builder(
        [{"id": 1, "status": "active", "share_type": "review", "resource_id": 99}]
    )
    insert_builder = _builder([{"id": 7, "resource_id": 99, "content": "hi"}])
    sb = _client({"shares": share_builder, "review_comments": insert_builder})

    body = ShareCommentCreate(content="hi", timecode=5.0)

    with patch(
        "app.api.shares_router.get_async_supabase_admin",
        AsyncMock(return_value=sb),
    ):
        result = await create_share_comment("abc123", body, auth=_make_auth(user_id))

    insert_builder.insert.assert_called_once()
    payload = insert_builder.insert.call_args.args[0]
    assert payload == {
        "resource_id": 99,
        "author_id": user_id,
        "content": "hi",
        "timecode": 5.0,
    }
    # no dropped columns leak into the insert
    assert "share_id" not in payload
    assert "file_id" not in payload
    assert "timestamp_seconds" not in payload
    assert "visibility" not in payload
    assert result["success"] is True


async def test_post_rejects_share_without_resource_id():
    share_builder = _builder(
        [{"id": 1, "status": "active", "share_type": "review", "resource_id": None}]
    )
    sb = _client({"shares": share_builder})

    body = ShareCommentCreate(content="hi", timecode=None)

    with patch(
        "app.api.shares_router.get_async_supabase_admin",
        AsyncMock(return_value=sb),
    ):
        with pytest.raises(HTTPException) as exc:
            await create_share_comment("abc123", body, auth=_make_auth())

    assert exc.value.status_code == 400


async def test_post_rejects_anonymous():
    share_builder = _builder(
        [{"id": 1, "status": "active", "share_type": "review", "resource_id": 99}]
    )
    sb = _client({"shares": share_builder})

    body = ShareCommentCreate(content="hi", timecode=None)

    with patch(
        "app.api.shares_router.get_async_supabase_admin",
        AsyncMock(return_value=sb),
    ):
        with pytest.raises(HTTPException) as exc:
            await create_share_comment("abc123", body, auth=None)

    assert exc.value.status_code == 401


def test_schema_drops_visibility():
    """ShareCommentCreate no longer accepts/declares a visibility field."""
    assert "visibility" not in ShareCommentCreate.model_fields

"""Verify resource_ref_resolver: dedup, permission filter, warning emission."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.ai.chat.resource_ref_resolver import resolve_resource_refs


@pytest.mark.asyncio
async def test_dedupes_same_id():
    attachments = [
        {
            "kind": "resource_ref",
            "resource_id": "1",
            "name": "a.md",
            "mime": "text/markdown",
            "scope": {"type": "personal", "id": "u"},
        },
        {
            "kind": "resource_ref",
            "resource_id": "1",
            "name": "a.md",
            "mime": "text/markdown",
            "scope": {"type": "personal", "id": "u"},
        },
    ]
    with patch(
        "app.services.ai.chat.resource_ref_resolver._fetch_accessible_meta",
        return_value={
            "1": {
                "id": "1",
                "name": "a.md",
                "kind": "doc",
                "mime": "text/markdown",
                "size": 100,
                "scope": "personal",
                "updated_at": "2026-05-24T00:00:00Z",
                "brief": None,
            }
        },
    ):
        refs, warnings = await resolve_resource_refs(attachments, user_id="u")

    assert len(refs) == 1
    assert warnings == []


@pytest.mark.asyncio
async def test_inaccessible_becomes_warning_not_ref():
    attachments = [
        {
            "kind": "resource_ref",
            "resource_id": "999",
            "name": "ghost.md",
            "mime": "text/markdown",
            "scope": {"type": "personal", "id": "u"},
        },
    ]
    with patch(
        "app.services.ai.chat.resource_ref_resolver._fetch_accessible_meta",
        return_value={},
    ):
        refs, warnings = await resolve_resource_refs(attachments, user_id="u")

    assert refs == []
    assert len(warnings) == 1
    assert "ghost.md" in warnings[0]


@pytest.mark.asyncio
async def test_skips_non_resource_ref_kinds():
    attachments = [{"kind": "image", "url": "https://example/img.png"}]
    refs, warnings = await resolve_resource_refs(attachments, user_id="u")
    assert refs == []
    assert warnings == []


@pytest.mark.asyncio
async def test_team_scope_renders_team_name():
    """scope should be 'team:<name>' when team_name is present in the row."""
    attachments = [
        {
            "kind": "resource_ref",
            "resource_id": "42",
            "name": "deck.pdf",
            "mime": "application/pdf",
            "scope": {"type": "team", "id": "7"},
        },
    ]
    with patch(
        "app.services.ai.chat.resource_ref_resolver._fetch_accessible_meta",
        return_value={
            "42": {
                "id": "42",
                "name": "deck.pdf",
                "kind": "pdf",
                "mime": "application/pdf",
                "size": 2048,
                "scope": "team:alpha",
                "updated_at": "2026-05-25T00:00:00Z",
                "brief": None,
            }
        },
    ):
        refs, warnings = await resolve_resource_refs(attachments, user_id="u")

    assert warnings == []
    assert len(refs) == 1
    assert refs[0]["scope"] == "team:alpha"


@pytest.mark.asyncio
async def test_team_scope_falls_back_to_scope_id_when_name_missing():
    """scope should fall back to 'team:<scope_id>' when team_name is None."""
    attachments = [
        {
            "kind": "resource_ref",
            "resource_id": "43",
            "name": "notes.md",
            "mime": "text/markdown",
            "scope": {"type": "team", "id": "9"},
        },
    ]
    with patch(
        "app.services.ai.chat.resource_ref_resolver._fetch_accessible_meta",
        return_value={
            "43": {
                "id": "43",
                "name": "notes.md",
                "kind": "doc",
                "mime": "text/markdown",
                "size": 512,
                "scope": "team:9",
                "updated_at": "2026-05-25T00:00:00Z",
                "brief": None,
            }
        },
    ):
        refs, warnings = await resolve_resource_refs(attachments, user_id="u")

    assert warnings == []
    assert len(refs) == 1
    assert refs[0]["scope"] == "team:9"

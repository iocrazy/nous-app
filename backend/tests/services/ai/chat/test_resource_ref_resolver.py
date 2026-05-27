"""Verify resource_ref_resolver: dedup, permission filter, warning emission."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.ai.chat.resource_ref_resolver import resolve_resource_refs


@pytest.mark.asyncio
async def test_dedupes_same_id():
    attachments = [
        {"kind": "resource_ref", "resource_id": "1", "name": "a.md",
         "mime": "text/markdown", "scope": {"type": "personal", "id": "u"}},
        {"kind": "resource_ref", "resource_id": "1", "name": "a.md",
         "mime": "text/markdown", "scope": {"type": "personal", "id": "u"}},
    ]
    with patch(
        "app.services.ai.chat.resource_ref_resolver._fetch_accessible_meta",
        return_value={"1": {"id": "1", "name": "a.md", "kind": "doc",
                            "mime": "text/markdown", "size": 100,
                            "scope": "personal", "updated_at": "2026-05-24T00:00:00Z",
                            "brief": None}},
    ):
        refs, warnings = await resolve_resource_refs(attachments, user_id="u")

    assert len(refs) == 1
    assert warnings == []


@pytest.mark.asyncio
async def test_inaccessible_becomes_warning_not_ref():
    attachments = [
        {"kind": "resource_ref", "resource_id": "999", "name": "ghost.md",
         "mime": "text/markdown", "scope": {"type": "personal", "id": "u"}},
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

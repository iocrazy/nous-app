# backend/tests/test_temp_token_tag_conflict.py
"""The Shortcuts tag-picker dedup must return WHICH tag conflicts, not a bare
409 — so the UI can tell the user the English name collides with an existing
(often system) tag."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


@pytest.mark.asyncio
async def test_create_tag_conflict_returns_structured_detail():
    from app.api.temp_token_router import CreateTagRequest, create_tag_by_token

    existing = {"id": 1, "name": "Healing", "name_zh": "治愈", "type": "system"}
    repo = MagicMock()
    repo.get_tag_by_name = AsyncMock(return_value=existing)

    req = CreateTagRequest(name="Healing", name_zh="康复")

    with patch(
        "app.api.temp_token_router._get_token_data",
        new=AsyncMock(return_value={"scopes": ["tags:write"], "user_id": "u1"}),
    ):
        with patch("app.api.temp_token_router.get_tags_repository", return_value=repo):
            with pytest.raises(HTTPException) as exc:
                await create_tag_by_token("tok", req)

    assert exc.value.status_code == 409
    detail = exc.value.detail
    assert isinstance(detail, dict)
    assert detail["code"] == "tag_name_conflict"
    assert detail["attempted_name"] == "Healing"
    assert detail["conflict"]["name"] == "Healing"
    assert detail["conflict"]["name_zh"] == "治愈"
    assert detail["conflict"]["type"] == "system"
    # human string still present for non-structured consumers
    assert "Healing" in detail["message"]
    repo.create_tag.assert_not_called()

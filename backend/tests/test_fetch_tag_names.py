"""single-fetch tag-name resolution (names → ids, auto-create missing).

Regression: POST /media/fetch accepted `tags` (names) but the single-fetch
path only forwarded `tag_ids`, so name-based tags were silently dropped and
never attached to the downloaded resource. resolve_tag_names_to_ids is the
unit that closes that gap.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.api.media_fetch_helpers import resolve_tag_names_to_ids


@pytest.mark.asyncio
async def test_resolves_existing_and_creates_missing():
    repo = AsyncMock()
    # "news" exists; "ai" does not → must be created
    repo.get_tag_by_name = AsyncMock(
        side_effect=lambda name, uid: {"id": 111} if name == "news" else None
    )
    repo.create_tag = AsyncMock(return_value={"id": 222})

    with patch("app.api.media_fetch_helpers.get_tags_repository", return_value=repo):
        ids = await resolve_tag_names_to_ids(["news", "ai"], "user-1")

    assert ids == ["111", "222"]
    repo.create_tag.assert_awaited_once_with(name="ai", user_id="user-1")


@pytest.mark.asyncio
async def test_skips_blank_names():
    repo = AsyncMock()
    repo.get_tag_by_name = AsyncMock(return_value={"id": 5})
    repo.create_tag = AsyncMock(return_value={"id": 5})
    with patch("app.api.media_fetch_helpers.get_tags_repository", return_value=repo):
        ids = await resolve_tag_names_to_ids(["  ", "", "x"], "u")
    assert ids == ["5"]  # only "x" resolved


@pytest.mark.asyncio
async def test_empty_list_returns_empty():
    with patch(
        "app.api.media_fetch_helpers.get_tags_repository", return_value=AsyncMock()
    ):
        assert await resolve_tag_names_to_ids([], "u") == []

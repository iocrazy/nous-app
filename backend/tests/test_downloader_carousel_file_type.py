"""Task 8 — downloader carousel resource must write a semantic file_type.

``_ensure_carousel_resource`` used to write the raw platform ``media_type``
code (0/4/68/2/51/55...) straight into ``resources.file_type``. The column is
a semantic enum (video/image/audio/document/gallery) — the platform code is
meaningless there. This locks in the fix: file_type must be derived from
mime_type via ``_file_type_from_mime``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.media.downloader.downloader import (
    DownloaderService,
    _file_type_from_mime,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    "mime, expected",
    [
        ("video/mp4", "video"),
        ("image/jpeg", "image"),
        ("audio/mp4", "audio"),
        ("application/pdf", "document"),
        (None, "document"),
        ("", "document"),
        ("VIDEO/MP4", "video"),  # case-insensitive
    ],
)
def test_file_type_from_mime(mime, expected):
    assert _file_type_from_mime(mime) == expected


@pytest.mark.unit
async def test_ensure_carousel_resource_writes_semantic_file_type():
    """Regression: platform media_type codes (e.g. "68") must never land in
    resources.file_type — it must be derived from mime_type instead."""

    captured = {}

    async def _fake_create_resource(data):
        captured.update(data)
        return {"id": "resource-1"}

    fake_repo = AsyncMock()
    fake_repo.get_resource_by_media_id_and_creator.return_value = None
    fake_repo.create_resource.side_effect = _fake_create_resource
    fake_repo.create_resource_item.return_value = {"id": "item-1"}

    with (
        patch(
            "app.repositories.resources_repository.ResourcesRepository",
            return_value=fake_repo,
        ),
        patch(
            "app.services.library.resources_service._resolve_personal_team_id",
            new=AsyncMock(return_value="team-1"),
        ),
    ):
        await DownloaderService._ensure_carousel_resource(
            media_id="media-1",
            user_id="user-1",
            platform_id="platform-1",
            resource_dir_relative="media-1/slides",
            # Platform numeric code "68" (image collection) — must NOT end
            # up verbatim in file_type.
            video_data={"media_type": "68"},
        )

    assert captured["file_type"] == "image"
    assert captured["file_type"] != "68"

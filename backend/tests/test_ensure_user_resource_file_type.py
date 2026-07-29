"""Task 8 — MediaService._ensure_user_resource must write a semantic file_type.

This is the DOMINANT resource-creation path (parse_workflow -> save_media_step
-> MediaService.save_metadata_only -> _ensure_user_resource, plus a direct call
from media_fetch_router). It used to write the raw platform ``media_type``
code (0/4/68/2/51/55...) straight into ``resources.file_type`` — the same bug
pattern as the carousel path in downloader.py, but far more frequently hit
(explains the ~796 bad rows in production vs. the carousel path's 1).

Regression guard: qishui/soda parsers already emit a SEMANTIC string
directly in ``parsed_data["media_type"]`` — "audio" for standalone tracks,
"video" for UGC clips (see app/services/media/parsers/soda_music/formatter.py
and ugc_formatter.py). Those must pass through unchanged: naively deriving
from the existing 2-value mime_type calc (image/jpeg vs video/mp4, no audio
branch) would mis-classify "audio" as "video", which is a REGRESSION versus
the pre-fix behaviour (where "audio" happened to already be a valid
semantic value and was written as-is).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.media.parsers.media_service import MediaService, _file_type_from_mime


@pytest.mark.unit
@pytest.mark.parametrize(
    "mime, expected",
    [
        ("video/mp4", "video"),
        ("image/jpeg", "image"),
        ("audio/mp4", "audio"),
        ("application/pdf", "document"),
        (None, "document"),
    ],
)
def test_file_type_from_mime(mime, expected):
    assert _file_type_from_mime(mime) == expected


def _make_repo(captured: dict) -> AsyncMock:
    async def _fake_create_resource(data):
        captured.update(data)
        return {"id": "resource-1"}

    repo = AsyncMock()
    repo.get_resource_by_media_id_and_creator.return_value = None
    repo.create_resource.side_effect = _fake_create_resource
    return repo


@pytest.mark.unit
@pytest.mark.parametrize(
    "media_type, is_image_type, expected_file_type",
    [
        # Douyin-style numeric platform codes — the bug this task fixes.
        ("0", False, "video"),
        ("4", False, "video"),
        ("68", True, "image"),
        ("2", True, "image"),
        ("51", False, "video"),
        # qishui/soda already emit semantic strings — must pass through
        # unchanged (regression guard, see module docstring).
        ("audio", False, "audio"),
        ("video", False, "video"),
    ],
)
async def test_ensure_user_resource_writes_semantic_file_type(
    media_type, is_image_type, expected_file_type
):
    captured: dict = {}
    repo = _make_repo(captured)

    resource_id = await MediaService._ensure_user_resource(
        resources_repo=repo,
        media_id="media-1",
        user_id="user-1",
        parsed_data={"media_type": media_type, "title": "Test Title"},
        need_download_video=True,
        need_download_music=False,
        need_download_cover=True,
        is_image_type=is_image_type,
        dedup_hit=False,
        existing_media=None,
    )

    assert resource_id == "resource-1"
    assert captured["file_type"] == expected_file_type
    # Numeric platform codes must never leak into file_type verbatim.
    if media_type not in ("video", "image", "audio", "document"):
        assert captured["file_type"] != media_type

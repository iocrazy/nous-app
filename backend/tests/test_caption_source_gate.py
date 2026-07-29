"""Which resources can be reverse-prompted, and from which file.

The gate widened from "images only" to "images + videos (via their cover
still)", with albums routed to the per-slide endpoint instead. Three things
are easy to get wrong and all three are pinned here:

  - the discriminator is ``mime_type``, NOT ``file_type``: downloaded rows
    carry the raw platform type code ('0', '4', '68', …), so a
    ``file_type == 'video'`` gate would miss ~87% of the video library
  - an album must NOT fall into the image branch — its ``file_path`` is a
    directory, so captioning it would hand a directory to the encoder
  - ``_image_gate_reason`` stays strict, because the LoRA training-set
    export and classify still depend on images only
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.api.resources_ai_router import _gate_reason, _image_gate_reason
from app.services.ai.caption_source import (
    KIND_ALBUM,
    KIND_IMAGE,
    KIND_UNSUPPORTED,
    KIND_VIDEO,
    caption_gate_reason,
    caption_kind,
    resolve_caption_source,
)

pytestmark = pytest.mark.asyncio


def _upload_image() -> dict:
    return {
        "id": "1",
        "file_type": "image",
        "mime_type": "image/png",
        "media_id": None,
        "file_path": "sb://library/t1/ab/cd/x.png",
    }


def _downloaded_video(**over) -> dict:
    # file_type '0' is douyin's aweme_type for a plain video — the shape 581
    # of the library's rows actually have.
    row = {
        "id": "2",
        "file_type": "0",
        "mime_type": "video/mp4",
        "media_id": "77",
        "file_path": "global/resources/web/douyin/77/video.mp4",
        "cover_image_path": "global/resources/web/douyin/77/cover.jpg",
        "thumbnail_path": "global/resources/web/douyin/77/thumbnail.webp",
    }
    row.update(over)
    return row


def _album() -> dict:
    # file_type '68' is douyin's 图文 carousel; file_path is the DIRECTORY.
    return {
        "id": "3",
        "file_type": "68",
        "mime_type": "image/jpeg",
        "media_id": "88",
        "file_path": "global/resources/web/douyin/88",
    }


# ─── classification ─────────────────────────────────────────────────────


async def test_kind_classifies_by_mime_not_file_type() -> None:
    assert caption_kind(_upload_image()) == KIND_IMAGE
    assert caption_kind(_downloaded_video()) == KIND_VIDEO
    assert caption_kind(_album()) == KIND_ALBUM
    assert caption_kind({"file_type": "audio", "mime_type": "audio/mp4"}) == (
        KIND_UNSUPPORTED
    )
    # '2' is used for BOTH an image album and a video in production — the mime
    # is what separates them.
    assert caption_kind({"file_type": "2", "mime_type": "video/mp4"}) == KIND_VIDEO
    assert (
        caption_kind({"file_type": "2", "mime_type": "image/jpeg", "media_id": "9"})
        == KIND_ALBUM
    )


async def test_kind_falls_back_to_file_type_when_mime_is_missing() -> None:
    assert caption_kind({"file_type": "video", "mime_type": None}) == KIND_VIDEO
    assert caption_kind({"file_type": "image", "mime_type": ""}) == KIND_IMAGE


# ─── source resolution ──────────────────────────────────────────────────


def _exists(*present: str):
    return patch(
        "app.services.ai.caption_source._stored_file_exists",
        AsyncMock(side_effect=lambda rel: rel in present),
    )


async def test_image_captions_its_own_file() -> None:
    assert await resolve_caption_source(_upload_image()) == (
        "sb://library/t1/ab/cd/x.png"
    )


async def test_video_prefers_the_full_cover_over_the_thumbnail() -> None:
    row = _downloaded_video()
    with _exists(row["cover_image_path"], row["thumbnail_path"]):
        assert await resolve_caption_source(row) == row["cover_image_path"]


async def test_video_falls_back_to_the_thumbnail_then_parsed_media() -> None:
    row = _downloaded_video()
    with _exists(row["thumbnail_path"]):
        assert await resolve_caption_source(row) == row["thumbnail_path"]

    row = _downloaded_video(cover_image_path=None, thumbnail_path=None)
    pm_cover = "global/resources/web/bilibili/77/cover.jpg"
    with (
        _exists(pm_cover),
        patch(
            "app.services.ai.caption_source._parsed_media_cover",
            AsyncMock(return_value=pm_cover),
        ),
    ):
        assert await resolve_caption_source(row) == pm_cover


async def test_video_skips_remote_cover_urls() -> None:
    # A cover that was never downloaded is an http URL; handing that to the
    # image encoder as a path is not a fallback, it's a failure.
    row = _downloaded_video(
        cover_image_path="https://p3.douyinpic.com/cover.jpeg",
        thumbnail_path=None,
    )
    with (
        _exists(),
        patch(
            "app.services.ai.caption_source._parsed_media_cover",
            AsyncMock(return_value=None),
        ),
    ):
        assert await resolve_caption_source(row) is None


async def test_album_has_no_whole_resource_source() -> None:
    assert await resolve_caption_source(_album()) is None


# ─── gate copy ──────────────────────────────────────────────────────────


async def test_gate_lets_images_and_videos_with_a_cover_through() -> None:
    assert await caption_gate_reason(_upload_image()) is None
    row = _downloaded_video()
    with _exists(row["cover_image_path"]):
        assert await caption_gate_reason(row) is None


async def test_gate_distinguishes_a_coverless_video_from_an_unsupported_type() -> None:
    coverless = _downloaded_video(cover_image_path=None, thumbnail_path=None)
    with (
        _exists(),
        patch(
            "app.services.ai.caption_source._parsed_media_cover",
            AsyncMock(return_value=None),
        ),
    ):
        video_reason = await caption_gate_reason(coverless)
    assert video_reason and "cover" in video_reason.lower()

    audio_reason = await caption_gate_reason(
        {"file_type": "audio", "mime_type": "audio/mpeg"}
    )
    assert audio_reason and "cover" not in audio_reason.lower()
    assert video_reason != audio_reason


async def test_gate_points_albums_at_the_per_slide_flow() -> None:
    reason = await caption_gate_reason(_album())
    assert reason and "slide" in reason.lower()


async def test_gate_reports_a_missing_resource() -> None:
    assert await caption_gate_reason(None) == "Resource not found"


# ─── the strict gate is unchanged for its other callers ─────────────────


async def test_strict_image_gate_still_rejects_video() -> None:
    # classify + the LoRA training-set export share this one; a zip of video
    # covers is not a training set.
    assert _image_gate_reason(_downloaded_video()) is not None
    assert _image_gate_reason(_upload_image()) is None


async def test_operation_gate_routes_caption_and_classify_differently() -> None:
    row = _downloaded_video()
    with _exists(row["cover_image_path"]):
        assert await _gate_reason("caption", row) is None
    assert await _gate_reason("classify", row) is not None

"""The ``parsed_media`` row as ``MediaRepository`` hands it to the routers.

Every full-row read of ``MediaRepository`` goes through ``_orm_obj_to_dict``
(``app/repositories/_orm_helpers.py``), which fixes the value types:

- **the Snowflake id is a JSON number** (``int``): nothing stringifies it.
- **timestamps are native datetimes**, so FastAPI's ``jsonable_encoder``
  wrote ``isoformat()`` (``+00:00``). Declared :data:`WireDatetime` to keep
  that string rather than Pydantic's ``Z``.
- the ``download_status`` enum columns are unwrapped to their bare string.
- the JSONB url columns hold lists in practice (``'[]'::jsonb`` default) but
  the column has no CHECK, so they stay open (``Any``).

Every field is required (nullable columns are ``X | None`` without a
default): each row carries every column. The wire tests pin the field set to
the ORM column set, so a column added to the table fails there until it is
declared here.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.schemas.wire import WireDatetime


class ParsedMediaRow(BaseModel):
    """One ``parsed_media`` row (``SELECT *`` shape)."""

    id: int
    platform_id: str
    original_url: str
    canonical_url: str | None
    source_platform: str
    extract_audio_status: str
    download_retry_count: int
    like_count: int | None
    comment_count: int | None
    share_count: int | None
    favorite_count: int | None
    duration: str | None
    resolution: str | None
    datasize: str | None
    hashtags: str | None
    published_at: WireDatetime | None
    author: str | None
    title: str | None
    media_type: str | None
    description: str | None
    video_download_urls: Any
    image_download_urls: Any
    music_name: str | None
    video_download_status: str | None
    music_download_status: str | None
    download_duration: float | None
    download_path: str | None
    error_message: str | None
    download_time: WireDatetime | None
    created_at: WireDatetime | None
    updated_at: WireDatetime | None
    cover_urls: Any
    dynamic_cover_url: str | None
    cover_download_status: str | None
    cover_download_path: str | None
    ai_extract_text: str | None
    ai_rewrite_text: str | None
    ai_analyze_text: str | None
    ai_generated_at: WireDatetime | None
    view_count: int | None
    last_viewed_at: WireDatetime | None
    storage_size: int | None
    keep_forever: bool | None
    datasize_bytes: int | None
    hls_path: str | None
    media_format: str | None
    image_download_status: str | None
    image_download_path: str | None
    music_download_path: str | None
    music_play_urls: Any
    extract_audio_path: str | None
    metadata: dict[str, Any] | None

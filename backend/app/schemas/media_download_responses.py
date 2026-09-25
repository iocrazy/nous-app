"""Response shapes of the media download / slides / audio / lyrics routes.

``media_download_router`` (``/media/pending``, ``/media/retry/{platform_id}``)
and ``media_slides_router`` (``/media/{media_id}/slides``, ``/lyrics``,
``/lyrics/fetch``). The file-serving routes of both routers return bytes and
are declared with :func:`app.schemas.wire.binary_response` instead.

These declare what the routes already sent; ``tests/api/test_media_download_wire.py``
and ``tests/api/test_media_slides_wire.py`` compare each body with
``jsonable_encoder`` of the dict the handler builds.
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict

from app.schemas.parsed_media_row import ParsedMediaRow


class PendingDownloadsResponse(BaseModel):
    """``GET /media/pending`` — the caller's media still waiting to download."""

    success: bool = True
    count: int
    videos: List[ParsedMediaRow]


class RetryDownloadResponse(BaseModel):
    """``POST /media/retry/{platform_id}``.

    ``task_id`` is the ``task_tracking`` id of the new download, ``None`` when
    nothing was dispatched (every requested type was already running and the
    retry subscribed to it, or neither type was requested), or the literal
    ``"background"`` when DBOS dispatch failed and the download fell back to a
    FastAPI background task.
    """

    success: bool = True
    message: str
    task_id: str | None


class MediaSlide(BaseModel):
    """One slide of a carousel / image-text post."""

    name: str
    type: Literal["image", "video"]
    media_type: str
    url: str


class MediaSlidesResponse(BaseModel):
    """``GET /media/{media_id}/slides``."""

    slides: List[MediaSlide]
    count: int


class MediaLyricToken(BaseModel):
    """One timed word of a lyric line (``soda_music.lyrics._parse_tokens``).

    Every key is optional and unknown keys pass through: this is stored jsonb
    read back, and a hand-edited historical row must still read (same ruling
    as ``resources.lyrics_json``). The routes use ``response_model_exclude_unset``
    so a key the row lacks stays absent instead of coming back as ``null``.
    """

    model_config = ConfigDict(extra="allow")

    text: Optional[str] = None
    offset_ms: Optional[int] = None
    duration_ms: Optional[int] = None
    flag: Optional[int] = None
    start_ms: Optional[int] = None
    end_ms: Optional[int] = None


class MediaLyricLine(BaseModel):
    """One lyric line (``soda_music.lyrics.parse_timed_lyrics``).

    The one writer (``lyrics_payload_from_track``) stores all six keys, but the
    read side stays lenient — see :class:`MediaLyricToken`.
    """

    model_config = ConfigDict(extra="allow")

    line_start_ms: Optional[int] = None
    line_duration_ms: Optional[int] = None
    line_end_ms: Optional[int] = None
    text: Optional[str] = None
    tokens: Optional[List[MediaLyricToken]] = None
    raw: Optional[str] = None


class MediaLyricsResponse(BaseModel):
    """``GET /media/{media_id}/lyrics`` and ``POST .../lyrics/fetch``.

    Both empty (``{"lrc": "", "lines": []}``) when the track has no lyrics;
    never null (``extract_lyrics`` normalises a stored null to the empty value).
    """

    lrc: str
    lines: List[MediaLyricLine]

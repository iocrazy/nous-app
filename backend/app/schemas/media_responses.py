"""Response shapes of the ``/media`` CRUD, fetch, batch and soda-download routes.

Each model declares what the handler already sent as a bare dict (spec
``docs/superpowers/specs/2026-09-24-openapi-typed-frontend-design.md``; the
wire tests are ``tests/api/test_media_*_wire.py``). Value types follow the
repositories:

- ``MediaRepository`` card and full-row reads keep native values, so the
  Snowflake ids are JSON numbers and timestamps are :data:`WireDatetime`
  (``isoformat()``, ``+00:00``).
- ``user_logs`` rows are already ISO strings (``_log_to_dict``) and their id
  is a native bigint (a JSON number).
- the fetch / batch payloads are built from parser output and task-manager
  ids, all plain strings.

Several routes answer with one of a few shapes. Those are unions whose
members cannot validate each other's dicts (a ``Literal`` or a required key
tells them apart), so Pydantic never reshapes one into another.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.parsed_media_row import ParsedMediaRow
from app.schemas.wire import WireDatetime

# ── /media CRUD (media_router) ───────────────────────────────────────────


class MediaCleanupStaleResponse(BaseModel):
    """``POST /media/cleanup-stale-downloads``."""

    success: bool = True
    cleaned: int


class MediaCard(BaseModel):
    """One library card: ``MediaRepository.CARD_SELECT`` columns plus the two
    per-user overlays ``resource_id`` and ``has_prompt`` (``_card_row``)."""

    id: int
    platform_id: str
    source_platform: str
    title: str | None
    author: str | None
    description: str | None
    original_url: str
    cover_urls: Any
    dynamic_cover_url: str | None
    cover_download_status: str | None
    cover_download_path: str | None
    like_count: int | None
    comment_count: int | None
    share_count: int | None
    favorite_count: int | None
    view_count: int | None
    extract_audio_path: str | None
    music_download_path: str | None
    music_download_status: str | None
    music_name: str | None
    music_play_urls: Any
    video_download_status: str | None
    video_download_urls: Any
    image_download_status: str | None
    image_download_urls: Any
    image_download_path: str | None
    hashtags: str | None
    error_message: str | None
    created_at: WireDatetime | None
    updated_at: WireDatetime | None
    published_at: WireDatetime | None
    last_viewed_at: WireDatetime | None
    media_type: str | None
    media_format: str | None
    duration: str | None
    resolution: str | None
    datasize: str | None
    datasize_bytes: int | None
    storage_size: int | None
    keep_forever: bool | None
    hls_path: str | None
    download_path: str | None
    download_time: WireDatetime | None
    download_duration: float | None
    resource_id: int
    has_prompt: bool


class MediaCardListResponse(BaseModel):
    """``GET /media`` and ``POST /media/search``."""

    success: bool = True
    count: int
    videos: list[MediaCard]


class MediaDetail(ParsedMediaRow):
    """``GET /media/{platform_id}``: the full row, plus the caller's
    ``resource_id`` when they have one on this media (absent otherwise — the
    route sets ``response_model_exclude_unset``)."""

    resource_id: int | None = None


class MediaDetailResponse(BaseModel):
    success: bool = True
    video: MediaDetail


class MediaDeleteResponse(BaseModel):
    """``DELETE /media/{platform_id}``."""

    success: bool = True
    message: str
    files_deleted: list[str]


class MediaStatistics(BaseModel):
    total: int
    pending: int
    completed: int
    failed: int
    skipped: int
    total_storage_bytes: int
    unique_authors: int


class MediaStatisticsResponse(BaseModel):
    """``GET /media/statistics``."""

    success: bool = True
    statistics: MediaStatistics


class MediaUserLog(BaseModel):
    """One ``user_logs`` row (``_log_to_dict``: ISO-string timestamps)."""

    id: int
    user_id: str
    action: str
    message: str
    status: str | None
    aweme_id: str | None
    details: Any
    created_at: str | None


class MediaUserLogsResponse(BaseModel):
    """``GET /media/logs``: one page of the caller's action log."""

    success: bool = True
    logs: list[MediaUserLog]
    total: int
    page: int
    page_size: int
    total_pages: int


# ── POST /media/fetch (media_fetch_router → handle_media_fetch_dispatch) ──


class _FetchBase(BaseModel):
    # ``async`` is a Python keyword; the wire key is still ``async``.
    model_config = ConfigDict(populate_by_name=True)

    success: bool = True
    message: str


class MediaFetchOwnedResponse(_FetchBase):
    """The caller already has this URL in their library; nothing was queued."""

    is_async: Literal[False] = Field(alias="async")
    dedup_action: Literal["already_owned"]
    resource_id: str
    media_id: str


class MediaFetchDedupResponse(_FetchBase):
    """A parse of this URL is already running or just finished; no new task."""

    is_async: Literal[True] = Field(alias="async")
    dedup_action: Literal["subscribed", "completed"]


class MediaFetchSubmittedResponse(_FetchBase):
    """A parse workflow was queued; ``task_id`` is its task_tracking id."""

    is_async: Literal[True] = Field(alias="async")
    task_id: str


MediaFetchResponse = (
    MediaFetchOwnedResponse | MediaFetchDedupResponse | MediaFetchSubmittedResponse
)


# ── POST /media/fetch/batch (media_batch_router) ─────────────────────────


class MediaBatchParsedData(BaseModel):
    """The parse summary of one submitted batch URL (douyin formatter)."""

    platform_id: str | None
    title: str | None
    description: str | None
    author: str | None
    media_type: str | None
    video_download_urls: list[Any] | None
    cover_urls: list[Any] | None
    like_count: int | None
    comment_count: int | None
    share_count: int | None
    favorite_count: int | None
    duration: str | None
    published_at: str | None
    image_urls: list[Any] | None
    sec_uid: str | None
    unique_id: str | None
    valid_url: str
    user_id: str


class MediaBatchResult(BaseModel):
    url: str
    platform_id: str | None
    status: Literal["submitted"]
    data: MediaBatchParsedData


class MediaBatchError(BaseModel):
    url: str
    error: str


class MediaBatchFetchResponse(BaseModel):
    """Inline batch: every URL parsed in the request."""

    success: bool = True
    total: int
    submitted: int
    failed: int
    results: list[MediaBatchResult]
    errors: list[MediaBatchError]


class MediaBatchDispatchedResponse(BaseModel):
    """``use_celery=true``: one parse workflow queued per URL."""

    success: bool = True
    message: str
    task_id: str
    workflow_ids: list[str]
    total: int
    use_celery: Literal[True]


MediaBatchResponse = MediaBatchFetchResponse | MediaBatchDispatchedResponse


# ── per-media actions (media_fetch_router) ───────────────────────────────


class MediaTypeFetchResponse(BaseModel):
    """``POST /media/{platform_id}/fetch``."""

    success: bool = True
    message: str
    already_in_library: bool
    platform_id: str
    task_id: str | None
    types_submitted: list[str]
    types_skipped: list[str]
    types_subscribed: list[str]


class MediaExtractAudioResponse(BaseModel):
    """``POST /media/{platform_id}/extract-audio``."""

    success: bool = True
    message: str
    platform_id: str
    task_id: str


# ── POST /media/soda/playlist/download (media_soda_router) ───────────────


class MediaSodaDownloadResponse(BaseModel):
    """``success`` is false when no track could be dispatched; ``flow_id`` is
    null when the task flow row could not be created (best effort)."""

    success: bool
    flow_id: str | None
    submitted: int
    total: int

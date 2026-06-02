# backend/app/api/media_soda_router.py

"""
Soda (汽水音乐 / qishui) playlist REST endpoints.

Mounted under the ``/media`` prefix via ``media_router.py``, so the final
paths are:

- ``POST /api/v1/media/soda/playlist``          — resolve a link → track list
- ``POST /api/v1/media/soda/playlist/download`` — batch-download selected tracks

The single-track parse+download pipeline is reused as the batch primitive: each
selected track is dispatched as a ``parse_workflow`` with a
``share/track?track_id=<id>`` URL (``platform="qishui"``), grouped under one
task flow. No new download path is introduced here.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel

from app.api.media_fetch_helpers import resolve_team_id
from app.core.deps import AuthDep
from app.repositories.resources_repository import get_resources_repository
from app.services.billing.points_service import PointsService
from app.services.infra.unified_task_manager import get_task_manager
from app.services.media.parsers.soda_music.cookie_source import get_soda_cookie
from app.services.media.parsers.soda_music.soda_api import (
    SodaApiClient,
    SodaApiError,
    classify_landing_url,
)
from app.workflows.parse import enqueue_parse_for_user

router = APIRouter()

TAGS_SODA = ["Soda Music"]

# Cap on a single batch dispatch. Larger playlists are truncated (with a
# logged warning — never a silent cap) to protect the task queue.
MAX_BATCH = 210


# ============================================
# Request / Response models
# ============================================


class SodaPlaylistRequest(BaseModel):
    """Resolve a Soda playlist link into its track list."""

    url: str


class SodaTrackSummary(BaseModel):
    """One item (music track or UGC video) inside a playlist."""

    track_id: str
    title: Optional[str] = None
    artist: Optional[str] = None
    cover_url: Optional[str] = None
    duration_ms: Optional[int] = None
    kind: str = "track"  # "track" | "video"
    downloaded: bool = False  # this user already downloaded this vid


class SodaPlaylistResponse(BaseModel):
    """The resolved playlist id plus its flattened track list.

    ``downloaded_count`` / ``new_count`` let the client default-select only
    the tracks the user has not downloaded yet (incremental sync)."""

    playlist_id: str
    tracks: list[SodaTrackSummary]
    total: int
    downloaded_count: int = 0
    new_count: int = 0


class SodaDownloadItem(BaseModel):
    """One selected playlist item to download (track or UGC video)."""

    id: str
    kind: str = "track"  # "track" | "video"


class SodaBatchDownloadRequest(BaseModel):
    """Batch-download a set of selected playlist items.

    ``items`` carries per-item ``kind`` and is preferred. ``track_ids`` is kept
    for backward compatibility — when only it is given, every id is treated as a
    music track.
    """

    items: Optional[list[SodaDownloadItem]] = None
    track_ids: Optional[list[str]] = None
    playlist_title: Optional[str] = None


# ============================================
# Pure helpers
# ============================================


async def resolve_playlist_id(url: str, client: Any) -> str | None:
    """Resolve a qishui URL to a playlist id, or ``None`` if it is not one.

    Mirrors ``parse_entry._track_id_from_url``: a ``playlist_id`` directly in
    the query wins; otherwise the URL is treated as a short link and HEAD-
    followed, returning the landing playlist id when the landing page is a
    playlist. Any other kind (track / ugc_video / unknown) returns ``None``.
    """
    query = parse_qs(urlparse(url).query)
    if "playlist_id" in query:
        return query["playlist_id"][0]

    content = classify_landing_url(url)
    if content is not None:
        return content.content_id if content.kind == "playlist" else None

    # Looks like a short link — follow it and classify the landing URL.
    resolved = await client.resolve_short_link(url)
    if resolved is not None and resolved.kind == "playlist":
        return resolved.content_id
    return None


def build_track_url(track_id: str) -> str:
    """Build the single-track share URL the parse pipeline consumes."""
    return f"https://music.douyin.com/qishui/share/track?track_id={track_id}"


def build_item_url(item_id: str, kind: str) -> str:
    """Build the share URL for a playlist item by kind.

    ``video`` → the UGC share URL (routed to the UGC download workflow); any
    other kind (``track``) → the track share URL. Mirrors the single-fetch
    entrypoints so the batch path reuses the exact same parse routing.
    """
    if kind == "video":
        return (
            "https://music.douyin.com/qishui/share/ugc_video" f"?ugc_video_id={item_id}"
        )
    return build_track_url(item_id)


def _workflow_id(url: str, user_id: str, bucket: int) -> str:
    """Deterministic DBOS workflow id (matches the single-fetch L3 scheme)."""
    url_hash = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
    return f"parse-{user_id[:8]}-{url_hash}-{bucket}"


def batch_plan(
    items: list[dict[str, Any]],
    flow_id: str,
    user_id: str,
    *,
    bucket: int | None = None,
) -> list[dict[str, Any]]:
    """Build the per-item dispatch plan for a playlist batch download.

    ``items`` is a list of ``{"id": str, "kind": str}`` (kind ``"track"`` or
    ``"video"``). Each entry is ``{"workflow_id": str, "kwargs": dict}`` where
    ``kwargs`` are the ``parse_workflow`` arguments. The URL is built per kind so
    videos route to the UGC download workflow. The list is capped at
    ``MAX_BATCH`` — an over-cap input is truncated with a logged warning (never
    a silent cap). The deterministic workflow id is keyed on the built URL.
    """
    if len(items) > MAX_BATCH:
        dropped = len(items) - MAX_BATCH
        logger.warning(
            f"[Soda/Batch] playlist exceeds MAX_BATCH={MAX_BATCH}; "
            f"dropping {dropped} item(s)"
        )
        items = items[:MAX_BATCH]

    if bucket is None:
        bucket = int(time.time() // 30)

    plan: list[dict[str, Any]] = []
    for item in items:
        url = build_item_url(item["id"], item.get("kind", "track"))
        plan.append(
            {
                "workflow_id": _workflow_id(url, user_id, bucket),
                "kwargs": {
                    "url": url,
                    "user_id": user_id,
                    "video_bool": False,
                    "cover_bool": True,
                    "platform": "qishui",
                    "flow_id": flow_id,
                },
            }
        )
    return plan


# ============================================
# Endpoints
# ============================================


@router.post("/soda/playlist", response_model=SodaPlaylistResponse, tags=TAGS_SODA)
async def resolve_soda_playlist(
    request: SodaPlaylistRequest,
    auth: AuthDep,
) -> SodaPlaylistResponse:
    """Resolve a Soda playlist link and return its music tracks."""
    cookie = await get_soda_cookie(auth.user_id)
    if not cookie:
        raise HTTPException(
            status_code=400, detail="Connect your Soda Music account first"
        )
    client = SodaApiClient(cookie=cookie)

    try:
        playlist_id = await resolve_playlist_id(request.url, client)
    except SodaApiError as e:
        logger.warning(f"[Soda/Playlist] resolve failed: {e}")
        raise HTTPException(
            status_code=400, detail="Could not resolve Soda playlist link"
        )

    if not playlist_id:
        raise HTTPException(status_code=400, detail="Not a Soda playlist link")

    try:
        tracks = await client.get_playlist_tracks(playlist_id)
    except SodaApiError as e:
        logger.warning(f"[Soda/Playlist] fetch tracks failed: {e}")
        raise HTTPException(
            status_code=502, detail="Failed to load Soda playlist tracks"
        )

    # Incremental sync: mark vids this user has already downloaded so the
    # client can default-select only the new ones. A failing probe must never
    # fail the load — fall back to treating everything as not-downloaded.
    ids = [t["track_id"] for t in tracks]
    owned: set[str] = set()
    try:
        owned = await get_resources_repository().get_owned_platform_ids(
            ids, auth.user_id
        )
    except Exception as e:  # pragma: no cover - defensive, exercised via stub
        logger.warning(f"[Soda/Playlist] ownership probe failed: {e}")
        owned = set()

    summaries = [
        SodaTrackSummary(**{**t, "downloaded": t["track_id"] in owned}) for t in tracks
    ]
    downloaded_count = sum(1 for s in summaries if s.downloaded)
    total = len(summaries)
    return SodaPlaylistResponse(
        playlist_id=playlist_id,
        tracks=summaries,
        total=total,
        downloaded_count=downloaded_count,
        new_count=total - downloaded_count,
    )


@router.post("/soda/playlist/download", tags=TAGS_SODA)
async def download_soda_playlist(
    request: SodaBatchDownloadRequest,
    auth: AuthDep,
    raw_request: Request,
) -> dict[str, Any]:
    """Batch-download selected playlist tracks, grouped under one task flow.

    Each track reuses the single-track parse+download pipeline by dispatching a
    ``parse_workflow`` with a ``share/track`` URL. Per-track dispatch errors are
    absorbed (best-effort batch) so one bad track does not abort the rest.

    Points are charged per track up front (mirroring the batch douyin path), and
    tracks that fail to dispatch are refunded after the loop.
    """
    # Effective item list: prefer ``items`` (carries per-item kind); fall back
    # to the legacy ``track_ids`` (all treated as music tracks) for back-compat.
    if request.items:
        effective_items = [{"id": it.id, "kind": it.kind} for it in request.items]
    elif request.track_ids:
        effective_items = [{"id": tid, "kind": "track"} for tid in request.track_ids]
    else:
        raise HTTPException(
            status_code=422, detail="items or track_ids must not be empty"
        )

    total = len(effective_items)

    # Charge points per track BEFORE any dispatch so a 402 short-circuits early.
    points_service = PointsService()
    _team_id = await resolve_team_id(auth.user_id, raw_request)
    _batch_points_cost = 0
    if _team_id:
        await points_service.ensure_team_quota(_team_id, user_id=auth.user_id)
        points_result = await points_service.check_and_consume(
            team_id=_team_id,
            user_id=auth.user_id,
            action_type="video_parse_batch",
            count=total,
        )
        if not points_result["success"]:
            raise HTTPException(status_code=402, detail=points_result["reason"])
        _batch_points_cost = points_result.get("points_cost", 0)

    mgr = get_task_manager()
    flow_id = await mgr.create_flow(
        user_id=auth.user_id,
        name=request.playlist_title or "Soda playlist download",
    )

    plan = batch_plan(effective_items, flow_id=flow_id, user_id=auth.user_id)

    submitted = 0
    for entry in plan:
        kwargs = entry["kwargs"]
        wf_id = entry["workflow_id"]
        try:
            await mgr.create(
                user_id=auth.user_id,
                task_type="parse",
                title=f"Parse {kwargs['url'][:50]}",
                subtitle="Initializing...",
                dbos_workflow_id=wf_id,
                flow_id=flow_id,
            )
        except Exception as e:
            # Within the 30-s bucket the row may already exist — treat the
            # unique-violation as an idempotent re-submit (same as single fetch).
            if "duplicate key" in str(e).lower() or "23505" in str(e):
                logger.info(f"[Soda/Batch] idempotent re-submit wf_id={wf_id[:32]}")
            else:
                logger.warning(f"[Soda/Batch] pre-create task failed: {e}")

        try:
            # Enqueue on the per-user partitioned parse queue so the user's
            # "max simultaneous downloads" cap bounds the playlist batch
            # (also protects the qishui API hit-rate per cookie).
            enqueue_parse_for_user(
                user_id=auth.user_id,
                workflow_id=wf_id,
                kwargs=kwargs,
            )
            submitted += 1
        except Exception as e:
            logger.warning(
                f"[Soda/Batch] dispatch failed for {kwargs['url'][:60]}: {e}"
            )

    # Refund the tracks that failed to dispatch (mirrors the batch douyin path).
    failed = total - submitted
    if failed > 0 and _batch_points_cost > 0 and _team_id and total > 0:
        per_track = _batch_points_cost // total
        refund = per_track * failed
        if refund > 0:
            try:
                await points_service.refund_points(
                    team_id=_team_id,
                    user_id=auth.user_id,
                    amount=refund,
                    reference_type="video_parse_batch",
                    reason=f"Partial Soda batch refund: {failed}/{total} tracks failed",
                )
                logger.info(
                    f"[Soda/Batch] refunded {refund} points for {failed} failed tracks"
                )
            except Exception as refund_err:
                logger.error(f"[Soda/Batch] failed to refund points: {refund_err}")

    success = submitted > 0
    return {
        "success": success,
        "flow_id": flow_id,
        "submitted": submitted,
        "total": total,
    }

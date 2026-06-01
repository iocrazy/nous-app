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
from app.services.billing.points_service import PointsService
from app.services.infra.dbos_orchestrator import start_workflow_routed
from app.services.infra.unified_task_manager import get_task_manager
from app.services.media.parsers.soda_music.cookie_source import get_soda_cookie
from app.services.media.parsers.soda_music.soda_api import (
    SodaApiClient,
    SodaApiError,
    classify_landing_url,
)
from app.workflows.parse import parse_workflow

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
    """One music track inside a playlist."""

    track_id: str
    title: Optional[str] = None
    artist: Optional[str] = None
    cover_url: Optional[str] = None
    duration_ms: Optional[int] = None


class SodaPlaylistResponse(BaseModel):
    """The resolved playlist id plus its flattened track list."""

    playlist_id: str
    tracks: list[SodaTrackSummary]
    total: int


class SodaBatchDownloadRequest(BaseModel):
    """Batch-download a set of selected playlist tracks."""

    track_ids: list[str]
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


def _workflow_id(url: str, user_id: str, bucket: int) -> str:
    """Deterministic DBOS workflow id (matches the single-fetch L3 scheme)."""
    url_hash = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
    return f"parse-{user_id[:8]}-{url_hash}-{bucket}"


def batch_plan(
    track_ids: list[str],
    flow_id: str,
    user_id: str,
    *,
    bucket: int | None = None,
) -> list[dict[str, Any]]:
    """Build the per-track dispatch plan for a playlist batch download.

    Each entry is ``{"workflow_id": str, "kwargs": dict}`` where ``kwargs`` are
    the ``parse_workflow`` arguments. The list is capped at ``MAX_BATCH`` — an
    over-cap input is truncated with a logged warning (never a silent cap).
    """
    if len(track_ids) > MAX_BATCH:
        dropped = len(track_ids) - MAX_BATCH
        logger.warning(
            f"[Soda/Batch] playlist exceeds MAX_BATCH={MAX_BATCH}; "
            f"dropping {dropped} track(s)"
        )
        track_ids = track_ids[:MAX_BATCH]

    if bucket is None:
        bucket = int(time.time() // 30)

    plan: list[dict[str, Any]] = []
    for track_id in track_ids:
        url = build_track_url(track_id)
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

    summaries = [SodaTrackSummary(**t) for t in tracks]
    return SodaPlaylistResponse(
        playlist_id=playlist_id, tracks=summaries, total=len(summaries)
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
    if not request.track_ids:
        raise HTTPException(status_code=422, detail="track_ids must not be empty")

    total = len(request.track_ids)

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

    plan = batch_plan(request.track_ids, flow_id=flow_id, user_id=auth.user_id)

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
            await start_workflow_routed(
                "parse",
                dbos_workflow_callable=parse_workflow,
                dbos_workflow_kwargs=kwargs,
                workflow_id=wf_id,
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

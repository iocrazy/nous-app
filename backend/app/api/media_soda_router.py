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

from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel

from app.core.deps import AuthDep
from app.services.media.parsers.soda_music.cookie_source import get_soda_cookie
from app.services.media.parsers.soda_music.soda_api import (
    SodaApiClient,
    SodaApiError,
    classify_landing_url,
)

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

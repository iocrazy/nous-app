"""Playback positions — cross-device resume points (mig 473).

The browser keeps its own copy in localStorage: instant, offline-capable, and
the reason a deploy-triggered reload resumes without a round trip. This API is
the layer that makes a phone and a laptop agree, so the write path is
deliberately cheap to call and the read path is a batch.

Every handler runs inside the caller's user scope; ``PlaybackPositions`` is
``UserScoped``, so the ORM choke point injects ``user_id = <caller>`` and there
is no code path that can read another user's viewing history.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from loguru import logger

from app.core.deps import AuthDep
from app.db.scope import Scope, request_scope
from app.repositories.playback_positions_repository import (
    get_playback_positions_repository,
)
from app.schemas.playback import (
    MAX_KEYS_PER_READ,
    PlaybackPosition,
    PlaybackPositionList,
    PlaybackPositionWrite,
)

router = APIRouter()

TAGS = ["Playback"]


@router.get("", response_model=PlaybackPositionList, tags=TAGS)
async def list_positions(
    auth: AuthDep,
    media_keys: str = Query(
        ...,
        description=(
            "Comma-separated media keys. Keys absent from the store are simply "
            f"absent from the response. At most {MAX_KEYS_PER_READ} per call."
        ),
    ),
) -> PlaybackPositionList:
    """Resume points for the given media, for the calling user."""
    keys = [k.strip() for k in media_keys.split(",") if k.strip()]
    if not keys:
        return PlaybackPositionList(positions=[])
    if len(keys) > MAX_KEYS_PER_READ:
        raise HTTPException(
            status_code=400,
            detail=f"At most {MAX_KEYS_PER_READ} media_keys per request",
        )
    repo = get_playback_positions_repository()
    async with request_scope(Scope(user_id=auth.user_id)):
        rows = await repo.get_many(user_id=auth.user_id, media_keys=keys)
    return PlaybackPositionList(positions=[PlaybackPosition(**r) for r in rows])


@router.put("", response_model=PlaybackPosition, tags=TAGS)
async def upsert_position(
    body: PlaybackPositionWrite,
    auth: AuthDep,
) -> PlaybackPosition:
    """Record where the caller got to.

    Called on a throttle while playing and once more on pause / unload, so it
    must stay a single cheap upsert. The response carries the SERVER
    ``updated_at``; the client stores it and uses it on the next load to tell
    "my own write came back" from "another device wrote after me".
    """
    if body.position_seconds > body.duration_seconds:
        # Not a CHECK: a position past the end is a client bug, and a 422 that
        # names it beats a row that silently resumes past the credits.
        raise HTTPException(
            status_code=422,
            detail="position_seconds cannot exceed duration_seconds",
        )
    repo = get_playback_positions_repository()
    async with request_scope(Scope(user_id=auth.user_id)):
        row = await repo.upsert(
            user_id=auth.user_id,
            media_key=body.media_key,
            position_seconds=body.position_seconds,
            duration_seconds=body.duration_seconds,
        )
    if not row:
        logger.error(
            f"[Playback] upsert returned no row user={auth.user_id} "
            f"key={body.media_key[:64]}"
        )
        raise HTTPException(status_code=500, detail="Failed to save position")
    return PlaybackPosition(**row)


@router.delete("", status_code=204, tags=TAGS)
async def delete_position(
    auth: AuthDep,
    media_key: str = Query(..., min_length=1),
) -> None:
    """Forget one position — the client calls this when a video plays out.

    Deleting something that is not there is a success: the caller's intent
    ("this should not resume") already holds.
    """
    repo = get_playback_positions_repository()
    async with request_scope(Scope(user_id=auth.user_id)):
        await repo.delete(user_id=auth.user_id, media_key=media_key)

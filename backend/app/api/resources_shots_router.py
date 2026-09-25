"""Shot index read side: ``GET /resources/{id}/shots`` and
``GET /resources/{id}/frame?ms=`` (spec §6).

Both are tenant-scoped reads of ``resources`` (``ScopedRequestDep``): a
resource the caller cannot see is a 404, the same seam every other resource
read uses. The frame is cut on demand with ffmpeg from the materialized
source (never persisted — spec §4.2: thumbnails are not stored).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Response
from loguru import logger

from app.core.deps import AuthDep
from app.core.scope_dep import ScopedRequestDep
from app.repositories.resources_repository import ResourcesRepository
from app.schemas.shots import ShotIndexInfo, ShotOut, ShotsResponse
from app.schemas.wire import binary_response

router = APIRouter(prefix="/resources")

#: Width of a served frame; the Search Hit card and the Shots hero are the
#: only readers, both well under this.
FRAME_WIDTH = 480
#: One ffmpeg seek on a materialized file.
FRAME_TIMEOUT_SECONDS = 60.0


async def _visible_resource(resource_id: str) -> dict:
    resource = await ResourcesRepository().get_resource_by_id(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")
    return resource


@router.get("/{resource_id}/shots", response_model=ShotsResponse)
async def get_resource_shots(resource_id: str, auth: AuthDep, _scope: ScopedRequestDep):
    """The video's cut list and index facts. ``indexed=false`` + ``[]`` for a
    video nobody has indexed; ``index.covered`` says whether the CURRENT
    embedding space holds its frame vectors (after a space switch it may not)."""
    from app.repositories.resource_embeddings_repository import EmbeddingStoreMissing
    from app.repositories.video_shots_repository import get_video_shots_repository
    from app.services.library.shot_cut import ALGO_VERSION
    from app.services.library.shot_index import (
        ShotIndexError,
        resolve_space_and_embedder,
    )

    await _visible_resource(resource_id)
    rid = int(resource_id)
    try:
        index = await get_video_shots_repository().get_index(rid)
        if index is None:
            return ShotsResponse(resource_id=resource_id, indexed=False)
        shots = await get_video_shots_repository().list_shots(rid)
        space_id: str | None = None
        covered = False
        try:
            space, _ = await resolve_space_and_embedder()
        except ShotIndexError:
            # No usable embedder: the cut list is still worth showing; the
            # coverage question has no space to be asked against.
            space = None
        if space is not None:
            space_id = str(space["id"])
            covered = await _resource_covered(rid, int(space["id"]))
    except EmbeddingStoreMissing as e:
        logger.error(f"shots: store missing: {e}")
        raise HTTPException(
            status_code=503,
            detail={
                "code": "vector_store_missing",
                "message": "The shot index is not ready yet (a database update "
                "is still rolling out). Try again shortly.",
            },
        )
    return ShotsResponse(
        resource_id=resource_id,
        indexed=True,
        index=ShotIndexInfo(
            algo_version=index["algo_version"],
            shot_count=index["shot_count"],
            duration_ms=index.get("duration_ms"),
            indexed_at=index.get("indexed_at"),
            space_id=space_id,
            covered=bool(covered),
            stale=index["algo_version"] != ALGO_VERSION,
        ),
        shots=[ShotOut.from_row(s) for s in shots],
    )


async def _resource_covered(resource_id: int, space_id: int) -> bool:
    """Does this ONE resource have a frame vector in ``space_id``?"""
    from sqlalchemy import exists, select

    from app.db.session import read_scope
    from app.models import VideoShotEmbeddings, VideoShots
    from app.repositories.video_shots_repository import FRAME_KIND

    stmt = select(
        exists().where(
            VideoShots.resource_id == resource_id,
            VideoShotEmbeddings.shot_id == VideoShots.id,
            VideoShotEmbeddings.space_id == space_id,
            VideoShotEmbeddings.kind == FRAME_KIND,
        )
    )
    async with read_scope() as session:
        return bool((await session.execute(stmt)).scalar())


@router.get(
    "/{resource_id}/frame",
    response_class=Response,
    responses=binary_response("One JPEG frame of the video at `ms`.", "image/jpeg"),
)
async def get_resource_frame(
    resource_id: str,
    auth: AuthDep,
    _scope: ScopedRequestDep,
    ms: int = Query(..., ge=0, description="Timestamp in milliseconds"),
):
    """A single frame cut on demand (Search Hit card, Shots hero). 422 when
    ``ms`` is past the end of the video, 404 when the source is gone."""
    from app.services.distribution.cover_frames import (
        CoverFrameError,
        load_source_video,
    )
    from app.services.library.media_storage import materialize
    from app.services.media.render.video_frame_extractor import (
        _probe_duration,
        extract_frame_at,
    )

    try:
        source = await load_source_video(ResourcesRepository(), resource_id)
    except CoverFrameError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e
    try:
        async with materialize(source.file_path) as local:
            duration = await _probe_duration(str(local))
            if duration is not None and ms > duration * 1000:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "frame_out_of_range",
                        "message": f"ms={ms} is past the end ({int(duration * 1000)} ms).",
                    },
                )
            jpeg = await extract_frame_at(
                str(local),
                timestamp_seconds=ms / 1000.0,
                frame_width=FRAME_WIDTH,
                timeout_seconds=FRAME_TIMEOUT_SECONDS,
            )
    except ValueError as e:  # materialize's containment guard
        raise HTTPException(status_code=404, detail="source file not found") from e
    if not jpeg:
        raise HTTPException(
            status_code=502,
            detail={
                "code": "frame_extract_failed",
                "message": "ffmpeg could not cut the frame.",
            },
        )
    return Response(
        content=jpeg,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=86400"},
    )

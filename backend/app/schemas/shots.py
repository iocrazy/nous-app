"""Wire shapes of the shot index (spec §6).

Ids are strings on the wire (Snowflake > 2^53 loses precision as a JSON
number); millisecond fields are plain ints.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class ShotOut(BaseModel):
    id: str
    shot_index: int
    start_ms: int
    end_ms: int
    rep_frame_ms: int
    cut_score: Optional[float] = None

    @classmethod
    def from_row(cls, row: dict) -> "ShotOut":
        return cls(
            id=str(row["id"]),
            shot_index=int(row["shot_index"]),
            start_ms=int(row["start_ms"]),
            end_ms=int(row["end_ms"]),
            rep_frame_ms=int(row["rep_frame_ms"]),
            cut_score=row.get("cut_score"),
        )


class ShotIndexInfo(BaseModel):
    """``video_shot_indexes`` row + whether the CURRENT space holds vectors
    for it (``covered``) and whether the cut is by an older algorithm."""

    algo_version: str
    shot_count: int
    duration_ms: Optional[int] = None
    indexed_at: Optional[str] = None
    #: Space the coverage below was checked against (string Snowflake), or
    #: null when no embedder is configured.
    space_id: Optional[str] = None
    covered: bool
    stale: bool


class ShotsResponse(BaseModel):
    """``GET /resources/{id}/shots``. ``indexed=false`` with an empty list is
    the normal answer for a video nobody has indexed — not a 404."""

    resource_id: str
    indexed: bool
    index: Optional[ShotIndexInfo] = None
    shots: List[ShotOut] = Field(default_factory=list)


class IndexShotsBody(BaseModel):
    #: Re-cut and re-embed even when the video is already indexed in the
    #: current space (the Shots tab's Re-index).
    force: bool = False


class IndexShotsResponse(BaseModel):
    """``POST /ai/analyze/index-shots/{resource_id}`` (202)."""

    task_id: str
    workflow_id: str
    resource_id: str


class BackfillShotsBody(BaseModel):
    limit: int = Field(default=20, ge=1, le=50)
    dry_run: bool = False


class BackfillShotsSkip(BaseModel):
    resource_id: str
    reason: str


class BackfillShotsResponse(BaseModel):
    """``POST /ai/analyze/backfill-shots``.

    ``dry_run``: ``candidates`` are the videos a real run would dispatch,
    with the shot / token estimate; nothing is created. Real run: one parent
    task (``parent_task_id``) and one ``index_shots`` child per candidate
    (``dispatched``); ``skipped`` names candidates refused before dispatch
    with a stable reason (``no_video_file`` / ``already_indexed`` /
    ``dispatch_failed``). Process-wide refusals (``embedder_unconfigured`` /
    ``provider_no_image`` / ``store_missing``) are a 409 instead.
    """

    success: bool
    dry_run: bool
    space_id: str
    total_pending: int
    stale: int
    candidates: List[str]
    estimated_shots: int
    estimated_tokens: int
    parent_task_id: Optional[str] = None
    dispatched: List[str]
    skipped: List[BackfillShotsSkip]

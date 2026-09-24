"""Response models for the ``/ai`` trigger routes that used to return bare dicts.

Every model declares what the handler ALREADY sends (OpenAPI P5). The wire
test ``tests/api/test_ai_trigger_wire.py`` compares each branch's HTTP body
with ``jsonable_encoder`` of the dict the handler builds.

The trigger answers are a family of shapes that differ by branch (queued /
already in progress / already done / blocked by an audio extraction). One
model per endpoint carries the keys every branch sends as required, and the
keys only some branches send as optional; the routes use
``response_model_exclude_unset=True`` so an optional key that a branch did
not build stays absent on the wire, exactly as before.

Names carry an ``Ai`` prefix: ``TriggerResponse`` / ``BackfillResult`` alone
would collide with unrelated surfaces.
"""

from __future__ import annotations

from typing import Annotated, List, Literal, Optional

from pydantic import BaseModel, Field

from app.schemas.search import SpaceInfo


class AiTranscribeTriggerResponse(BaseModel):
    """``POST /ai/transcribe/resource/{resource_id}``.

    ``transcription_pending_audio`` and ``already_transcribed`` are on every
    200 (a client never reads "absent" as "no"). ``extracting_audio`` only on
    the queued answer; ``blocking_task_id`` only when an audio extraction that
    will not transcribe holds the slot (null = it just finished, retry now).
    ``points_charged`` is always 0: transcription is billed when it lands.
    """

    message: str
    resource_id: str
    platform_id: str
    points_charged: int
    transcription_pending_audio: bool
    already_transcribed: bool
    extracting_audio: Optional[bool] = None
    blocking_task_id: Optional[str] = None


class AiSummarizeTriggerResponse(BaseModel):
    """``POST /ai/summarize/resource/{resource_id}``.

    ``platform_id`` is absent on exactly one branch: the "already in
    progress" answer found by the dedup SELECT (the race-lost variant of the
    same answer carries it).
    """

    message: str
    resource_id: str
    points_charged: int
    already_summarized: bool
    platform_id: Optional[str] = None


class AiAnalyzeTriggerResponse(BaseModel):
    """``POST /ai/analyze/resource/{resource_id}``; ``platform_id`` only on
    the queued answer, not on "already in progress"."""

    message: str
    resource_id: str
    platform_id: Optional[str] = None


class AiLegacyTranscribeTriggerResponse(BaseModel):
    """``POST /ai/transcribe/{platform_id}`` (legacy, platform-id keyed)."""

    message: str
    platform_id: str
    extracting_audio: bool


class AiLegacySummarizeTriggerResponse(BaseModel):
    """``POST /ai/summarize/{platform_id}`` (legacy, platform-id keyed)."""

    message: str
    platform_id: str


class AiBackfillSkip(BaseModel):
    """One batch row that did not land; ``resource_id`` is a string
    (Snowflake > 2^53), ``reason`` a stable code, never provider text."""

    resource_id: str
    reason: str


class AiBackfillEmbeddingsResponse(BaseModel):
    """``POST /ai/analyze/backfill-embeddings``.

    ``dispatched`` / ``in_flight`` are kept for readers of the pre-mig-499
    shape and are always ``[]`` / ``0`` now: nothing is dispatched, every
    candidate is embedded in place. ``aborted_reason`` is null when every row
    was attempted.
    """

    success: bool
    space: SpaceInfo
    dispatched: Annotated[List[str], Field(max_length=0)]
    in_flight: Literal[0]
    total_missing: int
    stale: int
    dry_run: bool
    reembedded: List[str]
    rehashed: int
    skipped: List[AiBackfillSkip]
    remaining: int
    aborted_reason: Optional[str]

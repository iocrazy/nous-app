# backend/app/api/ai_router.py

"""
AI Pipeline API

Endpoints for triggering and retrieving AI analysis results
(transcription, summary, visual analysis).

Supports both platform_id-based (legacy) and resource_id-based triggers.
"""


from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel

from app.core.deps import AuthDep, get_team_id_for_user
from app.core.scope_dep import ScopedRequestDep
from app.repositories.ai_repository import get_ai_repository
from app.repositories.analysis_repository import get_analysis_repository
from app.repositories.media_repository import MediaRepository
from app.repositories.resources_repository import ResourcesRepository
from app.schemas.ai import (
    SummaryResponse,
    TranscriptResponse,
    VisualAnalysisResponse,
)
from app.services.billing.points_service import PointsService

router = APIRouter(prefix="/ai", tags=["AI"])


async def _get_media_or_404(platform_id: str) -> dict:
    """Look up media by platform_id or raise 404."""
    repo = MediaRepository()
    media = await repo.get_by_platform_id(platform_id)
    if not media:
        raise HTTPException(status_code=404, detail=f"Media not found: {platform_id}")
    return media


async def _resolve_resource_to_platform_id(
    resource_id: str, user_id: str
) -> tuple[dict, str, dict]:
    """Resolve resource_id -> (resource dict, platform_id, media dict), or raise 404.

    Visibility is OWNER-OR-TEAM-MEMBER (``get_resource_by_id_for_caller``),
    not creator-only: under ``SCOPE_ENFORCE_RESOURCES=true`` (production), a
    bare creator-scoped read here used to 404 a team member triggering AI
    processing on a resource shared to their team — undoing PR #1743's
    "teammate triggers, owner pays" identity fix at the resolve step itself.
    404 either way (not found vs not visible) — no existence leak.
    """
    repo = ResourcesRepository()
    resource = await repo.get_resource_by_id_for_caller(resource_id, user_id)
    if not resource or not resource.get("media_id"):
        raise HTTPException(
            status_code=404,
            detail="Resource not found or has no linked media",
        )

    media_repo = MediaRepository()
    media = await media_repo.get_by_id(resource["media_id"])
    if not media:
        raise HTTPException(status_code=404, detail="Linked media not found")

    return resource, media["platform_id"], media


# Video container extensions that ffmpeg can stream-copy audio from. Used
# to tell "no audio yet but a video file is on disk" (extractable) apart
# from "gallery / nothing downloaded" (download_path is a directory or
# empty → NOT extractable). This is stricter than the extract-audio
# endpoint's bare download_path truthiness so a 图文 gallery never
# dispatches a doomed ffmpeg pass.
_VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".flv", ".ts")


def _has_extractable_video(media: dict | None) -> bool:
    """True when the media has a downloaded video file we can extract
    audio from (as opposed to a gallery directory or no download yet)."""
    download_path = (media or {}).get("download_path") or ""
    return bool(download_path) and download_path.lower().endswith(_VIDEO_EXTS)


def _format_duration_short(seconds: float) -> str:
    """Format seconds into MM:SS or HH:MM:SS."""
    total = int(seconds)
    h, remainder = divmod(total, 3600)
    m, s = divmod(remainder, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


# Task Center rows used to be titled with the raw platform_id
# ("Transcribe: 7643786260724632866") — a Snowflake-shaped number that
# says nothing about which video is being processed. Preference order,
# most human-readable first:
#   1. resources.filename  — what the library card shows the user
#   2. parsed_media.title  — the platform's own video title
#   3. platform_id         — last resort only; never the first choice
# Row titles AND the flow name both go through here, so a step card's
# header can never disagree with the steps inside it.
_TASK_NAME_LIMIT = 200


def _task_display_name(
    platform_id: str,
    resource: dict | None = None,
    media: dict | None = None,
) -> str:
    """Human-readable name for a task row / flow, falling back to the id."""
    for candidate in ((resource or {}).get("filename"), (media or {}).get("title")):
        text = str(candidate).strip() if candidate else ""
        if text:
            return text[:_TASK_NAME_LIMIT]
    return platform_id


# How far back a manual/auto summary click looks for the transcription it
# belongs to. A transcription that finished an hour ago is plausibly the
# same submission ("send to agent" transcribes, then the frontend fires
# the summary once the transcript lands); one from last week is a
# different session and grouping them would misrepresent history.
_FLOW_JOIN_WINDOW_HOURS = 24

# Task types whose flow a follow-up ai_summary may join. Both are shapes
# of the same "produce a transcript" step: extract_audio is the two-step
# variant that chains ai_transcription onto the same flow.
_TRANSCRIBE_TASK_TYPES = ("ai_transcription", "extract_audio")


async def _find_joinable_flow_id(
    resource_id: str,
    user_id: str,
    *,
    window_hours: int = _FLOW_JOIN_WINDOW_HOURS,
) -> str | None:
    """flow_id of this user's recent transcription chain for this resource,
    or None when the summary row should stand on its own.

    Why: "send to agent" transcribes first and the frontend fires the
    summary only once the transcript lands, so the two rows are created by
    two separate requests. Without this lookup the user sees a 1/1 step
    card plus an unrelated loose row for what is, to them, one job.

    A candidate flow must satisfy ALL of these — each one rules out a way
    of grouping rows that do not belong together:

    * a ``task_tracking`` row for THIS ``resource_id`` — the summary is
      about this asset, not a neighbouring one;
    * ``task_type`` in :data:`_TRANSCRIBE_TASK_TYPES` — a download or
      publish flow for the same resource is a different job;
    * both the row and the ``task_flows`` parent belong to ``user_id`` —
      flows are per-user (a teammate summarising the owner's resource
      gets their own row rather than being spliced into the owner's card);
    * created within ``window_hours``;
    * the flow holds no row for a DIFFERENT non-null ``resource_id`` —
      that is what a batch flow (Soda playlist, batch parse) looks like,
      and hanging one summary inside a 185-track card is noise. Rows with
      a NULL resource_id do NOT disqualify: that is the parse root of the
      very submission this resource came from.

    Best-effort like ``create_flow``: any failure returns None and the
    summary dispatches un-grouped. Grouping is presentation and must
    never break dispatch. The return is annotated ``str | None`` to match
    ``create_flow``'s own signature — both actually hand back the raw
    ``flow_id`` scalar, which is what ``create(flow_id=...)`` consumes.
    """
    # ``TaskTracking.resource_id == None`` compiles to ``IS NULL``, which
    # matches parse roots — a missing resource must mean "no grouping",
    # never "group with whatever has no resource". Enforced here rather
    # than at each call site so the footgun has one owner.
    if not resource_id:
        return None

    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select
    from sqlalchemy.orm import aliased

    from app.db.session import read_scope
    from app.models import TaskFlows, TaskTracking

    try:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
        sibling = aliased(TaskTracking)
        holds_a_foreign_resource = (
            select(sibling.dbos_workflow_id)
            .where(sibling.flow_id == TaskTracking.flow_id)
            .where(sibling.resource_id.is_not(None))
            .where(sibling.resource_id != resource_id)
            .exists()
        )
        stmt = (
            select(TaskTracking.flow_id)
            .join(TaskFlows, TaskFlows.id == TaskTracking.flow_id)
            .where(TaskTracking.resource_id == resource_id)
            .where(TaskTracking.user_id == user_id)
            .where(TaskTracking.task_type.in_(_TRANSCRIBE_TASK_TYPES))
            .where(TaskTracking.flow_id.is_not(None))
            .where(TaskTracking.created_at >= cutoff)
            .where(TaskFlows.user_id == user_id)
            .where(~holds_a_foreign_resource)
            .order_by(TaskTracking.created_at.desc())
            .limit(1)
        )
        async with read_scope() as session:
            # Returned as-is, not str()'d: this value goes straight into
            # ``create(flow_id=...)``, and ``create_flow`` already hands that
            # parameter the raw scalar of the same column type (a UUID under
            # psycopg3). Same shape on both paths, one less coercion to be
            # wrong about.
            return (await session.execute(stmt)).scalars().first() or None
    except Exception as e:
        logger.warning(f"[ai] flow join lookup failed (non-fatal): {e}")
        return None


# Two ways a transcribe request can find the resource already occupied,
# and they mean OPPOSITE things to the caller — collapsing them into one
# "already in progress" is what made this endpoint answer 200 to requests
# that then produced no transcription at all (silent no-op).
#
# ``transcription_pending_audio`` is the machine-readable discriminator;
# every 200 from this endpoint carries it so a client can branch without
# parsing prose.


def _transcription_in_progress_response(resource_id: str, platform_id: str) -> dict:
    """A transcript IS coming — either an ai_transcription run or an
    extract_audio run that chains one."""
    return {
        "message": "Transcription already in progress",
        "resource_id": resource_id,
        "platform_id": platform_id,
        "points_charged": 0,
        "transcription_pending_audio": False,
        "already_transcribed": False,
    }


def _audio_extraction_blocks_response(
    resource_id: str, platform_id: str, blocking_task_id: str | None
) -> dict:
    """Audio extraction holds the slot but will NOT transcribe.

    Migration 121's unique index is keyed on (resource_id, task_type) and
    knows nothing about intent, so a new extract_audio cannot be dispatched
    until that one finishes. Saying so plainly — and handing back the task
    id to wait on — is the only honest answer: the caller must retry.
    """
    return {
        "message": (
            "Audio extraction is already running for this media, and that run "
            "will not start a transcription by itself — retry once it finishes"
        ),
        "resource_id": resource_id,
        "platform_id": platform_id,
        "points_charged": 0,
        "transcription_pending_audio": True,
        "blocking_task_id": blocking_task_id,
        "already_transcribed": False,
    }


# ------------------------------------------------------------------
# Already-finished short-circuit
# ------------------------------------------------------------------
# "What you are asking for already exists" is the third answer these two
# endpoints owe their callers, next to "queued" and "already in progress" —
# and the only one that was missing.
#
# Both frontend trigger paths (the @ picker and the resource context menu's
# "Send to Agent") decide from a resource row cached earlier, so a resource
# that finished transcribing AFTER that snapshot was taken still looks
# untranscribed to them. The dedup below cannot catch it: by then nothing is
# in flight. 2026-08-20 production — resource 340888655925500 completed a
# transcription at 12:29 and was billed for a second full one at 12:31.
#
# The predicate is a CONJUNCTION (status column completed AND readable
# content present) on purpose:
#   * the status column alone would promise content that is not there —
#     `completed` with the row missing would short-circuit forever and no
#     run would ever be dispatched to fix it;
#   * the content row alone would hijack the retry paths — both tables
#     upsert by resource_id, so a run that FAILS after an earlier success
#     leaves the old row in place while the column reads `failed`, and the
#     detail panel's Retry button must still dispatch.
# Only "finished AND readable" means the caller can have what they asked for
# without paying again.
#
# A probe that ERRORS answers "not available" and the request continues to
# the normal dispatch path: that degrades to the behaviour we had before this
# short-circuit existed (at worst one duplicate run), while short-circuiting
# on a failed probe would hand back content nobody verified. The guard is
# HERE and not left to the repo getters' own `except -> None`: that is
# somebody else's property, and this file's sibling repository has already
# started re-raising deliberately (``get_resource_by_id_for_caller`` re-raises
# ``UnscopedQueryError`` — "no scope is a caller bug, don't swallow it"). If
# ``ai_repository`` ever follows, an unguarded probe would turn "runs twice"
# into "500 on a resource that is perfectly fine".


async def _content_probe(coro) -> dict | None:
    """Await a content lookup, turning any failure into "nothing found"."""
    try:
        return await coro
    except Exception as e:  # noqa: BLE001 — see the note above
        logger.warning(f"[ai_router] AI content probe failed (non-fatal): {e}")
        return None


async def _transcript_already_available(
    resource: dict | None, resource_id: str
) -> bool:
    """True when this resource already holds a readable transcript."""
    from app.services.ai.resource_ai_status import TRANSCRIPT_STATUS_FIELD
    from app.utils.ai_status import ai_status_str

    if ai_status_str((resource or {}).get(TRANSCRIPT_STATUS_FIELD)) != "completed":
        return False
    row = await _content_probe(get_ai_repository().get_transcript(resource_id))
    return bool((row or {}).get("full_text"))


async def _summary_already_available(resource: dict | None, resource_id: str) -> bool:
    """True when this resource already holds a readable summary."""
    from app.services.ai.resource_ai_status import SUMMARY_STATUS_FIELD
    from app.utils.ai_status import ai_status_str

    if ai_status_str((resource or {}).get(SUMMARY_STATUS_FIELD)) != "completed":
        return False
    row = await _content_probe(get_ai_repository().get_summary(resource_id))
    return bool((row or {}).get("summary_text"))


def _transcript_already_exists_response(resource_id: str, platform_id: str) -> dict:
    """Nothing dispatched, nothing charged — the transcript is already there
    to read."""
    return {
        "message": "Transcript already exists",
        "resource_id": resource_id,
        "platform_id": platform_id,
        "points_charged": 0,
        "transcription_pending_audio": False,
        "already_transcribed": True,
    }


def _summary_already_exists_response(resource_id: str, platform_id: str) -> dict:
    """Nothing dispatched, nothing charged — the summary is already there to
    read."""
    return {
        "message": "Summary already exists",
        "resource_id": resource_id,
        "platform_id": platform_id,
        "points_charged": 0,
        "already_summarized": True,
    }


# ------------------------------------------------------------------
# Manual triggers (resource_id-based)
# ------------------------------------------------------------------


class TranscribeTriggerBody(BaseModel):
    """Optional body for the transcribe trigger.

    ``follow_up_summary``: persist a server-side "summarize once this
    transcription completes" intent (resources.summary_follow_up, 438),
    consumed by ai_transcription's success chain. Set by the chat's
    ensure-processed flow; survives page refreshes, which the old
    browser-memory registry did not (PR #1927's documented boundary).
    """

    follow_up_summary: bool = False


async def _persist_summary_follow_up(resource_id: str, user_id: str) -> None:
    """Write the one-shot intent. Cleared by consume_summary_follow_up."""
    from datetime import datetime, timezone

    from sqlalchemy import update as _sa_update

    from app.db.session import write_scope
    from app.models.media import Resources

    async with write_scope() as session:
        await session.execute(
            _sa_update(Resources)
            .where(Resources.id == int(resource_id))
            .values(
                summary_follow_up={
                    "requested_by": str(user_id),
                    "requested_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        )


@router.post("/transcribe/resource/{resource_id}")
async def trigger_transcription_by_resource(
    resource_id: str,
    auth: AuthDep,
    _scope: ScopedRequestDep,
    force: bool = False,
    body: TranscribeTriggerBody | None = None,
):
    """Trigger AI transcription by resource_id.

    ``force=true`` skips the already-transcribed short-circuit below and
    dispatches a fresh (billed) run. Nothing in the app sends it today —
    the Task Center's Retry goes through ``POST /tasks/{id}/retry``, which
    re-keys the existing task row and never reaches this endpoint — it
    exists so that "re-transcribe this on purpose" has an explicit way in
    rather than riding on a stale snapshot.
    """
    resource, platform_id, media = await _resolve_resource_to_platform_id(
        resource_id, auth.user_id
    )

    # === Already-transcribed short-circuit (BEFORE everything else) ===
    # Ahead of the audio-readiness gate on purpose: an existing transcript
    # is readable whether or not the audio file survived, so answering 409
    # "no audio track available" would deny content we are holding.
    if not force and await _transcript_already_available(resource, resource_id):
        logger.info(
            f"[ai_router] transcript for resource {resource_id} already exists — "
            "short-circuit, nothing dispatched, nothing charged"
        )
        return _transcript_already_exists_response(resource_id, platform_id)
    # === End short-circuit ===

    # === Audio-readiness classification (BEFORE billing) ===
    # Three cases, decided up-front so a dead-end never leaves points
    # charged (the 409 used to raise AFTER check_and_consume):
    #   1. audio already on disk            → transcribe directly
    #   2. no audio yet but a video file    → extract audio first, then
    #                                         chain transcription
    #   3. neither audio nor video          → 409, nothing to transcribe
    _has_audio = bool(
        (media or {}).get("extract_audio_path")
        or (media or {}).get("music_download_path")
    )
    _can_extract = _has_extractable_video(media)
    if not _has_audio and not _can_extract:
        raise HTTPException(
            status_code=409,
            detail=(
                "This media has no audio track available — "
                "download/extraction hasn't produced one."
            ),
        )
    # === End classification ===

    # W2-5 (438): every exit below either returns with a transcription in
    # flight (dedup) or ends in one being dispatched (direct / via the
    # extract_audio chain) — and the transcription success chain consumes
    # this intent. Placed AFTER the two dead ends above on purpose: the
    # already-transcribed short-circuit needs no follow-up (the caller goes
    # straight to summary), and a 409 means nothing will ever complete, so a
    # stored intent would be a lie that outlives the request.
    if body and body.follow_up_summary:
        await _persist_summary_follow_up(resource_id, auth.user_id)

    # === Dedup: something already occupies the transcription slot? ===
    # Which slot that is depends on the classification above, so this runs
    # after it (still before billing — a short-circuit never charges).
    #
    #   audio ready  → we would insert `ai_transcription`, which collides
    #                  only with another `ai_transcription`. A non-chaining
    #                  `extract_audio` running alongside is a DIFFERENT
    #                  task_type: it neither blocks that insert nor produces
    #                  a transcript, so refusing on its account would be a
    #                  pointless rejection.
    #   no audio     → we would insert `extract_audio`, which collides with
    #                  ANY active `extract_audio` (migration 121's unique
    #                  index is keyed on (resource_id, task_type) and cannot
    #                  see intent) — including the audio-only kind that will
    #                  never transcribe. That one gets its own answer.
    #
    # Either way, a task that WILL produce a transcript means "already in
    # progress" is true and re-dispatching would double-charge.
    from app.services.ai.resource_ai_status import (
        find_active_transcription_task,
        is_active_task_conflict,
    )

    _active = await find_active_transcription_task(resource_id)
    if _active is not None:
        if _active.chains_transcription:
            return _transcription_in_progress_response(resource_id, platform_id)
        if not _has_audio:
            return _audio_extraction_blocks_response(
                resource_id, platform_id, _active.workflow_id
            )
    # === End dedup ===

    # === Nous billing — only charge if user selected a nous-* model ===
    import math

    from app.repositories.mediahub_model_repository import get_mediahub_model_repository
    from app.repositories.user_settings_repository import UserSettingsRepository

    settings_repo = UserSettingsRepository()
    user_settings = await settings_repo.get_by_user_id(auth.user_id)
    ai_settings = (user_settings or {}).get("settings_json", {}).get("ai_settings", {})
    selected_model = ai_settings.get("task_assignment", {}).get("transcription", "")

    points_service = PointsService()
    resource_owner = resource.get("creator_id") or auth.user_id
    _team_id = await get_team_id_for_user(resource_owner)
    _points_cost = 0
    _is_nous = selected_model.startswith("nous-")

    if _is_nous and _team_id:
        # Look up Nous model pricing
        nous_repo = get_mediahub_model_repository()
        mediahub_model = await nous_repo.get_by_name(selected_model)
        if not mediahub_model or not mediahub_model.get("is_enabled"):
            raise HTTPException(
                status_code=400, detail=f"Nous model '{selected_model}' not available"
            )

        # Compute cost by media duration (reuse media from resolver)
        duration_seconds = float(media.get("duration", 0)) if media else 0
        if duration_seconds <= 0:
            duration_seconds = 60  # fallback: charge 1 minute minimum

        pricing_value = float(mediahub_model["pricing_value"])
        if mediahub_model["pricing_type"] == "per_hour":
            _points_cost = max(1, math.ceil(duration_seconds / 3600 * pricing_value))
        else:
            _points_cost = max(1, int(pricing_value))

        # Build detailed description for transaction record
        video_title = (media.get("title") or platform_id)[:50]
        dur_str = (
            _format_duration_short(duration_seconds) if duration_seconds > 0 else ""
        )
        _description = f"AI Transcription: {video_title} ({selected_model}"
        if dur_str:
            _description += f", {dur_str}"
        _description += ")"

        await points_service.ensure_team_quota(_team_id, user_id=auth.user_id)
        points_result = await points_service.check_and_consume(
            team_id=_team_id,
            user_id=auth.user_id,
            action_type="ai_transcription",
            reference_id=resource_id,
            override_cost=_points_cost,
            description=_description,
        )
        if not points_result["success"]:
            raise HTTPException(status_code=402, detail=points_result["reason"])
        _points_cost = points_result.get("points_cost", 0)
    # === End billing ===

    # Track unified_task so we can mark it failed if the dispatch
    # itself throws — and so the Task Center sees this run + Realtime
    # pushes status changes back to the frontend.
    _orphan_task_id: str | None = None

    try:
        # Manual click path = always dispatch, do NOT go through tag-driven
        # `maybe_chain_ai_pipeline` (that helper is for the post-download
        # auto-chain). Two dispatch shapes, chosen by the classification
        # above:
        #   - audio ready  → ai_transcription directly
        #   - video only   → extract_audio(chain_transcription=True) which
        #                    extracts then unconditionally chains transcribe
        import uuid as _uuid

        from app.services.infra.dbos_orchestrator import start_workflow_routed
        from app.services.infra.unified_task_manager import get_task_manager

        tracker = get_task_manager()
        wf_id = str(_uuid.uuid4())

        # A manual transcribe click IS a pipeline root, so the flow is
        # created here — the same place the parse/download roots create
        # theirs (api/media_fetch_helpers.py). Two chain shapes hang off it:
        #   audio ready → one step  (ai_transcription)
        #   no audio    → two steps (extract_audio → chained ai_transcription)
        # Both carry the same flow_id so the Task Center groups them into one
        # step card (flowGrouping.ts groups by flow_id) instead of showing
        # unrelated single rows. create_flow is best-effort and returns None
        # on failure; flow_id=None simply falls back to the old un-grouped
        # behaviour — grouping is presentation and must never fail dispatch.
        # One name for the whole card: the flow header, this row, and the
        # ai_transcription row that extract_audio chains (via the
        # video_title kwarg below) all read from _video_title.
        _video_title = _task_display_name(platform_id, resource, media)
        # "Process", not "Transcribe": the same flow also carries the
        # extract_audio step and any summary the frontend fires once the
        # transcript lands, matching the parse chain's "Process {url}".
        flow_id = await tracker.create_flow(
            user_id=auth.user_id,
            name=f"Process {_video_title}",
        )

        if _has_audio:
            from app.workflows.ai_transcription import ai_transcription_workflow

            _orphan_task_id = await tracker.create(
                user_id=auth.user_id,
                task_type="ai_transcription",
                title=f"Transcribe {_video_title}",
                media_id=platform_id,
                resource_id=resource_id,
                dbos_workflow_id=wf_id,
                flow_id=flow_id,
            )
            await start_workflow_routed(
                "ai_transcription",
                dbos_workflow_callable=ai_transcription_workflow,
                dbos_workflow_kwargs={
                    "parsed_media_id": int(media["id"]),
                    "user_id": auth.user_id,
                },
                workflow_id=wf_id,
            )
        else:
            # No audio yet but a video file exists → extract audio first,
            # then chain transcription unconditionally. Turns the old
            # 409 dead-end (old B站 download, extract_audio_path empty)
            # into a working extract→transcribe chain.
            from app.workflows.extract_audio import extract_audio_workflow

            _orphan_task_id = await tracker.create(
                user_id=auth.user_id,
                task_type="extract_audio",
                title=f"Audio {_video_title}",
                subtitle="Extracting audio, then transcribing",
                media_id=platform_id,
                resource_id=resource_id,
                dbos_workflow_id=wf_id,
                flow_id=flow_id,
                # Mirrors chain_transcription=True below. The workflow arg
                # is a frozen DBOS input that nothing can read back, so the
                # intent is recorded here for the dedup and the read path.
                metadata={"chain_transcription": True},
            )
            await start_workflow_routed(
                "extract_audio",
                dbos_workflow_callable=extract_audio_workflow,
                dbos_workflow_kwargs={
                    "platform_id": platform_id,
                    "user_id": auth.user_id,
                    "resource_id": resource_id,
                    # Threaded into the workflow so the ai_transcription row
                    # it chains (download_helpers.chain_transcription_
                    # unconditional) lands on this same flow — without it the
                    # second step shows up as an orphan row.
                    "flow_id": flow_id,
                    "video_title": _video_title,
                    "chain_transcription": True,
                },
                workflow_id=wf_id,
            )
        _orphan_task_id = None
    except HTTPException:
        raise
    except Exception as e:
        # Lost the race: another request created the active task between our
        # dedup SELECT and this INSERT, and migration 121's partial unique
        # index rejected ours. Its own comment tells callers to treat that as
        # "already in progress" — reporting a 500 would tell the user their
        # media failed while it is in fact being processed. `_orphan_task_id`
        # is still None here (the create() itself is what raised), so nothing
        # needs marking failed; the charge does need giving back, since this
        # request dispatched no work.
        if _orphan_task_id is None and is_active_task_conflict(e):
            if _points_cost > 0 and _team_id:
                try:
                    await points_service.refund_points(
                        team_id=_team_id,
                        user_id=auth.user_id,
                        amount=_points_cost,
                        reference_type="ai_transcription",
                        reference_id=resource_id,
                        reason="Another transcription task is already active",
                    )
                except Exception as refund_err:
                    logger.error(
                        f"Failed to refund points after dedup conflict: {refund_err}"
                    )
            # Which kind of task beat us decides what we may promise. When
            # we were inserting ai_transcription, the winner is one too, so
            # a transcript is coming; when we were inserting extract_audio,
            # re-read to see whether the winner chains one.
            _blocker = (
                None
                if _has_audio
                else await find_active_transcription_task(resource_id)
            )
            logger.info(
                f"[ai_router] transcription slot for resource {resource_id} "
                f"already taken (unique index); has_audio={_has_audio} "
                f"blocker_chains={getattr(_blocker, 'chains_transcription', None)}"
            )
            if _has_audio or (_blocker is not None and _blocker.chains_transcription):
                return _transcription_in_progress_response(resource_id, platform_id)
            # `_blocker is None` means the winner finished between our
            # failed INSERT and this re-read. Nothing is running, so a
            # retry succeeds immediately — the null blocking_task_id says
            # exactly that ("retry now" rather than "wait for this task").
            return _audio_extraction_blocks_response(
                resource_id,
                platform_id,
                _blocker.workflow_id if _blocker else None,
            )
        if _orphan_task_id:
            try:
                from app.services.infra.unified_task_manager import get_task_manager

                await get_task_manager().fail(
                    _orphan_task_id,
                    f"Dispatch failed: {str(e)[:180]}",
                    error_code="DISPATCH_ERROR",
                )
            except Exception as fail_err:
                logger.error(
                    f"Failed to mark orphan task {_orphan_task_id} failed: {fail_err}"
                )
        if _points_cost > 0 and _team_id:
            try:
                await points_service.refund_points(
                    team_id=_team_id,
                    user_id=auth.user_id,
                    amount=_points_cost,
                    reference_type="ai_transcription",
                    reference_id=resource_id,
                    reason=f"Task dispatch failed: {str(e)[:100]}",
                )
                logger.info(
                    f"Refunded {_points_cost} points for failed transcription dispatch"
                )
            except Exception as refund_err:
                logger.error(f"Failed to refund points: {refund_err}")
        raise HTTPException(
            status_code=500, detail=f"Failed to queue transcription: {str(e)}"
        )

    return {
        "message": (
            "Transcription queued"
            if _has_audio
            else "Audio extraction started — transcription will follow"
        ),
        "resource_id": resource_id,
        "platform_id": platform_id,
        "points_charged": _points_cost,
        "extracting_audio": not _has_audio,
        # False on both success paths: this dispatch either transcribes
        # directly or extracts with chain_transcription=True.
        "transcription_pending_audio": False,
        # Present on EVERY 200 of this endpoint, like the discriminator
        # above: a client must never have to read "field absent" as "no".
        "already_transcribed": False,
    }


@router.post("/summarize/resource/{resource_id}")
async def trigger_summary_by_resource(
    resource_id: str,
    auth: AuthDep,
    _scope: ScopedRequestDep,
    force: bool = False,
):
    """Trigger AI summary by resource_id.

    ``force=true`` skips the already-summarized short-circuit — the same
    explicit way in as the transcribe endpoint, with the same (currently
    empty) caller set.
    """
    resource, platform_id, media = await _resolve_resource_to_platform_id(
        resource_id, auth.user_id
    )

    # === Already-summarized short-circuit ===
    # Before the dedup, and it wins over it: a run that is in flight ends up
    # completed too, so "the content is already here" is the more useful of
    # the two true answers — and the only one that stops a stale snapshot
    # from buying a second summary.
    if not force and await _summary_already_available(resource, resource_id):
        logger.info(
            f"[ai_router] summary for resource {resource_id} already exists — "
            "short-circuit, nothing dispatched, nothing charged"
        )
        return _summary_already_exists_response(resource_id, platform_id)
    # === End short-circuit ===

    # === Dedup: reject if already processing ===
    # Only one task_type to look for here (summary never chains through a
    # second one), so unlike the transcribe endpoint this SELECT is complete
    # — but it is still a check-then-insert, and the conflict branch below
    # covers the race it cannot. ACTIVE_TASK_STATUSES is copied
    # from migration 121's index predicate so the check and the constraint
    # cannot drift apart.
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import TaskTracking
    from app.services.ai.resource_ai_status import (
        ACTIVE_TASK_STATUSES,
        is_active_task_conflict,
    )

    async with read_scope() as session:
        _active = (
            await session.execute(
                select(TaskTracking.dbos_workflow_id)
                .where(TaskTracking.resource_id == resource_id)
                .where(TaskTracking.task_type == "ai_summary")
                .where(TaskTracking.status.in_(ACTIVE_TASK_STATUSES))
                .limit(1)
            )
        ).first()
    if _active:
        return {
            "message": "Summary already in progress",
            "resource_id": resource_id,
            "points_charged": 0,
            "already_summarized": False,
        }
    # === End dedup ===

    # === Points check — charge the resource owner's personal team ===
    points_service = PointsService()
    resource_owner = resource.get("creator_id") or auth.user_id
    _team_id = await get_team_id_for_user(resource_owner)
    _points_cost = 0
    if _team_id:
        # Build detailed description for transaction record
        video_title = (media.get("title") or platform_id)[:50]
        _description = f"AI Summary: {video_title}"

        await points_service.ensure_team_quota(_team_id, user_id=auth.user_id)
        points_result = await points_service.check_and_consume(
            team_id=_team_id,
            user_id=auth.user_id,
            action_type="ai_summary",
            reference_id=resource_id,
            description=_description,
        )
        if not points_result["success"]:
            raise HTTPException(status_code=402, detail=points_result["reason"])
        _points_cost = points_result.get("points_cost", 0)
    # === End points check ===

    ai_repo = get_ai_repository()
    transcript = await ai_repo.get_transcript(resource_id)

    # Track unified_task so we can mark it failed if the Celery dispatch
    # itself throws — otherwise the row is orphaned in "processing" until
    # the reaper catches it (which logs "Stale task timeout", masking the
    # real error).
    _orphan_task_id: str | None = None

    try:
        if transcript and transcript.get("full_text"):
            # Transcript exists, dispatch ai_summary_workflow.
            import uuid as _uuid

            from app.services.infra.dbos_orchestrator import start_workflow_routed
            from app.services.infra.unified_task_manager import get_task_manager
            from app.workflows.ai_summary import ai_summary_workflow

            tracker = get_task_manager()
            wf_id = str(_uuid.uuid4())
            # "Send to agent" transcribes first and fires this summary from a
            # separate request once the transcript lands. Hang it off that
            # transcription's flow so the user sees one multi-step card
            # instead of a 1/1 card plus a loose row. None (no recent
            # transcription of ours, or the lookup failed) keeps the old
            # un-grouped behaviour.
            _flow_id = await _find_joinable_flow_id(resource_id, auth.user_id)
            task_id = await tracker.create(
                user_id=auth.user_id,
                task_type="ai_summary",
                title=f"Summarize {_task_display_name(platform_id, resource, media)}",
                media_id=platform_id,
                resource_id=resource_id,
                dbos_workflow_id=wf_id,
                flow_id=_flow_id,
            )
            _orphan_task_id = task_id

            await start_workflow_routed(
                "ai_summary",
                dbos_workflow_callable=ai_summary_workflow,
                dbos_workflow_kwargs={
                    "parsed_media_id": int(media["id"]),
                    # Run as the resource OWNER, matching the points charge
                    # above and load_summary_inputs' creator_id filter — a
                    # team member triggering summary on a shared resource
                    # used to charge the owner then fail "no transcript"
                    # (identity mismatch, 2026-08-07 diagnosis).
                    "user_id": resource_owner,
                },
                workflow_id=wf_id,
            )
            _orphan_task_id = None
            return {
                "message": "Summary generation queued",
                "resource_id": resource_id,
                "platform_id": platform_id,
                # Always present, so a caller never has to read "field
                # absent" as "not charged" — the two already-in-progress
                # branches report 0 for the same reason.
                "points_charged": _points_cost,
                "already_summarized": False,
            }
        else:
            # No transcript yet — dispatch transcription. PR-D7 phase
            # 3b: chain_ai_pipeline used to celery-chain transcribe →
            # summary. With DBOS, summary needs a parent workflow to
            # depend on transcript completion. For now we dispatch
            # transcription only; the user re-triggers summary once
            # the transcript lands (frontend polls).
            from app.services.infra.dbos_orchestrator import start_workflow_routed
            from app.workflows.ai_transcription import ai_transcription_workflow

            await start_workflow_routed(
                "ai_transcription",
                dbos_workflow_callable=ai_transcription_workflow,
                dbos_workflow_kwargs={
                    "parsed_media_id": int(media["id"]),
                    "user_id": auth.user_id,
                },
            )
            return {
                "message": (
                    "Transcription queued; trigger summary again once "
                    "transcript is ready"
                ),
                "resource_id": resource_id,
                "platform_id": platform_id,
                "points_charged": _points_cost,
                "already_summarized": False,
            }
    except Exception as e:
        # Same race as the transcribe endpoint: another request created the
        # active ai_summary task between our dedup SELECT and this INSERT,
        # and migration 121's partial unique index rejected ours. Its own
        # comment tells callers to treat that as "already in progress" —
        # a 500 would report failure for work that is actually running.
        # `_orphan_task_id` is still None here (create() is what raised), so
        # there is no task row to fail; the charge does need giving back.
        if _orphan_task_id is None and is_active_task_conflict(e):
            if _points_cost > 0 and _team_id:
                try:
                    await points_service.refund_points(
                        team_id=_team_id,
                        user_id=auth.user_id,
                        amount=_points_cost,
                        reference_type="ai_summary",
                        reference_id=resource_id,
                        reason="Another summary task is already active",
                    )
                except Exception as refund_err:
                    logger.error(
                        f"Failed to refund points after dedup conflict: {refund_err}"
                    )
            logger.info(
                f"[ai_router] summary for resource {resource_id} already active "
                "(unique index); returning already-in-progress"
            )
            return {
                "message": "Summary already in progress",
                "resource_id": resource_id,
                "platform_id": platform_id,
                "points_charged": 0,
                "already_summarized": False,
            }
        if _orphan_task_id:
            try:
                from app.services.infra.unified_task_manager import get_task_manager

                await get_task_manager().fail(
                    _orphan_task_id,
                    f"Dispatch failed: {str(e)[:180]}",
                    error_code="DISPATCH_ERROR",
                )
            except Exception as fail_err:
                logger.error(
                    f"Failed to mark orphan task {_orphan_task_id} failed: {fail_err}"
                )
        if _points_cost > 0 and _team_id:
            try:
                await points_service.refund_points(
                    team_id=_team_id,
                    user_id=auth.user_id,
                    amount=_points_cost,
                    reference_type="ai_summary",
                    reference_id=resource_id,
                    reason=f"Task dispatch failed: {str(e)[:100]}",
                )
                logger.info(
                    f"Refunded {_points_cost} points for failed summary dispatch"
                )
            except Exception as refund_err:
                logger.error(f"Failed to refund points: {refund_err}")
        raise HTTPException(
            status_code=500, detail=f"Failed to queue summary: {str(e)}"
        )


@router.post("/analyze/resource/{resource_id}")
async def trigger_visual_analysis_by_resource(
    resource_id: str, auth: AuthDep, _scope: ScopedRequestDep
):
    """Manually trigger L1 cover analysis (analyze_l1_workflow).

    Mirrors the trigger_summary_by_resource pattern: dedup → pre-create
    task_tracking → dispatch DBOS workflow with the same workflow_id so
    the Task Center sees the run and Realtime pushes lifecycle changes
    back to the frontend (otherwise a click would be invisible)."""
    resource, platform_id, media = await _resolve_resource_to_platform_id(
        resource_id, auth.user_id
    )

    # Dedup: skip if a run is already in flight for this resource.
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import TaskTracking

    async with read_scope() as session:
        _active = (
            await session.execute(
                select(TaskTracking.dbos_workflow_id)
                .where(TaskTracking.resource_id == resource_id)
                .where(TaskTracking.task_type == "ai_extract")
                .where(TaskTracking.status.in_(["pending", "processing", "running"]))
                .limit(1)
            )
        ).first()
    if _active:
        return {
            "message": "Visual analysis already in progress",
            "resource_id": resource_id,
        }

    cover_url = (media or {}).get("cover_urls") or []
    cover_url = cover_url[0] if cover_url else None
    if not cover_url:
        raise HTTPException(
            status_code=400,
            detail="No cover image to analyze for this resource",
        )

    _orphan_task_id: str | None = None
    try:
        import uuid as _uuid

        from app.services.infra.dbos_orchestrator import start_workflow_routed
        from app.services.infra.unified_task_manager import get_task_manager
        from app.workflows.analyze_l1 import analyze_l1_workflow

        tracker = get_task_manager()
        wf_id = str(_uuid.uuid4())
        _orphan_task_id = await tracker.create(
            user_id=auth.user_id,
            task_type="ai_extract",
            title=f"Analyze {_task_display_name(platform_id, resource, media)}",
            subtitle="L1 cover analysis",
            media_id=platform_id,
            resource_id=resource_id,
            dbos_workflow_id=wf_id,
        )

        await start_workflow_routed(
            "ai_extract",
            dbos_workflow_callable=analyze_l1_workflow,
            dbos_workflow_kwargs={
                "media_id": int(media["id"]),
                "cover_url": cover_url,
                "title": (media or {}).get("title") or "",
                "description": (media or {}).get("description") or "",
                "user_id": auth.user_id,
            },
            workflow_id=wf_id,
        )
        _orphan_task_id = None
        return {
            "message": "Visual analysis queued",
            "resource_id": resource_id,
            "platform_id": platform_id,
        }
    except Exception as e:
        if _orphan_task_id:
            try:
                from app.services.infra.unified_task_manager import get_task_manager

                await get_task_manager().fail(
                    _orphan_task_id,
                    f"Dispatch failed: {str(e)[:180]}",
                    error_code="DISPATCH_ERROR",
                )
            except Exception as fail_err:
                logger.error(
                    f"Failed to mark orphan task {_orphan_task_id} failed: {fail_err}"
                )
        raise HTTPException(
            status_code=500, detail=f"Failed to queue visual analysis: {str(e)}"
        )


# ------------------------------------------------------------------
# Manual triggers (platform_id-based, legacy)
# ------------------------------------------------------------------


@router.post("/transcribe/{platform_id}")
async def trigger_transcription(
    platform_id: str, auth: AuthDep, _scope: ScopedRequestDep
):
    """Manually trigger transcription for a video (legacy, platform_id-based).

    Audio-ready → transcribe directly. No audio yet but a video file is on
    disk → extract audio first, then chain transcription (no more 409 dead
    end). Neither → 409.
    """
    media_row = await _get_media_or_404(platform_id)

    # === Audio-readiness classification (BEFORE billing so a dead-end
    # 409 never leaves points charged) ===
    _has_audio = bool(
        (media_row or {}).get("extract_audio_path")
        or (media_row or {}).get("music_download_path")
    )
    _can_extract = _has_extractable_video(media_row)
    if not _has_audio and not _can_extract:
        raise HTTPException(
            status_code=409,
            detail=(
                "This media has no audio track available — "
                "download/extraction hasn't produced one."
            ),
        )
    # === End classification ===

    # === Points check ===
    points_service = PointsService()
    _team_id = await get_team_id_for_user(auth.user_id)
    _points_cost = 0
    if _team_id:
        await points_service.ensure_team_quota(_team_id, user_id=auth.user_id)
        points_result = await points_service.check_and_consume(
            team_id=_team_id,
            user_id=auth.user_id,
            action_type="ai_transcription",
        )
        if not points_result["success"]:
            raise HTTPException(status_code=402, detail=points_result["reason"])
        _points_cost = points_result.get("points_cost", 0)
    # === End points check ===

    # Track unified_task so the Task Center sees this run + Realtime
    # pushes status changes back to the frontend (manual click would
    # otherwise be invisible — the bug behind the "I clicked but
    # nothing showed up" report).
    _orphan_task_id: str | None = None

    try:
        import uuid as _uuid

        # Lookup the user's resource for this platform_id (best-effort —
        # transcription can run without resource_id, the task_tracking
        # row just won't link back to a card).
        from app.repositories.resources_repository import ResourcesRepository
        from app.services.infra.dbos_orchestrator import start_workflow_routed
        from app.services.infra.unified_task_manager import get_task_manager

        owner_resource = (
            await ResourcesRepository().get_resource_by_media_id_and_creator(
                str(media_row["id"]), auth.user_id
            )
        )
        owner_resource_id = str(owner_resource["id"]) if owner_resource else None

        tracker = get_task_manager()
        wf_id = str(_uuid.uuid4())

        # Same flow root as the by-resource endpoint above. This legacy
        # platform_id path is still what the homepage MediaCard's Transcribe
        # button calls (frontend/components/MediaCard.tsx), so leaving it
        # flow-less would make the step card depend on which button was
        # clicked.
        _video_title = _task_display_name(platform_id, owner_resource, media_row)
        flow_id = await tracker.create_flow(
            user_id=auth.user_id,
            name=f"Process {_video_title}",
        )

        if _has_audio:
            # PR-D7 phase 3b: dispatch ai_transcription_workflow directly.
            from app.workflows.ai_transcription import ai_transcription_workflow

            _orphan_task_id = await tracker.create(
                user_id=auth.user_id,
                task_type="ai_transcription",
                title=f"Transcribe {_video_title}",
                media_id=platform_id,
                resource_id=owner_resource_id,
                dbos_workflow_id=wf_id,
                flow_id=flow_id,
            )
            await start_workflow_routed(
                "ai_transcription",
                dbos_workflow_callable=ai_transcription_workflow,
                dbos_workflow_kwargs={
                    "parsed_media_id": int(media_row["id"]),
                    "user_id": auth.user_id,
                },
                workflow_id=wf_id,
            )
        else:
            # No audio yet but a video file exists → extract audio first,
            # then chain transcription unconditionally.
            from app.workflows.extract_audio import extract_audio_workflow

            _orphan_task_id = await tracker.create(
                user_id=auth.user_id,
                task_type="extract_audio",
                title=f"Audio {_video_title}",
                subtitle="Extracting audio, then transcribing",
                media_id=platform_id,
                resource_id=owner_resource_id,
                dbos_workflow_id=wf_id,
                flow_id=flow_id,
                # Same intent record as the by-resource endpoint above.
                metadata={"chain_transcription": True},
            )
            await start_workflow_routed(
                "extract_audio",
                dbos_workflow_callable=extract_audio_workflow,
                dbos_workflow_kwargs={
                    "platform_id": platform_id,
                    "user_id": auth.user_id,
                    "resource_id": owner_resource_id,
                    "flow_id": flow_id,
                    "video_title": _video_title,
                    "chain_transcription": True,
                },
                workflow_id=wf_id,
            )
        _orphan_task_id = None
    except Exception as e:
        if _orphan_task_id:
            try:
                from app.services.infra.unified_task_manager import get_task_manager

                await get_task_manager().fail(
                    _orphan_task_id,
                    f"Dispatch failed: {str(e)[:180]}",
                    error_code="DISPATCH_ERROR",
                )
            except Exception as fail_err:
                logger.error(
                    f"Failed to mark orphan task {_orphan_task_id} failed: {fail_err}"
                )
        if _points_cost > 0 and _team_id:
            try:
                await points_service.refund_points(
                    team_id=_team_id,
                    user_id=auth.user_id,
                    amount=_points_cost,
                    reference_type="ai_transcription",
                    reference_id=platform_id,
                    reason=f"Task dispatch failed: {str(e)[:100]}",
                )
                logger.info(
                    f"Refunded {_points_cost} points for failed transcription dispatch"
                )
            except Exception as refund_err:
                logger.error(f"Failed to refund points: {refund_err}")
        raise HTTPException(
            status_code=500, detail=f"Failed to queue transcription: {str(e)}"
        )

    return {
        "message": (
            "Transcription queued"
            if _has_audio
            else "Audio extraction started — transcription will follow"
        ),
        "platform_id": platform_id,
        "extracting_audio": not _has_audio,
    }


@router.post("/summarize/{platform_id}")
async def trigger_summary(platform_id: str, auth: AuthDep, _scope: ScopedRequestDep):
    """Manually trigger summary generation for a video (legacy, platform_id-based).

    Requires an existing transcript. If no transcript exists,
    queues the full pipeline (extract -> transcribe -> summarize).
    """
    # === Points check ===
    points_service = PointsService()
    _team_id = await get_team_id_for_user(auth.user_id)
    _points_cost = 0
    if _team_id:
        await points_service.ensure_team_quota(_team_id, user_id=auth.user_id)
        points_result = await points_service.check_and_consume(
            team_id=_team_id,
            user_id=auth.user_id,
            action_type="ai_summary",
        )
        if not points_result["success"]:
            raise HTTPException(status_code=402, detail=points_result["reason"])
        _points_cost = points_result.get("points_cost", 0)
    # === End points check ===

    media = await _get_media_or_404(platform_id)
    media_id = media["id"]

    # Resolve resource_id for per-resource transcript lookup (user-specific)
    res_repo = ResourcesRepository()
    resource = await res_repo.get_resource_by_media_id_and_creator(
        media_id, auth.user_id
    )
    _resource_id = str(resource["id"]) if resource else None

    ai_repo = get_ai_repository()
    transcript = await ai_repo.get_transcript(_resource_id) if _resource_id else None

    _orphan_task_id: str | None = None

    try:
        if transcript and transcript.get("full_text"):
            # Transcript exists — dispatch ai_summary_workflow.
            import uuid as _uuid

            from app.services.infra.dbos_orchestrator import start_workflow_routed
            from app.services.infra.unified_task_manager import get_task_manager
            from app.workflows.ai_summary import ai_summary_workflow

            tracker = get_task_manager()
            wf_id = str(_uuid.uuid4())
            # Same grouping as the by-resource endpoint — which card the user
            # gets must not depend on which button they clicked. A missing
            # resource answers None (the helper's own guard).
            _flow_id = await _find_joinable_flow_id(_resource_id, auth.user_id)
            task_id = await tracker.create(
                user_id=auth.user_id,
                task_type="ai_summary",
                title=f"Summarize {_task_display_name(platform_id, resource, media)}",
                media_id=platform_id,
                resource_id=_resource_id,
                dbos_workflow_id=wf_id,
                flow_id=_flow_id,
            )
            _orphan_task_id = task_id

            await start_workflow_routed(
                "ai_summary",
                dbos_workflow_callable=ai_summary_workflow,
                dbos_workflow_kwargs={
                    "parsed_media_id": int(media_id),
                    "user_id": auth.user_id,
                },
                workflow_id=wf_id,
            )
            _orphan_task_id = None
            return {"message": "Summary generation queued", "platform_id": platform_id}
        else:
            # No transcript yet — dispatch transcription only. PR-D7
            # phase 3b: see trigger_summary_by_resource for rationale.
            from app.services.infra.dbos_orchestrator import start_workflow_routed
            from app.workflows.ai_transcription import ai_transcription_workflow

            await start_workflow_routed(
                "ai_transcription",
                dbos_workflow_callable=ai_transcription_workflow,
                dbos_workflow_kwargs={
                    "parsed_media_id": int(media_id),
                    "user_id": auth.user_id,
                },
            )
            return {
                "message": "Transcription queued; trigger summary again once transcript is ready",
                "platform_id": platform_id,
            }
    except Exception as e:
        if _orphan_task_id:
            try:
                from app.services.infra.unified_task_manager import get_task_manager

                await get_task_manager().fail(
                    _orphan_task_id,
                    f"Dispatch failed: {str(e)[:180]}",
                    error_code="DISPATCH_ERROR",
                )
            except Exception as fail_err:
                logger.error(
                    f"Failed to mark orphan task {_orphan_task_id} failed: {fail_err}"
                )
        if _points_cost > 0 and _team_id:
            try:
                await points_service.refund_points(
                    team_id=_team_id,
                    user_id=auth.user_id,
                    amount=_points_cost,
                    reference_type="ai_summary",
                    reference_id=platform_id,
                    reason=f"Task dispatch failed: {str(e)[:100]}",
                )
                logger.info(
                    f"Refunded {_points_cost} points for failed summary dispatch"
                )
            except Exception as refund_err:
                logger.error(f"Failed to refund points: {refund_err}")
        raise HTTPException(
            status_code=500, detail=f"Failed to queue summary: {str(e)}"
        )


@router.post("/analyze/{platform_id}")
async def trigger_visual_analysis(platform_id: str, auth: AuthDep):
    """Manually trigger visual analysis (L1: cover image) for a video.

    Queues L1 analysis via Celery. If a local video file exists,
    L2 analysis (cover + keyframes) is queued instead.
    """
    await _get_media_or_404(platform_id)

    # === Points check ===
    points_service = PointsService()
    _team_id = await get_team_id_for_user(auth.user_id)
    _points_cost = 0
    if _team_id:
        await points_service.ensure_team_quota(_team_id, user_id=auth.user_id)
        points_result = await points_service.check_and_consume(
            team_id=_team_id,
            user_id=auth.user_id,
            action_type="ai_visual_analysis",
        )
        if not points_result["success"]:
            raise HTTPException(status_code=402, detail=points_result["reason"])
        _points_cost = points_result.get("points_cost", 0)
    # === End points check ===

    # Visual analysis is not yet implemented -- refund consumed points
    if _points_cost > 0 and _team_id:
        try:
            await points_service.refund_points(
                team_id=_team_id,
                user_id=auth.user_id,
                amount=_points_cost,
                reference_type="ai_visual_analysis",
                reference_id=platform_id,
                reason="Visual analysis not yet implemented",
            )
        except Exception as refund_err:
            logger.error(f"Failed to refund points: {refund_err}")

    raise HTTPException(
        status_code=501,
        detail="Visual analysis is not yet implemented",
    )


# ------------------------------------------------------------------
# Data retrieval
# ------------------------------------------------------------------


@router.get("/transcript/resource/{resource_id}", response_model=TranscriptResponse)
async def get_transcript_by_resource(
    resource_id: str, auth: AuthDep, _scope: ScopedRequestDep
):
    """Get transcript for a resource.

    "Resource not transcribed yet" returns 200 with full_text=None — NOT
    404. Returning 404 makes Chrome paint the entire request red in
    DevTools every time the user opens the Transcript tab on an
    un-transcribed video, even though the UI handles it correctly. The
    poll helper looks for full_text presence, so it keeps polling on
    null and stops on a real transcript.
    """
    # Ownership check
    res_repo = ResourcesRepository()
    resource = await res_repo.get_resource_by_id(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")
    if resource.get("creator_id") != auth.user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    ai_repo = get_ai_repository()
    transcript = await ai_repo.get_transcript(resource_id) or {}

    return TranscriptResponse(
        media_id=resource_id,
        language=transcript.get("language"),
        full_text=transcript.get("full_text"),
        segments=transcript.get("segments"),
        whisper_model=transcript.get("whisper_model"),
        duration_seconds=transcript.get("duration_seconds"),
        created_at=transcript.get("created_at"),
    )


@router.get("/transcript/{platform_id}", response_model=TranscriptResponse)
async def get_transcript(platform_id: str, auth: AuthDep, _scope: ScopedRequestDep):
    """Get transcript for a media item (legacy, resolves resource from media)."""
    media = await _get_media_or_404(platform_id)
    media_id = media["id"]

    # Resolve resource_id from media (user-specific)
    res_repo = ResourcesRepository()
    resource = await res_repo.get_resource_by_media_id_and_creator(
        media_id, auth.user_id
    )
    if not resource:
        raise HTTPException(status_code=404, detail="No resource linked to this media")

    ai_repo = get_ai_repository()
    transcript = await ai_repo.get_transcript(str(resource["id"])) or {}

    # 200 + nulls (not 404) when no transcript exists yet — same reason
    # as the by-resource endpoint above. Stops Chrome from painting the
    # request red on every Transcript tab open.
    return TranscriptResponse(
        media_id=media_id,
        language=transcript.get("language"),
        full_text=transcript.get("full_text"),
        segments=transcript.get("segments"),
        whisper_model=transcript.get("whisper_model"),
        duration_seconds=transcript.get("duration_seconds"),
        created_at=transcript.get("created_at"),
    )


@router.get("/summary/resource/{resource_id}", response_model=SummaryResponse)
async def get_summary_by_resource(
    resource_id: str, auth: AuthDep, _scope: ScopedRequestDep
):
    """Get summary for a resource."""
    # Ownership check
    res_repo = ResourcesRepository()
    resource = await res_repo.get_resource_by_id(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")
    if resource.get("creator_id") != auth.user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    ai_repo = get_ai_repository()
    summary = await ai_repo.get_summary(resource_id) or {}

    return SummaryResponse(
        media_id=resource_id,
        summary_type=summary.get("summary_type"),
        summary_text=summary.get("summary_text"),
        key_points=summary.get("key_points"),
        topics=summary.get("topics"),
        llm_model=summary.get("llm_model"),
        llm_provider=summary.get("llm_provider"),
        created_at=summary.get("created_at"),
    )


@router.get("/analysis/resource/{resource_id}", response_model=VisualAnalysisResponse)
async def get_analysis_by_resource(
    resource_id: str, auth: AuthDep, _scope: ScopedRequestDep = None
):
    """Get the L1 visual (cover) analysis for a resource.

    Returns 200 with null fields (NOT 404) when the resource hasn't been
    analyzed yet — same rationale as the transcript/summary endpoints: avoids a
    red request in DevTools on every tab open, and lets the frontend detect
    presence via `visual_description`. ``resource_analysis`` is keyed by
    ``resource_id`` (FK → resources.id, mig 076/262) — NOT the media id — so we
    read by the resource's own id. (The write path keys it the same way; reading
    by ``media.id`` here was why the vision card always showed "not analyzed".)
    """
    resource, _platform_id, media = await _resolve_resource_to_platform_id(
        resource_id, auth.user_id
    )
    if resource.get("creator_id") != auth.user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    analysis = await get_analysis_repository().get_analysis(int(resource["id"])) or {}

    return VisualAnalysisResponse(
        media_id=str(media["id"]),
        analysis_level=analysis.get("analysis_level"),
        visual_description=analysis.get("visual_description"),
        detected_objects=analysis.get("detected_objects"),
        detected_scenes=analysis.get("detected_scenes"),
        detected_people=analysis.get("detected_people"),
        detected_text=analysis.get("detected_text"),
        analysis_model=analysis.get("analysis_model"),
        analysis_cost=analysis.get("analysis_cost"),
        analyzed_at=analysis.get("analyzed_at"),
    )


@router.get("/summary/{platform_id}", response_model=SummaryResponse)
async def get_summary(platform_id: str, auth: AuthDep, _scope: ScopedRequestDep):
    """Get summary for a media item (legacy, resolves resource from media)."""
    media = await _get_media_or_404(platform_id)
    media_id = media["id"]

    # Resolve resource_id from media (user-specific)
    res_repo = ResourcesRepository()
    resource = await res_repo.get_resource_by_media_id_and_creator(
        media_id, auth.user_id
    )
    if not resource:
        raise HTTPException(status_code=404, detail="No resource linked to this media")

    ai_repo = get_ai_repository()
    summary = await ai_repo.get_summary(str(resource["id"])) or {}

    return SummaryResponse(
        media_id=media_id,
        summary_type=summary.get("summary_type"),
        summary_text=summary.get("summary_text"),
        key_points=summary.get("key_points"),
        topics=summary.get("topics"),
        llm_model=summary.get("llm_model"),
        llm_provider=summary.get("llm_provider"),
        created_at=summary.get("created_at"),
    )

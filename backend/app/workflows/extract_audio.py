"""extract_audio DBOS workflow.

Promotes audio extraction from an inline call inside
``download.run_download_step`` to its own DBOS workflow with a visible
task_tracking row + status.

Why
---
Audio extraction (ffmpeg stream-copy from the downloaded video) used to
run synchronously inside ``run_download_step``. That buried it: no
independent status, no independent retry, and the transcript/summary
chain gate read the wrong field (``music_download_status`` — which means
"music URL downloaded", not "audio extracted from a video").

This workflow:
  - writes ``parsed_media.extract_audio_status`` (processing →
    completed/failed) so the homepage MediaCard audio icon shows a
    3-state indicator instead of a binary "file exists?" check
  - on success, chains ai_transcription / ai_summary if the resource
    carries the matching intent tags (via
    ``download_helpers.chain_transcript_summary_for_tags``)
  - on failure raises (CLAUDE.md 路线 C rule 4: never return a failed
    dict — DBOS would mark the workflow SUCCESS and the mirror trigger
    would stamp task_tracking completed while the audio is missing)

The actual extraction logic stays in
``download_helpers.extract_audio_from_video`` (still writes only
``extract_audio_path``, NOT ``music_download_status`` — ffmpeg-extracted
audio is not downloaded music).
"""

from __future__ import annotations

from typing import Any, Optional

from dbos import DBOS
from loguru import logger


@DBOS.step()
async def mark_extract_audio_status_step(platform_id: str, status: str) -> None:
    """Write parsed_media.extract_audio_status. Best-effort — a transient
    PostgREST hiccup here is cosmetic (the icon lags), it must not fail
    the workflow."""
    from app.repositories.media_repository import get_media_repository

    try:
        await get_media_repository().update(
            platform_id, {"extract_audio_status": status}
        )
    except Exception as e:
        logger.warning(
            f"[extract_audio] status write '{status}' failed for "
            f"{platform_id} (non-fatal): {e}"
        )


@DBOS.step(retries_allowed=True, max_attempts=2)
def run_extract_audio_step(platform_id: str) -> bool:
    """Run the ffmpeg stream-copy extraction. Returns True on success.

    Stays sync — ``extract_audio_from_video`` is a pure ffmpeg subprocess
    dispatch. Per-attempt retry covers transient IO errors."""
    from app.tasks.download_helpers import extract_audio_from_video

    return extract_audio_from_video(platform_id)


@DBOS.step()
async def mark_extract_audio_processing_step(workflow_id: str) -> None:
    """Push task_tracking.phase 'queued' → 'processing'. Same rationale as
    download.mark_extract_audio_processing_step — the mirror trigger only
    writes ``status``, leaving ``phase`` stuck at 'queued' otherwise."""
    from app.services.infra.unified_task_manager import get_task_manager

    try:
        await get_task_manager().start(workflow_id)
    except Exception as e:
        logger.warning(f"[extract_audio.mark_processing] {workflow_id}: {e}")


@DBOS.step()
async def log_extract_audio_outcome_step(
    *,
    user_id: str,
    platform_id: str,
    video_title: str,
    outcome: str,
    error: Optional[str] = None,
) -> None:
    """Audit row in user_logs (Activity Logs panel). Best-effort — mirrors
    download.log_download_outcome_step so extract_audio shows up alongside
    download / cover entries instead of silently disappearing from the log."""
    from app.repositories.user_logs_repository import log_user_action

    title_clip = (video_title or platform_id or "")[:30]
    try:
        if outcome == "success":
            await log_user_action(
                user_id=user_id,
                action="extract_audio",
                message=f"Audio extracted: {title_clip}...",
                status="success",
                aweme_id=platform_id,
            )
        else:
            await log_user_action(
                user_id=user_id,
                action="extract_audio",
                message=f"Audio extraction failed: {title_clip}...",
                status="error",
                aweme_id=platform_id,
                details={"error": (error or "unknown")[:200]},
            )
    except Exception as e:
        logger.warning(f"[extract_audio.log] {e}")


@DBOS.workflow()
async def extract_audio_workflow(
    platform_id: str,
    user_id: str,
    *,
    resource_id: Optional[str] = None,
    flow_id: Optional[str] = None,
    video_title: str = "",
) -> dict[str, Any]:
    """Extract audio from the downloaded video, then chain
    transcript/summary if the resource carries those intent tags.

    workflow_id idempotency: re-running with the same id replays the
    cached extraction result (the ffmpeg pass runs once).
    """
    await mark_extract_audio_processing_step(DBOS.workflow_id)
    await mark_extract_audio_status_step(platform_id, "processing")

    try:
        ok = run_extract_audio_step(platform_id)
    except Exception as e:
        await mark_extract_audio_status_step(platform_id, "failed")
        await log_extract_audio_outcome_step(
            user_id=user_id,
            platform_id=platform_id,
            video_title=video_title,
            outcome="failed",
            error=str(e),
        )
        # Re-raise so DBOS marks the workflow FAILED and the
        # mirror_dbos_lifecycle_to_tracking trigger writes phase=failed.
        raise RuntimeError(f"audio extraction errored for {platform_id}: {e}") from e

    if not ok:
        await mark_extract_audio_status_step(platform_id, "failed")
        await log_extract_audio_outcome_step(
            user_id=user_id,
            platform_id=platform_id,
            video_title=video_title,
            outcome="failed",
            error="ffmpeg returned non-success",
        )
        raise RuntimeError(f"audio extraction failed for {platform_id}")

    await mark_extract_audio_status_step(platform_id, "completed")
    await log_extract_audio_outcome_step(
        user_id=user_id,
        platform_id=platform_id,
        video_title=video_title,
        outcome="success",
    )

    # Chain transcript/summary IFF the resource carries the intent tags.
    # Called from the workflow body (not a @DBOS.step) because it calls
    # start_workflow_routed → DBOS.start_workflow, which asserts when
    # invoked from inside a step context. Best-effort — never fails this
    # workflow. No audio-ready gate needed: we just extracted it.
    try:
        from app.db.scope import Scope, request_scope
        from app.tasks.download_helpers import chain_transcript_summary_for_tags

        # A2 pass 4b: chain_transcript_summary_for_tags is SYNC and reads
        # `resources` via run_async (get_resource_by_media_id_and_creator). Set
        # the ambient USER scope HERE at the async caller; pass-4a's
        # copy_context wrap carries it down through run_async's thread hop into
        # that resource read. `user_id` is a required workflow arg (always
        # present). INERT until SCOPE_ENFORCE_RESOURCES flips.
        async with request_scope(Scope(user_id=user_id)):
            chain_transcript_summary_for_tags(
                platform_id, user_id, flow_id=flow_id, video_title=video_title
            )
    except Exception as e:
        logger.warning(
            f"[extract_audio] transcript/summary chain failed for "
            f"{platform_id}: {type(e).__name__}: {e!r}"
        )

    return {"status": "success", "platform_id": platform_id}

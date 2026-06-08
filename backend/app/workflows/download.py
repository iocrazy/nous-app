"""download DBOS workflow — port of `download_tasks.download_unified_task`.

The legacy file is 484 LOC; about 60% is unified_task_manager
lifecycle glue + Celery retry boilerplate that DBOS handles natively.
The actual download work delegates to:
    - download_strategies._do_douyin_download (unified strategy —
      reads URLs from DB, httpx direct + yt-dlp fallback)

Both strategy functions accept a UnifiedProgressTracker. We pass one
with unified_tracker=None — the tracker degrades to pure Redis pub/sub
mode (channel `task_progress:{user_id}`), which is exactly what the
WebSocket layer consumes anyway. DBOS owns workflow lifecycle now;
the legacy task_tracking bookkeeping is dropped here and rebuilt as
the DBOS-status bridge in D4.

Steps:
  - check_global_cache_step: short-circuit if all requested types are
    already on the shared cache (parsed_media has download_path +
    *_status='completed')
  - run_download_step: dispatch to strategy; retry-allowed for
    transient HTTP failures (max_attempts=3 mirrors the Celery
    max_retries=3 budget)
  - finalize_post_download_step: resource_version v1 creation,
    file_size_bytes mirror onto resources, parsed_media status
    fallback, audit log
  - chain_followups_step: thumbnail_workflow + transcode (TODO: rewire
    to DBOS once D4 swaps maybe_chain_* call sites) + AI pipeline

⚠️ Pending before flipping dbos_workflow_routing to 'shadow' then
'dbos': end-to-end test against (a) a fresh Douyin URL and (b) a
yt-dlp-supported URL. The dispatch site is parse_workflow (not yet
ported) — D4 wiring will swap parse → download chain.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Optional

from dbos import DBOS, Queue
from loguru import logger

# Per-user partitioned queue for GENERIC downloads (non-soda). Mirrors
# parse_user_queue / soda_download_queue: at most `concurrency` downloads run
# PER USER at once; the rest queue durably. Bounds a user's egress hit-rate for
# big batches (ban-avoidance) and fits the future per-user proxy/fingerprint
# model. Module-level so the poller registers before DBOS.launch().
MAX_DOWNLOAD_CONCURRENCY_DEFAULT = int(
    os.environ.get("MAX_DOWNLOAD_CONCURRENCY_PER_USER", "3")
)
download_user_queue = Queue(
    "download_user",
    worker_concurrency=MAX_DOWNLOAD_CONCURRENCY_DEFAULT,
    partition_queue=True,
)


def set_download_concurrency(n: int) -> None:
    """Set the per-user generic-download queue concurrency live (clamped 1..20).

    Driven by the `config.parse_concurrency` lifecycle subscriber when a user
    saves Settings → General. The DBOS poller re-reads `concurrency` each cycle,
    so the change takes effect without a restart. Garbage input is swallowed
    (the value comes from user settings).
    """
    try:
        n = max(1, min(20, int(n)))
        download_user_queue.worker_concurrency = n
        logger.info(f"[download_queue] per-user concurrency set to {n}")
    except Exception as e:
        logger.warning(f"[download_queue] set concurrency failed: {e}")


@DBOS.step()
async def check_global_cache_step(
    *,
    platform_id: str,
    media_type: int,
    download_video: bool,
    download_cover: bool,
) -> dict[str, Any]:
    """Return {cache_hit: True} when all requested asset types are
    already on shared parsed_media AND the underlying files actually
    exist on disk. Otherwise return {cache_hit: False} so the workflow
    proceeds to the strategy dispatch.

    The on-disk verification matters: dev DBs often carry status=completed
    rows pointing at files that were moved / never copied during a
    storage-isolation cut-over (or that prod cleaned up). Without it L4
    happily writes a resource_version v1 against a missing file and the
    user sees a black thumbnail + an empty video player."""
    import os

    from app.core.config import settings
    from app.repositories.media_repository import get_media_repository

    def _file_present(rel_or_abs_path: str | None) -> bool:
        if not rel_or_abs_path:
            return False
        p = (
            rel_or_abs_path
            if os.path.isabs(rel_or_abs_path)
            else os.path.join(settings.DOWNLOAD_PATH, rel_or_abs_path)
        )
        try:
            return os.path.exists(p) and os.path.getsize(p) > 0
        except OSError:
            return False

    media_repo = get_media_repository()
    global_media = await media_repo.get_by_platform_id(platform_id)
    if not global_media:
        return {"cache_hit": False}

    video_path = global_media.get("download_path")
    cover_path = global_media.get("cover_download_path")
    has_video_path = bool(video_path)
    has_cover_path = bool(cover_path)

    all_cached = True
    if download_video:
        video_status_ok = (
            global_media.get("image_download_status") == "completed"
            if int(media_type) in (2, 68)
            else global_media.get("video_download_status") == "completed"
        )
        all_cached = (
            all_cached
            and video_status_ok
            and has_video_path
            and _file_present(video_path)
        )
    if download_cover:
        all_cached = (
            all_cached
            and global_media.get("cover_download_status") == "completed"
            and has_cover_path
            and _file_present(cover_path)
        )
    return {"cache_hit": bool(all_cached)}


@DBOS.step(retries_allowed=True, max_attempts=3)
def run_download_step(
    *,
    workflow_id: str,
    platform_id: str,
    user_id: str,
    download_video: bool,
    download_cover: bool,
    media_type: int,
    user_agent: Optional[str],
) -> dict[str, Any]:
    """Drive the unified download strategy. Returns the per-asset
    {video, cover, music} → status dict.

    `workflow_id` is the parent DBOS workflow's id (= task_tracking PK).
    Passed in (rather than read from `DBOS.workflow_id` here) because we
    need it to flow into UnifiedProgressTracker so the Redis pub/sub
    payload carries the same id the frontend reducer matches against
    (`t.id === payload.unified_task_id`).

    Stays sync because `_do_douyin_download` is a pure-sync external-tool
    dispatch (httpx + yt-dlp subprocess) with sync Redis tracker.

    The previous URL-presence dispatch (`if url: _do_ytdlp_download
    else: _do_douyin_download`) was removed in PR #254 — see
    `download_strategies` module docstring for the douyin short-URL
    failure mode that motivated it."""
    from app.core.redis import get_sync_redis
    from app.tasks.download_progress import UnifiedProgressTracker
    from app.tasks.download_strategies import _do_douyin_download

    tracker = UnifiedProgressTracker(
        task_id=workflow_id,  # used as Redis key suffix `download_progress:{id}`
        redis_client=get_sync_redis(),
        unified_tracker=None,  # DBOS workflow status replaces this
        unified_task_id=workflow_id,  # frontend matches this against task.id
        user_id=user_id,
    )

    results = _do_douyin_download(
        platform_id=platform_id,
        user_id=user_id,
        download_video=download_video,
        download_cover=download_cover,
        media_type=media_type,
        tracker=tracker,
        user_agent=user_agent,
    )

    completed = [k for k, v in results.items() if v == "completed"]
    failed = [k for k, v in results.items() if v not in (None, "completed")]
    return {
        "results": results,
        "completed_parts": completed,
        "failed_parts": failed,
    }


@DBOS.step()
async def finalize_post_download_step(
    *,
    platform_id: str,
    user_id: str,
    resource_id: Optional[str],
    download_video: bool,
    download_cover: bool,
    media_type: int,
    results: dict[str, Any],
) -> dict[str, Any]:
    """Resource_version v1 creation, file_size_bytes mirror,
    parsed_media status fallback. Returns the canonical
    `fresh_download_path` so the chain step can dispatch thumbnail.

    Mirrors the legacy task's post-download bookkeeping verbatim
    except the task_tracking lifecycle calls (DBOS owns those now)."""
    from app.db.scope import Scope, request_scope
    from app.repositories.media_repository import get_media_repository
    from app.repositories.resources_repository import get_resources_repository

    media_repo = get_media_repository()
    fresh_download_path: Optional[str] = None
    actual_size = 0

    if resource_id:
        res_repo = get_resources_repository()

        # A2 pass 2: establish the ambient USER tenant scope for the
        # resources-repo access below (update_resource / get_versions /
        # create_version / update_version all act ON BEHALF OF this download's
        # user). `user_id` is a required step kwarg (uuid string) — always
        # present — so no system-scope fallback is needed. INERT until
        # SCOPE_ENFORCE_RESOURCES flips. The interleaved media_repo (parsed_media)
        # calls are harmless inside the scope (parsed_media is not enforced).
        async with request_scope(Scope(user_id=user_id)):
            fresh_media = await media_repo.get_by_platform_id(platform_id)
            if fresh_media:
                fresh_download_path = fresh_media.get("download_path")
                actual_size = (
                    fresh_media.get("storage_size")
                    or fresh_media.get("datasize_bytes")
                    or 0
                )
                if actual_size > 0:
                    # File is already on disk + parsed_media row carries the
                    # canonical size; the resources.file_size_bytes mirror is
                    # a UI nicety. Don't let a transient PostgREST hiccup
                    # here promote a successful download to a failed task —
                    # downstream `mark_task_user_visible_complete_step`
                    # would never get a chance to run, and 资源库 already
                    # shows the file the user wanted.
                    try:
                        await res_repo.update_resource(
                            resource_id, {"file_size_bytes": actual_size}
                        )
                    except Exception as e:
                        logger.warning(
                            "[download.finalize] file_size mirror failed "
                            "(non-fatal, file is already on disk) "
                            "resource_id=%s actual_size=%d err=%s: %r",
                            resource_id,
                            actual_size,
                            type(e).__name__,
                            e,
                        )

                # Mirror resolution from parsed_media → resources so the library
                # grid / Justified view can size by aspect ratio. The download
                # path previously only stored resolution on parsed_media, leaving
                # resources.resolution NULL (the download *detail* reads
                # parsed_media, so it looked fine there). Non-fatal.
                resolution = fresh_media.get("resolution")
                if resolution:
                    try:
                        await res_repo.update_resource(
                            resource_id, {"resolution": resolution}
                        )
                    except Exception as e:
                        logger.warning(
                            "[download.finalize] resolution mirror failed "
                            "(non-fatal) resource_id=%s err=%s: %r",
                            resource_id,
                            type(e).__name__,
                            e,
                        )

            try:
                existing_versions = await res_repo.get_versions(resource_id)
                if not existing_versions and fresh_download_path:
                    file_path = fresh_download_path
                    filename = (
                        file_path.rsplit("/", 1)[-1] if "/" in file_path else file_path
                    )
                    mime_type = "video/mp4"
                    if filename.endswith(".webm"):
                        mime_type = "video/webm"
                    elif filename.endswith(".mkv"):
                        mime_type = "video/x-matroska"
                    version_data = {
                        "resource_id": resource_id,
                        "version_number": 1,
                        "filename": filename,
                        "file_path": file_path,
                        "file_size_bytes": actual_size if actual_size > 0 else None,
                        "mime_type": mime_type,
                        "uploaded_by": user_id,
                    }
                    await res_repo.create_version(version_data)
                elif existing_versions and actual_size > 0:
                    for ver in existing_versions:
                        if not ver.get("file_size_bytes"):
                            await res_repo.update_version(
                                ver["id"], {"file_size_bytes": actual_size}
                            )
            except Exception as ve:
                logger.warning(f"[download.finalize] resource_version backfill: {ve}")

        # parsed_media status fallback
        pm_status_updates: dict[str, str] = {}
        if download_video:
            v = results.get("video")
            if v == "completed":
                if int(media_type) in (2, 68):
                    pm_status_updates["image_download_status"] = "completed"
                else:
                    pm_status_updates["video_download_status"] = "completed"
            # ffmpeg-extracted audio used to set results["music"] here so
            # this block could mirror it onto music_download_status. After
            # PR #283 the inline extraction moved to extract_audio_workflow,
            # results["music"] is never set, and ffmpeg-extracted audio
            # writes its own extract_audio_path/extract_audio_status fields.
            # music_download_status is now reserved for URL-downloaded
            # music (set by downloader.py directly).
        if download_cover and results.get("cover") == "completed":
            pm_status_updates["cover_download_status"] = "completed"

        if pm_status_updates:
            current_pm = fresh_media or await media_repo.get_by_platform_id(platform_id)
            if current_pm:
                needs_update = {
                    k: v
                    for k, v in pm_status_updates.items()
                    if current_pm.get(k) != "completed"
                }
                if needs_update:
                    # Same rationale as the file_size_bytes mirror above —
                    # this update flips parsed_media.*_download_status
                    # flags from 'pending' to 'completed' for UI badges.
                    # The file is already on disk; if PostgREST is
                    # momentarily unavailable here we'd otherwise mark
                    # the whole download workflow failed and stomp the
                    # subtitle the user sees, even though the user can
                    # already open the file in 资源库. (Reproducer:
                    # download_workflow 2d9d667d 2026-05-08, file
                    # landed but task_tracking ended up phase=failed
                    # subtitle stuck on "video + cover".)
                    try:
                        await media_repo.update(platform_id, needs_update)
                    except Exception as e:
                        logger.warning(
                            "[download.finalize] parsed_media status "
                            "flag update failed (non-fatal, file is "
                            "already on disk) platform_id=%s "
                            "needs_update=%s err=%s: %r",
                            platform_id,
                            needs_update,
                            type(e).__name__,
                            e,
                        )

    return {
        "fresh_download_path": fresh_download_path,
        "actual_size": actual_size,
    }


async def chain_followups_step(
    *,
    platform_id: str,
    user_id: str,
    resource_id: Optional[str],
    fresh_download_path: Optional[str],
    flow_id: Optional[str] = None,
    video_title: str = "",
) -> None:
    """Dispatch thumbnail + extract_audio + transcode + AI pipeline.
    Best-effort — failures here never fail the workflow.

    Every dispatched workflow pre-creates its own task_tracking row
    carrying ``flow_id`` so the UI can render parse → download →
    thumbnail / extract_audio / transcode / ai_* as one chain.

    NOT a `@DBOS.step` — `start_workflow_routed` calls
    `DBOS.start_workflow` which asserts when invoked from inside a step
    context (same constraint as dispatch_download_step). Must run in
    workflow body where the workflow context is active."""
    if not resource_id or not fresh_download_path:
        return

    mime_type = "video/mp4"
    if fresh_download_path.endswith(".webm"):
        mime_type = "video/webm"
    elif fresh_download_path.endswith(".mkv"):
        mime_type = "video/x-matroska"

    if mime_type.startswith("video/"):
        import uuid as _uuid

        from app.services.infra.dbos_orchestrator import start_workflow_routed
        from app.services.infra.unified_task_manager import get_task_manager
        from app.workflows.thumbnail import thumbnail_workflow

        # Friendly title prefix for the chained tasks so TaskCenter shows
        # "Thumbnail 惨！视频生成skill..." instead of a bare "Thumbnail" row
        # that the user can't tell which media it belongs to.
        title_clip = (video_title or platform_id or "")[:50]

        # thumbnail (also produces preview_sprite.jpg inside the service)
        try:
            thumb_wf_id = str(_uuid.uuid4())
            try:
                await get_task_manager().create(
                    user_id=user_id,
                    task_type="thumbnail",
                    title=f"Thumbnail {title_clip}",
                    media_id=str(platform_id) if platform_id else None,
                    resource_id=str(resource_id),
                    dbos_workflow_id=thumb_wf_id,
                    flow_id=flow_id,
                )
            except Exception as e:
                logger.warning(f"[download.chain] pre-create thumbnail row: {e}")
            await start_workflow_routed(
                "thumbnail",
                dbos_workflow_callable=thumbnail_workflow,
                dbos_workflow_kwargs={
                    "resource_id": resource_id,
                    "file_path": fresh_download_path,
                    "mime_type": mime_type,
                },
                workflow_id=thumb_wf_id,
            )
        except Exception as e:
            logger.warning(f"[download.chain] thumbnail: {type(e).__name__}: {e!r}")

        # extract_audio — own workflow + own task_tracking row/status.
        # It chains ai_transcription / ai_summary on completion when the
        # resource carries the matching intent tags.
        try:
            from app.workflows.extract_audio import extract_audio_workflow

            audio_wf_id = str(_uuid.uuid4())
            try:
                await get_task_manager().create(
                    user_id=user_id,
                    task_type="extract_audio",
                    title=f"Audio {title_clip}",
                    media_id=str(platform_id) if platform_id else None,
                    resource_id=str(resource_id),
                    dbos_workflow_id=audio_wf_id,
                    flow_id=flow_id,
                )
            except Exception as e:
                logger.warning(f"[download.chain] pre-create extract_audio row: {e}")
            await start_workflow_routed(
                "extract_audio",
                dbos_workflow_callable=extract_audio_workflow,
                dbos_workflow_kwargs={
                    "platform_id": platform_id,
                    "user_id": user_id,
                    "resource_id": resource_id,
                    "flow_id": flow_id,
                    "video_title": video_title,
                },
                workflow_id=audio_wf_id,
            )
        except Exception as e:
            logger.warning(f"[download.chain] extract_audio: {type(e).__name__}: {e!r}")

    try:
        from app.db.scope import Scope, request_scope
        from app.tasks.download_helpers import (
            maybe_chain_ai_pipeline,
            maybe_chain_transcode,
        )

        # §2.4b: both chain helpers are now async-native and read `resources`
        # by awaiting the repos directly. Set the ambient USER scope HERE and
        # await them inside it — the contextvar is naturally visible to the
        # awaited reads (same async context; no copy_context thread hop).
        # `user_id` is a required step kwarg (always present). INERT until
        # SCOPE_ENFORCE_RESOURCES flips. Kept inside the existing try/except so
        # the best-effort guarantee is preserved.
        async with request_scope(Scope(user_id=user_id)):
            await maybe_chain_transcode(
                platform_id, user_id, flow_id=flow_id, video_title=video_title
            )
            await maybe_chain_ai_pipeline(
                platform_id, user_id, flow_id=flow_id, video_title=video_title
            )
    except Exception as e:
        logger.warning(
            f"[download.chain] transcode/ai chain: {type(e).__name__}: {e!r}"
        )


@DBOS.step()
async def log_download_outcome_step(
    *,
    user_id: str,
    platform_id: str,
    video_title: str,
    strategy: str,
    media_type: int,
    outcome: str,
    error: Optional[str] = None,
) -> None:
    """Audit row in user_logs. Best-effort."""
    from app.repositories.user_logs_repository import log_user_action

    try:
        if outcome == "success":
            await log_user_action(
                user_id=user_id,
                action="download",
                message=(f"Download completed ({strategy}): {video_title[:30]}..."),
                status="success",
                aweme_id=platform_id,
                details={"media_type": media_type},
            )
        else:
            await log_user_action(
                user_id=user_id,
                action="download",
                message=f"Download failed ({strategy}): {video_title[:30]}...",
                status="error",
                aweme_id=platform_id,
                details={"error": (error or "unknown")[:200]},
            )
    except Exception as e:
        logger.warning(f"[download.log] {e}")


@DBOS.step()
async def mark_task_user_visible_complete_step(
    *,
    workflow_id: str,
    subtitle: str,
) -> None:
    """Patch task_tracking row to phase=completed AS SOON AS the file is
    on disk and visible in 资源库.

    Background:
    `download_workflow` finalize_post_download_step writes parsed_media +
    resources + resource_versions, which is the moment the user can open
    the file in 资源库. But the workflow still has 1-2 more steps to run
    (audit log + chain_followups), so DBOS doesn't mark the workflow
    SUCCESS until those finish. Without this step, mirror_dbos_lifecycle
    wouldn't flip task_tracking.phase to 'completed' for another
    minute-or-two, leaving the Task Center showing "downloading…" while
    the user already sees the file in 资源库 — which is the "Task Center
    is slower than reality" complaint we tracked down on 2026-05-05.

    Anti-regression in mirror_dbos_lifecycle_to_tracking guards against
    a SUCCESS transition demoting an already-failed/cancelled phase, so
    landing 'completed' here early can never be silently overwritten.
    """
    from app.services.infra.unified_task_manager import get_task_manager

    try:
        await get_task_manager()._atomic_update(
            workflow_id,
            {
                "phase": "completed",
                "status": "completed",
                "progress": 100,
                "subtitle": subtitle[:120],
                "completed_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception as e:
        logger.warning(
            f"[download.mark_complete] failed to early-mark "
            f"task_tracking complete for {workflow_id}: {e}"
        )


@DBOS.step()
async def mark_download_processing_step(workflow_id: str) -> None:
    """Push task_tracking.phase 'queued' → 'processing'. Same rationale
    as parse.mark_parse_processing_step — `mirror_dbos_lifecycle_to_tracking`
    only writes `status`, leaving `phase` stuck at 'queued' for the
    workflow lifetime, which the TaskMonitor stat panel renders as
    "WORKER Idle" while a download is actually in flight.

    Renamed from ``mark_workflow_processing_step`` (was the same name in
    parse.py + download.py, triggering DBOS's "Duplicate registration
    of function 'mark_workflow_processing_step'" warning).

    Best-effort wrap — a transient supabase hiccup here is purely
    cosmetic; the file will still download regardless."""
    from app.services.infra.unified_task_manager import get_task_manager

    try:
        await get_task_manager().start(workflow_id)
    except Exception as e:
        logger.warning(f"[download.mark_processing] {workflow_id}: {e}")


@DBOS.workflow()
async def download_workflow(
    platform_id: str,
    user_id: str,
    *,
    download_video: bool = True,
    download_cover: bool = True,
    media_type: int = 0,
    video_title: str = "undefined",
    resource_id: Optional[str] = None,
    user_agent: Optional[str] = None,
    flow_id: Optional[str] = None,
) -> dict[str, Any]:
    """DBOS port of download_unified_task.

    Recommended workflow_id pattern:
        f"download-{platform_id}-{int(time.time())}"
    so retries within the same minute share results, but a fresh user
    request bumps the suffix to bypass cache.
    """
    # 0. Mark task_tracking.phase='processing' immediately so admin
    # counters reflect that this workflow is actually running. Without
    # it, mirror_dbos_lifecycle_to_tracking would only update `status`
    # and `phase` stays 'queued' until terminal — which is what made
    # TaskMonitor display "WORKER Idle" mid-download.
    await mark_download_processing_step(DBOS.workflow_id)

    # Single unified strategy (PR #254). Strategy label kept on audit
    # logs so older log queries (`strategy=douyin` / `strategy=yt-dlp`)
    # still match in spirit; future platform-aware dispatch can stamp
    # a real label here.
    strategy = "unified"

    # 1. Cache short-circuit
    cache = await check_global_cache_step(
        platform_id=platform_id,
        media_type=media_type,
        download_video=download_video,
        download_cover=download_cover,
    )
    if cache.get("cache_hit"):
        # L4 dedup: another user already pulled this content. Skip the
        # download itself but still attach a resource_version v1 to the
        # current user's resource — pointing at the existing global file
        # — so 我的下载 sees a real, openable card instead of an empty
        # shell. Master did the equivalent inside save_metadata_only's
        # dedup_hit branch; the DBOS port lost it on the workflow side.
        synthetic_results: dict[str, Any] = {}
        if download_video:
            synthetic_results["video"] = "completed"
        if download_cover:
            synthetic_results["cover"] = "completed"
        await finalize_post_download_step(
            platform_id=platform_id,
            user_id=user_id,
            resource_id=resource_id,
            download_video=download_video,
            download_cover=download_cover,
            media_type=media_type,
            results=synthetic_results,
        )
        await mark_task_user_visible_complete_step(
            workflow_id=DBOS.workflow_id,
            subtitle=f"{video_title or 'Cached'} (cache hit)",
        )
        await log_download_outcome_step(
            user_id=user_id,
            platform_id=platform_id,
            video_title=video_title,
            strategy=strategy,
            media_type=media_type,
            outcome="success",  # cache hit counts as success in audit log
        )
        return {
            "status": "success",
            "platform_id": platform_id,
            "cache_hit": True,
        }

    # 2. Strategy dispatch
    try:
        # run_download_step stays sync — it dispatches yt-dlp /
        # DrissionPage subprocesses with sync Redis tracker. No await.
        download_result = run_download_step(
            workflow_id=DBOS.workflow_id,
            platform_id=platform_id,
            user_id=user_id,
            download_video=download_video,
            download_cover=download_cover,
            media_type=media_type,
            user_agent=user_agent,
        )
    except Exception as e:
        # Mark parsed_media as failed before bailing — same fallback
        # the legacy task did inside its except branch.
        from app.repositories.media_repository import get_media_repository

        try:
            repo = get_media_repository()
            fail_updates: dict[str, str] = {}
            if download_video:
                if int(media_type) in (2, 68):
                    fail_updates["image_download_status"] = "failed"
                else:
                    fail_updates["video_download_status"] = "failed"
            if download_cover:
                fail_updates["cover_download_status"] = "failed"
            if fail_updates:
                await repo.update(platform_id, fail_updates)
        except Exception:
            pass

        await log_download_outcome_step(
            user_id=user_id,
            platform_id=platform_id,
            video_title=video_title,
            strategy=strategy,
            media_type=media_type,
            outcome="failed",
            error=str(e),
        )
        # Re-raise so DBOS marks the workflow FAILED and the
        # mirror_dbos_lifecycle_to_tracking trigger writes
        # phase=failed + error_msg to task_tracking.
        #
        # Returning a `{"status":"failed",...}` dict here used to let
        # DBOS think the workflow succeeded — trigger then mirrored
        # phase=completed to task_tracking, while parsed_media kept
        # `video_download_status='failed'`. The frontend showed the
        # task as ✅ done, but no file was on disk. That's the "假
        # completed" symptom hit by all 4 failed bilibili/douyin
        # downloads on 2026-05-12 (CLAUDE.md 路线 C 第 4 条).
        raise

    results = download_result["results"]
    has_failures = bool(download_result["failed_parts"])

    # 2b. Treat "user asked for video, video didn't complete" as a full
    # workflow failure even when cover succeeded. Without this, the
    # workflow returned status="partial" → DBOS treated it as SUCCESS →
    # mirror_dbos_lifecycle_to_tracking stamped task_tracking.phase
    # =completed, while parsed_media.video_download_status stayed
    # 'failed'. UI showed ✅ next to tasks that had no video file on
    # disk — the "假 completed" anti-pattern (CLAUDE.md 路线 C 第 4 条).
    #
    # The PR #252 fix only handled catastrophic exceptions from
    # run_download_step; this catches the orthogonal case where the
    # step returns "partial" with video among failed_parts. Image
    # downloads (media_type 2/68) also write their status into
    # results["video"], so this single check covers both.
    if download_video and results.get("video") != "completed":
        from app.repositories.media_repository import get_media_repository

        try:
            repo = get_media_repository()
            fail_updates: dict[str, str] = {}
            if int(media_type) in (2, 68):
                fail_updates["image_download_status"] = "failed"
            else:
                fail_updates["video_download_status"] = "failed"
            await repo.update(platform_id, fail_updates)
        except Exception:
            pass

        await log_download_outcome_step(
            user_id=user_id,
            platform_id=platform_id,
            video_title=video_title,
            strategy=strategy,
            media_type=media_type,
            outcome="failed",
            error=f"Video download did not complete (result={results.get('video')!r})",
        )
        raise RuntimeError(
            f"Video download did not complete for {platform_id}: "
            f"video={results.get('video')!r}, failed_parts={download_result['failed_parts']!r}"
        )

    # 3. Post-download bookkeeping
    finalize = await finalize_post_download_step(
        platform_id=platform_id,
        user_id=user_id,
        resource_id=resource_id,
        download_video=download_video,
        download_cover=download_cover,
        media_type=media_type,
        results=results,
    )

    # 3b. Early-mark task_tracking complete the moment the file is on
    # disk and visible in 资源库. The remaining steps (audit log + chain
    # follow-ups) take 30s+ and would otherwise leave the Task Center
    # showing "downloading…" while 资源库 already shows the file.
    await mark_task_user_visible_complete_step(
        workflow_id=DBOS.workflow_id,
        subtitle=video_title or "Downloaded",
    )

    # 4. Audit log
    await log_download_outcome_step(
        user_id=user_id,
        platform_id=platform_id,
        video_title=video_title,
        strategy=strategy,
        media_type=media_type,
        outcome="success" if not has_failures else "failed",
        error=(
            "Partial failure: " + ", ".join(download_result["failed_parts"])
            if has_failures
            else None
        ),
    )

    # 5. Chain follow-ups (best-effort, never fails the workflow)
    await chain_followups_step(
        platform_id=platform_id,
        user_id=user_id,
        resource_id=resource_id,
        fresh_download_path=finalize.get("fresh_download_path"),
        flow_id=flow_id,
        video_title=video_title,
    )

    return {
        "status": "success" if not has_failures else "partial",
        "platform_id": platform_id,
        "results": results,
    }

"""parse DBOS workflow — port of `parse_tasks.parse_single_link_task`.

The legacy file is 1033 LOC but the task body itself is ~130 lines of
orchestration around 6 helper functions. The helpers stay in
`app/tasks/parse_tasks.py` (touching them would balloon the diff and
they're well-isolated already); this module just wraps them in DBOS
steps and rewires the two downstream dispatches through
`start_workflow_routed` so the migration table controls cutover.

Pipeline phases:
  1. validate URL (sync, pure)
  2. fetch + parse via DrissionPage / DouyinFormatter
  3. save parsed_media row
  4. auto-tag (best-effort)
  5. dispatch download via start_workflow_routed("download", ...)
  6. dispatch L1 analysis via start_workflow_routed("ai_extract", ...)
  7. user_logs audit row

⚠️ Pending before flipping dbos_workflow_routing for 'parse' to
'shadow' then 'dbos': end-to-end test against (a) a fresh Douyin URL
and (b) yt-dlp via parse_media_task (the third Celery task in
parse_tasks.py — separate port if needed). Confirm the dispatched
download_workflow + analyze_l1_workflow chain to completion under
shadow mode against a real ResourceVersion before the canonical flip.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Optional

from dbos import DBOS, Queue
from loguru import logger

# ── Per-user batch concurrency cap ──────────────────────────────────
# Partitioned by user_id → at most N parse workflows run per user at once;
# the rest queue durably. `concurrency` is read by the DBOS poller each
# cycle, so mutating it at runtime (set_parse_concurrency) takes effect live
# — the Settings → General "max simultaneous downloads" value applies without
# a restart. Defined at module level (imported pre-launch via
# _dispatch_bundle) so the queue + its poller are registered BEFORE
# DBOS.launch().
MAX_PARSE_CONCURRENCY_DEFAULT = int(
    os.environ.get("MAX_PARSE_CONCURRENCY_PER_USER", "3")
)
parse_user_queue = Queue(
    "parse_user",
    worker_concurrency=MAX_PARSE_CONCURRENCY_DEFAULT,
    partition_queue=True,
)


def set_parse_concurrency(n: int) -> None:
    """Set the per-user parse queue concurrency live (clamped 1..20).

    Called by the `config.parse_concurrency` lifecycle subscriber whenever a
    user saves Settings → General. The DBOS poller re-reads `concurrency`
    each cycle, so the new cap applies without a restart.
    """
    try:
        n = max(1, min(int(n), 20))
        parse_user_queue.worker_concurrency = n
        logger.info(f"[parse_queue] per-user concurrency set to {n}")
    except Exception as e:
        logger.warning(f"[parse_queue] set concurrency failed: {e}")


def enqueue_parse_for_user(*, user_id: str, workflow_id: str, kwargs: dict) -> str:
    """Enqueue parse_workflow on the per-user partitioned queue. Returns wf id.

    Partition key = user_id → the queue enforces `concurrency` PER user, so a
    big batch from one user can't starve another (or hammer the source site
    past the cap). `parse_workflow` is a module-level @DBOS.workflow referenced
    at call-time, so define order doesn't matter.
    """
    # Gateway→DBOSClient path (DORMANT): when a DBOSClient handle exists
    # (gateway role, future), enqueue cross-process via the client onto the
    # same `parse_user` partitioned queue the worker dequeues from. Today
    # `get_dbos_client()` returns None everywhere → the existing singleton
    # `parse_user_queue.enqueue` path below runs → zero behavior change.
    from app.services.infra.dbos_orchestrator import (
        _resolve_pinned_app_version,
        get_dbos_client,
    )

    client = get_dbos_client()
    if client is not None:
        from dbos import EnqueueOptions

        opts: dict[str, Any] = dict(
            workflow_name="parse_workflow",
            queue_name="parse_user",
            queue_partition_key=str(user_id),
            workflow_id=workflow_id,
            authenticated_user=str(user_id),
        )
        version = _resolve_pinned_app_version()
        if version:
            opts["app_version"] = version
        client.enqueue(EnqueueOptions(**opts), **kwargs)
        return workflow_id

    from dbos import SetEnqueueOptions, SetWorkflowID

    with (
        SetWorkflowID(workflow_id),
        SetEnqueueOptions(queue_partition_key=str(user_id)),
    ):
        parse_user_queue.enqueue(parse_workflow, **kwargs)
    return workflow_id


def is_soda_platform(platform: str) -> bool:
    """qishui (Soda music) routes to the dedicated soda download workflow."""
    return platform == "qishui"


@DBOS.step()
def extract_url_step(url: str) -> str:
    """Validate + canonicalise URL. Raises ValueError on bad input —
    workflow catches that and returns a failed-status dict."""
    from app.services.media.parsers.parse_helpers import extract_url

    return extract_url(url)


@DBOS.step(retries_allowed=True, max_attempts=3)
def fetch_and_parse_step(
    valid_url: str,
    video_bool: bool,
    cover_bool: bool,
    categories: Optional[str],
    user_agent: str,
    user_id: Optional[str] = None,
    platform: str = "douyin",
) -> dict[str, Any]:
    """Platform-aware parse step. douyin → unified chain
    (ABogus → DrissionPage, see douyin_parse.parse_chain) + DouyinFormatter;
    other platforms (bilibili / youtube / …) → yt-dlp metadata +
    YtdlpService schema map.

    `platform` defaults to "douyin" so any in-flight workflows queued
    before this change keep the legacy behaviour. Heavy I/O — 3 retries
    matches the legacy Celery budget.

    `parse_method` records which tier actually delivered (abogus /
    drissionpage / ytdlp / qishui) — patched into task_tracking.metadata
    so admin Tasks shows the parse channel again."""
    if platform == "qishui":
        from app.services.media.parsers.parse_helpers import fetch_and_parse_qishui

        aweme_detail, parsed_data = fetch_and_parse_qishui(
            valid_url,
            video_bool,
            cover_bool,
            user_agent=user_agent,
            user_id=user_id,
        )
        parse_method = "qishui"
    elif platform == "douyin":
        from app.services.media.parsers.parse_helpers import fetch_and_parse

        aweme_detail, parsed_data, parse_method = fetch_and_parse(
            valid_url,
            video_bool,
            cover_bool,
            categories,
            user_agent=user_agent,
            user_id=user_id,
        )
    else:
        from app.services.media.parsers.parse_helpers import fetch_and_parse_ytdlp

        aweme_detail, parsed_data = fetch_and_parse_ytdlp(
            valid_url,
            video_bool,
            cover_bool,
            user_agent=user_agent,
            user_id=user_id,
        )
        parse_method = "ytdlp"
    return {
        "aweme_detail": aweme_detail,
        "parsed_data": parsed_data,
        "parse_method": parse_method,
    }


@DBOS.step()
def save_media_step(
    parsed_data: dict[str, Any], platform_id: str, video_bool: bool
) -> Optional[dict[str, Any]]:
    """Insert/update parsed_media row. Returns the saved record or None."""
    from app.services.media.parsers.parse_helpers import save_media_to_db

    return save_media_to_db(parsed_data, platform_id, video_bool)


@DBOS.step()
def auto_tag_step(
    video_db_id: Any,
    platform_id: str,
    aweme_detail: dict[str, Any],
    title: str,
    description: str,
) -> None:
    """Best-effort hashtag → tag classification. Never raises."""
    from app.services.media.parsers.parse_helpers import auto_tag_media

    auto_tag_media(video_db_id, platform_id, aweme_detail, title, description)


def dispatch_download_step(
    *,
    platform_id: str,
    user_id: str,
    download_video: bool,
    download_cover: bool,
    media_type: int,
    video_title: str,
    user_agent: str,
    resource_id: Optional[str] = None,
    flow_id: Optional[str] = None,
) -> dict[str, Any]:
    """Route the download via the migration table. Pre-creates the
    task_tracking row with a friendly title so admin/Task Center see
    "Download <title>" while the workflow runs (the trigger updates
    lifecycle from there).

    `resource_id` lets finalize_post_download_step build resource_version
    v1 + mirror file_size_bytes — without it "我的下载" page shows nothing.

    NOT a `@DBOS.step` — `start_workflow_routed` calls
    `DBOS.start_workflow` which asserts when invoked from inside a step
    context."""
    import uuid as _uuid

    from dbos import SetEnqueueOptions, SetWorkflowID

    from app.services.infra.unified_task_manager import get_task_manager
    from app.workflows.download import download_user_queue, download_workflow

    wf_id = str(_uuid.uuid4())

    async def _do() -> dict[str, Any]:
        # Pre-create task_tracking row — trigger updates lifecycle later.
        try:
            await get_task_manager().create(
                user_id=user_id,
                task_type="download",
                title=f"Download {(video_title or platform_id)[:50]}",
                subtitle=(
                    "video + cover"
                    if download_video and download_cover
                    else "video" if download_video else "cover"
                ),
                media_id=str(platform_id) if platform_id else None,
                resource_id=str(resource_id) if resource_id else None,
                dbos_workflow_id=wf_id,
                flow_id=flow_id,
            )
        except Exception as e:
            logger.warning(f"[parse] pre-create download task_tracking: {e}")

        # Enqueue on the per-user partitioned download queue (not
        # start_workflow_routed's unbounded default queue) so the user's "max
        # simultaneous downloads" cap bounds regular downloads too — parity with
        # the soda path. Bypassing the routing gate is fine: download is always
        # DBOS. enqueue() is sync; the with-blocks are sync context managers.
        with SetWorkflowID(wf_id), SetEnqueueOptions(queue_partition_key=str(user_id)):
            download_user_queue.enqueue(
                download_workflow,
                platform_id,
                user_id,
                download_video=download_video,
                download_cover=download_cover,
                media_type=media_type,
                video_title=video_title,
                user_agent=user_agent,
                resource_id=resource_id,
                flow_id=flow_id,
            )
        return {"workflow_id": wf_id, "status": "enqueued"}

    return asyncio.run(_do())


def dispatch_soda_download_step(
    *,
    platform_id: str,
    user_id: str,
    media_id: Optional[str],
    video_title: str,
    resource_id: Optional[str] = None,
    user_agent: Optional[str] = None,
    flow_id: Optional[str] = None,
) -> dict[str, Any]:
    """Soda (qishui) variant of ``dispatch_download_step``.

    qishui media is audio-only, so it routes through ``soda_download_workflow``
    (Phase 2) instead of the generic ``download_workflow``. Reuses task_type
    "download" so no new routing row is needed — the routing table only keys
    off task_type, while the actual callable is passed explicitly.

    Mirrors ``dispatch_download_step`` exactly: not a ``@DBOS.step`` (it calls
    ``DBOS.start_workflow`` via ``start_workflow_routed``, which asserts when
    invoked from inside a step context), same wf_id generation, same
    ``asyncio.run`` sync/async bridge.

    NOTE: ``int(media_type)`` is deliberately NOT computed here — qishui's
    parsed media_type is the string "audio", and the soda workflow does not
    need a numeric media_type."""
    import uuid as _uuid

    from dbos import SetEnqueueOptions, SetWorkflowID

    from app.services.infra.unified_task_manager import get_task_manager
    from app.workflows.soda_download import soda_download_queue, soda_download_workflow

    wf_id = str(_uuid.uuid4())

    async def _do() -> dict[str, Any]:
        # Pre-create task_tracking row — trigger updates lifecycle later.
        try:
            await get_task_manager().create(
                user_id=user_id,
                task_type="download",
                title=f"Download {(video_title or platform_id)[:50]}",
                subtitle="audio",
                media_id=str(platform_id) if platform_id else None,
                resource_id=str(resource_id) if resource_id else None,
                dbos_workflow_id=wf_id,
                flow_id=flow_id,
            )
        except Exception as e:
            logger.error(
                f"[parse] pre-create soda download task_tracking FAILED "
                f"(wf={wf_id}, will self-heal in workflow.start): {e!r}"
            )

        # Enqueue on the per-user partitioned soda queue (combined audio+UGC
        # cap) instead of start_workflow_routed — this bounds the qishui API
        # hit-rate per user for big playlist batches. Bypassing the routing
        # gate is fine: soda is always DBOS. enqueue() is sync; the with-blocks
        # are sync context managers.
        with SetWorkflowID(wf_id), SetEnqueueOptions(queue_partition_key=str(user_id)):
            soda_download_queue.enqueue(
                soda_download_workflow,
                platform_id,
                user_id,
                media_id=media_id,
                title=video_title,
                resource_id=resource_id,
                user_agent=user_agent,
                flow_id=flow_id,
            )
        return {"dbos_workflow_id": wf_id, "queued": True}

    return asyncio.run(_do())


def dispatch_soda_ugc_download_step(
    *,
    platform_id: str,
    user_id: str,
    media_id: Optional[str],
    video_title: str,
    resource_id: Optional[str] = None,
    user_agent: Optional[str] = None,
    flow_id: Optional[str] = None,
) -> dict[str, Any]:
    """Soda (qishui) UGC-video variant of ``dispatch_soda_download_step``.

    A qishui UGC video is a plain MP4 (no decrypt), so it routes through
    ``soda_ugc_download_workflow`` (Phase 6) instead of the audio
    ``soda_download_workflow``. Identical to ``dispatch_soda_download_step``
    except the task_tracking subtitle is "video" and the dispatched callable is
    the UGC workflow.

    Reuses task_type "download" (the routing table keys off task_type; the
    callable is passed explicitly). Like the audio path, ``int(media_type)`` is
    deliberately NOT computed here — qishui's parsed media_type is the string
    "video" and the soda workflow needs no numeric media_type."""
    import uuid as _uuid

    from dbos import SetEnqueueOptions, SetWorkflowID

    from app.services.infra.unified_task_manager import get_task_manager
    from app.workflows.soda_download import soda_download_queue
    from app.workflows.soda_ugc_download import soda_ugc_download_workflow

    wf_id = str(_uuid.uuid4())

    async def _do() -> dict[str, Any]:
        # Pre-create task_tracking row — trigger updates lifecycle later.
        try:
            await get_task_manager().create(
                user_id=user_id,
                task_type="download",
                title=f"Download {(video_title or platform_id)[:50]}",
                subtitle="video",
                media_id=str(platform_id) if platform_id else None,
                resource_id=str(resource_id) if resource_id else None,
                dbos_workflow_id=wf_id,
                flow_id=flow_id,
            )
        except Exception as e:
            logger.warning(f"[parse] pre-create soda ugc download task_tracking: {e}")

        # Enqueue on the SAME per-user partitioned soda queue as the audio path
        # (combined per-user cap — ban is per-user/cookie), just with the UGC
        # workflow callable. Bypassing the routing gate is fine: soda is always
        # DBOS. enqueue() is sync; the with-blocks are sync context managers.
        with SetWorkflowID(wf_id), SetEnqueueOptions(queue_partition_key=str(user_id)):
            soda_download_queue.enqueue(
                soda_ugc_download_workflow,
                platform_id,
                user_id,
                media_id=media_id,
                title=video_title,
                resource_id=resource_id,
                user_agent=user_agent,
                flow_id=flow_id,
            )
        return {"dbos_workflow_id": wf_id, "queued": True}

    return asyncio.run(_do())


@DBOS.step()
def update_parse_tracking_step(
    *,
    workflow_id: str,
    platform_id: str,
    subtitle: str,
    parse_method: Optional[str] = None,
) -> None:
    """Patch the parse workflow's own task_tracking row with the resolved
    media_id (= platform_id) and a friendlier subtitle.

    Frontend `useParser` keys off `parseTask.media_id` to render the
    parsed media card and link the follow-up download task — without
    this update the parse task stays without a media_id and the result
    card never appears (legacy parse_tasks.py used to do the same write
    via `manager._atomic_update`).

    `parse_method` lands in metadata.parse_method (merged, not replaced —
    metadata is a shared jsonb column) so the admin Tasks page can show
    which parse tier delivered (the legacy Celery path used to write
    this; the DBOS port had dropped it)."""
    from app.services.infra.unified_task_manager import get_task_manager

    async def _do() -> None:
        try:
            mgr = get_task_manager()
            await mgr._atomic_update(
                workflow_id,
                {"media_id": str(platform_id), "subtitle": subtitle[:120]},
            )
            if parse_method:
                await mgr.patch_metadata(workflow_id, {"parse_method": parse_method})
        except Exception as e:
            logger.warning(f"[parse] update_parse_tracking failed: {e}")

    asyncio.run(_do())


@DBOS.step()
def log_parse_outcome_step(
    *,
    user_id: str,
    platform_id: str,
    video_title: str,
    media_type: int,
    outcome: str,
    error: Optional[str] = None,
) -> None:
    """user_logs audit row. Best-effort."""
    from app.repositories.user_logs_repository import log_user_action

    async def _do() -> None:
        try:
            if outcome == "success":
                await log_user_action(
                    user_id=user_id,
                    action="fetch",
                    message=f"{video_title[:20]}...: Metadata parsed",
                    status="success",
                    aweme_id=platform_id,
                    details={"media_type": media_type, "platform": "douyin"},
                )
            else:
                await log_user_action(
                    user_id=user_id,
                    action="fetch",
                    message=f"Parse failed: {platform_id[:30]}...",
                    status="error",
                    details={"error": (error or "unknown")[:200]},
                )
        except Exception as e:
            logger.warning(f"[parse.log] {e}")

    asyncio.run(_do())


@DBOS.step()
def attach_tags_step(*, resource_id: str, tag_ids: list[str]) -> int:
    """Attach the user-selected tags from the parse-page picker to the
    freshly-created resource. Without this step the tag_ids submitted
    in POST /api/v1/media/fetch were silently dropped, which broke the
    tag-driven AI chain (no Transcript tag → no transcription)."""
    from app.repositories.tags_repository import get_tags_repository

    async def _do() -> int:
        try:
            await get_tags_repository().bulk_add_tags_to_resource(
                resource_id, tag_ids, source="manual"
            )
            return len(tag_ids)
        except Exception as e:
            logger.warning(f"[parse] attach_tags_step failed: {e}")
            return 0

    return asyncio.run(_do())


async def set_resource_rating(resource_id: str, rating: int) -> bool:
    """把抓取请求带来的评级写到 resources.rating。失败只告警——评级是装饰
    字段，不该让一次成功的解析变红。"""
    from app.repositories.resources_repository import ResourcesRepository

    try:
        updated = await ResourcesRepository().update_resource(
            resource_id, {"rating": rating}
        )
    except Exception as e:
        logger.warning(f"[parse] set_resource_rating failed for {resource_id}: {e}")
        return False
    # update_resource 对不存在 / 不可见的行返回 {} 而不 raise——那是没写进去。
    if not updated:
        logger.warning(
            f"[parse] set_resource_rating: resource {resource_id} not found, "
            "rating not written"
        )
        return False
    return True


@DBOS.step()
def set_rating_step(*, resource_id: str, rating: int) -> bool:
    """DBOS 步骤外壳；逻辑在 set_resource_rating 便于直接测。"""
    return asyncio.run(set_resource_rating(resource_id, rating))


@DBOS.step()
def update_parse_subtitle_step(workflow_id: str, subtitle: str) -> None:
    """Update task_tracking.subtitle in place (no phase change).

    Called between major steps so a mid-workflow raise leaves the row
    with a useful "stuck at X step" subtitle rather than the placeholder
    "Initializing..." that ``manager.create()`` writes at task creation.

    Background: ``mirror_dbos_lifecycle_to_tracking`` populates ``status``
    and ``error_msg`` on workflow ERROR, but ``error_msg`` collapses to
    "Workflow failed — open detail to see the exception." for the
    pickled-exception payloads DBOS persists (see migration 180:188 —
    SQL can't unpickle Python objects). So the only signal a user sees
    is ``subtitle``; keeping it progressing through the workflow gives
    them at least a hint of which step failed.

    Per CLAUDE.md 路线 C 第 3 条: subtitle is a business-decorated field,
    not a trigger-managed lifecycle field, so business code may PATCH it
    freely. (error_msg is trigger-managed and stays off-limits.)

    Best-effort — never raises (a subtitle update failure shouldn't kill
    the real workflow).

    Implementation note: uses ``_atomic_update`` (not ``update_progress``).
    ``update_progress`` requires a ``progress: int`` positional that we
    don't have at these checkpoints; passing 0 would visually reset the
    progress bar mid-flight. ``_atomic_update`` lets us PATCH just the
    subtitle column, which is what ``update_parse_tracking_step`` already
    does on line 200 of this file for the same reason."""
    from app.services.infra.unified_task_manager import get_task_manager

    async def _do() -> None:
        try:
            await get_task_manager()._atomic_update(
                workflow_id, {"subtitle": subtitle[:120]}
            )
        except Exception as e:
            logger.warning(f"[parse.update_subtitle] {workflow_id}: {e}")

    asyncio.run(_do())


@DBOS.step()
def mark_parse_processing_step(workflow_id: str, user_id: str | None = None) -> None:
    """Push task_tracking.phase from 'queued' to 'processing'.

    Renamed from ``mark_workflow_processing_step`` (was the same name
    in download.py + parse.py, triggering DBOS's "Duplicate registration
    of function 'mark_workflow_processing_step'" warning at import).

    Best-effort. Background:
    `mirror_dbos_lifecycle_to_tracking` only writes `status` (not
    `phase`); nothing else explicitly transitions the row to
    PROCESSING when DBOS picks the workflow up. As a result tasks
    were observed sitting on phase='queued' the entire time they ran,
    which the TaskMonitor stat panel rendered as "WORKER Idle" while
    a parse / download was clearly in flight.

    A bare `manager.start()` does the trick — it sets
    phase='processing', status='processing', started_at=NOW.
    Wrapped so a transient supabase hiccup never demotes a real
    workflow run to failed: the badge correctness is nice-to-have,
    completing the user's task is not."""
    from app.services.infra.unified_task_manager import get_task_manager

    async def _do() -> None:
        try:
            # user_id must reach start(): its self-heal INSERT needs it
            # (task_tracking.user_id is UUID NOT NULL; start() otherwise
            # falls back to "" and the recovery can never succeed).
            await get_task_manager().start(
                workflow_id, user_id=user_id, task_type="parse"
            )
        except Exception as e:
            logger.warning(f"[parse.mark_processing] {workflow_id}: {e}")

    asyncio.run(_do())


@DBOS.workflow()
def parse_workflow(
    url: str,
    user_id: str,
    *,
    video_bool: bool = True,
    cover_bool: bool = True,
    categories: Optional[str] = None,
    tag_ids: Optional[list[str]] = None,
    platform: str = "douyin",
    flow_id: Optional[str] = None,
    rating: Optional[int] = None,
) -> dict[str, Any]:
    """DBOS port of parse_single_link_task.

    Recommended workflow_id: `f"parse-{user_id}-{hash(url)}"` so a
    duplicate user-click within the same retry budget short-circuits
    to the cached result.
    """
    # 0. Mark task_tracking.phase='processing' so admin counters /
    # TaskMonitor "WORKER" stat reflect that this workflow is actually
    # running (the `mirror_dbos_lifecycle_to_tracking` trigger only
    # touches `status`, not `phase`, so without this call the row
    # would stay phase='queued' for the full lifetime of the run).
    mark_parse_processing_step(DBOS.workflow_id, user_id)

    # Subtitle progression — see `update_parse_subtitle_step` docstring
    # for why we do this (failure error_msg collapses to a placeholder,
    # so subtitle is the only mid-flight signal the user sees).
    update_parse_subtitle_step(DBOS.workflow_id, "Validating URL...")

    # 1. URL validation (sync, fast)
    #
    # Short-circuit failures MUST raise so DBOS marks the workflow ERROR
    # and the mirror_dbos_lifecycle_to_tracking trigger flips
    # task_tracking.phase to 'failed'. Returning a {"status":"failed"}
    # dict makes DBOS think the workflow SUCCEEDED — the row then ends
    # up phase='completed' but subtitle still 'Initializing...' because
    # update_parse_tracking_step never runs, which is exactly the
    # "Parse Initializing... but the file is already in 资源库" footgun
    # we just dug out of the logs (run 17:44:12 -> 17:46:18 NO_ROUTER_DATA
    # then media_repository.create failed; the row still got mirrored to
    # completed by the SUCCESS-only trigger).
    try:
        valid_url = extract_url_step(url)
    except ValueError as e:
        raise RuntimeError(f"Invalid URL: {e}") from e

    update_parse_subtitle_step(DBOS.workflow_id, "Fetching metadata...")

    # 2. Fetch + parse (heavy)
    from app.services.media.parsers.douyin_parse.ua_pool import pick_ua

    legacy_ua = pick_ua()
    fetched = fetch_and_parse_step(
        valid_url,
        video_bool,
        cover_bool,
        categories,
        legacy_ua,
        user_id=user_id,
        platform=platform,
    )
    aweme_detail = fetched["aweme_detail"]
    parsed_data = fetched["parsed_data"]
    parse_method = fetched.get("parse_method")

    platform_id = parsed_data.get("platform_id")
    media_type = parsed_data.get("media_type", 0)
    video_title = parsed_data.get("title", "undefined")
    parsed_data["user_id"] = user_id

    update_parse_subtitle_step(DBOS.workflow_id, "Saving metadata...")

    # 3. Save metadata — same short-circuit-by-raise rule.
    saved_video = save_media_step(parsed_data, platform_id, video_bool)
    if not saved_video:
        raise RuntimeError(
            f"save_metadata_only failed for {platform_id}: see media_repository.create logs"
        )
    video_db_id = saved_video.get("id")
    resource_id = saved_video.get("resource_id")

    # 3a. Attach user-selected tags (from the parse-page tag picker) to
    # the resource. Must run BEFORE dispatch_download_step so that
    # chain_followups_step's tag-driven AI dispatch can see them.
    if tag_ids and resource_id:
        attach_tags_step(resource_id=str(resource_id), tag_ids=list(tag_ids))

    # 3a'. 抓取请求带的评级（spec 2026-09-10）。None = 调用方没给，不写。
    if rating is not None and resource_id:
        set_rating_step(resource_id=str(resource_id), rating=int(rating))

    # 3b. Backfill parse task_tracking row with media_id + friendly
    # subtitle so the frontend can show the parsed-media card and link
    # the upcoming download task.
    if platform_id:
        update_parse_tracking_step(
            workflow_id=DBOS.workflow_id,
            platform_id=str(platform_id),
            subtitle=(video_title or "Parsed")[:120],
            parse_method=parse_method,
        )

    # 4. Auto-tag (non-blocking — step swallows errors)
    if video_db_id:
        auto_tag_step(
            video_db_id,
            platform_id,
            aweme_detail,
            video_title,
            parsed_data.get("description", ""),
        )

    # 5. Dispatch download (routed); pass resource_id so finalize step
    # can build resource_version v1 and the file shows up in 我的下载.
    download_dispatch: dict[str, Any] = {}
    if video_bool or cover_bool:
        if is_soda_platform(platform):
            # qishui's parsed media_type is the STRING "audio" (track) or
            # "video" (UGC), so we MUST NOT call int(media_type) on this path.
            # Route audio → soda_download_workflow (decrypt), video → the
            # soda_ugc_download_workflow (plain MP4 stream, no decrypt).
            if str(media_type) == "video":
                download_dispatch = dispatch_soda_ugc_download_step(
                    platform_id=platform_id,
                    user_id=user_id,
                    media_id=video_db_id,
                    video_title=video_title,
                    resource_id=str(resource_id) if resource_id else None,
                    user_agent=legacy_ua,
                    flow_id=flow_id,
                )
            else:
                download_dispatch = dispatch_soda_download_step(
                    platform_id=platform_id,
                    user_id=user_id,
                    media_id=video_db_id,
                    video_title=video_title,
                    resource_id=str(resource_id) if resource_id else None,
                    user_agent=legacy_ua,
                    flow_id=flow_id,
                )
        else:
            download_dispatch = dispatch_download_step(
                platform_id=platform_id,
                user_id=user_id,
                download_video=video_bool,
                download_cover=cover_bool,
                media_type=int(media_type),
                video_title=video_title,
                user_agent=legacy_ua,
                resource_id=str(resource_id) if resource_id else None,
                flow_id=flow_id,
            )

    # 6. (removed) Auto-dispatch of L1 cover analysis.
    # D9 design: AI tasks (analyze / summary / transcript) are user-triggered
    # via tag intents on the resource card, not chained off parse. The
    # download chain's maybe_chain_ai_pipeline handles the analyze dispatch
    # when the resource carries the "Analyze" tag.

    # 7. Audit log
    #
    # qishui's media_type is the string "audio" — int() would crash here too
    # (this step runs for ALL platforms, not just the download branch above),
    # so coerce to a numeric type code only for the non-soda path; soda logs 0.
    audit_media_type = 0 if is_soda_platform(platform) else int(media_type)
    log_parse_outcome_step(
        user_id=user_id,
        platform_id=platform_id,
        video_title=video_title,
        media_type=audit_media_type,
        outcome="success",
    )

    return {
        "status": "success",
        "url": valid_url,
        "platform_id": platform_id,
        "download_dispatch": download_dispatch,
        "metadata": {
            "platform_id": platform_id,
            "title": parsed_data.get("title"),
            "author": parsed_data.get("author"),
            "duration": parsed_data.get("duration"),
            "media_type": media_type,
            "published_at": parsed_data.get("published_at"),
            "statistics": {
                "likes": parsed_data.get("like_count", 0),
                "comments": parsed_data.get("comment_count", 0),
                "shares": parsed_data.get("share_count", 0),
                "collects": parsed_data.get("favorite_count", 0),
            },
            "cover_urls": parsed_data.get("cover_urls", []),
            "description": parsed_data.get("description"),
        },
    }

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
from typing import Any, Optional

from dbos import DBOS
from loguru import logger


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
    """Platform-aware parse step. douyin → 3-tier fallback chain
    (LightHTTP → ABogus → DrissionPage) + DouyinFormatter; other platforms
    (bilibili / youtube / …) → yt-dlp metadata + YtdlpService schema map.

    `platform` defaults to "douyin" so any in-flight workflows queued
    before this change keep the legacy behaviour. Heavy I/O — 3 retries
    matches the legacy Celery budget."""
    if platform == "douyin":
        from app.services.media.parsers.parse_helpers import fetch_and_parse

        aweme_detail, parsed_data = fetch_and_parse(
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
    return {"aweme_detail": aweme_detail, "parsed_data": parsed_data}


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

    from app.services.infra.dbos_orchestrator import start_workflow_routed
    from app.services.infra.unified_task_manager import get_task_manager
    from app.workflows.download import download_workflow

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

        return await start_workflow_routed(
            "download",
            dbos_workflow_callable=download_workflow,
            dbos_workflow_kwargs={
                "platform_id": platform_id,
                "user_id": user_id,
                "download_video": download_video,
                "download_cover": download_cover,
                "media_type": media_type,
                "video_title": video_title,
                "user_agent": user_agent,
                "resource_id": resource_id,
                "flow_id": flow_id,
            },
            workflow_id=wf_id,
        )

    return asyncio.run(_do())


@DBOS.step()
def update_parse_tracking_step(
    *,
    workflow_id: str,
    platform_id: str,
    subtitle: str,
) -> None:
    """Patch the parse workflow's own task_tracking row with the resolved
    media_id (= platform_id) and a friendlier subtitle.

    Frontend `useParser` keys off `parseTask.media_id` to render the
    parsed media card and link the follow-up download task — without
    this update the parse task stays without a media_id and the result
    card never appears (legacy parse_tasks.py used to do the same write
    via `manager._atomic_update`)."""
    from app.services.infra.unified_task_manager import get_task_manager

    async def _do() -> None:
        try:
            await get_task_manager()._atomic_update(
                workflow_id,
                {"media_id": str(platform_id), "subtitle": subtitle[:120]},
            )
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
    from app.repositories.tags_repository import TagsRepository

    async def _do() -> int:
        try:
            await TagsRepository().bulk_add_tags_to_resource(
                resource_id, tag_ids, source="manual"
            )
            return len(tag_ids)
        except Exception as e:
            logger.warning(f"[parse] attach_tags_step failed: {e}")
            return 0

    return asyncio.run(_do())


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
def mark_parse_processing_step(workflow_id: str) -> None:
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
            await get_task_manager().start(workflow_id)
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
    mark_parse_processing_step(DBOS.workflow_id)

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

    # 3b. Backfill parse task_tracking row with media_id + friendly
    # subtitle so the frontend can show the parsed-media card and link
    # the upcoming download task.
    if platform_id:
        update_parse_tracking_step(
            workflow_id=DBOS.workflow_id,
            platform_id=str(platform_id),
            subtitle=(video_title or "Parsed")[:120],
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
    log_parse_outcome_step(
        user_id=user_id,
        platform_id=platform_id,
        video_title=video_title,
        media_type=int(media_type),
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

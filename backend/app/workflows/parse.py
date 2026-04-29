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

from app.services.workflow_tracker import tracked_workflow


@DBOS.step()
def extract_url_step(url: str) -> str:
    """Validate + canonicalise URL. Raises ValueError on bad input —
    workflow catches that and returns a failed-status dict."""
    from app.services.parse_helpers import extract_url

    return extract_url(url)


@DBOS.step(retries_allowed=True, max_attempts=3)
def fetch_and_parse_step(
    valid_url: str,
    video_bool: bool,
    cover_bool: bool,
    categories: Optional[str],
    user_agent: str,
    user_id: Optional[str] = None,
) -> dict[str, Any]:
    """3-tier fallback parse (LightHTTP → ABogus → DrissionPage) +
    formatter. Heavy I/O — 3 retries matches legacy Celery budget."""
    from app.services.parse_helpers import fetch_and_parse

    aweme_detail, parsed_data = fetch_and_parse(
        valid_url,
        video_bool,
        cover_bool,
        categories,
        user_agent=user_agent,
        user_id=user_id,
    )
    return {"aweme_detail": aweme_detail, "parsed_data": parsed_data}


@DBOS.step()
def save_media_step(
    parsed_data: dict[str, Any], platform_id: str, video_bool: bool
) -> Optional[dict[str, Any]]:
    """Insert/update parsed_media row. Returns the saved record or None."""
    from app.services.parse_helpers import save_media_to_db

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
    from app.services.parse_helpers import auto_tag_media

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
) -> dict[str, Any]:
    """Route the download via the migration table. Returns the routing
    decision dict from start_workflow_routed.

    NOT a `@DBOS.step` — `start_workflow_routed` calls
    `DBOS.start_workflow` internally, which asserts when invoked from
    inside a step context. Must be called directly from the workflow
    body where DBOS workflow context is active. Idempotency is handled
    at the routing layer (DBOS workflow_id dedup)."""
    from app.services.dbos_orchestrator import start_workflow_routed
    from app.workflows.download import download_workflow

    return asyncio.run(
        start_workflow_routed(
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
            },
        )
    )


def dispatch_l1_analysis_step(
    *,
    media_id: Any,
    cover_url: str,
    title: str,
    description: str,
    user_id: str,
) -> dict[str, Any]:
    """Route L1 cover analysis via the migration table. Best-effort —
    returns the routing decision but never raises.

    NOT a `@DBOS.step` for the same reason as dispatch_download_step."""
    try:
        from app.services.dbos_orchestrator import start_workflow_routed
        from app.workflows.analyze_l1 import analyze_l1_workflow

        return asyncio.run(
            start_workflow_routed(
                "ai_extract",
                dbos_workflow_callable=analyze_l1_workflow,
                dbos_workflow_kwargs={
                    "media_id": media_id,
                    "cover_url": cover_url,
                    "title": title,
                    "description": description,
                    "user_id": user_id,
                },
            )
        )
    except Exception as e:
        logger.warning(f"[parse] L1 analysis dispatch failed: {e}")
        return {"mode": "skipped", "error": str(e)}


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


@DBOS.workflow()
@tracked_workflow(
    task_type="parse",
    title_fn=lambda kw: f"Parse {(kw.get('url') or '')[:50]}",
)
def parse_workflow(
    url: str,
    user_id: str,
    *,
    video_bool: bool = True,
    cover_bool: bool = True,
    categories: Optional[str] = None,
) -> dict[str, Any]:
    """DBOS port of parse_single_link_task.

    Recommended workflow_id: `f"parse-{user_id}-{hash(url)}"` so a
    duplicate user-click within the same retry budget short-circuits
    to the cached result.
    """
    # 1. URL validation (sync, fast)
    try:
        valid_url = extract_url_step(url)
    except ValueError as e:
        return {"status": "failed", "url": url, "error": f"Invalid URL: {e}"}

    # 2. Fetch + parse (heavy)
    from app.services.douyin_parse.ua_pool import pick_ua

    legacy_ua = pick_ua()
    fetched = fetch_and_parse_step(
        valid_url, video_bool, cover_bool, categories, legacy_ua, user_id=user_id
    )
    aweme_detail = fetched["aweme_detail"]
    parsed_data = fetched["parsed_data"]

    platform_id = parsed_data.get("platform_id")
    media_type = parsed_data.get("media_type", 0)
    video_title = parsed_data.get("title", "undefined")
    parsed_data["user_id"] = user_id

    # 3. Save metadata
    saved_video = save_media_step(parsed_data, platform_id, video_bool)
    if not saved_video:
        return {
            "status": "failed",
            "url": valid_url,
            "error": "Data validation failed",
        }
    video_db_id = saved_video.get("id")

    # 4. Auto-tag (non-blocking — step swallows errors)
    if video_db_id:
        auto_tag_step(
            video_db_id,
            platform_id,
            aweme_detail,
            video_title,
            parsed_data.get("description", ""),
        )

    # 5. Dispatch download (routed)
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
        )

    # 6. Dispatch L1 analysis (routed, best-effort)
    if video_db_id:
        cover_url = (
            parsed_data.get("cover_urls", [None])[0]
            if parsed_data.get("cover_urls")
            else None
        )
        if cover_url:
            dispatch_l1_analysis_step(
                media_id=video_db_id,
                cover_url=cover_url,
                title=video_title or "",
                description=parsed_data.get("description", ""),
                user_id=user_id,
            )

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

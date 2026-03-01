# app/tasks/parse_tasks.py

"""
Parse Tasks Module

Contains link parsing related Celery tasks.
"""

from celery import group, shared_task
from loguru import logger

from app.core.utils import Utils
from app.repositories.user_logs_repository import log_user_action
from app.services.classification_service import ClassificationService
from app.tasks.utils import run_async


# ---------------------------------------------------------------------------
# Helper functions (extracted from parse_single_link_task)
# ---------------------------------------------------------------------------


def _extract_url(url: str) -> str:
    """Extract and validate URL. Raises ValueError on failure."""
    valid_urls = Utils.extract_valid_url(url)
    return valid_urls[0]


def _fetch_and_parse(valid_url: str, video_bool: bool,
                     cover_bool: bool, categories: str = None) -> tuple:
    """Fetch video data from platform and parse metadata.
    Returns (aweme_detail, parsed_data) tuple.
    Raises RuntimeError if fetch or parse fails.
    """
    from app.services.douyin_analysis import DouyinAnalysis
    from app.services.douyin_parser import DouyinParser

    aweme_detail = run_async(DouyinAnalysis.fetch_one_video(valid_url))
    if not aweme_detail:
        raise RuntimeError("Cannot fetch video info")

    parsed_data = run_async(
        DouyinParser.parse_aweme_detail(
            aweme_detail=aweme_detail,
            valid_url=valid_url,
            download_video=video_bool,
            download_music=False,
            download_cover=cover_bool,
            categories=categories,
        )
    )
    if not parsed_data:
        raise RuntimeError("Parse failed")

    return aweme_detail, parsed_data


def _save_media_to_db(parsed_data: dict, platform_id: str,
                      video_bool: bool) -> dict | None:
    """Save or update parsed media in database. Returns saved record."""
    from app.core.enums import DownloadStatus
    from app.repositories.media_repository import MediaRepository
    from app.schemas.media import MediaCreate

    repo = MediaRepository()
    existing = run_async(repo.get_by_platform_id(platform_id))

    try:
        video_data = MediaCreate(**parsed_data)
        data_dict = video_data.model_dump()
    except Exception as e:
        logger.error(f"Data validation failed: {e}")
        return None

    data_dict["video_download_status"] = (
        DownloadStatus.PENDING.value if video_bool else DownloadStatus.SKIPPED.value
    )
    data_dict["music_download_status"] = DownloadStatus.SKIPPED.value

    if existing:
        saved = run_async(repo.update(platform_id, data_dict))
        logger.info(f"[Parse] Updated metadata: {platform_id}")
    else:
        saved = run_async(repo.create(data_dict))
        logger.info(f"[Parse] Created metadata: {platform_id}")
    return saved


def _auto_tag_media(video_db_id, platform_id: str, aweme_detail: dict,
                    title: str, description: str):
    """Apply auto-tagging based on content classification. Non-blocking."""
    try:
        original_tags = []
        text_extra = aweme_detail.get("text_extra", [])
        if text_extra:
            original_tags = [
                tag.get("hashtag_name", "")
                for tag in text_extra
                if tag.get("hashtag_name")
            ]

        added_tags = run_async(
            ClassificationService.auto_tag_media(
                media_id=video_db_id,
                title=title or "",
                description=description,
                original_tags=original_tags,
            )
        )
        if added_tags:
            logger.info(f"[Parse] Auto-tagged {platform_id} with {len(added_tags)} tags")
    except Exception as e:
        logger.warning(f"[Parse] Auto-tagging failed for {platform_id}: {e}")


def _dispatch_download(platform_id: str, user_id: str,
                       video_bool: bool, cover_bool: bool,
                       media_type: int, video_title: str) -> str | None:
    """Dispatch download task. Returns download_task_id or None."""
    from app.services.system_monitor_service import check_worker_ready
    from app.tasks.download_tasks import download_unified_task

    ready, err_msg = check_worker_ready()
    if not ready:
        logger.error(f"[Parse] Cannot dispatch download: {err_msg}")
        raise RuntimeError(f"Download worker not ready: {err_msg}")

    download_task = download_unified_task.delay(
        platform_id=platform_id,
        user_id=user_id,
        download_video=video_bool,
        download_cover=cover_bool,
        media_type=media_type,
        video_title=video_title,
    )
    logger.info(f"[Parse] Download task dispatched: {download_task.id}")
    return download_task.id


def _trigger_l1_analysis(video_db_id, platform_id: str,
                         parsed_data: dict, video_title: str):
    """Trigger L1 cover analysis. Non-blocking."""
    try:
        cover_url = (
            parsed_data.get("cover_urls", [None])[0]
            if parsed_data.get("cover_urls")
            else None
        )
        if video_db_id and cover_url:
            from app.tasks.analysis_tasks import analyze_video_l1_task
            analyze_video_l1_task.delay(
                media_id=video_db_id,
                cover_url=cover_url,
                title=video_title or "",
                description=parsed_data.get("description", ""),
            )
            logger.info(f"[Parse] Triggered L1 analysis for {platform_id}")
    except Exception as e:
        logger.warning(f"[Parse] Failed to trigger L1 analysis for {platform_id}: {e}")


# ---------------------------------------------------------------------------
# Celery tasks
# ---------------------------------------------------------------------------


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def parse_single_link_task(
    self,
    url: str,
    user_id: str,
    video_bool: bool = True,
    cover_bool: bool = True,
    categories: str = None,
):
    """Parse metadata for a video link, save to DB, dispatch download + analysis."""
    logger.info(f"[Parse] Starting: {url[:50]}...")

    try:
        # 1. Validate URL
        try:
            valid_url = _extract_url(url)
        except ValueError as e:
            return {"status": "failed", "url": url, "error": f"Invalid URL: {e}"}

        # 2. Fetch + parse
        try:
            aweme_detail, parsed_data = _fetch_and_parse(
                valid_url, video_bool, cover_bool, categories
            )
        except RuntimeError as e:
            raise self.retry(
                exc=e, countdown=30 * (2 ** self.request.retries)
            )

        platform_id = parsed_data.get("platform_id")
        media_type = parsed_data.get("media_type", 0)
        video_title = parsed_data.get("title", "undefined")
        parsed_data["user_id"] = user_id

        # 3. Save to database
        saved_video = _save_media_to_db(parsed_data, platform_id, video_bool)
        if not saved_video:
            return {"status": "failed", "url": valid_url, "error": "Data validation failed"}

        video_db_id = saved_video.get("id")

        # 4. Auto-tag (non-blocking)
        if video_db_id:
            _auto_tag_media(video_db_id, platform_id, aweme_detail,
                           video_title, parsed_data.get("description", ""))

        # 5. Dispatch download
        download_task_id = None
        if video_bool or cover_bool:
            download_task_id = _dispatch_download(
                platform_id, user_id, video_bool, cover_bool,
                media_type, video_title,
            )

        # 6. Trigger L1 analysis (non-blocking)
        if video_db_id:
            _trigger_l1_analysis(video_db_id, platform_id, parsed_data, video_title)

        # 7. Log + respond
        run_async(log_user_action(
            user_id=user_id, action="fetch",
            message=f"{video_title[:20]}...: Metadata parsed",
            status="success", aweme_id=platform_id,
            details={"media_type": media_type, "platform": "douyin"},
        ))

        logger.success(f"[Parse] Complete: {platform_id}")
        return {
            "status": "success",
            "url": valid_url,
            "platform_id": platform_id,
            "download_task_id": download_task_id,
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

    except Exception as e:
        logger.error(f"[Parse] Task error: {url}, error: {e}")
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e, countdown=30 * (2 ** self.request.retries))
        run_async(log_user_action(
            user_id=user_id, action="fetch",
            message=f"Parse failed: {url[:30]}...",
            status="error", details={"error": str(e)[:200]},
        ))
        return {"status": "failed", "url": url, "error": str(e)}


@shared_task(bind=True)
def parse_batch_links_task(
    self,
    urls: list,
    user_id: str,
    video_bool: bool = True,
    cover_bool: bool = True,
    categories: str = None,
):
    """
    Celery task for batch parsing video links.

    Splits batch task into multiple individual sub-tasks for parallel execution.

    Args:
        urls: List of video links
        user_id: User ID
        video_bool: Whether to download video
        cover_bool: Whether to download cover
        categories: Video categories

    Returns:
        dict: Batch task result
    """
    logger.info(f"[Celery] Starting batch parse task: {len(urls)} links")

    # Create sub-task group
    tasks = group(
        parse_single_link_task.s(
            url=url,
            user_id=user_id,
            video_bool=video_bool,
            cover_bool=cover_bool,
            categories=categories,
        )
        for url in urls
    )

    # Execute task group and wait for results
    result = tasks.apply_async()

    # Return task group ID for caller tracking
    return {
        "status": "submitted",
        "total": len(urls),
        "group_id": result.id,
        "message": f"Submitted {len(urls)} parse tasks",
    }

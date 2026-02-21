# app/tasks/parse_tasks.py

"""
Parse Tasks Module

Contains link parsing related Celery tasks.
"""

import asyncio

from celery import group, shared_task
from loguru import logger

from app.core.utils import Utils
from app.repositories.user_logs_repository import log_user_action
from app.services.classification_service import ClassificationService


def run_async(coro):
    """Run async coroutine in synchronous environment"""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, coro)
                return future.result()
        else:
            return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def parse_single_link_task(
    self,
    url: str,
    user_id: str,
    video_bool: bool = True,
    music_bool: bool = False,
    cover_bool: bool = True,
    categories: str = None,
):
    """
    Parse metadata for a video link (Phase 1).

    This task quickly parses video metadata and returns it immediately.
    Downloads are handled by a separate download_media_task (Phase 2).

    Args:
        url: Video URL
        user_id: User ID
        video_bool: Whether to download video
        music_bool: Whether to download music
        cover_bool: Whether to download cover
        categories: Video categories

    Returns:
        dict: Metadata + download_task_id for progress tracking
    """
    logger.info(f"[Celery] Starting metadata parse: {url[:50]}...")

    try:
        # Extract valid URL
        try:
            valid_urls = Utils.extract_valid_url(url)
            valid_url = valid_urls[0]
        except ValueError as e:
            return {
                "status": "failed",
                "url": url,
                "error": f"Invalid URL: {str(e)}",
            }

        from app.repositories.media_repository import MediaRepository
        from app.services.douyin_analysis import DouyinAnalysis
        from app.services.douyin_parser import DouyinParser
        from app.tasks.download_tasks import download_unified_task

        # Fetch video data
        aweme_detail = run_async(DouyinAnalysis.fetch_one_video(valid_url))

        if not aweme_detail:
            logger.warning(f"[Celery] Cannot fetch video info: {valid_url}")
            raise self.retry(
                exc=Exception("Cannot fetch video info"),
                countdown=30 * (2**self.request.retries),
            )

        # Parse metadata (without downloading files)
        parsed_data = run_async(
            DouyinParser.parse_aweme_detail(
                aweme_detail=aweme_detail,
                valid_url=valid_url,
                download_video=video_bool,
                download_music=music_bool,
                download_cover=cover_bool,
                categories=categories,
            )
        )

        if not parsed_data:
            logger.warning(f"[Celery] Parse failed: {valid_url}")
            raise self.retry(
                exc=Exception("Parse failed"), countdown=30 * (2**self.request.retries)
            )

        platform_id = parsed_data.get("platform_id")
        media_type = parsed_data.get("media_type", 0)
        video_title = parsed_data.get("title", "undefined")
        parsed_data["user_id"] = user_id

        # Save metadata to database (without downloading)
        from app.core.enums import DownloadStatus
        from app.schemas.media import MediaCreate

        repo = MediaRepository()

        # Check if exists
        existing = run_async(repo.get_by_platform_id(platform_id, user_id=user_id))

        # Prepare data
        try:
            video_data = MediaCreate(**parsed_data)
            data_dict = video_data.model_dump()
        except Exception as e:
            logger.error(f"Data validation failed: {str(e)}")
            return {
                "status": "failed",
                "url": valid_url,
                "error": f"Data validation failed: {str(e)}",
            }

        # Set download status to pending
        if video_bool:
            data_dict["video_download_status"] = DownloadStatus.PENDING.value
        else:
            data_dict["video_download_status"] = DownloadStatus.SKIPPED.value

        if music_bool:
            data_dict["music_download_status"] = DownloadStatus.PENDING.value
        else:
            data_dict["music_download_status"] = DownloadStatus.SKIPPED.value

        # Save or update
        saved_video = None
        if existing:
            saved_video = run_async(repo.update(platform_id, data_dict))
            logger.info(f"[Celery] Updated metadata: {platform_id}")
        else:
            saved_video = run_async(repo.create(data_dict))
            logger.info(f"[Celery] Created metadata: {platform_id}")

        # Auto-tag the video after saving metadata
        try:
            # Get the video's database ID (primary key, not platform_id)
            video_db_id = saved_video.get("id") if saved_video else None
            if video_db_id:
                # Extract original hashtags from aweme_detail's text_extra
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
                        title=video_title or "",
                        description=parsed_data.get("description", ""),
                        original_tags=original_tags,
                    )
                )
                if added_tags:
                    logger.info(
                        f"[Celery] Auto-tagged video {platform_id} with {len(added_tags)} tags"
                    )
            else:
                logger.warning(
                    f"[Celery] Cannot auto-tag video {platform_id}: no database ID returned"
                )
        except Exception as e:
            # Auto-tagging is non-blocking - failures should not affect the main flow
            logger.warning(f"[Celery] Auto-tagging failed for {platform_id}: {e}")

        # Determine what needs downloading
        need_download = video_bool or music_bool or cover_bool
        download_task_id = None

        if need_download:
            # Pre-flight: verify download task is registered with worker
            from app.services.system_monitor_service import check_worker_ready

            ready, err_msg = check_worker_ready()
            if not ready:
                logger.error(f"[Celery] Cannot dispatch download: {err_msg}")
                raise RuntimeError(
                    f"Download worker not ready: {err_msg}"
                )

            # Trigger download task (Phase 2)
            download_task = download_unified_task.delay(
                platform_id=platform_id,
                user_id=user_id,
                download_video=video_bool,
                download_music=music_bool,
                download_cover=cover_bool,
                media_type=media_type,
                video_title=video_title,
            )
            download_task_id = download_task.id
            logger.info(f"[Celery] Download task triggered: {download_task_id}")

        # Trigger L1 analysis automatically (non-blocking)
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
                logger.info(f"[Celery] Triggered L1 analysis for video {platform_id}")
        except Exception as e:
            # L1 analysis is non-blocking - failures should not affect the main flow
            logger.warning(
                f"[Celery] Failed to trigger L1 analysis for {platform_id}: {e}"
            )

        # Log user action
        run_async(
            log_user_action(
                user_id=user_id,
                action="fetch",
                message=f"{video_title[:20]}...: Metadata parsed",
                status="success",
                aweme_id=platform_id,
                details={"media_type": media_type, "platform": "douyin"},
            )
        )

        # Build metadata response
        metadata = {
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
        }

        logger.success(f"[Celery] Metadata parse complete: {platform_id}")

        return {
            "status": "success",
            "url": valid_url,
            "platform_id": platform_id,
            "download_task_id": download_task_id,
            "metadata": metadata,
        }

    except Exception as e:
        logger.error(f"[Celery] Parse task error: {url}, error: {str(e)}")
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e, countdown=30 * (2**self.request.retries))
        # Log failure after max retries
        run_async(
            log_user_action(
                user_id=user_id,
                action="fetch",
                message=f"Parse failed: {url[:30]}...",
                status="error",
                details={"error": str(e)[:200]},
            )
        )
        return {
            "status": "failed",
            "url": url,
            "error": str(e),
        }


@shared_task(bind=True)
def parse_batch_links_task(
    self,
    urls: list,
    user_id: str,
    video_bool: bool = True,
    music_bool: bool = False,
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
        music_bool: Whether to download music
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
            music_bool=music_bool,
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

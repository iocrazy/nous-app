# app/tasks/parse_tasks.py

"""
Parse Tasks Module

Contains link parsing related Celery tasks.
"""

import json

from celery import group, shared_task
from loguru import logger

from app.celery_app import celery_app
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


# ---------------------------------------------------------------------------
# Async parse task (yt-dlp metadata fetch → save DB → dispatch download)
# ---------------------------------------------------------------------------


def _get_douyin_method_flags() -> dict[str, bool]:
    """Read Douyin parse method toggles from system_settings."""
    flags = {"ytdlp": True, "lighthttp": True, "drissionpage": True}
    try:
        from app.db import get_async_supabase_admin
        client = run_async(get_async_supabase_admin())
        result = run_async(
            client.table("system_settings")
            .select("key, value")
            .in_("key", [
                "douyin_ytdlp_enabled",
                "douyin_lighthttp_enabled",
                "douyin_drissionpage_enabled",
            ])
            .execute()
        )
        for row in (result.data or []):
            key_map = {
                "douyin_ytdlp_enabled": "ytdlp",
                "douyin_lighthttp_enabled": "lighthttp",
                "douyin_drissionpage_enabled": "drissionpage",
            }
            short_key = key_map.get(row["key"])
            if short_key:
                flags[short_key] = row["value"] is True or row["value"] == "true"
    except Exception as e:
        logger.warning(f"[Douyin] Failed to read method flags, using defaults: {e}")
    return flags


def _try_lighthttp(url: str, user_id: str):
    """Attempt LightHTTP parse. Returns (parsed, method, name) or None."""
    from app.services.lightweight_parser import LightweightParser
    from app.services.douyin_parser import DouyinParser

    try:
        aweme_detail = run_async(LightweightParser.parse(url, user_id=user_id))
        if aweme_detail:
            parsed = run_async(DouyinParser.parse_aweme_detail(
                aweme_detail=aweme_detail, valid_url=url,
                download_video=True, download_music=False, download_cover=True,
            ))
            if parsed:
                return parsed, "lightweight", "Lightweight"
    except Exception as e:
        logger.warning(f"[Douyin] LightHTTP failed: {e}")
    return None


def _try_drissionpage(url: str, user_id: str):
    """Attempt DrissionPage parse. Returns (parsed, method, name) or None."""
    from app.services.douyin_analysis import DouyinAnalysis
    from app.services.douyin_parser import DouyinParser

    try:
        aweme_detail = run_async(DouyinAnalysis.fetch_one_video(url, user_id=user_id))
        if aweme_detail:
            parsed = run_async(DouyinParser.parse_aweme_detail(
                aweme_detail=aweme_detail, valid_url=url,
                download_video=True, download_music=False, download_cover=True,
            ))
            if parsed:
                return parsed, "drissionpage", "DrissionPage"
    except Exception as e:
        logger.warning(f"[Douyin] DrissionPage failed: {e}")
    return None


def _douyin_parse_fallback_sync(url: str, user_id: str) -> tuple:
    """Sync Douyin fallback: LightHTTP → DrissionPage (respects admin toggles).

    Returns (parsed_data, parse_method, parse_method_name).
    Raises RuntimeError if all enabled methods fail.
    """
    flags = _get_douyin_method_flags()
    logger.info(f"[Douyin Fallback] Method flags: {flags}")

    methods = []
    if flags["lighthttp"]:
        methods.append(("LightHTTP", _try_lighthttp))
    if flags["drissionpage"]:
        methods.append(("DrissionPage", _try_drissionpage))

    if not methods:
        raise RuntimeError("All Douyin parse methods are disabled in admin settings")

    for name, fn in methods:
        logger.info(f"[Douyin Fallback] Trying {name}...")
        result = fn(url, user_id)
        if result:
            return result

    raise RuntimeError("All enabled Douyin parse methods failed")


def _dispatch_download_deduped(
    *,
    platform_id: str,
    user_id: str,
    resource_id: str | None,
    media_type: int,
    video_title: str,
    download_video: bool,
    download_cover: bool,
    url: str | None,
) -> str | None:
    """Sync wrapper: dedup check + unified_task pre-create + Celery dispatch.

    Called from within parse_media_task (Celery worker context).
    Returns the Celery task ID or None.
    """
    from app.services.unified_task_manager import get_task_manager
    from app.tasks.download_tasks import download_unified_task

    mgr = get_task_manager()
    is_image_type = int(media_type) in (2, 68)

    # Per-type dedup
    requested = {}
    if download_video:
        requested["image" if is_image_type else "video"] = True
    if download_cover:
        requested["cover"] = True

    if not requested:
        return None

    types_to_download = []
    for dtype in requested:
        try:
            result = run_async(mgr.acquire_or_subscribe(
                task_type=f"download:{dtype}",
                dedup_identifier=platform_id,
                user_id=user_id,
                resource_id=resource_id or "",
            ))
            if result["action"] == "created":
                types_to_download.append(dtype)
            else:
                logger.info(f"[Parse/Download] {dtype}={result['action']} for {platform_id}")
        except Exception:
            types_to_download.append(dtype)

    if not types_to_download:
        return None

    # Pre-create unified_task for download
    dl_parts = [t.capitalize() for t in types_to_download]
    unified_task_id = None
    try:
        unified_task_id = run_async(mgr.create(
            user_id=user_id,
            task_type="download",
            title=f"Download {video_title[:50] or platform_id}",
            subtitle=" + ".join(dl_parts),
            media_id=platform_id,
            resource_id=resource_id,
        ))
    except Exception as e:
        logger.warning(f"[Parse/Download] Pre-create download unified_task failed: {e}")

    # Celery dispatch
    dl_video = ("video" in types_to_download) or ("image" in types_to_download)
    dl_cover = "cover" in types_to_download
    celery_task = download_unified_task.delay(
        platform_id=platform_id,
        user_id=user_id,
        url=url,
        download_video=dl_video,
        download_cover=dl_cover,
        media_type=media_type,
        video_title=video_title[:50] or "undefined",
        resource_id=resource_id,
        _unified_task_id=unified_task_id,
    )

    # Write celery_task_id back to pre-created unified_task
    if unified_task_id:
        try:
            run_async(mgr._atomic_update(unified_task_id, {"celery_task_id": celery_task.id}))
        except Exception:
            pass

    # Publish download_started event via Redis WebSocket (no Realtime dependency)
    try:
        redis_client = celery_app.backend.client
        channel = f"task_progress:{user_id}"
        redis_client.publish(channel, json.dumps({
            "type": "download_started",
            "unified_task_id": unified_task_id,
            "celery_task_id": celery_task.id,
            "media_id": platform_id,
            "status": "pending",
            "percent": 0,
        }))
    except Exception as e:
        logger.debug(f"[Parse/Download] Redis publish download_started failed: {e}")

    return celery_task.id


@shared_task(
    bind=True,
    max_retries=2,
    default_retry_delay=30,
    soft_time_limit=120,
    time_limit=150,
)
def parse_media_task(
    self,
    url: str,
    platform: str,
    user_id: str,
    video_bool: bool = True,
    cover_bool: bool = True,
    resource_id: str = None,
    tags: list = None,
    tag_ids: list = None,
    skip_ytdlp: bool = False,
    _unified_task_id: str = None,
    _dedup_key: str = None,
):
    """Async parse: yt-dlp metadata fetch → save DB → dispatch download.

    This task replaces the synchronous metadata fetch that previously blocked
    the HTTP handler. The HTTP endpoint now returns immediately after dispatching
    this task, and progress is visible in Task Center.
    """
    task_id = self.request.id
    logger.info(f"[Parse/Task] Starting: url={url[:50]}..., platform={platform}")

    # 1. UnifiedTaskManager: start pre-created task
    from app.services.unified_task_manager import get_task_manager
    manager = get_task_manager()
    unified_task_id = _unified_task_id

    if unified_task_id:
        try:
            run_async(manager.start(unified_task_id))
        except Exception as e:
            logger.warning(f"[Parse/Task] Failed to start unified task: {e}")

    try:
        # 2. yt-dlp metadata fetch
        from app.services.ytdlp_service import YtdlpService

        parsed_data = None
        fallback_used = False
        parse_method = "ytdlp"
        dispatch_url = url

        if unified_task_id:
            try:
                run_async(manager.update_progress(unified_task_id, 5, subtitle="Fetching metadata..."))
            except Exception:
                pass

        # Check admin toggle for yt-dlp on Douyin
        douyin_flags = _get_douyin_method_flags() if platform == "douyin" else {}
        ytdlp_disabled_by_admin = platform == "douyin" and not douyin_flags.get("ytdlp", True)

        if (skip_ytdlp or ytdlp_disabled_by_admin) and platform == "douyin":
            # Douyin: skip yt-dlp (no cookie or admin disabled)
            reason = "admin disabled" if ytdlp_disabled_by_admin else "no cookie"
            logger.info(f"[Parse/Task] Skipping yt-dlp for Douyin ({reason}): {url[:60]}")
            fallback_used = True
            dispatch_url = None
            parsed_data, parse_method, _ = _douyin_parse_fallback_sync(url, user_id)
            if unified_task_id:
                try:
                    run_async(manager.update_progress(unified_task_id, 20, subtitle="Parsing metadata..."))
                except Exception:
                    pass
        else:
            try:
                ytdlp_info = run_async(YtdlpService.fetch_metadata(url, user_id=user_id))
                if unified_task_id:
                    try:
                        run_async(manager.update_progress(unified_task_id, 20, subtitle="Parsing metadata..."))
                    except Exception:
                        pass
                parsed_data = YtdlpService._map_metadata_to_media(ytdlp_info, url)
            except Exception as e:
                # Invalidate cookie if yt-dlp failed due to auth error
                # Skip for Douyin — yt-dlp cookie issues don't affect DrissionPage fallback
                error_str = str(e)
                if platform != "douyin":
                    auth_keywords = ["login", "401", "403", "cookie", "sign in", "authenticated"]
                    if any(kw in error_str.lower() for kw in auth_keywords):
                        try:
                            from app.repositories.cookies_repository import CookiesRepository
                            cookies_repo = CookiesRepository()
                            run_async(cookies_repo.mark_invalid(user_id, platform, error_str[:200]))
                            logger.info(
                                f"[Cookie] Marked {platform} cookie invalid for user {user_id} "
                                f"after yt-dlp auth failure"
                            )
                        except Exception as cookie_err:
                            logger.warning(f"[Cookie] Failed to mark cookie invalid: {cookie_err}")

                if platform != "douyin":
                    raise  # Non-Douyin: yt-dlp failure is fatal
                # Douyin fallback
                logger.warning(f"[Parse/Task] yt-dlp failed for Douyin, falling back: {e}")
                fallback_used = True
                dispatch_url = None
                parsed_data, parse_method, _ = _douyin_parse_fallback_sync(url, user_id)

        _METHOD_LABELS = {"ytdlp": "yt-dlp", "lightweight": "Lightweight", "light_http": "LightHTTP", "drissionpage": "DrissionPage", "browser_auto": "DrissionPage"}
        method_label = _METHOD_LABELS.get(parse_method, parse_method)

        if unified_task_id:
            try:
                video_title = parsed_data.get("title") or parsed_data.get("description", "")[:50]
                run_async(manager.update_progress(
                    unified_task_id, 30,
                    subtitle=f"via {method_label} · Enriching data...",
                    title=f"Parse {video_title[:70]}" if video_title else None,
                ))
            except Exception:
                pass

        # 3. Enrich Bilibili stats
        if platform == "bilibili":
            raw_video_id = parsed_data["platform_id"].split("_", 1)[1] if "_" in parsed_data["platform_id"] else parsed_data["platform_id"]
            if raw_video_id:
                try:
                    extra_stats = run_async(YtdlpService._fetch_bilibili_stats(raw_video_id))
                    if extra_stats:
                        parsed_data["favorite_count"] = extra_stats.get("favorite", 0)
                        parsed_data["share_count"] = extra_stats.get("share", 0)
                except Exception as e:
                    logger.warning(f"[Parse/Task] Bilibili stats enrichment failed: {e}")

        if unified_task_id:
            try:
                run_async(manager.update_progress(unified_task_id, 40, subtitle=f"via {method_label} · Saving metadata..."))
            except Exception:
                pass

        # 4. Save metadata to database
        platform_id = parsed_data["platform_id"]
        parsed_data["user_id"] = user_id
        parsed_data["need_download_video"] = video_bool
        parsed_data["need_download_cover"] = True

        from app.services.media_service import MediaService
        save_result = run_async(MediaService.save_metadata_only(platform_id, parsed_data))
        if not save_result.get("success"):
            raise RuntimeError(f"Failed to save metadata: {save_result.get('message')}")

        resource_id = save_result.get("resource_id")
        dedup_hit = save_result.get("dedup_hit", False)

        # 4b. Attach tags to resource if provided
        if resource_id and (tags or tag_ids):
            try:
                from app.repositories.tags_repository import TagsRepository
                tags_repo = TagsRepository()

                # Resolve tag names to IDs (auto-create if missing)
                resolved_ids = list(tag_ids or [])
                if tags:
                    for name in tags:
                        name = name.strip()
                        if not name:
                            continue
                        tag = run_async(tags_repo.get_tag_by_name(name, user_id))
                        if not tag:
                            tag = run_async(tags_repo.create_tag(name=name, user_id=user_id))
                        resolved_ids.append(str(tag["id"]))

                if resolved_ids:
                    run_async(tags_repo.bulk_add_tags_to_resource(
                        resource_id, resolved_ids, source="manual"
                    ))
                    logger.info(f"[Parse/Task] Attached {len(resolved_ids)} tags to resource {resource_id}")
            except Exception as e:
                logger.warning(f"[Parse/Task] Failed to attach tags: {e}")

        # 5. Update unified_task with media_id for frontend tracking
        if unified_task_id and platform_id:
            try:
                run_async(manager._atomic_update(unified_task_id, {"media_id": platform_id}))
            except Exception:
                pass

        # 6. Complete parse task
        if unified_task_id:
            try:
                run_async(manager.complete(
                    unified_task_id,
                    subtitle=f"via {method_label}",
                    metadata_patch={"parse_method": parse_method, "original_url": url},
                ))
            except Exception:
                pass

        # 7. Dispatch download
        media_type = int(parsed_data.get("media_type", 0))
        video_title = parsed_data.get("title", "")
        need_download_video = video_bool and not dedup_hit

        download_task_id = None
        if need_download_video or cover_bool:
            if fallback_used or media_type in (2, 68):
                dispatch_url = None
            download_task_id = _dispatch_download_deduped(
                platform_id=platform_id,
                user_id=user_id,
                resource_id=resource_id,
                media_type=media_type,
                video_title=video_title,
                download_video=need_download_video,
                download_cover=cover_bool,
                url=dispatch_url,
            )

        # 8. Log success
        run_async(log_user_action(
            user_id=user_id, action="fetch",
            message=f"Video parsed ({parse_method}): {video_title[:30]}...",
            status="success", aweme_id=platform_id,
            details={"platform": platform, "parse_method": parse_method, "async": True},
        ))

        logger.success(f"[Parse/Task] Complete: {platform_id}")
        return {
            "status": "success",
            "platform_id": platform_id,
            "parse_method": parse_method,
            "download_task_id": download_task_id,
        }

    except Exception as e:
        error_msg = str(e)[:300]
        logger.error(f"[Parse/Task] Failed: {url[:50]}..., error: {error_msg}")

        if self.request.retries < self.max_retries:
            # Will retry — update progress with error info but don't mark as terminal
            if unified_task_id:
                try:
                    run_async(manager.update_progress(
                        unified_task_id, None,
                        subtitle=f"Retrying ({self.request.retries + 1}/{self.max_retries})...",
                    ))
                except Exception:
                    pass
            raise self.retry(exc=e, countdown=30 * (2 ** self.request.retries))

        # Final failure — no more retries
        if unified_task_id:
            try:
                run_async(manager.fail(unified_task_id, error_msg))
            except Exception:
                pass

        run_async(log_user_action(
            user_id=user_id, action="fetch",
            message=f"Parse failed: {url[:30]}...",
            status="error", details={"error": error_msg},
        ))
        return {"status": "failed", "url": url, "error": error_msg}

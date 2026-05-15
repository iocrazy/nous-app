# app/tasks/download_strategies.py

"""
Download Strategies

Unified download implementation. The single public entrypoint is
`_do_douyin_download` — the name is historical, the function routes by
`source_platform` and handles douyin / bilibili / xhs / youtube /
twitter via DB-cached URLs (httpx) with a yt-dlp fallback for cases
where the parse-stage URLs expired or are gated.

The previous `_do_ytdlp_download` (PR #246's "skip DB, hand
`original_url` straight to yt-dlp") was removed in PR #254: routing on
URL presence sent douyin short URLs (`v.douyin.com/...`) into yt-dlp's
generic webpage scraper, which times out (Read timed out 20s). The
unified path was already working pre-PR-#246, so we collapsed back to
it. Reintroduce a yt-dlp-only path only behind a *platform-aware*
dispatch (e.g. `URLRouter.detect_platform`), not URL-presence.
"""

import os

from loguru import logger

from app.core.enums import DownloadStatus
from app.core.utils import Utils
from app.services.media.downloader.downloader import DownloaderService
from app.tasks.download_helpers import (
    ensure_download_urls,
    validate_and_refresh_urls,
)
from app.tasks.download_progress import (
    UnifiedProgressTracker,
    calc_stage_ranges,
    force_progress,
)
from app.tasks.utils import run_async


def _do_douyin_download(
    platform_id: str,
    user_id: str,
    download_video: bool,
    download_cover: bool,
    media_type: int,
    tracker: UnifiedProgressTracker,
    user_agent: str | None = None,
) -> dict:
    """Douyin download strategy: reads URLs from DB, downloads via httpx.

    user_agent: the same Douyin UA picked by parse_media_task via
      ua_pool.pick_ua(). When provided, overrides the random UA from
      Utils.get_headers() so parse signature and download request share
      a single UA across the whole task.
    """
    results = {"video": None, "music": None, "cover": None}

    # Calculate stage progress ranges
    stages = calc_stage_ranges(download_video, download_cover)

    # Ensure download URLs are available (re-parse if missing)
    from app.repositories.media_repository import MediaRepository as _MR_urls

    media = run_async(_MR_urls().get_by_platform_id(platform_id))
    if media:
        needed = []
        if download_video:
            needed.append("image" if int(media_type) in (2, 68) else "video")
        if download_cover:
            needed.append("cover")
        media = ensure_download_urls(platform_id, media, needed)
        # Diagnostic: log URL availability after ensure
        for t in needed:
            url_field = {
                "video": "video_download_urls",
                "cover": "cover_urls",
                "image": "image_download_urls",
            }.get(t)
            urls = media.get(url_field) if url_field else None
            url_count = len(urls) if urls else 0
            logger.info(
                f"[Download/Diag] {t}: {url_count} URLs available for {platform_id} (field={url_field})"
            )

    # ── Pre-download: validate URL accessibility, refresh if expired ──
    if int(media_type) in (0, 4, 61):  # Video types
        if download_video:
            # Set stage boundaries for fine-grained video progress
            if "video" in stages:
                offset, weight = stages["video"]
                tracker.set_stage(offset, weight)
                force_progress(tracker, offset, subtitle="Downloading video...")

            media, video_ok, reason = validate_and_refresh_urls(
                platform_id, media, "video_download_urls", "video"
            )
            if not video_ok:
                logger.warning(
                    f"[Download/Exec] video: HEAD check failed for {platform_id} ({reason}), "
                    f"attempting GET download anyway (HEAD/GET may differ)"
                )
            logger.info(f"[Download/Exec] video: downloading {platform_id}...")
            video_result = run_async(
                DownloaderService.download_video_by_platform_id(
                    platform_id,
                    user_id=user_id,
                    progress_tracker=tracker,
                    user_agent=user_agent,
                )
            )
            results["video"] = (
                video_result.video_download_status.value
                if hasattr(video_result, "video_download_status")
                else "unknown"
            )
            logger.info(f"[Download/Exec] video: {results['video']} for {platform_id}")
            if results["video"] != "completed":
                error_msg = getattr(video_result, "error", None) or "Download failed"
                logger.warning(
                    f"[Download/Exec] video failed for {platform_id}: {error_msg}"
                )

                # ── yt-dlp fallback: try downloading via yt-dlp if httpx failed ──
                original_url = media.get("original_url") if media else None
                if original_url:
                    logger.info(
                        f"[Download/Exec] video: httpx failed, trying yt-dlp fallback "
                        f"with original URL for {platform_id}"
                    )
                    try:
                        from app.boundary import validate_url
                        from app.services.media.parsers.ytdlp_service import (
                            YtdlpService,
                        )

                        # Boundary: SSRF guard. Original_url stored at parse
                        # time was validated, but defensive re-check protects
                        # against rows pre-dating boundary layer.
                        validated_url = validate_url(original_url)

                        source_platform = media.get("source_platform", "douyin")
                        media_id = str(media["id"])
                        storage_dir, relative_prefix = Utils.create_web_resource_path(
                            source_platform, media_id
                        )

                        async def on_progress(downloaded: int, total: int, speed: str):
                            await tracker.update(downloaded, total)

                        ytdlp_result = run_async(
                            YtdlpService.download_video(
                                validated_url,
                                str(storage_dir),
                                platform_id,
                                progress_callback=on_progress,
                                user_id=user_id,
                                user_agent=user_agent,
                            )
                        )
                        if ytdlp_result.get("file_path"):
                            file_name = os.path.basename(ytdlp_result["file_path"])
                            relative_path = f"{relative_prefix}/{file_name}"
                            from app.repositories.media_repository import (
                                MediaRepository as _MR_yt,
                            )

                            run_async(
                                _MR_yt().mark_media_as_downloaded(
                                    platform_id=platform_id,
                                    download_path=relative_path,
                                    duration=0,
                                    storage_size=ytdlp_result.get("file_size", 0),
                                )
                            )
                            run_async(
                                DownloaderService.optimize_video_for_streaming(
                                    ytdlp_result["file_path"]
                                )
                            )
                            results["video"] = DownloadStatus.COMPLETED.value
                            logger.success(
                                f"[Download/Exec] video: yt-dlp fallback succeeded for {platform_id}"
                            )
                        else:
                            logger.warning(
                                f"[Download/Exec] video: yt-dlp fallback also failed for {platform_id}"
                            )
                    except Exception as ytdlp_err:
                        logger.warning(
                            f"[Download/Exec] video: yt-dlp fallback error for {platform_id}: "
                            f"{type(ytdlp_err).__name__}: {ytdlp_err}"
                        )
                else:
                    logger.warning(
                        f"[Download/Exec] video: no original_url available for yt-dlp fallback: {platform_id}"
                    )

                # ── BrowserAuto fallback: re-parse via DrissionPage when httpx+yt-dlp both fail ──
                # DrissionPage + DouyinFormatter are douyin-specific (the
                # browser scraper targets douyin's share page DOM, and the
                # formatter expects aweme_detail-shaped input). Running
                # them on bilibili / youtube / etc. wastes ~30s loading a
                # headless Chromium and always returns empty parsed data.
                # 2026-05-13: confirmed on prod — bilibili tasks reached
                # this branch and spent 30s in DrissionPage before the
                # PR #264 partial-fail raise kicked in. Skip for non-
                # douyin platforms; let the raise fire immediately.
                source_platform = (
                    (media or {}).get("source_platform") if media else None
                )
                if (
                    results["video"] != "completed"
                    and original_url
                    and source_platform in ("douyin", "tiktok")
                ):
                    logger.info(
                        f"[Download/Exec] video: httpx+yt-dlp both failed, trying BrowserAuto "
                        f"(DrissionPage) to get fresh URLs for {platform_id}"
                    )
                    try:
                        from app.repositories.media_repository import (
                            MediaRepository as _MR_browser,
                        )
                        from app.services.media.parsers.douyin_parse.drissionpage_parser import (
                            DrissionPageParser,
                        )
                        from app.services.media.parsers.douyin_parse.formatter import (
                            DouyinFormatter,
                        )

                        browser_detail = run_async(
                            DrissionPageParser.fetch_one_video(
                                original_url, user_agent=user_agent
                            )
                        )
                        if browser_detail:
                            browser_parsed = run_async(
                                DouyinFormatter.parse_aweme_detail(
                                    aweme_detail=browser_detail,
                                    valid_url=original_url,
                                    download_video=True,
                                    download_music=False,
                                    download_cover=False,
                                )
                            )
                            fresh_urls = (
                                browser_parsed.get("video_download_urls")
                                if browser_parsed
                                else None
                            )
                            if fresh_urls:
                                run_async(
                                    _MR_browser().update(
                                        platform_id,
                                        {"video_download_urls": fresh_urls},
                                    )
                                )
                                logger.info(
                                    f"[Download/Exec] video: BrowserAuto got {len(fresh_urls)} "
                                    f"fresh URLs, retrying httpx for {platform_id}"
                                )
                                video_result = run_async(
                                    DownloaderService.download_video_by_platform_id(
                                        platform_id,
                                        user_id=user_id,
                                        progress_tracker=tracker,
                                        user_agent=user_agent,
                                    )
                                )
                                results["video"] = (
                                    video_result.video_download_status.value
                                    if hasattr(video_result, "video_download_status")
                                    else "unknown"
                                )
                                if results["video"] == "completed":
                                    logger.success(
                                        f"[Download/Exec] video: BrowserAuto fallback "
                                        f"succeeded for {platform_id}"
                                    )
                                else:
                                    logger.warning(
                                        f"[Download/Exec] video: BrowserAuto fallback "
                                        f"download also failed for {platform_id}"
                                    )
                            else:
                                logger.warning(
                                    f"[Download/Exec] video: BrowserAuto parse yielded no "
                                    f"video URLs for {platform_id}"
                                )
                        else:
                            logger.warning(
                                f"[Download/Exec] video: BrowserAuto returned empty "
                                f"for {platform_id}"
                            )
                    except Exception as browser_err:
                        logger.warning(
                            f"[Download/Exec] video: BrowserAuto fallback error for "
                            f"{platform_id}: {type(browser_err).__name__}: {browser_err}"
                        )

            # Mark video stage complete
            if "video" in stages:
                video_end = stages["video"][0] + stages["video"][1]
                force_progress(tracker, video_end)

            # Audio extraction moved to extract_audio_workflow (own DBOS
            # workflow with task_tracking row + status). Dispatched from
            # download.chain_followups_step after the video lands.

    elif int(media_type) in (2, 68):  # Image types
        if download_video:  # "video" flag used for images too
            # Mark image stage start
            if "video" in stages:
                offset, weight = stages["video"]
                tracker.set_stage(offset, weight)
                force_progress(tracker, offset, subtitle="Downloading images...")

            media, img_ok, reason = validate_and_refresh_urls(
                platform_id, media, "image_download_urls", "image"
            )
            if not img_ok:
                logger.warning(
                    f"[Download/Exec] image: HEAD check failed for {platform_id} ({reason}), "
                    f"attempting GET download anyway"
                )
            logger.info(f"[Download/Exec] image: downloading {platform_id}...")
            video_result = run_async(
                DownloaderService.download_images_by_platform_id(
                    platform_id,
                    user_id=user_id,
                    user_agent=user_agent,
                )
            )
            results["video"] = (
                video_result.video_download_status.value
                if hasattr(video_result, "video_download_status")
                else "unknown"
            )
            logger.info(f"[Download/Exec] image: {results['video']} for {platform_id}")
            if results["video"] != "completed":
                error_msg = (
                    getattr(video_result, "error", None) or "Image download failed"
                )
                logger.warning(
                    f"[Download/Exec] image failed for {platform_id}: {error_msg}"
                )

                # Fallback: re-parse to get fresh image URLs and retry
                try:
                    from app.repositories.media_repository import MediaRepository
                    from app.services.media.parsers.douyin_parse.formatter import (
                        DouyinFormatter,
                    )
                    from app.services.media.parsers.douyin_parse.ies_parser import (
                        IesDouyinParser,
                    )

                    logger.info(
                        f"[Download/Exec] image: re-parsing for fresh URLs {platform_id}"
                    )
                    # Use the task's chosen UA so re-parse request is
                    # consistent with the original parse+download chain.
                    _re_ua = user_agent
                    if not _re_ua:
                        from app.services.media.parsers.douyin_parse.ua_pool import (
                            pick_ua,
                        )

                        _re_ua = pick_ua()
                    aweme_detail = run_async(
                        IesDouyinParser._fetch_share_page(
                            platform_id, user_agent=_re_ua
                        )
                    )
                    if aweme_detail:
                        IesDouyinParser._process_video_urls(aweme_detail)
                        new_parsed = run_async(
                            DouyinFormatter.parse_aweme_detail(
                                aweme_detail=aweme_detail,
                                valid_url=media.get("original_url", ""),
                                download_video=True,
                                download_music=False,
                                download_cover=False,
                            )
                        )
                        if new_parsed:
                            # Update DB with fresh URLs
                            update_fields = {}
                            for field in ("image_download_urls", "video_download_urls"):
                                if new_parsed.get(field):
                                    update_fields[field] = new_parsed[field]
                            if update_fields:
                                repo = MediaRepository()
                                run_async(repo.update(platform_id, update_fields))
                                logger.info(
                                    f"[Download/Exec] image: re-parsed {platform_id}, "
                                    f"updated {list(update_fields.keys())}"
                                )

                            # Retry download with fresh URLs
                            video_result = run_async(
                                DownloaderService.download_images_by_platform_id(
                                    platform_id, user_id=user_id
                                )
                            )
                            results["video"] = (
                                video_result.video_download_status.value
                                if hasattr(video_result, "video_download_status")
                                else "unknown"
                            )
                            logger.info(
                                f"[Download/Exec] image retry: "
                                f"{results['video']} for {platform_id}"
                            )
                except Exception as reparse_err:
                    logger.warning(
                        f"[Download/Exec] image: re-parse fallback failed "
                        f"for {platform_id}: {reparse_err}"
                    )

            # Mark image stage complete
            if "video" in stages:
                video_end = stages["video"][0] + stages["video"][1]
                force_progress(tracker, video_end)

    if download_cover:
        # Mark cover stage start
        if "cover" in stages:
            offset, weight = stages["cover"]
            tracker.set_stage(offset, weight)
            force_progress(tracker, offset, subtitle="Downloading cover...")

        logger.info(f"[Download/Exec] cover: downloading {platform_id}...")
        result = run_async(
            DownloaderService.download_cover_by_platform_id(
                platform_id,
                user_id=user_id,
                user_agent=user_agent,
            )
        )
        results["cover"] = (
            result.cover_download_status.value
            if hasattr(result, "cover_download_status")
            else "unknown"
        )
        logger.info(f"[Download/Exec] cover: {results['cover']} for {platform_id}")

        # Mark cover stage complete
        if "cover" in stages:
            cover_end = stages["cover"][0] + stages["cover"][1]
            force_progress(tracker, cover_end)

    return results

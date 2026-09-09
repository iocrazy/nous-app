# app/services/downloader.py

"""
Video download service module

Provides async video and audio download functionality with custom headers,
filename generation, and error handling. Uses httpx and aiofiles for efficient
async downloads.
"""

import asyncio
import os
from typing import Any, Dict, Optional

import aiofiles
import httpx
from loguru import logger

# from app.db.session import get_async_transaction_session
from app.agent_framework.process_lifecycle import safe_popen_kwargs
from app.boundary import safe_async_client
from app.core.config import settings
from app.core.enums import DownloadStatus
from app.core.utils import Utils
from app.repositories.media_repository import MediaRepository
from app.repositories.user_logs_repository import log_user_action
from app.schemas.media import (
    DownloadCoverResult,
    DownloadImagesResult,
    DownloadMusicResult,
    DownloadVideoResult,
)


def _file_type_from_mime(mime: Optional[str]) -> str:
    """Derive the semantic ``resources.file_type`` enum from a MIME type.

    Task 8: the platform's numeric ``media_type`` code (0/4/68/2/51...) is
    NOT a valid file_type — it must never be written to this column
    directly. mime_type is the reliable source to derive from.
    """
    m = (mime or "").lower()
    if m.startswith("video/"):
        return "video"
    if m.startswith("image/"):
        return "image"
    if m.startswith("audio/"):
        return "audio"
    return "document"


async def persist_video_download(
    repo: MediaRepository,
    *,
    platform_id: str,
    download_path: str,
    duration: float,
    storage_size: int,
) -> bool:
    """Persist a finished video download to parsed_media; return False on failure.

    The DB write is the step that makes a download visible to the UI — if it
    fails, the download must NOT be reported as completed (2026-07-05
    incident: asyncpg bind error here was swallowed, every download looked
    successful while download_path stayed NULL for 8 days). On failure this
    also best-effort marks the row failed so the UI offers Retry.
    """
    try:
        await repo.mark_media_as_downloaded(
            platform_id=platform_id,
            download_path=download_path,
            duration=duration,
            storage_size=storage_size,
        )
        return True
    except Exception as e:
        logger.error(
            f"Video {platform_id} downloaded to disk, "
            f"but failed to update the database: {e}."
        )
        try:
            await repo.mark_download_failed(
                platform_id,
                f"DB write failed after download: {e}",
                is_video=True,
            )
        except Exception as mark_err:
            logger.error(f"Also failed to mark {platform_id} as failed: {mark_err}")
        return False


async def _upload_downloaded_video_to_s3(
    *,
    user_id: Optional[str],
    local_path: str,
    relative_path: str,
    mime: str = "video/mp4",
) -> str:
    """成品落盘后补传对象存储 — 实现已抽到 ``library/transit_upload``
    (2026-09-10),与汽水音频/UGC/封面上传共用同一条路;这里只保留名字,
    既有调用点与测试不动。语义见那边的模块文档。"""
    from app.services.library.transit_upload import upload_transit_file

    return await upload_transit_file(
        user_id=user_id, local_path=local_path, relative_path=relative_path, mime=mime
    )


async def _upload_album_to_s3(
    *,
    user_id: Optional[str],
    resource_id,
    local_dir: str,
    relative_path: str,
) -> str:
    """图集(carousel)整目录补传对象存储,返回 sb:// album 前缀。

    图集是目录形态(slides/ + audio.mp3 + cover.jpg),没有单文件内容哈希可
    寻址,所以走 album 前缀布局 ``t{scope}/album/{rid}/``——与
    storage_migration._migrate_album_row 迁移存量图集的 key 方案完全一致,
    读端(_resolve_album_location / serve_slide_file / is_prefix)已按此消费。
    这是 PR-1 之后第 4 条漏接 S3 的写路径(video-httpx/video-ytdlp/cover/
    thumbnail 已接),新图集此前一直整目录落文件系统(2026-08-01 深扫撞出)。

    开关关闭 / user_id 缺失 / resource_id 缺失(resource 建行失败)时原样返回
    ``relative_path``,保持旧 FS 行为可回退。上传失败上抛——外层已有
    "写失败即报 FAILED,retry 可重跑"语义,与视频路径一致。
    """
    from app.services.library.storage_flag import unified_storage_enabled

    if not user_id or not resource_id or not await unified_storage_enabled():
        return relative_path

    from app.services.library import media_storage
    from app.services.library.resources_service import _resolve_personal_team_id

    scope_id = int(await _resolve_personal_team_id(user_id))
    prefix = media_storage.album_key_prefix(scope_id, resource_id)
    store = media_storage.library_store()
    # skip_existing: retry 重放不重复 PUT 已落对象(对齐迁移模块)。
    await store.put_dir(local_dir, lambda rel: f"{prefix}{rel}", skip_existing=True)
    album_path = media_storage.to_file_path(store.bucket, prefix)
    # 整目录已在 S3 → 回收本地副本(slides/ + audio.mp3)。注意封面下载在
    # download_strategies 里排在图集分支之后,它会重建该目录写 cover.jpg,
    # 那份由封面自己的上传路径回收。
    media_storage.discard_local_source(local_dir, album_path)
    return album_path


# C1: 图集读端(media_slides_router.py _resolve_album_location)解析的是
# resource_versions.file_path(JOIN version_number = r.current_version),
# 不是 resources.file_path。重指块只更新了 resources.file_path,retry 场景
# (rv 已存在、仍指着文件系统)_resolve_album_location 永远拿不到 sb:// 前缀,
# slides 端点 404。这条 UPDATE 同时把该 resource 当前版本的 rv.file_path
# 刷成 album_path——与 storage_migration._downloads_update_row 同表同步的
# 做法一致。条件 `file_path NOT LIKE 'sb://%'` 使其对已迁移的行无害幂等;
# 首次下载时 rv 尚不存在,UPDATE 0 行——首下的 rv 由
# finalize_post_download_step 从 pm.download_path 建,那时 pm.download_path
# 已经是本函数写回的 sb:// 值,不会漏接。
#
# Phase A raw-SQL-to-ORM migration (docs/decisions/2026-08-04-raw-sql-to-orm-
# full-migration.md): _repoint_album_resource_version below now issues this
# as an ORM update()...where() (SQLAlchemy renders the cross-table WHERE
# reference as UPDATE...FROM on Postgres) — kept here as the reference shape
# it must stay semantically equivalent to.
_ALBUM_RV_REPOINT_SQL = """
    UPDATE resource_versions rv
    SET file_path = :file_path
    FROM resources r
    WHERE rv.resource_id = r.id
      AND rv.resource_id = :resource_id
      AND rv.version_number = r.current_version
      AND rv.file_path IS NOT NULL
      AND rv.file_path NOT LIKE 'sb://%'
"""


async def _repoint_album_resource_version(resource_id, album_path: str) -> None:
    """把 ``resource_id`` 当前版本的 resource_versions.file_path 重指到
    ``album_path``(见 ``_ALBUM_RV_REPOINT_SQL`` 的注释)。

    Called from the download pipeline (no HTTP request context, so no
    ambient user Scope) — wrapped in an ``is_enforced``-gated
    ``system_request_scope`` (same pattern as
    ``resources_repository.count_resources_by_media_id``).
    ``SCOPE_ENFORCE_RESOURCES`` DEFAULTS to false in code, but production
    sets it TRUE via ``secrets/backend.env`` (outside this repo tree —
    CLAUDE.md's 部署陷阱 on env overriding config.yml): in production this
    wrap is LOAD-BEARING, not a no-op — without it, this
    Resources-referencing UPDATE...FROM would either fail-closed raise (no
    scope set) or be forbidden outright (bulk DML under a real user scope)
    per the choke point's write-path rules, and every album repoint would
    raise instead of committing. The ``is_enforced`` gate exists only to
    stay byte-for-byte legacy where the flag really is off (e.g. this
    repo's local/test default). Resources is referenced only via the
    UPDATE...FROM join condition (not the UPDATE target itself);
    resource_versions carries no scope mixin.
    """
    from contextlib import nullcontext

    from sqlalchemy import update

    from app.db.scope import is_enforced, system_request_scope
    from app.db.session import write_scope
    from app.models import Resources, ResourceVersions

    stmt = (
        update(ResourceVersions)
        .where(ResourceVersions.resource_id == Resources.id)
        .where(ResourceVersions.resource_id == int(resource_id))
        .where(ResourceVersions.version_number == Resources.current_version)
        .where(ResourceVersions.file_path.is_not(None))
        .where(ResourceVersions.file_path.notlike("sb://%"))
        .values(file_path=album_path)
    )

    scope_cm = (
        system_request_scope(
            reason="album-resource-version-repoint: download pipeline "
            "write, no ambient request scope"
        )
        if is_enforced("resources")
        else nullcontext()
    )
    async with scope_cm:
        async with write_scope() as session:
            await session.execute(stmt)


class DownloaderService:

    @staticmethod
    async def optimize_video_for_streaming(file_path: str) -> bool:
        """
        Optimize video for web streaming by moving moov atom to the beginning.

        Uses ffmpeg with -movflags faststart to enable progressive playback.
        This allows browsers to start playing before the entire file is downloaded.

        Returns True if optimization succeeded, False otherwise.
        """
        import shutil

        if not file_path.endswith(".mp4"):
            return True  # Skip non-MP4 files

        temp_path = file_path + ".optimizing.mp4"

        try:
            # Run ffmpeg to optimize the video
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-y",  # Overwrite output
                "-i",
                file_path,
                "-c",
                "copy",  # Copy streams without re-encoding (fast)
                "-movflags",
                "faststart",  # Move moov atom to beginning
                temp_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **safe_popen_kwargs(),
            )
            try:
                _, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=300
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                logger.warning(f"视频优化超时: {os.path.basename(file_path)}")
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                return False

            if proc.returncode == 0 and os.path.exists(temp_path):
                # Check if the optimized file is valid
                optimized_size = os.path.getsize(temp_path)
                original_size = os.path.getsize(file_path)

                # Optimized file should be similar size (within 5%)
                if optimized_size >= original_size * 0.95:
                    # Replace original with optimized version
                    shutil.move(temp_path, file_path)
                    logger.info(f"视频已优化为流式播放: {os.path.basename(file_path)}")
                    return True
                else:
                    logger.warning(
                        f"优化后文件大小异常，保留原文件: {os.path.basename(file_path)}"
                    )
                    os.remove(temp_path)
                    return False
            else:
                stderr_text = (
                    stderr_bytes.decode(errors="replace") if stderr_bytes else ""
                )
                logger.warning(
                    f"视频优化失败: {stderr_text[:200] if stderr_text else 'Unknown error'}"
                )
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                return False

        except FileNotFoundError:
            logger.warning("ffmpeg not found, skipping video optimization")
            return False
        except Exception as e:
            logger.warning(f"视频优化出错: {e}")
            if os.path.exists(temp_path):
                os.remove(temp_path)
            return False

    @staticmethod
    async def verify_video_integrity(file_path: str) -> bool:
        """
        Verify video file integrity using ffmpeg.

        Returns True if video is valid, False if corrupted.
        """
        try:
            # Use ffmpeg to verify the file can be decoded
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-v",
                "error",
                "-i",
                file_path,
                "-f",
                "null",
                "-",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **safe_popen_kwargs(),
            )
            try:
                _, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=60)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                logger.warning(f"视频验证超时: {os.path.basename(file_path)}")
                return True  # Don't fail on timeout, assume OK

            stderr = (
                stderr_bytes.decode(errors="replace").lower() if stderr_bytes else ""
            )

            # Check for critical errors
            critical_errors = [
                "partial file",
                "invalid nal unit",
                "error splitting",
                "truncated",
                "moov atom not found",
            ]

            for error in critical_errors:
                if error in stderr:
                    logger.error(
                        f"视频文件损坏: {error} in {os.path.basename(file_path)}"
                    )
                    return False

            # If return code is non-zero and has errors, file is bad
            if proc.returncode != 0 and stderr:
                logger.error(f"视频验证失败: {stderr[:200]}")
                return False

            logger.debug(f"视频完整性验证通过: {os.path.basename(file_path)}")
            return True

        except FileNotFoundError:
            logger.warning("ffmpeg not found, skipping video integrity check")
            return True  # Skip check if ffmpeg not installed
        except Exception as e:
            logger.warning(f"视频验证出错: {e}")
            return True  # Don't fail on unknown errors

    @staticmethod
    async def download_file(
        url: str,
        file_path: str,
        headers: Dict[str, Any] = None,
        progress_tracker=None,
        platform_id: str = None,
        min_video_bytes: int = 200 * 1024,
    ) -> bool:
        """
        Download a single file with optional progress tracking

        Args:
            url: Download URL
            file_path: Save path
            headers: Request headers
            progress_tracker: Optional progress tracker (DownloadProgressTracker instance)
            platform_id: Optional platform ID for looking up cached browser cookies (Douyin)
            min_video_bytes: Reject .mp4 responses smaller than this as CDN error
                pages / truncated responses. Defaults to 200KB, the right floor
                for full-length videos. Live Photo slide clips are legitimately
                2-3s loops (~70-180KB), so the slide path passes a much smaller
                floor — ffprobe integrity verification below is the real gate.

        Returns:
            bool: Whether download was successful
        """
        # Boundary: safe_async_client (used below) validates URL + every
        # redirect hop. Defense-in-depth: catch URLBlockedError here to
        # apply the consistent "download failed" semantics (mark tracker
        # failed, return False) rather than letting it propagate.
        if not headers:
            headers = Utils.get_headers()
        else:
            headers = dict(headers)  # copy to avoid mutating caller's dict

        # Douyin CDN returns 403 for httpx requests. If DrissionPage cached browser
        # cookies during parse, merge them into the Cookie header.
        # Use the async Redis client here — a blocking sync call stalls the event
        # loop for every concurrent download on this worker.
        if platform_id and "douyin" in url.lower():
            try:
                from app.core.redis import get_async_redis

                r = await get_async_redis()
                cached = await r.get(f"douyin_browser_cookies:{platform_id}")
                if cached:
                    cookie_value = (
                        cached.decode() if isinstance(cached, bytes) else cached
                    )
                    existing = headers.get("Cookie", "")
                    headers["Cookie"] = (
                        f"{existing}; {cookie_value}" if existing else cookie_value
                    )
                    logger.debug(
                        f"[Download/File] Using cached browser cookies for {platform_id}"
                    )
            except Exception as e:
                logger.debug(f"[Download/File] Cookie lookup failed: {e}")

        try:
            os.makedirs(os.path.dirname(file_path), exist_ok=True)

            async with safe_async_client(http2=True) as client:
                # Check if file exists and is complete
                if os.path.exists(file_path):
                    existing_size = os.path.getsize(file_path)
                    # Get expected size via HEAD request
                    try:
                        head_resp = await client.head(
                            url, headers=headers, follow_redirects=True, timeout=10.0
                        )
                        expected_size = int(head_resp.headers.get("content-length", 0))
                        if expected_size > 0 and existing_size >= expected_size:
                            logger.info(
                                f"文件已存在且完整，跳过下载: {os.path.basename(file_path)} ({existing_size} bytes)"
                            )
                            if progress_tracker:
                                progress_tracker.complete()
                            return True
                        else:
                            logger.warning(
                                f"文件不完整 ({existing_size}/{expected_size} bytes)，重新下载: {os.path.basename(file_path)}"
                            )
                            os.remove(file_path)
                    except Exception as e:
                        logger.warning(f"无法验证文件完整性，重新下载: {e}")
                        os.remove(file_path)
                # Get expected size via HEAD request first
                expected_size = 0
                try:
                    head_resp = await client.head(
                        url, headers=headers, follow_redirects=True, timeout=10.0
                    )
                    expected_size = int(head_resp.headers.get("content-length", 0))
                except Exception as e:
                    logger.warning(f"无法获取预期文件大小: {e}")

                # Use streaming if progress tracker is provided
                if progress_tracker:
                    async with client.stream(
                        "GET",
                        url,
                        headers=headers,
                        follow_redirects=True,
                        timeout=settings.DOWNLOAD_TIMEOUT,
                    ) as response:
                        if response.status_code != 200:
                            logger.warning(
                                f"[Download/File] Stream failed: HTTP {response.status_code} "
                                f"for {url[:100]}, headers={dict(response.headers)}"
                            )
                            return False

                        total = int(response.headers.get("content-length", 0))
                        logger.info(
                            f"[Download/File] Stream started: content-length={total}, "
                            f"HEAD expected_size={expected_size}, "
                            f"transfer-encoding={response.headers.get('transfer-encoding', 'none')}"
                        )
                        if expected_size == 0:
                            expected_size = total
                        if total == 0 and expected_size > 0:
                            total = (
                                expected_size  # Use HEAD's content-length as fallback
                            )
                            logger.info(
                                f"[Download/File] Streaming content-length=0, "
                                f"using HEAD expected_size={expected_size} for progress"
                            )
                        downloaded = 0

                        async with aiofiles.open(file_path, mode="wb") as f:
                            async for chunk in response.aiter_bytes(
                                chunk_size=65536
                            ):  # 64KB chunks
                                await f.write(chunk)
                                downloaded += len(chunk)
                                await progress_tracker.update(downloaded, total)

                        # Post-download verification
                        actual_size = (
                            os.path.getsize(file_path)
                            if os.path.exists(file_path)
                            else 0
                        )
                        if (
                            expected_size > 0 and actual_size < expected_size * 0.95
                        ):  # Allow 5% tolerance
                            logger.error(
                                f"文件大小不匹配: 预期 {expected_size} bytes, 实际 {actual_size} bytes"
                            )
                            os.remove(file_path)
                            raise Exception(
                                f"File size mismatch: expected {expected_size}, got {actual_size}"
                            )

                        # Minimum size check: video files should be > 200KB
                        # CDN error pages or truncated responses are typically < 200KB
                        if file_path.endswith(".mp4") and actual_size < min_video_bytes:
                            logger.error(
                                f"[Download/File] Video too small ({actual_size} bytes), "
                                f"likely CDN error response: {os.path.basename(file_path)}"
                            )
                            os.remove(file_path)
                            return False

                        # Verify video file integrity with ffprobe
                        if file_path.endswith(".mp4"):
                            if not await DownloaderService.verify_video_integrity(
                                file_path
                            ):
                                os.remove(file_path)
                                raise Exception(
                                    f"Video file corrupted or incomplete: {os.path.basename(file_path)}"
                                )

                        logger.success(
                            f"成功下载文件: {os.path.basename(file_path)} ({actual_size} bytes)"
                        )
                        return True
                else:
                    # Original non-streaming download (larger timeout for big files)
                    response = await client.get(
                        url,
                        headers=headers,
                        follow_redirects=True,
                        timeout=httpx.Timeout(settings.DOWNLOAD_TIMEOUT, connect=30.0),
                    )

                    if response.status_code == 200:
                        content_len = len(response.content)
                        logger.info(
                            f"[Download/File] HTTP 200, content_length={content_len}, saving to {os.path.basename(file_path)}"
                        )
                        async with aiofiles.open(file_path, mode="wb") as f:
                            await f.write(response.content)

                        # Post-download verification
                        actual_size = (
                            os.path.getsize(file_path)
                            if os.path.exists(file_path)
                            else 0
                        )
                        if (
                            expected_size > 0 and actual_size < expected_size * 0.95
                        ):  # Allow 5% tolerance
                            logger.error(
                                f"文件大小不匹配: 预期 {expected_size} bytes, 实际 {actual_size} bytes"
                            )
                            os.remove(file_path)
                            raise Exception(
                                f"File size mismatch: expected {expected_size}, got {actual_size}"
                            )

                        # Minimum size check: video files should be > 200KB
                        if file_path.endswith(".mp4") and actual_size < min_video_bytes:
                            logger.error(
                                f"[Download/File] Video too small ({actual_size} bytes), "
                                f"likely CDN error response: {os.path.basename(file_path)}"
                            )
                            os.remove(file_path)
                            return False

                        # Verify video file integrity with ffprobe
                        if file_path.endswith(".mp4"):
                            if not await DownloaderService.verify_video_integrity(
                                file_path
                            ):
                                os.remove(file_path)
                                raise Exception(
                                    f"Video file corrupted or incomplete: {os.path.basename(file_path)}"
                                )

                        logger.success(
                            f"成功下载文件: {os.path.basename(file_path)} ({actual_size} bytes)"
                        )
                        return True
                    else:
                        logger.warning(
                            f"[Download/File] HTTP {response.status_code} for {url[:100]}..., "
                            f"headers={dict(response.headers)}"
                        )
                        return False

        except Exception as e:
            logger.error(
                f"[Download/File] Exception downloading {url[:100]}...: {type(e).__name__}: {str(e)}"
            )
            if progress_tracker:
                progress_tracker.failed(str(e))
            return False

    @staticmethod
    async def download_video_by_platform_id(
        platform_id,
        user_id: str = None,
        progress_tracker=None,
        user_agent: str = None,
    ) -> DownloadVideoResult:
        """
        Download video and optional music files

        Args:
            platform_id: Video platform ID
            user_id: User ID (for data isolation)
            progress_tracker: Optional progress tracker
            user_agent: Optional explicit UA — when provided, overrides the
                random UA from Utils.get_headers() so the download request
                matches the UA used during parse+ABogus signing.

        Returns:
            DownloadResult: Download result info
        """
        # Initialize result object, pydantic2.0 method
        result = DownloadVideoResult.model_construct()
        headers = Utils.get_headers()
        if user_agent:
            headers["User-Agent"] = user_agent

        try:

            # Establish database connection
            repo = MediaRepository()

            # Get video data
            try:
                video_data = await repo.get_by_platform_id(platform_id)
            except Exception as db_err:
                logger.error(
                    f"DB query failed for {platform_id}: {db_err}", exc_info=True
                )
                result.video_download_status = DownloadStatus.FAILED
                result.error = f"Database query failed: {db_err}"
                return result
            if not video_data:
                logger.error(
                    f"Record not found in parsed_media: platform_id={platform_id}, user_id={user_id}"
                )
                result.video_download_status = DownloadStatus.FAILED
                result.error = f"Record not found: {platform_id} (user={user_id})"
                return result

            logger.info(f"准备下载视频: {platform_id}")

            # Create structured path: global/resources/web/{platform}/{media_id}/
            source_platform = video_data.get("source_platform", "douyin")
            media_id = str(video_data["id"])
            full_path, relative_prefix = Utils.create_web_resource_path(
                source_platform, media_id
            )
            logger.debug(f"下载路径: {full_path}, 相对前缀: {relative_prefix}")

            video_full_path = os.path.join(full_path, "video.mp4")
            video_relative_path = f"{relative_prefix}/video.mp4"
            video_title = video_data.get("title", "undefined")
            logger.debug(f"视频文件: {video_relative_path}")

            # Download video
            video_urls = video_data.get("video_download_urls") or []

            # Ensure a stable play URL fallback exists (no expiry, unlike CDN URLs)
            play_addr = (
                video_data.get("video", {}).get("play_addr", {})
                if isinstance(video_data.get("video"), dict)
                else {}
            )
            video_uri = play_addr.get("uri", "")
            if not video_uri:
                # Try to extract video_id from existing URLs
                import re

                for _u in video_urls:
                    _m = re.search(r"video_id=([^&]+)", _u)
                    if _m:
                        video_uri = _m.group(1)
                        break
            if video_uri:
                stable_url = f"https://aweme.snssdk.com/aweme/v1/play/?video_id={video_uri}&ratio=720p&line=0"
                if stable_url not in video_urls:
                    video_urls.append(stable_url)
                    logger.info(
                        f"[Download/Video] Appended stable play URL fallback for {platform_id}"
                    )

            if not video_urls:
                logger.warning(f"视频 {platform_id} 没有可用的下载URL")
                result.video_download_status = DownloadStatus.FAILED
                result.error = f"视频 {platform_id} 没有可用的下载URL"

                # Update database status to "failed"
                await repo.update(
                    platform_id,
                    {
                        "video_download_status": DownloadStatus.FAILED,
                        "error_message": result.error,
                    },
                )
                return result

            # todo calculate download time
            download_duration = 10

            # Diagnostic: test URL accessibility before download
            download_errors = []
            for i, url in enumerate(video_urls):
                try:
                    async with safe_async_client(http2=True) as _diag:
                        _r = await _diag.head(
                            url, headers=headers, follow_redirects=True, timeout=10.0
                        )
                        logger.info(
                            f"[Download/Diag] URL {i+1}/{len(video_urls)} HEAD: "
                            f"status={_r.status_code}, final_url={str(_r.url)[:80]}"
                        )
                except Exception as e:
                    logger.warning(
                        f"[Download/Diag] URL {i+1} HEAD failed: {type(e).__name__}: {e}"
                    )

            # download video while one of the urls is successful
            for idx, url in enumerate(video_urls):
                logger.info(
                    f"[Download/Video] Trying URL {idx+1}/{len(video_urls)} for {platform_id}: {url[:100]}"
                )
                success = await DownloaderService.download_file(
                    url,
                    video_full_path,
                    headers,
                    progress_tracker,
                    platform_id=platform_id,
                )
                if not success:
                    download_errors.append(f"URL{idx+1}: download_file returned False")
                    logger.warning(
                        f"[Download/Video] URL {idx+1} failed for {platform_id}"
                    )
                    continue
                if success:
                    # Optimize video for streaming (move moov atom to beginning)
                    await DownloaderService.optimize_video_for_streaming(
                        video_full_path
                    )

                    # Calculate file size (after optimization)
                    file_size = (
                        os.path.getsize(video_full_path)
                        if os.path.exists(video_full_path)
                        else 0
                    )

                    # 存储分层:新下载的成品直接补传 S3(止血,存量不动)。开关
                    # 关闭 / user_id 缺失时原样返回本地相对路径,行为不变。
                    video_relative_path = await _upload_downloaded_video_to_s3(
                        user_id=user_id,
                        local_path=video_full_path,
                        relative_path=video_relative_path,
                    )

                    # Store relative path and file size to database. If this
                    # write fails the UI will never see the file — report
                    # FAILED so the user gets a working Retry, not a phantom
                    # "completed" with no video (2026-07-05 incident).
                    persisted = await persist_video_download(
                        repo,
                        platform_id=platform_id,
                        download_path=video_relative_path,
                        duration=download_duration,
                        storage_size=file_size,
                    )
                    if not persisted:
                        result.video_download_status = DownloadStatus.FAILED
                        result.error = (
                            f"Video {platform_id} downloaded to disk but the "
                            f"database write failed; reported as failed so "
                            f"retry can re-run the registration."
                        )
                        break

                    # Update result object
                    result.video_download_status = DownloadStatus.COMPLETED
                    result.video_path = video_relative_path  # Return relative path
                    result.download_duration = download_duration
                    logger.success(
                        f"Video {platform_id} downloaded successfully and saved to {video_full_path}."
                    )

                    # Log success
                    if user_id:
                        await log_user_action(
                            user_id=user_id,
                            action="download",
                            message=f"Video downloaded: {video_title[:30]}...",
                            status="success",
                            aweme_id=platform_id,
                            details={
                                "media_type": "video",
                                "platform": video_data.get("source_platform", "douyin"),
                            },
                        )
                    break

            # "All URLs failed" applies only when no earlier step already
            # recorded a specific failure — the persist-failure path above
            # sets result.error itself, and this block would clobber that
            # message (and the DB error_message) with "unknown".
            if result.video_download_status != DownloadStatus.COMPLETED and not (
                result.error
            ):
                error_detail = (
                    "; ".join(download_errors) if download_errors else "unknown"
                )
                logger.error(
                    f"All {len(video_urls)} download URLs for video {platform_id} failed: {error_detail}"
                )
                result.video_download_status = DownloadStatus.FAILED
                result.error = f"All {len(video_urls)} URLs failed: {error_detail}"

                # Update database status to "failed" with detailed error
                await repo.update(
                    platform_id,
                    {
                        "video_download_status": DownloadStatus.FAILED,
                        "error_message": result.error[:500],
                    },
                )

                # Log failure
                if user_id:
                    await log_user_action(
                        user_id=user_id,
                        action="download",
                        message=f"Video download failed: {video_title[:30]}...",
                        status="error",
                        aweme_id=platform_id,
                        details={
                            "error": result.error,
                            "platform": video_data.get("source_platform", "douyin"),
                        },
                    )

            return result

        except Exception as e:
            logger.error(f"Download processing error:: {str(e)}")
            result.video_download_status = DownloadStatus.FAILED
            result.error = str(e)
            return result

    @staticmethod
    async def download_slide_item(
        index: int,
        url_list: list,
        slides_dir: str,
        is_video: bool,
        headers: dict,
        platform_id: str = None,
    ) -> dict:
        """Download a single slide item (image or video clip) to the slides/ subfolder.

        Files are named sequentially: 001.jpg, 002.mp4, etc.

        Args:
            index: Zero-based index for sequential numbering
            url_list: List of fallback URLs for this item
            slides_dir: Full path to the slides/ directory
            is_video: True for video clips (.mp4), False for images (.jpg)
            headers: HTTP request headers

        Returns:
            dict with 'success', 'path', and optionally 'already_exists' or 'message'
        """
        extension = ".mp4" if is_video else ".jpg"
        filename = f"{index + 1:03d}{extension}"
        file_path = os.path.join(slides_dir, filename)

        os.makedirs(slides_dir, exist_ok=True)

        if os.path.exists(file_path):
            logger.info(f"Slide already exists, skipping: {filename}")
            return {"success": True, "path": file_path, "already_exists": True}

        for j, url in enumerate(url_list):
            try:
                if await DownloaderService.download_file(
                    url,
                    file_path,
                    headers,
                    platform_id=platform_id,
                    # Live Photo slide clips are 2-3s loops (~70-180KB). The
                    # default 200KB floor (sized for full videos) silently
                    # deleted every real slide as a "CDN error response" →
                    # 0/N slides. Drop the floor here; the bare 403 error page
                    # is ~353B and ffprobe integrity-checks the result anyway.
                    min_video_bytes=16 * 1024,
                ):
                    return {"success": True, "path": file_path}
            except Exception as e:
                logger.warning(f"Slide {filename} URL {j} failed: {e}")
                continue

        return {
            "success": False,
            "path": None,
            "message": f"All {len(url_list)} URLs failed for slide {filename}",
        }

    @staticmethod
    async def _download_standalone_music(
        platform_id: str,
        music_play_urls: list,
        resource_dir_full: str,
        resource_dir_relative: str,
        headers: dict,
        repo: "MediaRepository",
    ) -> bool:
        """Download standalone background music for carousel/image-text content.

        Args:
            platform_id: Media platform ID
            music_play_urls: List of fallback music URLs
            resource_dir_full: Full path to the resource directory
            resource_dir_relative: Relative path prefix
            headers: HTTP request headers
            repo: MediaRepository instance for DB updates

        Returns:
            True if music was downloaded successfully, False otherwise
        """
        if not music_play_urls:
            logger.info(f"[Music/Standalone] No music_play_urls for {platform_id}")
            return False

        music_full_path = os.path.join(resource_dir_full, "audio.mp3")
        music_relative_path = f"{resource_dir_relative}/audio.mp3"

        for idx, url in enumerate(music_play_urls):
            logger.info(
                f"[Music/Standalone] {platform_id}: trying URL[{idx}] → {url[:80]}..."
            )
            if await DownloaderService.download_file(url, music_full_path, headers):
                try:
                    await repo.update(
                        platform_id,
                        {
                            "music_download_status": DownloadStatus.COMPLETED.value,
                            "music_download_path": music_relative_path,
                        },
                    )
                except Exception as e:
                    logger.error(
                        f"[Music/Standalone] DB update failed for {platform_id}: {e}"
                    )
                logger.success(f"[Music/Standalone] Downloaded music for {platform_id}")
                return True

        logger.warning(
            f"[Music/Standalone] All {len(music_play_urls)} URLs failed for {platform_id}"
        )
        await repo.update(
            platform_id,
            {"music_download_status": DownloadStatus.FAILED.value},
        )
        return False

    @staticmethod
    async def _ensure_carousel_resource(
        media_id: str,
        user_id: str,
        platform_id: str,
        resource_dir_relative: str,
        video_data: dict,
    ) -> Optional[str]:
        """Create a resource record for carousel content if one doesn't exist.

        Returns the resource id (existing or newly created) so the caller can
        build the album's S3 prefix (``t{scope}/album/{rid}/``); None when
        creation failed.

        Args:
            media_id: parsed_media ID (Snowflake BIGINT as string)
            user_id: Creator user ID
            platform_id: Platform content ID
            resource_dir_relative: Relative path to resource folder
            video_data: parsed_media record dict
        """
        from app.db.scope import Scope, request_scope
        from app.repositories.resources_repository import ResourcesRepository

        resources_repo = ResourcesRepository()

        # A2 pass 4b: this ASYNC function reads + creates the per-user
        # `resources` row (and its resource_item), so the ambient USER scope
        # must be set around the resource work. Wrapped in its own body (self-
        # contained — does not depend on pass-4a). `user_id` is a required
        # param. INERT until SCOPE_ENFORCE_RESOURCES flips.
        async with request_scope(Scope(user_id=user_id)):
            existing = await resources_repo.get_resource_by_media_id_and_creator(
                media_id, user_id
            )
            if existing:
                logger.info(
                    f"[Carousel/Resource] Resource already exists for media {media_id} "
                    f"(user {user_id})"
                )
                return str(existing.get("id")) if existing.get("id") else None

            # Shared assets — cover_image_path, *_download_status — live on
            # parsed_media. ``file_path`` for carousel slides points at the
            # resource_dir; parsed_media.image_download_path covers the same
            # files but the resource needs a path to materialise the per-user
            # row, so this one stays as a per-user file pointer (matches the
            # shared dir, but written via a different lifecycle than the
            # mirror writes we removed elsewhere).
            # Carousel slides default to image/jpeg unless the platform
            # payload carries its own mime_type (e.g. a video slide).
            # file_type is derived from mime, NOT from the platform's
            # numeric media_type code (0/4/68/2/51...) — that code is not
            # a valid resources.file_type value (task 8 fix).
            mime_type = video_data.get("mime_type") or "image/jpeg"
            resource_data = {
                "creator_id": user_id,
                "media_id": media_id,
                "source_type": "web",
                "file_path": resource_dir_relative,
                "mime_type": mime_type,
                "filename": f"{platform_id}_slides",
                "file_type": _file_type_from_mime(mime_type),
            }
            try:
                resource = await resources_repo.create_resource(resource_data)
                resource_id = resource.get("id") if resource else None
                logger.info(
                    f"[Carousel/Resource] Created resource {resource_id} for media {media_id}"
                )

                # Create resource_item for user's personal scope.
                # scope_id MUST be the personal-team snowflake (bigint), not the
                # user UUID — resource_items.scope_id became bigint in PR-E 4c-3,
                # so writing the raw UUID now fails 22P02 and silently orphans the
                # downloaded resource from the owner's library.
                if resource_id and user_id:
                    from app.services.library.resources_service import (
                        _resolve_personal_team_id,
                    )

                    scope_id = await _resolve_personal_team_id(user_id)
                    await resources_repo.create_resource_item(
                        {
                            "resource_id": resource_id,
                            # PR-E 4b: scope_type no longer written.
                            "scope_id": scope_id,
                            "added_by": user_id,
                        }
                    )
                return str(resource_id) if resource_id else None
            except Exception as e:
                logger.error(
                    f"[Carousel/Resource] Failed to create resource for media {media_id}: {e}"
                )
                return None

    @staticmethod
    async def download_images_by_platform_id(
        platform_id,
        user_id: str = None,
        user_agent: str = None,
    ):
        """
        Download carousel images/videos to slides/ subfolder, standalone music,
        and create resource record.

        Saves files as:
          {media_id}/slides/001.jpg, 002.mp4, ...  (sequential numbering)
          {media_id}/audio.mp3                       (standalone background music)

        Args:
            platform_id: Video platform ID
            user_id: User ID (for data isolation)
            user_agent: Optional explicit UA — see download_video_by_platform_id.

        Returns:
            DownloadImagesResult: Download result info
        """
        result = DownloadImagesResult.model_construct()
        headers = Utils.get_headers()
        if user_agent:
            headers["User-Agent"] = user_agent

        try:
            repo = MediaRepository()

            # Get media data from DB
            try:
                video_data = await repo.get_by_platform_id(platform_id)
            except Exception as db_err:
                logger.error(
                    f"DB query failed for {platform_id}: {db_err}", exc_info=True
                )
                result.video_download_status = DownloadStatus.FAILED
                result.error = f"Database query failed: {db_err}"
                return result
            if not video_data:
                logger.error(
                    f"Record not found in parsed_media: platform_id={platform_id}, user_id={user_id}"
                )
                result.video_download_status = DownloadStatus.FAILED
                result.error = f"Record not found: {platform_id} (user={user_id})"
                return result

            logger.info(f"准备下载 {platform_id} 的图片集 (slides/ subfolder)")

            # Create structured path: global/resources/web/{platform}/{media_id}/
            source_platform = video_data.get("source_platform", "douyin")
            media_id = str(video_data["id"])
            resource_dir_full, resource_dir_relative = Utils.create_web_resource_path(
                source_platform, media_id
            )
            slides_dir = os.path.join(str(resource_dir_full), "slides")
            os.makedirs(slides_dir, exist_ok=True)
            video_title = video_data.get("title", "undefined")
            logger.debug(f"Slides download path: {slides_dir}")

            video_urls = video_data.get("video_download_urls")
            image_urls = video_data.get("image_download_urls")

            # Build a unified ordered list of slide items:
            # Each item is (url_list, is_video)
            # We need to reconstruct the original order from images data.
            # For type 68 (image-text), images and videos are interleaved.
            # The parser extracts them separately, so we merge back in order.
            # Since we don't have original ordering info, we download videos first,
            # then images — each group keeps its own order.
            slide_items = []
            if Utils.is_nested_list(video_urls):
                for url_list in video_urls:
                    slide_items.append((url_list, True))
            if Utils.is_nested_list(image_urls):
                for url_list in image_urls:
                    slide_items.append((url_list, False))

            total_expected = len(slide_items)
            downloaded_count = 0

            if slide_items:
                logger.info(f"Downloading {total_expected} slides for {platform_id}")
                download_tasks = []
                async with asyncio.TaskGroup() as tg:
                    for i, (url_list, is_video) in enumerate(slide_items):
                        task = tg.create_task(
                            DownloaderService.download_slide_item(
                                i,
                                url_list,
                                slides_dir,
                                is_video,
                                headers,
                                platform_id=platform_id,
                            )
                        )
                        download_tasks.append(task)

                for task in download_tasks:
                    try:
                        task_result = task.result()
                        if task_result and task_result.get("success", False):
                            downloaded_count += 1
                    except Exception as e:
                        logger.error(f"Slide download task result error: {e}")

                logger.info(
                    f"{platform_id} slides download: {downloaded_count}/{total_expected}"
                )

            if downloaded_count == total_expected and total_expected > 0:
                logger.success(
                    f"{platform_id} slides download complete: {downloaded_count} files"
                )
                await repo.update(
                    platform_id,
                    {
                        "video_download_status": DownloadStatus.COMPLETED,
                        "download_path": resource_dir_relative,
                    },
                )
                result.video_download_status = DownloadStatus.COMPLETED

                # Download standalone music (non-blocking for overall result)
                music_play_urls = video_data.get("music_play_urls") or []
                if music_play_urls:
                    await DownloaderService._download_standalone_music(
                        platform_id=platform_id,
                        music_play_urls=music_play_urls,
                        resource_dir_full=str(resource_dir_full),
                        resource_dir_relative=resource_dir_relative,
                        headers=headers,
                        repo=repo,
                    )

                # Create resource record (backfill)
                if user_id:
                    # I5: _ensure_carousel_resource opens its own
                    # request_scope internally, but the upload+repoint block
                    # below also writes to `resources` (update_resource,
                    # load-then-modify via write_scope) — that call needs the
                    # SAME ambient USER scope or it raises UnscopedQueryError
                    # the moment SCOPE_ENFORCE_RESOURCES flips on. Wrapping the
                    # whole block (not just the _ensure_carousel_resource call)
                    # covers both. Nesting with the scope
                    # _ensure_carousel_resource itself opens is safe —
                    # request_scope tokens stack and unwind independently.
                    from app.db.scope import Scope, request_scope

                    async with request_scope(Scope(user_id=user_id)):
                        carousel_rid = (
                            await DownloaderService._ensure_carousel_resource(
                                media_id=media_id,
                                user_id=user_id,
                                platform_id=platform_id,
                                resource_dir_relative=resource_dir_relative,
                                video_data=video_data,
                            )
                        )

                        # BGM pointer determinism (discard-review C1):
                        # ``_upload_album_to_s3`` now rmtree's the local album
                        # directory on success (discard_local_source), so
                        # audio.mp3's existence must be captured BEFORE the
                        # upload call — checking afterwards would always see
                        # an already-deleted directory and silently drop
                        # music_download_path forever (BGM 404 + a
                        # regenerated fs_residue row on the next scan).
                        # audio.mp3 (if any) was written earlier by
                        # ``_download_standalone_music`` above, so it is
                        # already in place by the time we snapshot this.
                        has_audio = os.path.isfile(
                            os.path.join(str(resource_dir_full), "audio.mp3")
                        )

                        # Storage tiering: upload the whole album directory
                        # (slides/ + audio.mp3 + cover.jpg) to the canonical
                        # album prefix t{scope}/album/{rid}/ and repoint the
                        # index columns at it — the 4th unwired write path
                        # after video-httpx/video-ytdlp/cover/thumbnail; new
                        # albums silently landed on the filesystem until
                        # 2026-08-01's deep scan caught one. Flag off /
                        # missing rid → FS paths stay (old behavior).
                        album_path = await _upload_album_to_s3(
                            user_id=user_id,
                            resource_id=carousel_rid,
                            local_dir=str(resource_dir_full),
                            relative_path=resource_dir_relative,
                        )
                        if album_path.startswith("sb://"):
                            pm_updates = {"download_path": album_path}
                            if has_audio:
                                pm_updates["music_download_path"] = (
                                    f"{album_path}audio.mp3"
                                )
                            await repo.update(platform_id, pm_updates)
                            from app.repositories.resources_repository import (
                                ResourcesRepository,
                            )

                            await ResourcesRepository().update_resource(
                                carousel_rid, {"file_path": album_path}
                            )
                            # C1: also repoint the CURRENT resource_versions
                            # row — the slides read path resolves via
                            # resource_versions.file_path, not
                            # resources.file_path (see
                            # _ALBUM_RV_REPOINT_SQL's comment).
                            await _repoint_album_resource_version(
                                carousel_rid, album_path
                            )

                # Log success
                if user_id:
                    await log_user_action(
                        user_id=user_id,
                        action="download",
                        message=f"Image set downloaded: {video_title[:30]}... ({downloaded_count} files)",
                        status="success",
                        aweme_id=platform_id,
                        details={
                            "media_type": "images",
                            "file_count": downloaded_count,
                            "platform": source_platform,
                        },
                    )

            else:
                error_msg = (
                    f"{platform_id} slides download failed: "
                    f"{downloaded_count}/{total_expected} files. "
                    f"Path: {slides_dir}"
                )
                logger.error(error_msg)
                await repo.update(
                    platform_id,
                    {
                        "video_download_status": DownloadStatus.FAILED,
                        "error_message": error_msg,
                    },
                )
                result.video_download_status = DownloadStatus.FAILED
                result.error = error_msg

                if user_id:
                    await log_user_action(
                        user_id=user_id,
                        action="download",
                        message=f"Image set download failed: {video_title[:30]}...",
                        status="error",
                        aweme_id=platform_id,
                        details={
                            "error": error_msg[:200],
                            "platform": source_platform,
                        },
                    )

        except Exception as e:
            logger.error(f"{platform_id} 下载处理出错: {str(e)}")
            result.error = str(e)
            result.video_download_status = DownloadStatus.FAILED
            return result
        return result

    @staticmethod
    async def download_music_by_platform_id(
        *, platform_id, user_id: str = None
    ) -> DownloadMusicResult:
        """
        Download music file only

        Args:
            platform_id: Video platform ID
            user_id: User ID (for data isolation)

        Returns:
            DownloadMusicResult: Download result info
        """
        # Initialize result object
        result = DownloadMusicResult.model_construct()
        headers = Utils.get_headers()

        try:
            repo = MediaRepository()

            # Get video ID
            logger.info(f"准备下载媒体 {platform_id} 的音乐")

            # Check for music URL/Name
            try:
                music_data = await repo.get_music_data(platform_id)
                # Use dict comprehension to shorten each value
                shortened_data = {
                    k: Utils.shorten_item(v, 30) for k, v in music_data.items()
                }
                logger.debug(f" {platform_id} music data: {shortened_data}")
            except ValueError as e:
                logger.error(f"Failed to retrieve music data: {e}")
                result.error = str(e)
                result.music_download_status = DownloadStatus.FAILED
                return result

            # Get music URL list from dict (guard against None from DB)
            music_urls = music_data.get("music_download_urls") or []
            logger.info(
                f"[Music/Diag] {platform_id}: found {len(music_urls)} music URLs, "
                f"music_name={music_data.get('music_name', 'N/A')}"
            )
            if music_urls:
                for i, u in enumerate(music_urls):
                    logger.debug(f"[Music/Diag] {platform_id}: URL[{i}]={u[:120]}...")

            if not music_urls:
                logger.warning(
                    f"[Music/Diag] {platform_id}: NO music URLs in DB — music download will fail"
                )

            # Create structured path: global/resources/web/{platform}/{media_id}/
            source_platform = music_data.get("source_platform", "douyin")
            media_id = str(music_data["id"])
            full_path, relative_prefix = Utils.create_web_resource_path(
                source_platform, media_id
            )

            # Generate audio file path (unified naming)
            music_full_path = os.path.join(full_path, "audio.mp3")
            music_relative_path = f"{relative_prefix}/audio.mp3"

            # Try to download music
            for idx, url in enumerate(music_urls):
                logger.info(
                    f"[Music/Diag] {platform_id}: trying URL[{idx}] → {url[:80]}..."
                )
                if await DownloaderService.download_file(url, music_full_path, headers):
                    try:
                        await repo.update(
                            platform_id,
                            {
                                "music_download_status": DownloadStatus.COMPLETED.value,
                                "music_download_path": music_relative_path,
                            },
                        )
                    except Exception as e:
                        logger.error(
                            f"Music {platform_id} downloaded but failed to update DB: {e}."
                        )
                        result.error = f"Music {platform_id} downloaded but failed to update DB: {e}."

                    result.music_path = music_relative_path  # Return relative path
                    result.music_download_status = DownloadStatus.COMPLETED

                    logger.success(
                        f"Music for video {platform_id} downloaded successfully and saved to {music_full_path}."
                    )
                    break

            if result.music_download_status != DownloadStatus.COMPLETED:
                logger.error(
                    f"All download URLs for the music of video {platform_id} failed."
                )
                result.music_download_status = DownloadStatus.FAILED
                result.error = (
                    f"All download URLs for the music of video {platform_id} failed."
                )

                # Update database status to "failed"
                await repo.update(
                    platform_id,
                    {
                        "music_download_status": DownloadStatus.FAILED,
                        "error_message": result.error,
                    },
                )

            return result

        except Exception as e:
            logger.error(f"Music download processing error: {str(e)}")
            result.error = str(e)
            result.music_download_status = DownloadStatus.FAILED
            return result

    @staticmethod
    async def download_single_list_item(
        i, url_list, sub_download_full_path, file_name, headers
    ):
        """Download a single list item (video or image), try multiple URLs until success"""
        # Determine file extension
        extension = ".mp4" if "mp4" in str(url_list) else ".jpg"
        file_path = os.path.join(sub_download_full_path, f"{file_name}_{i}{extension}")

        # Ensure directory exists
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        # If file already exists, return success
        if os.path.exists(file_path):
            logger.info(f"文件已存在，跳过下载: {os.path.basename(file_path)}")
            return {"success": True, "path": file_path, "already_exists": True}

        # Try to download
        for j, url in enumerate(url_list):
            try:
                if await DownloaderService.download_file(url, file_path, headers):
                    return {"success": True, "path": file_path}
            except Exception as e:
                logger.warning(f"URL {j} 下载失败: {str(e)}")
                continue

        # All URLs failed
        return {
            "success": False,
            "path": None,
            "message": f"所有URL都失败了，共尝试了{len(url_list)}个URL",
        }

    @staticmethod
    async def download_cover_by_platform_id(
        platform_id: str,
        user_id: str = None,
        user_agent: str = None,
    ) -> DownloadCoverResult:
        """
        Download video cover image

        Args:
            platform_id: Video platform ID
            user_id: User ID (for data isolation)
            user_agent: Optional explicit UA — see download_video_by_platform_id.

        Returns:
            DownloadCoverResult: Download result info
        """
        result = DownloadCoverResult.model_construct()
        headers = Utils.get_headers()
        if user_agent:
            headers["User-Agent"] = user_agent

        try:
            repo = MediaRepository()

            # Get video data
            try:
                video_data = await repo.get_by_platform_id(platform_id)
            except Exception as db_err:
                logger.error(
                    f"DB query failed for cover {platform_id}: {db_err}", exc_info=True
                )
                result.cover_download_status = DownloadStatus.FAILED
                result.error = f"Database query failed: {db_err}"
                return result
            if not video_data:
                logger.error(
                    f"Record not found for cover: platform_id={platform_id}, user_id={user_id}"
                )
                result.cover_download_status = DownloadStatus.FAILED
                result.error = f"Record not found: {platform_id} (user={user_id})"
                return result

            video_title = video_data.get("title", "undefined")
            logger.info(f"准备下载视频 {platform_id} 的封面")

            # Set platform-appropriate Referer for CDN compatibility
            source_platform = video_data.get("source_platform", "douyin")
            platform_referers = {
                "bilibili": "https://www.bilibili.com/",
                "youtube": "https://www.youtube.com/",
                "twitter": "https://x.com/",
                "tiktok": "https://www.tiktok.com/",
                "xiaohongshu": "https://www.xiaohongshu.com/",
            }
            if source_platform in platform_referers:
                headers["Referer"] = platform_referers[source_platform]

            # Get cover URL list
            cover_urls = video_data.get("cover_urls", [])
            if not cover_urls:
                logger.warning(f"视频 {platform_id} 没有封面 URL")
                result.cover_download_status = DownloadStatus.SKIPPED
                result.error = "没有封面 URL"
                return result

            # Create structured path: global/resources/web/{platform}/{media_id}/
            media_id = str(video_data["id"])
            full_path, relative_prefix = Utils.create_web_resource_path(
                source_platform, media_id
            )

            cover_full_path = os.path.join(full_path, "cover.jpg")
            cover_relative_path = f"{relative_prefix}/cover.jpg"

            # Try to download cover (try multiple URLs)
            for url in cover_urls:
                # Total-deadline guard: settings.DOWNLOAD_TIMEOUT is httpx's
                # PER-READ timeout — a slow byte-trickle from a throttled/blocked
                # CDN never trips it but can hang the fetch for tens of minutes,
                # leaving cover_download_status stuck at 'pending' and dragging the
                # whole (already video-complete) workflow to a 'lost' reap. Cover is
                # small + best-effort, so bound the TOTAL time per URL and move on.
                try:
                    cover_ok = await asyncio.wait_for(
                        DownloaderService.download_file(url, cover_full_path, headers),
                        timeout=settings.COVER_DOWNLOAD_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    # Non-streaming GET buffers fully before writing the file, so
                    # cancellation here leaves no partial cover.jpg on disk.
                    logger.warning(
                        f"封面下载超时 ({settings.COVER_DOWNLOAD_TIMEOUT}s),"
                        f"跳过该 URL: {url[:80]}"
                    )
                    cover_ok = False
                if cover_ok:
                    # Storage tiering: upload the cover to S3 and persist the
                    # sb:// path. Falls back to the FS relative path when the
                    # flag is off or user_id is missing (same guard as video).
                    # This path — shared by every platform — previously persisted
                    # only the FS path, so new covers kept re-introducing FS
                    # debt after the S3 migration (2026-07-31).
                    stored_cover_path = await _upload_downloaded_video_to_s3(
                        user_id=user_id,
                        local_path=cover_full_path,
                        relative_path=cover_relative_path,
                        mime="image/jpeg",
                    )
                    # Update database (store S3 / relative path)
                    try:
                        await repo.update(
                            platform_id,
                            {
                                "cover_download_status": DownloadStatus.COMPLETED.value,
                                "cover_download_path": stored_cover_path,
                            },
                        )
                    except Exception as e:
                        logger.error(f"更新封面下载状态失败: {e}")

                    result.cover_download_status = DownloadStatus.COMPLETED
                    result.cover_path = stored_cover_path
                    logger.success(f"封面 {platform_id} 下载成功: {cover_full_path}")

                    # Log success
                    if user_id:
                        await log_user_action(
                            user_id=user_id,
                            action="download",
                            message=f"Cover downloaded: {video_title[:30]}...",
                            status="success",
                            aweme_id=platform_id,
                            details={
                                "media_type": "cover",
                                "platform": source_platform,
                            },
                        )
                    return result

            # All URLs failed
            logger.error(f"视频 {platform_id} 的所有封面 URL 都下载失败")
            result.cover_download_status = DownloadStatus.FAILED
            result.error = "所有封面 URL 下载失败"

            # Clear stale path: if a previous attempt left a path but this
            # one failed, the file on disk may be gone. Path field must
            # only ever represent a real, completed download.
            await repo.update(
                platform_id,
                {
                    "cover_download_status": DownloadStatus.FAILED.value,
                    "cover_download_path": None,
                    "error_message": result.error,
                },
            )

            # Log failure
            if user_id:
                await log_user_action(
                    user_id=user_id,
                    action="download",
                    message=f"Cover download failed: {video_title[:30]}...",
                    status="error",
                    aweme_id=platform_id,
                    details={"platform": source_platform},
                )

            return result

        except Exception as e:
            logger.error(f"下载封面出错 {platform_id}: {str(e)}", exc_info=True)
            result.cover_download_status = DownloadStatus.FAILED
            result.error = str(e)
            # Persist failure status to DB so it doesn't stay NULL
            try:
                repo = MediaRepository()
                await repo.update(
                    platform_id,
                    {
                        "cover_download_status": DownloadStatus.FAILED.value,
                        "cover_download_path": None,
                        "error_message": str(e)[:500],
                    },
                )
            except Exception:
                logger.debug(
                    f"Failed to persist cover failure status for {platform_id}"
                )
            return result

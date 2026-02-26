# app/services/downloader.py

"""
Video download service module

Provides async video and audio download functionality with custom headers,
filename generation, and error handling. Uses httpx and aiofiles for efficient
async downloads.
"""

import asyncio
import os
from typing import Any, Dict

import aiofiles
import httpx
from loguru import logger

# from app.db.session import get_async_transaction_session
from app.core.config import settings
from app.core.enums import DownloadStatus
from app.core.utils import Utils
from app.repositories.user_logs_repository import log_user_action
from app.repositories.media_repository import MediaRepository
from app.schemas.media import (
    DownloadCoverResult,
    DownloadImagesResult,
    DownloadMusicResult,
    DownloadVideoResult,
)


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
        import subprocess

        if not file_path.endswith(".mp4"):
            return True  # Skip non-MP4 files

        temp_path = file_path + ".optimizing.mp4"

        try:
            # Run ffmpeg to optimize the video
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-y",  # Overwrite output
                    "-i",
                    file_path,
                    "-c",
                    "copy",  # Copy streams without re-encoding (fast)
                    "-movflags",
                    "faststart",  # Move moov atom to beginning
                    temp_path,
                ],
                capture_output=True,
                text=True,
                timeout=300,  # 5 minute timeout for large files
            )

            if result.returncode == 0 and os.path.exists(temp_path):
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
                logger.warning(
                    f"视频优化失败: {result.stderr[:200] if result.stderr else 'Unknown error'}"
                )
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                return False

        except subprocess.TimeoutExpired:
            logger.warning(f"视频优化超时: {os.path.basename(file_path)}")
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
        import subprocess

        try:
            # Use ffmpeg to verify the file can be decoded
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-i",
                    file_path,
                    "-f",
                    "null",
                    "-",
                ],
                capture_output=True,
                text=True,
                timeout=60,  # 1 minute timeout
            )

            # Check for critical errors
            stderr = result.stderr.lower()
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
            if result.returncode != 0 and stderr:
                logger.error(f"视频验证失败: {stderr[:200]}")
                return False

            logger.debug(f"视频完整性验证通过: {os.path.basename(file_path)}")
            return True

        except subprocess.TimeoutExpired:
            logger.warning(f"视频验证超时: {os.path.basename(file_path)}")
            return True  # Don't fail on timeout, assume OK
        except FileNotFoundError:
            logger.warning("ffmpeg not found, skipping video integrity check")
            return True  # Skip check if ffmpeg not installed
        except Exception as e:
            logger.warning(f"视频验证出错: {e}")
            return True  # Don't fail on unknown errors

    @staticmethod
    async def download_file(
        url: str, file_path: str, headers: Dict[str, Any] = None, progress_tracker=None
    ) -> bool:
        """
        Download a single file with optional progress tracking

        Args:
            url: Download URL
            file_path: Save path
            headers: Request headers
            progress_tracker: Optional progress tracker (DownloadProgressTracker instance)

        Returns:
            bool: Whether download was successful
        """
        if not headers:
            headers = Utils.get_headers()

        try:
            os.makedirs(os.path.dirname(file_path), exist_ok=True)

            async with httpx.AsyncClient(http2=True) as client:
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
                        if expected_size == 0:
                            expected_size = total
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
                        if file_path.endswith(".mp4") and actual_size < 200 * 1024:
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
                        logger.info(f"[Download/File] HTTP 200, content_length={content_len}, saving to {os.path.basename(file_path)}")
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
                        if file_path.endswith(".mp4") and actual_size < 200 * 1024:
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
            logger.error(f"[Download/File] Exception downloading {url[:100]}...: {type(e).__name__}: {str(e)}")
            if progress_tracker:
                progress_tracker.failed(str(e))
            return False

    @staticmethod
    async def download_video_by_platform_id(
        platform_id, user_id: str = None, progress_tracker=None
    ) -> DownloadVideoResult:
        """
        Download video and optional music files

        Args:
            platform_id: Video platform ID
            user_id: User ID (for data isolation)
            progress_tracker: Optional progress tracker

        Returns:
            DownloadResult: Download result info
        """
        # Initialize result object, pydantic2.0 method
        result = DownloadVideoResult.model_construct()
        headers = Utils.get_headers()

        try:

            # Establish database connection
            repo = MediaRepository()

            # Get video data
            try:
                video_data = await repo.get_by_platform_id(platform_id)
            except Exception as db_err:
                logger.error(f"DB query failed for {platform_id}: {db_err}", exc_info=True)
                result.video_download_status = DownloadStatus.FAILED
                result.error = f"Database query failed: {db_err}"
                return result
            if not video_data:
                logger.error(f"Record not found in parsed_media: platform_id={platform_id}, user_id={user_id}")
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
            play_addr = video_data.get("video", {}).get("play_addr", {}) if isinstance(video_data.get("video"), dict) else {}
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
                    logger.info(f"[Download/Video] Appended stable play URL fallback for {platform_id}")

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
                    async with httpx.AsyncClient(http2=True) as _diag:
                        _r = await _diag.head(url, headers=headers, follow_redirects=True, timeout=10.0)
                        logger.info(
                            f"[Download/Diag] URL {i+1}/{len(video_urls)} HEAD: "
                            f"status={_r.status_code}, final_url={str(_r.url)[:80]}"
                        )
                except Exception as e:
                    logger.warning(f"[Download/Diag] URL {i+1} HEAD failed: {type(e).__name__}: {e}")

            # download video while one of the urls is successful
            for idx, url in enumerate(video_urls):
                logger.info(f"[Download/Video] Trying URL {idx+1}/{len(video_urls)} for {platform_id}: {url[:100]}")
                success = await DownloaderService.download_file(
                    url, video_full_path, headers, progress_tracker
                )
                if not success:
                    download_errors.append(f"URL{idx+1}: download_file returned False")
                    logger.warning(f"[Download/Video] URL {idx+1} failed for {platform_id}")
                    continue
                if success:
                    # Optimize video for streaming (move moov atom to beginning)
                    await DownloaderService.optimize_video_for_streaming(
                        video_full_path
                    )

                    try:
                        # Calculate file size (after optimization)
                        file_size = (
                            os.path.getsize(video_full_path)
                            if os.path.exists(video_full_path)
                            else 0
                        )

                        # Store relative path and file size to database
                        await repo.mark_media_as_downloaded(
                            platform_id=platform_id,
                            download_path=video_relative_path,  # Use relative path
                            duration=download_duration,
                            storage_size=file_size,
                        )
                    except Exception as e:
                        logger.error(
                            f"Marked video {platform_id} as downloaded successfully, "
                            f"but failed to update the database: {e}."
                        )
                        result.error = (
                            f"Marked video {platform_id} as downloaded successfully, "
                            f"but failed to update the database: {e}."
                        )

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

            if result.video_download_status != DownloadStatus.COMPLETED:
                error_detail = "; ".join(download_errors) if download_errors else "unknown"
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
    async def download_images_by_platform_id(platform_id, user_id: str = None):
        """
        Download image files only

        Args:
            platform_id: Video platform ID
            user_id: User ID (for data isolation)

        Returns:
            DownloadImagesResult: Download result info
        """
        # todo: get and accumulate error_message

        result = DownloadImagesResult.model_construct()
        headers = Utils.get_headers()

        try:
            # Establish database connection
            repo = MediaRepository()

            # Get video data
            try:
                video_data = await repo.get_by_platform_id(platform_id)
            except Exception as db_err:
                logger.error(f"DB query failed for {platform_id}: {db_err}", exc_info=True)
                result.video_download_status = DownloadStatus.FAILED
                result.error = f"Database query failed: {db_err}"
                return result
            if not video_data:
                logger.error(f"Record not found in parsed_media: platform_id={platform_id}, user_id={user_id}")
                result.video_download_status = DownloadStatus.FAILED
                result.error = f"Record not found: {platform_id} (user={user_id})"
                return result

            logger.info(f"准备下载 {platform_id} 的图片集")

            # Create structured path: global/resources/web/{platform}/{media_id}/
            source_platform = video_data.get("source_platform", "douyin")
            media_id = str(video_data["id"])
            sub_download_full_path, sub_download_relative_path = (
                Utils.create_web_resource_path(source_platform, media_id)
            )
            video_title = video_data.get("title", "undefined")
            file_name = platform_id  # Use platform_id as base name for image files
            logger.debug(f"图片下载路径: {sub_download_full_path}")

            video_urls = video_data.get("video_download_urls")
            image_urls = video_data.get("image_download_urls")

            video_downloaded_count = 0
            image_downloaded_count = 0

            # Download videos

            if Utils.is_nested_list(video_urls):
                logger.info(f"准备下载  {len(video_urls)} 个视频")
                video_tasks = []
                async with asyncio.TaskGroup() as tg:
                    for i, url_list in enumerate(video_urls):
                        task = tg.create_task(
                            DownloaderService.download_single_list_item(
                                i, url_list, sub_download_full_path, file_name, headers
                            )
                        )
                        logger.debug(f"添加视频第{i+1}下载任务: {task}")
                        video_tasks.append(task)

                for task in video_tasks:
                    try:
                        result_data = task.result()
                        if result_data and result_data.get("success", False):
                            video_downloaded_count += 1
                    except Exception as e:
                        logger.error(f"获取视频下载任务结果失败: {e}")

                logger.info(
                    f"{file_name} 视频下载完成，共下载了 {video_downloaded_count}/{len(video_urls)} 个视频文件。"
                )
            else:
                logger.info("No videos require downloading.")

            # todo check errors
            if Utils.is_nested_list(image_urls):
                logger.debug(f"准备下载 {len(image_urls)} 张图片")
                image_tasks = []
                async with asyncio.TaskGroup() as tg:
                    for i, url_list in enumerate(image_urls):
                        task = tg.create_task(
                            DownloaderService.download_single_list_item(
                                i, url_list, sub_download_full_path, file_name, headers
                            )
                        )
                        logger.debug(f"添加图片第{i+1}下载任务: {task}")
                        image_tasks.append(task)

                # Process image download results
                for task in image_tasks:
                    try:
                        result_data = task.result()
                        if result_data and result_data.get("success", False):
                            image_downloaded_count += 1
                            logger.debug(
                                f"成功下载图片，当前计数: {image_downloaded_count}"
                            )

                    except Exception as e:
                        logger.error(f"获取图片下载任务结果失败: {e}")

                logger.info(
                    f"{file_name} 图片下载完成，共下载了 {image_downloaded_count}/{len(image_urls)} 个图片文件。"
                )

            # Calculate total download count
            total_expected = 0
            if Utils.is_nested_list(video_urls):
                total_expected += len(video_urls)
            if Utils.is_nested_list(image_urls):
                total_expected += len(image_urls)

            total_downloaded = video_downloaded_count + image_downloaded_count

            if total_downloaded == total_expected and total_expected > 0:
                logger.success(
                    f"{file_name} 下载完成，共下载了 {video_downloaded_count}个视频文件和 {image_downloaded_count}个图片文件。"
                )
                await repo.update(
                    platform_id,
                    {
                        "video_download_status": DownloadStatus.COMPLETED,
                        "download_path": sub_download_relative_path,  # Use relative path
                    },
                )
                result.video_download_status = DownloadStatus.COMPLETED

                # Log success
                if user_id:
                    await log_user_action(
                        user_id=user_id,
                        action="download",
                        message=f"Image set downloaded: {video_title[:30]}... ({total_downloaded} files)",
                        status="success",
                        aweme_id=platform_id,
                        details={
                            "media_type": "images",
                            "file_count": total_downloaded,
                            "platform": video_data.get("source_platform", "douyin"),
                        },
                    )

            else:
                video_total = len(video_urls) if Utils.is_nested_list(video_urls) else 0
                image_total = len(image_urls) if Utils.is_nested_list(image_urls) else 0
                error_msg = (
                    f"{file_name} 下载失败，共下载了 "
                    f"{video_downloaded_count}/{video_total} 个视频文件和 "
                    f"{image_downloaded_count}/{image_total} 个图片文件。"
                    f"Download Path: {sub_download_full_path}"
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

                # Log failure
                if user_id:
                    await log_user_action(
                        user_id=user_id,
                        action="download",
                        message=f"Image set download failed: {video_title[:30]}...",
                        status="error",
                        aweme_id=platform_id,
                        details={
                            "error": error_msg[:200],
                            "platform": video_data.get("source_platform", "douyin"),
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
                logger.warning(f"[Music/Diag] {platform_id}: NO music URLs in DB — music download will fail")

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
                logger.info(f"[Music/Diag] {platform_id}: trying URL[{idx}] → {url[:80]}...")
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
                        result.error = (
                            f"Music {platform_id} downloaded but failed to update DB: {e}."
                        )

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
        platform_id: str, user_id: str = None
    ) -> DownloadCoverResult:
        """
        Download video cover image

        Args:
            platform_id: Video platform ID
            user_id: User ID (for data isolation)

        Returns:
            DownloadCoverResult: Download result info
        """
        result = DownloadCoverResult.model_construct()
        headers = Utils.get_headers()

        try:
            repo = MediaRepository()

            # Get video data
            try:
                video_data = await repo.get_by_platform_id(platform_id)
            except Exception as db_err:
                logger.error(f"DB query failed for cover {platform_id}: {db_err}", exc_info=True)
                result.cover_download_status = DownloadStatus.FAILED
                result.error = f"Database query failed: {db_err}"
                return result
            if not video_data:
                logger.error(f"Record not found for cover: platform_id={platform_id}, user_id={user_id}")
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
                if await DownloaderService.download_file(url, cover_full_path, headers):
                    # Update database (store relative path)
                    try:
                        await repo.update(
                            platform_id,
                            {
                                "cover_download_status": DownloadStatus.COMPLETED.value,
                                "cover_download_path": cover_relative_path,  # Use relative path
                            },
                        )
                    except Exception as e:
                        logger.error(f"更新封面下载状态失败: {e}")

                    result.cover_download_status = DownloadStatus.COMPLETED
                    result.cover_path = cover_relative_path  # Return relative path
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

            await repo.update(
                platform_id,
                {
                    "cover_download_status": DownloadStatus.FAILED.value,
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
                    {"cover_download_status": DownloadStatus.FAILED.value,
                     "error_message": str(e)[:500]},
                )
            except Exception:
                logger.debug(f"Failed to persist cover failure status for {platform_id}")
            return result

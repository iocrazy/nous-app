# backend/app/services/video_service.py

"""
Video service module

Integrates video data fetching, parsing, storage (Supabase) and download functionality.
"""

import asyncio
from typing import Any, Dict

from loguru import logger

from app.core.enums import DownloadStatus
from app.repositories.video_repository import VideoRepository
from app.schemas.video import VideoCreate
from app.services.downloader import DownloaderService


class VideoService:
    """Video service, integrates data fetching, parsing, storage and download functionality"""

    @staticmethod
    async def process_video(platform_id: str, parsed_data: dict) -> Dict[str, Any]:
        """
        Process video complete workflow: fetch, parse, store and download

        Args:
            platform_id: Video ID
            parsed_data: Parsed video data

        Returns:
            Dict[str, Any]: Processing result
        """
        try:
            # Get user_id for subsequent operations
            user_id = parsed_data.get("user_id")

            # Store to Supabase
            logger.info("开始存储到 Supabase...")
            db_result = await VideoService._store_to_supabase(parsed_data)

            # Process download tasks
            logger.info("开始处理下载任务...")

            # Initialize message variable
            message = db_result.get("message", "")

            if db_result.get("success"):
                to_download_video = db_result.get("download_video", False)
                to_download_music = db_result.get("download_music", False)
                to_download_cover = db_result.get("download_cover", False)
                media_type = db_result.get("media_type", 0)
                title = db_result.get("title", "undefined")

                logger.info(
                    f"Video:{platform_id}_{title} 下载请求状态: "
                    f"video_{to_download_video}, music_{to_download_music}, cover_{to_download_cover}"
                )

                if to_download_video or to_download_music or to_download_cover:
                    logger.info(
                        f"开始执行下载任务 Video:{platform_id}_{title}, media_type: {media_type}"
                    )
                    # Directly await download tasks
                    await VideoService._execute_downloads(
                        platform_id,
                        to_download_video,
                        to_download_music,
                        to_download_cover,
                        media_type,
                        title,
                        user_id,
                    )

                download_msgs = []
                if to_download_video:
                    download_msgs.append("视频下载完成")
                if to_download_music:
                    download_msgs.append("音频下载完成")
                if to_download_cover:
                    download_msgs.append("封面下载完成")

                if download_msgs:
                    message += f"; {'; '.join(download_msgs)}"

            logger.success(f"{message}")

            return {
                "success": True,
                "message": message,
                "platform_id": platform_id,
            }

        except Exception as e:
            logger.error(f"处理视频失败: {str(e)}")
            return {"success": False, "message": f"处理失败: {str(e)}"}

    @staticmethod
    async def _store_to_supabase(parsed_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Store video data to Supabase

        Args:
            parsed_data: Parsed video data

        Returns:
            Dict[str, Any]: Storage result with download status info
        """
        platform_id = parsed_data.get("platform_id")
        media_type = parsed_data.get("media_type")
        title = parsed_data.get("title", "undefined")
        user_id = parsed_data.get("user_id")

        need_download_video = parsed_data.get("need_download_video", False)
        need_download_music = parsed_data.get("need_download_music", False)
        need_download_cover = parsed_data.get("need_download_cover", True)

        # Data validation
        try:
            video_data = VideoCreate(**parsed_data)
            logger.success(
                f"数据验证通过: platform_id={platform_id}, user_id={user_id}"
            )
        except Exception as e:
            logger.error(f"数据验证失败: {str(e)}")
            return {"success": False, "message": f"存储失败: {str(e)}"}

        try:
            repo = VideoRepository()

            # Check current user's video status (with user_id for data isolation)
            existing_video = await repo.get_by_platform_id(platform_id, user_id=user_id)
            data_exists = existing_video is not None

            # If current user doesn't have this video but it exists (belongs to another user), create new record
            if not data_exists:
                global_exists = await repo.check_video_existence(platform_id)
                if global_exists:
                    logger.info(
                        f"视频 {platform_id} 已存在但属于其他用户，为当前用户创建新记录"
                    )
                    data_exists = True

            video_downloaded = await repo.check_video_downloaded(platform_id)
            music_downloaded = await repo.check_music_downloaded(platform_id)

            logger.debug(
                f"媒体状态: {platform_id}:{title} media_type={media_type}, "
                f"exists={data_exists}, video_dl={video_downloaded}, "
                f"music_dl={music_downloaded}, need_video={need_download_video}, "
                f"need_music={need_download_music}, user_id={user_id}"
            )

            data_dict = video_data.model_dump()

            if data_exists:
                # Update existing record
                update_data = {
                    k: v
                    for k, v in data_dict.items()
                    if k
                    not in [
                        "video_download_status",
                        "download_path",
                        "music_download_status",
                        "download_duration",
                    ]
                }

                if video_downloaded and music_downloaded:
                    await repo.update(platform_id, update_data)
                    message = f"媒体 {platform_id}_{title} 已下载，仅更新数据"
                elif video_downloaded and not music_downloaded:
                    if need_download_music:
                        update_data["music_download_status"] = (
                            DownloadStatus.PENDING.value
                        )
                        message = f"媒体 {platform_id}_{title} 视频已下载，音频待下载"
                    else:
                        update_data["music_download_status"] = (
                            DownloadStatus.SKIPPED.value
                        )
                        message = f"媒体 {platform_id}_{title} 视频已下载，跳过音频"
                    await repo.update(platform_id, update_data)
                elif not video_downloaded and music_downloaded:
                    if need_download_video:
                        update_data["video_download_status"] = (
                            DownloadStatus.PENDING.value
                        )
                        message = f"媒体 {platform_id}_{title} 音频已下载，视频待下载"
                    else:
                        update_data["video_download_status"] = (
                            DownloadStatus.SKIPPED.value
                        )
                        message = f"媒体 {platform_id}_{title} 音频已下载，跳过视频"
                    await repo.update(platform_id, update_data)
                else:
                    # Neither downloaded
                    if need_download_video and need_download_music:
                        update_data["video_download_status"] = (
                            DownloadStatus.PENDING.value
                        )
                        update_data["music_download_status"] = (
                            DownloadStatus.PENDING.value
                        )
                        message = (
                            f"媒体 {platform_id}_{title} 更新数据，视频和音频待下载"
                        )
                    elif need_download_video:
                        update_data["video_download_status"] = (
                            DownloadStatus.PENDING.value
                        )
                        update_data["music_download_status"] = (
                            DownloadStatus.SKIPPED.value
                        )
                        message = f"媒体 {platform_id}_{title} 更新数据，视频待下载"
                    elif need_download_music:
                        update_data["music_download_status"] = (
                            DownloadStatus.PENDING.value
                        )
                        update_data["video_download_status"] = (
                            DownloadStatus.SKIPPED.value
                        )
                        message = f"媒体 {platform_id}_{title} 更新数据，音频待下载"
                    else:
                        update_data["video_download_status"] = (
                            DownloadStatus.SKIPPED.value
                        )
                        update_data["music_download_status"] = (
                            DownloadStatus.SKIPPED.value
                        )
                        message = f"媒体 {platform_id}_{title} 仅更新数据"
                    await repo.update(platform_id, update_data)
            else:
                # Create new record
                if need_download_video and need_download_music:
                    data_dict["video_download_status"] = DownloadStatus.PENDING.value
                    data_dict["music_download_status"] = DownloadStatus.PENDING.value
                    message = f"媒体 {platform_id}_{title} 已创建，视频和音频待下载"
                elif need_download_video:
                    data_dict["video_download_status"] = DownloadStatus.PENDING.value
                    data_dict["music_download_status"] = DownloadStatus.SKIPPED.value
                    message = f"媒体 {platform_id}_{title} 已创建，视频待下载"
                elif need_download_music:
                    data_dict["music_download_status"] = DownloadStatus.PENDING.value
                    data_dict["video_download_status"] = DownloadStatus.SKIPPED.value
                    message = f"媒体 {platform_id}_{title} 已创建，音频待下载"
                else:
                    data_dict["video_download_status"] = DownloadStatus.SKIPPED.value
                    data_dict["music_download_status"] = DownloadStatus.SKIPPED.value
                    message = f"媒体 {platform_id}_{title} 已创建，无下载请求"

                await repo.create(data_dict)
                logger.info(
                    f"创建新视频记录: platform_id={platform_id}, user_id={user_id}"
                )

            # Re-check download status
            video_downloaded = await repo.check_video_downloaded(platform_id)
            music_downloaded = await repo.check_music_downloaded(platform_id)

            logger.debug(
                f"下载状态: video_dl={video_downloaded}, music_dl={music_downloaded}"
            )
            logger.debug(f"{message}")

            # Check cover download status
            cover_downloaded = await repo.check_cover_downloaded(platform_id)

            return {
                "success": True,
                "message": message,
                "download_video": need_download_video and not video_downloaded,
                "download_music": need_download_music and not music_downloaded,
                "download_cover": need_download_cover and not cover_downloaded,
                "platform_id": platform_id,
                "media_type": media_type,
                "title": title,
            }

        except Exception as e:
            logger.error(f"存储视频数据失败: {str(e)}")
            return {"success": False, "message": f"存储失败: {str(e)}"}

    @staticmethod
    async def _execute_downloads(
        platform_id: str,
        download_video: bool,
        download_music: bool,
        download_cover: bool,
        media_type: int,
        title: str,
        user_id: str = None,
    ):
        """
        Execute download tasks

        Args:
            platform_id: Video ID
            download_video: Whether to download video
            download_music: Whether to download audio
            download_cover: Whether to download cover
            media_type: Media type
            title: Video title
            user_id: User ID (for data isolation)
        """
        try:
            async with asyncio.TaskGroup() as tg:
                if int(media_type) in (0, 4, 61):  # Video types
                    logger.info(
                        f"开始下载 Video: {platform_id}_{title}, media_type: {media_type}"
                    )
                    if download_video:
                        logger.info("创建视频下载任务")
                        tg.create_task(
                            DownloaderService.download_video_by_platform_id(
                                platform_id, user_id=user_id
                            )
                        )
                    if download_music:
                        logger.info("创建音频下载任务")
                        tg.create_task(
                            DownloaderService.download_music_by_platform_id(
                                platform_id=platform_id, user_id=user_id
                            )
                        )
                    if download_cover:
                        logger.info("创建封面下载任务")
                        tg.create_task(
                            DownloaderService.download_cover_by_platform_id(
                                platform_id, user_id=user_id
                            )
                        )

                elif int(media_type) in (2, 68):  # Image collection/image-text types
                    logger.info(
                        f"开始下载 Video: {platform_id}_{title}, media_type: {media_type}"
                    )
                    if download_video:
                        logger.info("创建图片下载任务")
                        tg.create_task(
                            DownloaderService.download_images_by_platform_id(
                                platform_id, user_id=user_id
                            )
                        )
                    if download_music:
                        logger.info("创建音频下载任务")
                        tg.create_task(
                            DownloaderService.download_music_by_platform_id(
                                platform_id=platform_id, user_id=user_id
                            )
                        )
                    if download_cover:
                        logger.info("创建封面下载任务")
                        tg.create_task(
                            DownloaderService.download_cover_by_platform_id(
                                platform_id, user_id=user_id
                            )
                        )

                else:
                    logger.error(f"暂不支持 media_type: {media_type} 类型的下载")
                    raise Exception(f"暂不支持 media_type: {media_type} 类型的下载")

            logger.success(f"Video:{platform_id}_{title} 所有下载任务已完成")
        except* Exception as exc_group:
            for exc in exc_group.exceptions:
                logger.error(f"下载任务异常: {exc}")

    @staticmethod
    async def save_metadata_only(platform_id: str, parsed_data: dict) -> Dict[str, Any]:
        """
        Save video metadata only to database (no downloads)

        Used for Progressive Download workflow: save metadata first, downloads handled by Celery tasks

        Args:
            platform_id: Video ID
            parsed_data: Parsed video data

        Returns:
            Dict[str, Any]: Save result
        """
        try:
            title = parsed_data.get("title", "undefined")
            user_id = parsed_data.get("user_id")

            need_download_video = parsed_data.get("need_download_video", False)
            need_download_music = parsed_data.get("need_download_music", False)

            # Data validation
            try:
                video_data = VideoCreate(**parsed_data)
                logger.success(
                    f"数据验证通过: platform_id={platform_id}, user_id={user_id}"
                )
            except Exception as e:
                logger.error(f"数据验证失败: {str(e)}")
                return {"success": False, "message": f"存储失败: {str(e)}"}

            repo = VideoRepository()
            data_dict = video_data.model_dump()

            # Check if already exists
            existing_video = await repo.get_by_platform_id(platform_id, user_id=user_id)

            # Set download status to PENDING (waiting for Celery task to download)
            if need_download_video:
                data_dict["video_download_status"] = DownloadStatus.PENDING.value
            else:
                data_dict["video_download_status"] = DownloadStatus.SKIPPED.value

            if need_download_music:
                data_dict["music_download_status"] = DownloadStatus.PENDING.value
            else:
                data_dict["music_download_status"] = DownloadStatus.SKIPPED.value

            video_id = None
            if existing_video:
                # Update existing record
                video_id = existing_video.get("id")
                update_data = {
                    k: v
                    for k, v in data_dict.items()
                    if k not in ["download_path", "download_duration"]
                }
                await repo.update(platform_id, update_data)
                message = f"媒体 {platform_id}_{title} 元数据已更新"
            else:
                # Create new record
                result = await repo.create(data_dict)
                video_id = result.get("id") if result else None
                message = f"媒体 {platform_id}_{title} 元数据已创建"

            logger.info(message)

            return {
                "success": True,
                "message": message,
                "platform_id": platform_id,
                "id": video_id,  # Database ID for tag operations
            }

        except Exception as e:
            logger.error(f"保存元数据失败: {str(e)}")
            return {"success": False, "message": f"保存失败: {str(e)}"}

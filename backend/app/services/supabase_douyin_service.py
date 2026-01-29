# backend/app/services/supabase_douyin_service.py

"""
Supabase 抖音服务模块

整合抖音数据的获取、解析、存储（Supabase）和下载功能。
"""

import asyncio
from typing import Dict, Any
from loguru import logger

from app.core.enums import DownloadStatus
from app.repositories.supabase_douyin_repository import SupabaseDouyinRepository
from app.schemas.douyin import DouyinCreate
from app.services.downloader import DownloaderService


class SupabaseDouyinService:
    """Supabase 抖音服务，整合数据获取、解析、存储和下载功能"""

    @staticmethod
    async def process_video(aweme_id: str, parsed_data: dict) -> Dict[str, Any]:
        """
        处理抖音视频的完整流程：获取、解析、存储和下载

        Args:
            aweme_id: 视频ID
            parsed_data: 抖音视频原始数据

        Returns:
            Dict[str, Any]: 处理结果
        """
        try:
            # 获取 user_id 用于后续操作
            user_id = parsed_data.get("user_id")

            # 存储到 Supabase
            logger.info("开始存储到 Supabase...")
            db_result = await SupabaseDouyinService._store_to_supabase(parsed_data)

            # 处理下载任务
            logger.info("开始处理下载任务...")

            # 初始化 message 变量
            message = db_result.get("message", "")

            if db_result.get("success"):
                to_download_video = db_result.get("download_video", False)
                to_download_music = db_result.get("download_music", False)
                to_download_cover = db_result.get("download_cover", False)
                aweme_type = db_result.get("aweme_type", 0)
                video_title = db_result.get("video_title", "undefined")

                logger.info(
                    f"Douyin:{aweme_id}_{video_title} 下载请求状态: "
                    f"video_{to_download_video}, music_{to_download_music}, cover_{to_download_cover}"
                )

                if to_download_video or to_download_music or to_download_cover:
                    logger.info(
                        f"开始执行下载任务 Douyin:{aweme_id}_{video_title}, aweme_type: {aweme_type}"
                    )
                    # 直接 await 下载任务，确保在 BackgroundTask 中正确执行
                    await SupabaseDouyinService._execute_downloads(
                        aweme_id,
                        to_download_video,
                        to_download_music,
                        to_download_cover,
                        aweme_type,
                        video_title,
                        user_id
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
                "aweme_id": aweme_id,
            }

        except Exception as e:
            logger.error(f"处理抖音视频失败: {str(e)}")
            return {"success": False, "message": f"处理失败: {str(e)}"}

    @staticmethod
    async def _store_to_supabase(parsed_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        将视频数据存储到 Supabase

        Args:
            parsed_data: 解析后的视频数据

        Returns:
            Dict[str, Any]: 存储结果，包含下载状态信息
        """
        aweme_id = parsed_data.get("aweme_id")
        aweme_type = parsed_data.get('aweme_type')
        video_title = parsed_data.get("video_title", "undefined")
        user_id = parsed_data.get("user_id")  # 获取 user_id 用于数据隔离

        need_download_video = parsed_data.get("need_download_video", False)
        need_download_music = parsed_data.get("need_download_music", False)
        need_download_cover = parsed_data.get("need_download_cover", True)

        # 数据验证
        try:
            douyin_data = DouyinCreate(**parsed_data)
            logger.success(f"数据验证通过: aweme_id={aweme_id}, user_id={user_id}")
        except Exception as e:
            logger.error(f"数据验证失败: {str(e)}")
            return {"success": False, "message": f"存储失败: {str(e)}"}

        try:
            repo = SupabaseDouyinRepository()

            # 检查当前用户的视频状态（使用 user_id 进行数据隔离）
            existing_video = await repo.get_by_aweme_id(aweme_id, user_id=user_id)
            data_exists = existing_video is not None

            # 如果当前用户没有这个视频，但视频存在（属于其他用户），则为当前用户创建新记录
            if not data_exists:
                global_exists = await repo.check_video_existence(aweme_id)
                if global_exists:
                    logger.info(f"视频 {aweme_id} 已存在但属于其他用户，为当前用户创建新记录")
                    # 注意：数据库 aweme_id 有 UNIQUE 约束，需要更新而不是创建
                    # 这里我们选择更新现有记录的 user_id（获取视频所有权）
                    data_exists = True

            video_downloaded = await repo.check_video_downloaded(aweme_id)
            music_downloaded = await repo.check_music_downloaded(aweme_id)

            logger.debug(
                f"媒体状态: {aweme_id}:{video_title} aweme_type={aweme_type}, "
                f"exists={data_exists}, video_dl={video_downloaded}, "
                f"music_dl={music_downloaded}, need_video={need_download_video}, "
                f"need_music={need_download_music}, user_id={user_id}"
            )

            data_dict = douyin_data.model_dump()

            if data_exists:
                # 更新现有记录（不传递 user_id 以允许获取视频所有权）
                update_data = {
                    k: v for k, v in data_dict.items()
                    if k not in ["video_download_status", "download_path",
                                 "music_download_status", "download_duration"]
                }

                if video_downloaded and music_downloaded:
                    await repo.update(aweme_id, update_data)
                    message = f"媒体 {aweme_id}_{video_title} 已下载，仅更新数据"
                elif video_downloaded and not music_downloaded:
                    if need_download_music:
                        update_data["music_download_status"] = DownloadStatus.PENDING.value
                        message = f"媒体 {aweme_id}_{video_title} 视频已下载，音频待下载"
                    else:
                        update_data["music_download_status"] = DownloadStatus.SKIPPED.value
                        message = f"媒体 {aweme_id}_{video_title} 视频已下载，跳过音频"
                    await repo.update(aweme_id, update_data)
                elif not video_downloaded and music_downloaded:
                    if need_download_video:
                        update_data["video_download_status"] = DownloadStatus.PENDING.value
                        message = f"媒体 {aweme_id}_{video_title} 音频已下载，视频待下载"
                    else:
                        update_data["video_download_status"] = DownloadStatus.SKIPPED.value
                        message = f"媒体 {aweme_id}_{video_title} 音频已下载，跳过视频"
                    await repo.update(aweme_id, update_data)
                else:
                    # 都未下载
                    if need_download_video and need_download_music:
                        update_data["video_download_status"] = DownloadStatus.PENDING.value
                        update_data["music_download_status"] = DownloadStatus.PENDING.value
                        message = f"媒体 {aweme_id}_{video_title} 更新数据，视频和音频待下载"
                    elif need_download_video:
                        update_data["video_download_status"] = DownloadStatus.PENDING.value
                        update_data["music_download_status"] = DownloadStatus.SKIPPED.value
                        message = f"媒体 {aweme_id}_{video_title} 更新数据，视频待下载"
                    elif need_download_music:
                        update_data["music_download_status"] = DownloadStatus.PENDING.value
                        update_data["video_download_status"] = DownloadStatus.SKIPPED.value
                        message = f"媒体 {aweme_id}_{video_title} 更新数据，音频待下载"
                    else:
                        update_data["video_download_status"] = DownloadStatus.SKIPPED.value
                        update_data["music_download_status"] = DownloadStatus.SKIPPED.value
                        message = f"媒体 {aweme_id}_{video_title} 仅更新数据"
                    await repo.update(aweme_id, update_data)
            else:
                # 创建新记录
                if need_download_video and need_download_music:
                    data_dict["video_download_status"] = DownloadStatus.PENDING.value
                    data_dict["music_download_status"] = DownloadStatus.PENDING.value
                    message = f"媒体 {aweme_id}_{video_title} 已创建，视频和音频待下载"
                elif need_download_video:
                    data_dict["video_download_status"] = DownloadStatus.PENDING.value
                    data_dict["music_download_status"] = DownloadStatus.SKIPPED.value
                    message = f"媒体 {aweme_id}_{video_title} 已创建，视频待下载"
                elif need_download_music:
                    data_dict["music_download_status"] = DownloadStatus.PENDING.value
                    data_dict["video_download_status"] = DownloadStatus.SKIPPED.value
                    message = f"媒体 {aweme_id}_{video_title} 已创建，音频待下载"
                else:
                    data_dict["video_download_status"] = DownloadStatus.SKIPPED.value
                    data_dict["music_download_status"] = DownloadStatus.SKIPPED.value
                    message = f"媒体 {aweme_id}_{video_title} 已创建，无下载请求"

                await repo.create(data_dict)
                logger.info(f"创建新视频记录: aweme_id={aweme_id}, user_id={user_id}")

            # 重新检查下载状态
            video_downloaded = await repo.check_video_downloaded(aweme_id)
            music_downloaded = await repo.check_music_downloaded(aweme_id)

            logger.debug(f"下载状态: video_dl={video_downloaded}, music_dl={music_downloaded}")
            logger.debug(f"{message}")

            # 检查封面是否已下载
            cover_downloaded = await repo.check_cover_downloaded(aweme_id)

            return {
                "success": True,
                "message": message,
                "download_video": need_download_video and not video_downloaded,
                "download_music": need_download_music and not music_downloaded,
                "download_cover": need_download_cover and not cover_downloaded,
                "aweme_id": aweme_id,
                "aweme_type": aweme_type,
                "video_title": video_title
            }

        except Exception as e:
            logger.error(f"存储视频数据失败: {str(e)}")
            return {"success": False, "message": f"存储失败: {str(e)}"}

    @staticmethod
    async def _execute_downloads(
        aweme_id: str,
        download_video: bool,
        download_music: bool,
        download_cover: bool,
        aweme_type: int,
        video_title: str,
        user_id: str = None
    ):
        """
        执行下载任务

        Args:
            aweme_id: 视频ID
            download_video: 是否下载视频
            download_music: 是否下载音频
            download_cover: 是否下载封面
            aweme_type: 媒体类型
            video_title: 视频标题
            user_id: 用户ID（用于数据隔离）
        """
        try:
            async with asyncio.TaskGroup() as tg:
                if int(aweme_type) in (0, 4, 61):  # 视频类型
                    logger.info(f"开始下载 Douyin: {aweme_id}_{video_title}, aweme_type: {aweme_type}")
                    if download_video:
                        logger.info("创建视频下载任务")
                        tg.create_task(DownloaderService.download_video_by_aweme_id(aweme_id, user_id=user_id))
                    if download_music:
                        logger.info("创建音频下载任务")
                        tg.create_task(DownloaderService.download_music_by_aweme_id(aweme_id=aweme_id, user_id=user_id))
                    if download_cover:
                        logger.info("创建封面下载任务")
                        tg.create_task(DownloaderService.download_cover_by_aweme_id(aweme_id, user_id=user_id))

                elif int(aweme_type) in (2, 68):  # 图集/图文类型
                    logger.info(f"开始下载 Douyin: {aweme_id}_{video_title}, aweme_type: {aweme_type}")
                    if download_video:
                        logger.info("创建图片下载任务")
                        tg.create_task(DownloaderService.download_images_by_aweme_id(aweme_id, user_id=user_id))
                    if download_music:
                        logger.info("创建音频下载任务")
                        tg.create_task(DownloaderService.download_music_by_aweme_id(aweme_id=aweme_id, user_id=user_id))
                    if download_cover:
                        logger.info("创建封面下载任务")
                        tg.create_task(DownloaderService.download_cover_by_aweme_id(aweme_id, user_id=user_id))

                else:
                    logger.error(f"暂不支持 aweme_type: {aweme_type} 类型的下载")
                    raise Exception(f"暂不支持 aweme_type: {aweme_type} 类型的下载")

            logger.success(f"Douyin:{aweme_id}_{video_title} 所有下载任务已完成")
        except* Exception as exc_group:
            for exc in exc_group.exceptions:
                logger.error(f"下载任务异常: {exc}")

    @staticmethod
    async def save_metadata_only(aweme_id: str, parsed_data: dict) -> Dict[str, Any]:
        """
        仅保存视频元数据到数据库（不执行下载）

        用于 Progressive Download 流程：先保存元数据，下载由 Celery 任务处理

        Args:
            aweme_id: 视频ID
            parsed_data: 解析后的视频数据

        Returns:
            Dict[str, Any]: 保存结果
        """
        try:
            video_title = parsed_data.get("video_title", "undefined")
            user_id = parsed_data.get("user_id")

            need_download_video = parsed_data.get("need_download_video", False)
            need_download_music = parsed_data.get("need_download_music", False)

            # 数据验证
            try:
                douyin_data = DouyinCreate(**parsed_data)
                logger.success(f"数据验证通过: aweme_id={aweme_id}, user_id={user_id}")
            except Exception as e:
                logger.error(f"数据验证失败: {str(e)}")
                return {"success": False, "message": f"存储失败: {str(e)}"}

            repo = SupabaseDouyinRepository()
            data_dict = douyin_data.model_dump()

            # 检查是否已存在
            existing_video = await repo.get_by_aweme_id(aweme_id, user_id=user_id)

            # 设置下载状态为 PENDING（等待 Celery 任务下载）
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
                # 更新现有记录
                video_id = existing_video.get("id")
                update_data = {
                    k: v for k, v in data_dict.items()
                    if k not in ["download_path", "download_duration"]
                }
                await repo.update(aweme_id, update_data)
                message = f"媒体 {aweme_id}_{video_title} 元数据已更新"
            else:
                # 创建新记录
                result = await repo.create(data_dict)
                video_id = result.get("id") if result else None
                message = f"媒体 {aweme_id}_{video_title} 元数据已创建"

            logger.info(message)

            return {
                "success": True,
                "message": message,
                "aweme_id": aweme_id,
                "id": video_id,  # Database ID for tag operations
            }

        except Exception as e:
            logger.error(f"保存元数据失败: {str(e)}")
            return {"success": False, "message": f"保存失败: {str(e)}"}

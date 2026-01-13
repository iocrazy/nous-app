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
                aweme_type = db_result.get("aweme_type", 0)
                video_title = db_result.get("video_title", "undefined")

                logger.info(
                    f"Douyin:{aweme_id}_{video_title} 下载请求状态: "
                    f"video_{to_download_video}, music_{to_download_music}"
                )

                if to_download_video or to_download_music:
                    logger.info(
                        f"创建下载任务 Douyin:{aweme_id}_{video_title}, aweme_type: {aweme_type}"
                    )
                    asyncio.create_task(
                        SupabaseDouyinService._execute_downloads(
                            aweme_id,
                            to_download_video,
                            to_download_music,
                            aweme_type,
                            video_title
                        )
                    )

                download_msgs = []
                if to_download_video:
                    download_msgs.append("视频下载任务已加入队列")
                if to_download_music:
                    download_msgs.append("音频下载任务已加入队列")

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

        need_download_video = parsed_data.get("need_download_video", False)
        need_download_music = parsed_data.get("need_download_music", False)

        # 数据验证
        try:
            douyin_data = DouyinCreate(**parsed_data)
            logger.success(f"数据验证通过: {douyin_data}")
        except Exception as e:
            logger.error(f"数据验证失败: {str(e)}")
            return {"success": False, "message": f"存储失败: {str(e)}"}

        try:
            repo = SupabaseDouyinRepository()

            # 检查视频状态
            data_exists = await repo.check_video_existence(aweme_id)
            video_downloaded = await repo.check_video_downloaded(aweme_id)
            music_downloaded = await repo.check_music_downloaded(aweme_id)

            logger.debug(
                f"媒体状态: {aweme_id}:{video_title} aweme_type={aweme_type}, "
                f"exists={data_exists}, video_dl={video_downloaded}, "
                f"music_dl={music_downloaded}, need_video={need_download_video}, "
                f"need_music={need_download_music}"
            )

            data_dict = douyin_data.model_dump()

            if data_exists:
                # 更新现有记录
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

            # 重新检查下载状态
            video_downloaded = await repo.check_video_downloaded(aweme_id)
            music_downloaded = await repo.check_music_downloaded(aweme_id)

            logger.debug(f"下载状态: video_dl={video_downloaded}, music_dl={music_downloaded}")
            logger.debug(f"{message}")

            return {
                "success": True,
                "message": message,
                "download_video": need_download_video and not video_downloaded,
                "download_music": need_download_music and not music_downloaded,
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
        aweme_type: int,
        video_title: str
    ):
        """执行下载任务"""
        try:
            async with asyncio.TaskGroup() as tg:
                if int(aweme_type) in (0, 4, 61):  # 视频类型
                    logger.info(f"开始下载 Douyin: {aweme_id}_{video_title}, aweme_type: {aweme_type}")
                    if download_video:
                        logger.info("创建视频下载任务")
                        tg.create_task(DownloaderService.download_video_by_aweme_id(aweme_id))
                    if download_music:
                        logger.info("创建音频下载任务")
                        tg.create_task(DownloaderService.download_music_by_aweme_id(aweme_id=aweme_id))

                elif int(aweme_type) == 68:  # 图文类型
                    logger.info(f"开始下载 Douyin: {aweme_id}_{video_title}, aweme_type: {aweme_type}")
                    if download_video:
                        logger.info("创建图片下载任务")
                        tg.create_task(DownloaderService.download_images_by_aweme_id(aweme_id))
                    if download_music:
                        logger.info("创建音频下载任务")
                        tg.create_task(DownloaderService.download_music_by_aweme_id(aweme_id=aweme_id))

                else:
                    logger.error(f"暂不支持 aweme_type: {aweme_type} 类型的下载")
                    raise Exception(f"暂不支持 aweme_type: {aweme_type} 类型的下载")

            logger.success(f"Douyin:{aweme_id}_{video_title} 所有下载任务已完成")
        except* Exception as exc_group:
            for exc in exc_group.exceptions:
                logger.error(f"下载任务异常: {exc}")

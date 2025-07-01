"""
抖音服务模块

整合抖音数据的获取、解析、存储和下载功能。
提供完整的抖音视频处理流程，包括数据获取、解析、数据库存储和媒体下载。
"""

import asyncio
from typing import Dict, Any
from loguru import logger



from app.core.enums import DownloadStatus
from app.db.session import get_async_transaction_session
from app.repositories.douyin_repository import DouyinRepository
from app.schemas.douyin import DouyinCreate

from app.services.downloader import DownloaderService



class DouyinService:
    """抖音服务，整合数据获取、解析、存储和下载功能"""
    
    @staticmethod
    async def process_video(aweme_id:str , parsed_data:dict) -> Dict[str, Any]:
        """
        处理抖音视频的完整流程：获取、解析、存储和下载
        
        Args:
            aweme_id: 视频ID
            parsed_data: 抖音视频原始数据

        Returns:
            Dict[str, Any]: 处理结果
        """
        try:

            # 存储到数据库
            logger.info("Start storing to the database…")
            db_result = await DouyinService._store_to_database(parsed_data)





            # todo:处理下载任务
            logger.info("Start processing the download task…")


            if db_result.get("success"):
                # 获取下载选项和状态
                to_download_video = db_result.get("download_video", False)
                to_download_music = db_result.get("download_music", False)
                message = db_result.get("message", "")
                aweme_type =db_result.get("aweme_type", 0)

                logger.info(f"Douyin {aweme_id} download request status:download_video {to_download_video}, download_music {to_download_music}.")


                if to_download_video or to_download_music:

                    asyncio.create_task(DouyinService._execute_downloads(
                        aweme_id,
                        to_download_video,
                        to_download_music,
                        aweme_type
                    ))

                # 使用列表推导式和join构建消息
                download_msgs = []
                if to_download_video:
                    download_msgs.append("Video download has been queued.")
                if to_download_music:
                    download_msgs.append("Music download has been queued.")

                if download_msgs:
                    message += f"; {'; '.join(download_msgs)}"


            # todo: push info to notion

            logger.success(f" {message}")



            return {
                "success": True,
                "message": message,
                "aweme_id": aweme_id,
            }
            
        except Exception as e:
            logger.error(f"处理抖音视频失败: {str(e)}")
            return {"success": False, "message": f"处理失败: {str(e)}"}
    
    @staticmethod
    async def _store_to_database(parsed_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        将视频数据存储到数据库
        
        Args:
            parsed_data: 解析后的视频数据

            
        Returns:
            Dict[str, Any]: 存储结果，包含下载状态信息
        """

        aweme_id = parsed_data.get("aweme_id")
        aweme_type = parsed_data.get('aweme_type')
        # 生成短视频名称用于日志和消息
        video_title = parsed_data.get("video_title", "")
        short_video_name = video_title[:10] + "…" if len(video_title) > 10 else video_title
        # 获取下载选项
        need_download_video = parsed_data.get("need_download_video", False)
        need_download_music = parsed_data.get("need_download_music", False)


        # 使用 Pydantic 模型验证数据
        try:
            douyin_data_to_post = DouyinCreate(**parsed_data)
            logger.success(f"数据验证通过: {douyin_data_to_post}")
        except Exception as e:
            logger.error(f"数据验证失败: {str(e)}")
            return {"success": False, "message": f"存储失败: {str(e)}"}
        
        # 建立数据库连接
        async with get_async_transaction_session() as db:
            try:
                douyin_repo = DouyinRepository(db)

                logger.debug("检查视频是否已存在")
                
                # 检查视频是否已存在
                data_exists = await douyin_repo.check_video_existence(aweme_id)
                video_downloaded = await douyin_repo.check_video_downloaded(aweme_id)
                music_downloaded = await douyin_repo.check_music_downloaded(aweme_id)


                logger.debug(f"Media status:{aweme_id}:{short_video_name} aweme_type={aweme_type}, exists={data_exists}, video_dl={video_downloaded}, music_dl={music_downloaded}, need_video={need_download_video}, need_music={need_download_music}")

                # 使用match-case语句处理不同的视频存在状态
                if data_exists:
                    # 视频数据存在
                    douyin_data_to_update = douyin_data_to_post.model_dump(
                        exclude={"video_download_status", "download_path", "music_download_status", "music_path",
                                 "download_duration"}
                    )

                    match (video_downloaded, music_downloaded):
                        case (True, True):
                            # 视频、音频已下载
                            await douyin_repo.update(aweme_id, douyin_data_to_update)
                            message = f"Message: Media {aweme_id}_{short_video_name} downloaded, only updating data."

                        case (True, False):
                            # 视频已下载，音乐未下载

                            # 检查是否需要下载音乐
                            if parsed_data.get("need_download_music"):
                                douyin_data_to_update["music_download_status"] = DownloadStatus.PENDING
                                message = f"Message: Media {aweme_id}_{short_video_name} video downloaded, music pending download, updating data."
                            else:
                                # 不需要下载音乐，设置为跳过状态
                                douyin_data_to_update["music_download_status"] = DownloadStatus.SKIPPED
                                message = f"Message: Media {aweme_id}_{short_video_name} video downloaded, music download skipped, updating data."

                            await douyin_repo.update(aweme_id, douyin_data_to_update)


                        case (False, True):
                            # 视频未下载，音频下载
                            if parsed_data.get("need_download_video"):
                                douyin_data_to_update["video_download_status"] = DownloadStatus.PENDING
                                message = f"Message: Media {aweme_id}_{short_video_name} music downloaded,video pending download,updating data."

                            else:
                                douyin_data_to_update["video_download_status"] = DownloadStatus.SKIPPED
                                message = f"Message: Media {aweme_id}_{short_video_name} music downloaded,video download skipped,updating data."
                            await douyin_repo.update(aweme_id, douyin_data_to_update)


                        case (False, False):
                            # 视频未下载，音频未下载

                            if need_download_video and need_download_music:
                                douyin_data_to_update["video_download_status"] = DownloadStatus.PENDING
                                douyin_data_to_update["music_download_status"] = DownloadStatus.PENDING
                                message = f"Message: Media {aweme_id}_{short_video_name} updated data, video and music queued for download"
                            elif need_download_video:
                                douyin_data_to_update["video_download_status"] = DownloadStatus.PENDING
                                douyin_data_to_update["music_download_status"] = DownloadStatus.SKIPPED
                                message = f"Message: Media {aweme_id}_{short_video_name} updated data, video queued for download"
                            elif need_download_music:
                                douyin_data_to_update["music_download_status"] = DownloadStatus.PENDING
                                douyin_data_to_update["video_download_status"] = DownloadStatus.SKIPPED
                                message = f"Message: Media {aweme_id}_{short_video_name} updated data, music queued for download"
                            else:
                                douyin_data_to_update["video_download_status"] = DownloadStatus.SKIPPED
                                douyin_data_to_update["music_download_status"] = DownloadStatus.SKIPPED
                                message = f"Message: Media {aweme_id}_{short_video_name} updated data only, no download requested"
                            await douyin_repo.update(aweme_id, douyin_data_to_update)

                else:
                    # 视频数据不存在，创建新记录
                    douyin_data_to_create = douyin_data_to_post.model_dump()

                    # 根据需求设置下载状态
                    if need_download_video and need_download_music:
                        douyin_data_to_create["video_download_status"] = DownloadStatus.PENDING
                        douyin_data_to_create["music_download_status"] = DownloadStatus.PENDING
                        message = f"Message: Media {aweme_id}_{short_video_name} data created with video and music queued for download"
                    elif need_download_video:
                        douyin_data_to_create["video_download_status"] = DownloadStatus.PENDING
                        douyin_data_to_create["music_download_status"] = DownloadStatus.SKIPPED
                        message = f"Message: Media {aweme_id}_{short_video_name} data created with video queued for download"
                    elif need_download_music:
                        douyin_data_to_create["music_download_status"] = DownloadStatus.PENDING
                        douyin_data_to_create["video_download_status"] = DownloadStatus.SKIPPED
                        message = f"Message: Media {aweme_id}_{short_video_name} data created with music queued for download"
                    else:
                        douyin_data_to_create["video_download_status"] = DownloadStatus.SKIPPED
                        douyin_data_to_create["music_download_status"] = DownloadStatus.SKIPPED
                        message = f"Message: Media {aweme_id}_{short_video_name} data created without download requested"

                    # 创建新记录
                    await douyin_repo.create(douyin_data_to_create)


                await db.commit()


                # 提交后重新获取最新下载状态
                video_downloaded = await douyin_repo.check_video_downloaded(aweme_id)
                music_downloaded = await douyin_repo.check_music_downloaded(aweme_id)


                message += f"video_dl_status={video_downloaded}, music_dl_status={music_downloaded}"
                logger.debug(f"{message}")

                return {
                    "success": True,
                    "message": message,
                    "download_video": need_download_video and not video_downloaded,
                    "download_music": need_download_music and not music_downloaded,
                    "aweme_id": aweme_id,
                    "aweme_type": aweme_type
                }

            except Exception as e:
                await db.rollback()
                logger.error(f"存储视频数据失败: {str(e)}")
                return {"success": False, "message": f"存储失败: {str(e)}"}

    @staticmethod
    async def _execute_downloads(
        aweme_id: str,
        download_video: bool,
        download_music: bool,
        aweme_type: int,
    ):
        """
        使用 TaskGroup 执行下载任务
        
        Args:
            aweme_id: 视频ID
            download_video: 是否下载视频
            download_music: 是否下载音乐
            aweme_type: 媒体类型

        """
        try:
            # 添加日志，帮助诊断问题
            logger.info(f"开始执行下载任务: aweme_id={aweme_id}, download_video={download_video}, download_music={download_music}, aweme_type={aweme_type}")

            # 将 aweme_type 转换为字符串进行比较
            aweme_type_str = str(aweme_type)

            async with asyncio.TaskGroup() as tg:
                if aweme_type_str == "0":  # 视频类型
                    logger.debug(f"Start downloading Douyin: {aweme_id}, aweme_type: {aweme_type_str}")
                    if download_video:
                        logger.info(f"Create a download task for video: {aweme_id}")
                        tg.create_task(DownloaderService.download_video_by_aweme_id(aweme_id))
                    if download_music:
                        logger.info(f"Create a download task for music: {aweme_id}")
                        # 修复这里的错误：应该调用 download_music_by_aweme_id 而不是 download_video_by_aweme_id
                        tg.create_task(DownloaderService.download_music_by_aweme_id(aweme_id=aweme_id))

                elif aweme_type_str == "68":  # 图文类型
                    logger.debug(f"Start downloading Douyin: {aweme_id}, aweme_type: {aweme_type_str}")
                    # 处理图文下载逻辑
                    if download_video:
                        logger.info(f"Create a download task for images: {aweme_id}")
                        tg.create_task(DownloaderService.download_images_by_aweme_id(aweme_id))

                    if download_music:
                        logger.info(f"Create a download task for music:  {aweme_id}")
                        tg.create_task(DownloaderService.download_music_by_aweme_id(aweme_id=aweme_id))

                else:  # 其他类型
                    logger.error(f"暂不提供{aweme_type_str}类型的下载， {aweme_id} 下载失败")
                    # 处理其他类型媒体的下载逻辑
                    raise Exception(f"暂不提供{aweme_type_str}类型的下载， {aweme_id} 下载失败")

            logger.info(f"所有下载任务完成: {aweme_id}")
        except* Exception as exc_group:
            # 处理所有任务中的异常
            for exc in exc_group.exceptions:
                logger.error(f"下载任务异常: {exc}")

"""
抖音服务模块

整合抖音数据的获取、解析、存储和下载功能。
提供完整的抖音视频处理流程，包括数据获取、解析、数据库存储和媒体下载。
"""

import asyncio
from typing import Dict, Any
from loguru import logger
import json


from app.core.enums import DownloadStatus
from app.db.session import get_async_transaction_session
from app.repositories.douyin_repository import DouyinRepository
from app.schemas.douyin import DouyinCreate, DownloadVideoResult
from app.services.douyin_parser import DouyinParser
from app.services.douyin_analysis import DouyinAnalysis
from app.services.downloader import DownloaderService
from app.core.utils import Utils
from app.models.douyin import Douyin


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

            # todo:存储到数据库
            db_result = await DouyinService._store_to_database(parsed_data)


            # todo:处理下载任务


            #
            #
            # download_tasks = []
            # if download_video and db_result.get("download_video"):
            #     # 创建视频下载任务
            #     download_tasks.append(
            #         DouyinService._download_video(aweme_id)
            #     )
            #
            # if download_music and db_result.get("download_music"):
            #     # 创建音乐下载任务
            #     download_tasks.append(
            #         DouyinService._download_music(aweme_id)
            #     )
            #
            # # 并行执行下载任务
            # if download_tasks:
            #     asyncio.create_task(DouyinService._execute_download_tasks(download_tasks))
            #
            # # 构建返回消息
            # message = "下载任务已创建"
            # if download_video and db_result.get("download_video"):
            #     message += "; 视频下载已加入队列"
            # if download_music and db_result.get("download_music"):
            #     message += "; 音乐下载已加入队列"
            #

            #todo: push info to notion

            return {
                "success": True,
                "message": db_result.get("message"),
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
            Dict[str, Any]: 存储结果
        """
        result={
            "success": True,
            "message": ""
        }

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
            logger.info(f"数据验证通过: {douyin_data_to_post}")
        except Exception as e:
            logger.error(f"数据验证失败: {str(e)}")
            return {"success": False, "message": f"存储失败: {str(e)}"}
        
        # 建立数据库连接
        async with get_async_transaction_session() as db:
            try:
                douyin_repo = DouyinRepository(db)
                
                # 检查视频是否已存在
                data_exists = await douyin_repo.check_video_existence(aweme_id)
                video_downloaded = await douyin_repo.check_video_downloaded(aweme_id)
                music_downloaded = await douyin_repo.check_music_downloaded(aweme_id)


                logger.info(f"Media status:{aweme_id}:{short_video_name} aweme_type={aweme_type}, exists={data_exists}, video_dl={video_downloaded}, music_dl={music_downloaded}, need_video={need_download_video}, need_music={need_download_music}")

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
                            message = f"Media {aweme_id}_{short_video_name} downloaded, only updating data."

                        case (True, False):
                            # 视频下载，音频未下载

                            if parsed_data.get("need_download_music"):
                                douyin_data_to_update["music_download_status"] = DownloadStatus.PENDING
                            await douyin_repo.update(aweme_id, douyin_data_to_update)

                            message = f"Media {aweme_id}_{short_video_name}  video downloaded,music pending download,updating data."

                        case (False, True):
                            # 视频未下载，音频下载
                            if parsed_data.get("need_download_video"):
                                douyin_data_to_update["video_download_status"] = DownloadStatus.PENDING
                            await douyin_repo.update(aweme_id, douyin_data_to_update)

                            message = f"Media {aweme_id}_{short_video_name} music downloaded,video pending download,updating data."

                        case (False, False):
                            # 视频未下载，音频未下载

                            if need_download_video:
                                douyin_data_to_update["video_download_status"] = DownloadStatus.PENDING

                            if need_download_music:
                                douyin_data_to_update["music_download_status"] = DownloadStatus.PENDING
                            await douyin_repo.update(aweme_id, douyin_data_to_update)

                            # 构建消息
                            if need_download_video and need_download_music:
                                message = f"Message: Media {aweme_id}_{short_video_name} updated data, video and music queued for download"
                            elif need_download_video:
                                message = f"Message: Media {aweme_id}_{short_video_name} updated data, video queued for download"
                            elif need_download_music:
                                message = f"Message: Media {aweme_id}_{short_video_name} updated data, music queued for download"
                            else:
                                message = f"Message: Media {aweme_id}_{short_video_name} updated data only, no download requested"

                else:
                    # 视频数据不存在，创建新记录
                    douyin_data_to_create = douyin_data_to_post.model_dump()

                    # 设置下载状态
                    if need_download_video:
                        douyin_data_to_create["video_download_status"] = DownloadStatus.PENDING

                    if need_download_music:
                        douyin_data_to_create["music_download_status"] = DownloadStatus.PENDING

                    # 创建新记录
                    await douyin_repo.create(douyin_data_to_create)

                    # 构建消息
                    if need_download_video and need_download_music:
                        message = f"Message: Media {aweme_id}_{short_video_name} data created with video and music queued for download"
                    elif need_download_video:
                        message = f"Message: Media {aweme_id}_{short_video_name} data created with video queued for download"
                    elif need_download_music:
                        message = f"Message: Media {aweme_id}_{short_video_name} data created with music queued for download"
                    else:
                        message = f"Message: Media {aweme_id}_{short_video_name} data created without download requested"


                await db.commit()
                logger.info(f"{message}")

                return {"success": True, "message": message}
                
            except Exception as e:
                await db.rollback()
                logger.error(f"存储视频数据失败: {str(e)}")
                return {"success": False, "message": f"存储失败: {str(e)}"}
    
    @staticmethod
    async def _download_video(aweme_id: str) -> DownloadVideoResult:
        """
        下载视频
        
        Args:
            aweme_id: 视频ID
            
        Returns:
            DownloadVideoResult: 下载结果
        """
        try:
            return await DownloaderService.download_video_by_aweme_id(aweme_id)
        except Exception as e:
            logger.error(f"下载视频 {aweme_id} 失败: {str(e)}")
            result = DownloadVideoResult.model_construct()
            result.video_download_status = DownloadStatus.FAILED
            result.error = str(e)
            return result
    
    @staticmethod
    async def _download_music(aweme_id: str) -> Dict[str, Any]:
        """
        下载音乐
        
        Args:
            aweme_id: 视频ID
            
        Returns:
            Dict[str, Any]: 下载结果
        """
        try:
            return await DownloaderService.download_music_by_aweme_id(aweme_id=aweme_id)
        except Exception as e:
            logger.error(f"下载音乐 {aweme_id} 失败: {str(e)}")
            return {"success": False, "error": str(e)}
    
    @staticmethod
    async def _execute_download_tasks(tasks):
        """
        执行下载任务
        
        Args:
            tasks: 下载任务列表
        """
        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(f"下载任务 {i+1} 失败: {str(result)}")
                else:
                    logger.info(f"下载任务 {i+1} 完成")
        except Exception as e:
            logger.error(f"执行下载任务失败: {str(e)}")
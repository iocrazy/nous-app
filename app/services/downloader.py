# app/services/downloader.py

"""
视频下载服务模块

提供异步视频和音频下载功能，支持自定义请求头、文件名生成和错误处理。
使用 httpx 和 aiofiles 实现高效的异步下载。
"""

import os

from typing import  Dict, Any

import aiofiles
import httpx
from loguru import logger


from app.core.utils import Utils
from app.core.enums import DownloadStatus
from app.repositories.douyin_repository import DouyinRepository
from app.schemas.douyin import DownloadVideoResult, DownloadMusicResult, DownloadImagesResult
from app.db.session import get_async_transaction_session
from app.core.config import settings


class DownloaderService:



    @staticmethod
    async def download_file(url: str, file_path: str, headers: Dict[str, Any]= None) -> bool:
        """
        下载单个文件
        
        Args:
            url: 下载URL
            file_path: 保存路径
            headers: 请求头
            
        Returns:
            bool: 下载是否成功
        """
        if not headers:
            headers = Utils.get_headers()
            
        try:

            # os.makedirs(os.path.dirname(file_path), exist_ok=True)
            if os.path.exists(file_path):
                logger.info(f"文件已存在，跳过下载: {os.path.basename(file_path)}")
                return True
            
            # 使用 httpx 下载文件
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=headers, follow_redirects=True, timeout=settings.DOWNLOAD_TIMEOUT)
                
                if response.status_code == 200:
                    # 保存文件
                    async with aiofiles.open(file_path, mode='wb') as f:
                        await f.write(response.content)
                    logger.success(f"成功下载文件: {os.path.basename(file_path)}")
                    return True
                else:
                    logger.warning(f"下载失败 {url}, 状态码: {response.status_code}")
                    return False
                    
        except Exception as e:
            logger.error(f"下载出错 {url}: {str(e)}")
            return False

    @staticmethod
    async def download_video_by_aweme_id(aweme_id) -> DownloadVideoResult:
        """
        下载视频和可选的音乐文件
        
        Args:
            aweme_id: 视频id

        Returns:
            DownloadResult: 下载结果信息
        """
        # 初始化结果对象,pydantic2.0方法
        result = DownloadVideoResult.model_construct()
        headers = Utils.get_headers()
        
        try:

            # 建立数据库链接 - 注意这里的缩进，会话应该包含所有数据库操作
            async with get_async_transaction_session() as session:
                repo = DouyinRepository(session)

                # 获取视频数据 - 这是 Douyin 模型实例
                video_data = await repo.get_by_aweme_id(aweme_id)
                if not video_data:
                    logger.error(f"找不到视频数据: {aweme_id}")
                    result.video_download_status = DownloadStatus.FAILED
                    result.error = f"找不到视频数据: {aweme_id}"
                    return result



                logger.info(f"准备下载视频: {aweme_id}")

                # create file path
                download_path = Utils.create_download_folder()

                # generate file name
                video_title = video_data.video_title
                file_name = Utils.safe_filename(video_title, aweme_id)
                video_path = os.path.join(download_path, file_name + ".mp4")
                
                # 下载视频
                video_urls = video_data.video_download_urls
                if not video_urls:
                    logger.warning(f"视频 {aweme_id} 没有可用的下载URL")
                    result.video_download_status = DownloadStatus.FAILED
                    result.error = f"视频 {aweme_id} 没有可用的下载URL"

                    # 更新数据库状态为"失败"
                    await repo.update(aweme_id, {
                        "download_status": DownloadStatus.FAILED,
                        "error_message": result.error
                    })
                    await session.commit()  # 提交事务
                    return result
                
                # todo calculate download time
                download_duration = 10

                # download video while one of the urls is successful
                for url in video_urls:
                    if await DownloaderService.download_file(url, video_path, headers):
                        logger.info(f"视频 {aweme_id} 下载成功下载到 {video_path}")
                        try:
                            await repo.mark_as_downloaded(
                                aweme_id=aweme_id,
                                download_path=video_path,
                                download_duration=download_duration
                            )
                            await session.commit()  # 提交事务
                        except Exception as e:
                            logger.error(f"标记视频 {aweme_id} 下载成功，但更新数据库失败: {e}")
                            result.error = f"标记视频 {aweme_id} 下载成功，但更新数据库失败: {e}"

                        # 更新结果对象
                        result.video_download_status = DownloadStatus.COMPLETED
                        result.video_path = video_path
                        result.download_duration = download_duration
                        logger.info(f"视频 {aweme_id} 下载成功，保存到 {video_path}")
                        break
                    
                if result.video_download_status != DownloadStatus.COMPLETED:
                    logger.error(f"视频 {aweme_id} 所有下载URL均失败")
                    result.video_download_status = DownloadStatus.FAILED
                    result.error = f"视频 {aweme_id} 所有下载URL均失败"

                    # 更新数据库状态为"失败"
                    await repo.update(aweme_id, {
                        "download_status": DownloadStatus.FAILED,
                        "error_message": result.error
                    })
                    await session.commit()  # 提交事务

            return result
        
        except Exception as e:
            logger.error(f"下载处理出错: {str(e)}")
            result.video_download_status = DownloadStatus.FAILED
            result.error = str(e)
            return result

    async def download_images_by_aweme_id(self, *, aweme_id) -> DownloadImagesResult:
        """
        只下载图片文件

        Args:
            aweme_id: 视频ID aweme_id

        Returns:
            DownloadImagesResult: 下载结果信息
        """
        # 初始化结果对象
        result = DownloadImagesResult.model_construct()
        headers = Utils.get_headers()
        try:
            # 建立数据库链接 - 注意这里的缩进，会话应该包含所有数据库操作
            async with get_async_transaction_session() as session:
                repo = DouyinRepository(session)
            # 获取视频ID
            logger.info(f"准备下载媒体 {aweme_id} 的图片集")
        finally:
            # 关闭数据库链接
            await session.close()






    @staticmethod
    async def download_music_by_aweme_id(*, aweme_id) -> DownloadMusicResult:
        """
        只下载音乐文件
        
        Args:
            aweme_id: 视频ID aweme_id

        Returns:
            DownloadMusicResult: 下载结果信息
        """
        # 初始化结果对象
        result = DownloadMusicResult.model_construct()
        headers = Utils.get_headers()

        try:

            # 建立数据库链接 - 注意这里的缩进，会话应该包含所有数据库操作
            async with get_async_transaction_session() as session:
                repo = DouyinRepository(session)

                # 获取视频ID
                logger.info(f"准备下载媒体 {aweme_id} 的音乐")

                # 检查是否有音乐URL\Name
                music_data = await repo.get_music_data(aweme_id)
                logger.info(f"音乐数据: {music_data}")

                # 从字典中获取音乐URL列表
                music_urls = music_data.get("music_download_urls", [])
                if not music_urls:
                    logger.warning(f"视频 {aweme_id} 没有可用的音乐下载URL")
                    result.error = f"视频 {aweme_id} 没有可用的音乐下载URL"
                    return result
                
                # 创建下载路径
                download_path = Utils.create_download_folder()
            
                # 从字典中获取音乐名称
                music_name = music_data.get("music_name")
                if not music_name:
                    music_name = f"{aweme_id}_music"
                
                # 生成音乐文件路径
                music_path = os.path.join(download_path, f"{music_name}.mp3")
            
                # 尝试下载音乐
                for url in music_urls:
                    if await DownloaderService.download_file(url, music_path, headers):
                        # 更新数据库，标记音乐已下载
                        await repo.update(aweme_id, {"music_downloaded": True})
                        await session.commit()  # 提交事务
                        result.music_path = music_path
                        result.music_downloaded = True
                        logger.info(f"视频 {aweme_id} 的音乐下载成功，保存到 {music_path}")
                        break

                if not result.music_downloaded:
                    logger.error(f"视频 {aweme_id} 的音乐下载失败，所有URL均失败")
                    result.error = f"视频 {aweme_id} 的音乐下载失败，所有URL均失败"
            
            return result
        
        except Exception as e:
            logger.error(f"音乐下载处理出错: {str(e)}")
            result.error = str(e)
            return result
        


# app/services/downloader.py

"""
视频下载服务模块

提供异步视频和音频下载功能，支持自定义请求头、文件名生成和错误处理。
使用 httpx 和 aiofiles 实现高效的异步下载。
"""

import os

from typing import  Dict, Any
import asyncio

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

            os.makedirs(os.path.dirname(file_path), exist_ok=True)
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
                video_data = await repo.get_douyin_by_aweme_id(aweme_id)
                if not video_data:
                    logger.error(f"找不到视频数据: {aweme_id}")
                    result.video_download_status = DownloadStatus.FAILED
                    result.error = f"找不到视频数据: {aweme_id}"
                    return result



                logger.info(f"准备下载视频: {aweme_id}")

                # create file path
                download_path = Utils.create_download_folder()
                logger.debug(f"NAS_BASE_PATH: {settings.NAS_BASE_PATH}")

                # generate file name
                video_title = video_data.video_title
                file_name = Utils.concat_filename_safe_title(video_title, aweme_id)
                video_path = os.path.join(download_path, file_name + ".mp4")
                logger.debug(f"视频文件名: {file_name}")
                
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
                        try:
                            await repo.mark_video_as_downloaded(
                                aweme_id=aweme_id,
                                download_path=video_path,
                                download_duration=download_duration
                            )
                            await session.commit()  # 提交事务
                        except Exception as e:
                            logger.error(f"Marked video {aweme_id} as downloaded successfully, but failed to update the database: {e}.")
                            result.error = f"Marked video {aweme_id} as downloaded successfully, but failed to update the database: {e}."

                        # 更新结果对象
                        result.video_download_status = DownloadStatus.COMPLETED
                        result.video_path = video_path
                        result.download_duration = download_duration
                        logger.success(f"Video {aweme_id} downloaded successfully and saved to {video_path}.")
                        break
                    
                if result.video_download_status != DownloadStatus.COMPLETED:
                    logger.error(f"All download URLs for the video {aweme_id} failed.")
                    result.video_download_status = DownloadStatus.FAILED
                    result.error = f"All download URLs for the video {aweme_id} failed."

                    # 更新数据库状态为"失败"
                    await repo.update(aweme_id, {
                        "video_download_status": DownloadStatus.FAILED,
                        "error_message": result.error
                    })
                    await session.commit()  # 提交事务

            return result
        
        except Exception as e:
            logger.error(f"Download processing error:: {str(e)}")
            result.video_download_status = DownloadStatus.FAILED
            result.error = str(e)
            return result

    @staticmethod
    async def download_images_by_aweme_id(aweme_id) :
        """
        只下载图片文件

        Args:
            aweme_id: 视频ID aweme_id

        Returns:
            DownloadImagesResult: 下载结果信息
        """
        #todo:获取并叠加error_message

        result = DownloadImagesResult.model_construct()
        headers = Utils.get_headers()

        try:
            # 建立数据库链接
            async with get_async_transaction_session() as session:
                repo = DouyinRepository(session)

                # 获取视频数据
                video_data = await repo.get_douyin_by_aweme_id(aweme_id)
                if not video_data:
                    logger.error(f"找不到视频数据: {aweme_id}")
                    result.video_download_status = DownloadStatus.FAILED
                    result.error = f"找不到视频数据: {aweme_id}"
                    return result

                logger.info(f"准备下载Douyin {aweme_id} 的图片集")

                # create file path
                logger.debug(f"NAS_BASE_PATH: {settings.NAS_BASE_PATH}")
                parent_download_path = Utils.create_download_folder()

                # generate file name
                video_title = video_data.video_title
                file_name = Utils.concat_filename_safe_title(video_title, aweme_id)

                sub_download_path = os.path.join(parent_download_path, file_name)
                os.makedirs(sub_download_path, exist_ok=True)
                logger.success(f"Successfully created download file path: {sub_download_path}")

                video_urls = video_data.video_download_urls
                image_urls = video_data.image_download_urls

                # 打印调试信息
                # logger.debug(f"视频URL类型: {type(video_urls)}")
                # logger.debug(f"图片URL类型: {type(image_urls)}")

                video_downloaded_count = 0
                image_downloaded_count = 0

                # 下载视频

                if  Utils.is_nested_list(video_urls):
                    logger.info(f"准备下载  {len(video_urls)} 个视频")
                    video_tasks = []
                    async with asyncio.TaskGroup() as tg:
                        for i, url_list in enumerate(video_urls):
                            task = tg.create_task(DownloaderService.download_single_list_item(i,url_list, sub_download_path,file_name, headers))
                            logger.debug(f"添加视频第{i+1}下载任务: {task}")
                            video_tasks.append(task)

                    for task in video_tasks:
                        try:
                            result_data = task.result()
                            if result_data and result_data.get("success", False):
                                video_downloaded_count += 1
                        except Exception as e:
                            logger.error(f"获取视频下载任务结果失败: {e}")

                    logger.info(f"Douyin{file_name} 视频下载完成，共下载了 {video_downloaded_count}/{len(video_urls)} 个视频文件。")
                else:
                    logger.info(f"No videos require downloading.")


                # todo 检查错误，这里没有
                if Utils.is_nested_list(image_urls):
                    logger.debug(f"准备下载 {len(image_urls)} 张图片")
                    image_tasks = []
                    async with asyncio.TaskGroup() as tg:
                        for i, url_list in enumerate(image_urls):
                            task = tg.create_task(DownloaderService.download_single_list_item(i,url_list, sub_download_path,file_name, headers))
                            logger.debug(f"添加图片第{i+1}下载任务: {task}")
                            image_tasks.append(task)

                    # 处理图片下载结果
                    for task in image_tasks:
                        try:
                            result_data = task.result()
                            # logger.info(f"图片下载结果: {result_data}")
                            if result_data and result_data.get("success", False):
                                image_downloaded_count += 1
                                logger.debug(f"成功下载图片，当前计数: {image_downloaded_count}")

                        except Exception as e:
                            logger.error(f"获取图片下载任务结果失败: {e}")

                    logger.info(f"Douyin {file_name} 图片下载完成，共下载了 {image_downloaded_count}/{len(image_urls)} 个图片文件。")

                # 计算总下载数量
                total_expected = 0
                if Utils.is_nested_list(video_urls):
                    total_expected += len(video_urls)
                if Utils.is_nested_list(image_urls):
                    total_expected += len(image_urls)

                total_downloaded = video_downloaded_count + image_downloaded_count


                if total_downloaded == total_expected and total_expected > 0:
                    logger.success(f"Douyin {file_name} 下载完成，共下载了 {video_downloaded_count}个视频文件和 {image_downloaded_count}个图片文件。")
                    await repo.update(aweme_id, {
                        "video_download_status": DownloadStatus.COMPLETED,
                        "download_path": sub_download_path
                    })
                    await session.commit()
                    result.video_download_status = DownloadStatus.COMPLETED

                else:
                    error_msg = f"Douyin {file_name} 下载失败，共下载了 {video_downloaded_count}/{len(video_urls) if Utils.is_nested_list(video_urls) else 0} 个视频文件和 {image_downloaded_count}/{len(image_urls) if Utils.is_nested_list(image_urls) else 0} 个图片文件。Download Path: {sub_download_path}"
                    logger.error(error_msg)
                    await repo.update(aweme_id, {
                        "video_download_status": DownloadStatus.FAILED,
                        "error_message": error_msg
                    })
                    await session.commit()
                    result.video_download_status = DownloadStatus.FAILED
                    result.error = error_msg

        except Exception as e:
            logger.error(f"douyin {aweme_id} 下载处理出错: {str(e)}")
            result.error = str(e)
            result.video_download_status = DownloadStatus.FAILED
            return result
        return result

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

            async with get_async_transaction_session() as session:
                repo = DouyinRepository(session)

                # 获取视频ID
                logger.info(f"准备下载媒体 {aweme_id} 的音乐")

                # 检查是否有音乐URL\Name
                try:
                    music_data = await repo.get_music_data(aweme_id)
                    # 使用字典推导式处理每个值
                    shortened_data = {k: Utils.shorten_item(v, 30) for k, v in music_data.items()}
                    logger.debug(f" {aweme_id} music data: {shortened_data}")
                except ValueError as e:
                    logger.error(f"Failed to retrieve music data: {e}")
                    result.error = str(e)
                    return result

                # 从字典中获取音乐URL列表
                music_urls = music_data.get("music_download_urls", [])
                
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
                        try:
                            await repo.mark_music_as_downloaded(aweme_id,music_path)
                            await session.commit()  # 提交事务
                        except Exception as e:
                            logger.error(f"Marked music {aweme_id} as downloaded successfully, but failed to update the database: {e}.")
                            result.error += f"Marked music {aweme_id} as downloaded successfully, but failed to update the database: {e}."

                        result.music_path = music_path
                        result.music_download_status = DownloadStatus.COMPLETED

                        logger.success(f"Music for video {aweme_id} downloaded successfully and saved to {music_path}.")
                        break

                if result.music_download_status != DownloadStatus.COMPLETED:
                    logger.error(f"All download URLs for the music of video {aweme_id} failed.")
                    result.music_download_status = DownloadStatus.FAILED
                    result.error += f"All download URLs for the music of video {aweme_id} failed."

                    # 更新数据库状态为"失败"
                    await repo.update(aweme_id, {
                        "music_download_status": DownloadStatus.FAILED,
                        "error_message": result.error
                    })
                    await session.commit()  # 提交事务


            
            return result
        
        except Exception as e:
            logger.error(f"Music download processing error: {str(e)}")
            result.error = str(e)
            return result

    @staticmethod
    async def download_single_list_item(i, url_list, sub_download_path, file_name, headers):
        """下载单个列表项（视频或图片），尝试多个URL直到成功"""
        # 确定文件扩展名
        extension = ".mp4" if "mp4" in str(url_list) else ".jpg"
        file_path = os.path.join(sub_download_path, f"{file_name}_{i}{extension}")

        # 确保目录存在
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        # 如果文件已存在，直接返回成功
        if os.path.exists(file_path):
            logger.info(f"文件已存在，跳过下载: {os.path.basename(file_path)}")
            return {"success": True, "path": file_path, "already_exists": True}  # 添加标记表示文件已存在

        # 尝试下载
        for j, url in enumerate(url_list):
            try:
                if await DownloaderService.download_file(url, file_path, headers):
                    return {"success": True, "path": file_path}
            except Exception as e:
                logger.warning(f"URL {j} 下载失败: {str(e)}")
                continue

        # 所有URL都失败了
        return {"success": False, "path": None, "message": f"所有URL都失败了，共尝试了{len(url_list)}个URL"}

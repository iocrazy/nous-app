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
from app.repositories.supabase_douyin_repository import SupabaseDouyinRepository
from app.repositories.user_logs_repository import log_user_action
from app.schemas.douyin import DownloadVideoResult, DownloadMusicResult, DownloadImagesResult, DownloadCoverResult
# from app.db.session import get_async_transaction_session
from app.core.config import settings


class DownloaderService:



    @staticmethod
    async def download_file(
        url: str,
        file_path: str,
        headers: Dict[str, Any] = None,
        progress_tracker=None
    ) -> bool:
        """
        下载单个文件，可选进度追踪

        Args:
            url: 下载URL
            file_path: 保存路径
            headers: 请求头
            progress_tracker: 可选的进度追踪器（DownloadProgressTracker实例）

        Returns:
            bool: 下载是否成功
        """
        if not headers:
            headers = Utils.get_headers()

        try:
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            if os.path.exists(file_path):
                logger.info(f"文件已存在，跳过下载: {os.path.basename(file_path)}")
                if progress_tracker:
                    progress_tracker.complete()
                return True

            async with httpx.AsyncClient() as client:
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
                            logger.warning(f"下载失败 {url}, 状态码: {response.status_code}")
                            return False

                        total = int(response.headers.get("content-length", 0))
                        downloaded = 0

                        async with aiofiles.open(file_path, mode='wb') as f:
                            async for chunk in response.aiter_bytes(chunk_size=8192):
                                await f.write(chunk)
                                downloaded += len(chunk)
                                progress_tracker.update(downloaded, total)

                        logger.success(f"成功下载文件: {os.path.basename(file_path)}")
                        return True
                else:
                    # Original non-streaming download
                    response = await client.get(
                        url,
                        headers=headers,
                        follow_redirects=True,
                        timeout=settings.DOWNLOAD_TIMEOUT
                    )

                    if response.status_code == 200:
                        async with aiofiles.open(file_path, mode='wb') as f:
                            await f.write(response.content)
                        logger.success(f"成功下载文件: {os.path.basename(file_path)}")
                        return True
                    else:
                        logger.warning(f"下载失败 {url}, 状态码: {response.status_code}")
                        return False

        except Exception as e:
            logger.error(f"下载出错 {url}: {str(e)}")
            if progress_tracker:
                progress_tracker.failed(str(e))
            return False

    @staticmethod
    async def download_video_by_aweme_id(
        aweme_id,
        user_id: str = None,
        progress_tracker=None
    ) -> DownloadVideoResult:
        """
        下载视频和可选的音乐文件

        Args:
            aweme_id: 视频id
            user_id: 用户ID（用于数据隔离）
            progress_tracker: 可选的进度追踪器

        Returns:
            DownloadResult: 下载结果信息
        """
        # 初始化结果对象,pydantic2.0方法
        result = DownloadVideoResult.model_construct()
        headers = Utils.get_headers()

        try:

            # 建立数据库链接
            repo = SupabaseDouyinRepository()

            # 获取视频数据 - 这是 Douyin 模型实例
            video_data = await repo.get_by_aweme_id(aweme_id, user_id=user_id)
            if not video_data:
                logger.error(f"找不到视频数据: {aweme_id}")
                result.video_download_status = DownloadStatus.FAILED
                result.error = f"找不到视频数据: {aweme_id}"
                return result

            logger.info(f"准备下载视频: {aweme_id}")

            # create file path (返回完整路径和相对路径)
            full_path, relative_month = Utils.create_download_folder()
            logger.debug(f"下载基础路径: {full_path}, 相对路径: {relative_month}")

            # generate file name
            video_title = video_data.get("video_title", "undefined")
            file_name = Utils.concat_filename_safe_title(video_title, aweme_id)
            video_full_path = os.path.join(full_path, file_name + ".mp4")
            video_relative_path = f"{relative_month}/{file_name}.mp4"  # 相对路径
            logger.debug(f"视频文件名: {file_name}")

            # 下载视频
            video_urls = video_data.get("video_download_urls")
            if not video_urls:
                logger.warning(f"视频 {aweme_id} 没有可用的下载URL")
                result.video_download_status = DownloadStatus.FAILED
                result.error = f"视频 {aweme_id} 没有可用的下载URL"

                # 更新数据库状态为"失败"
                await repo.update(aweme_id, {
                    "download_status": DownloadStatus.FAILED,
                    "error_message": result.error
                }, user_id=user_id)
                return result

            # todo calculate download time
            download_duration = 10

            # download video while one of the urls is successful
            for url in video_urls:
                if await DownloaderService.download_file(url, video_full_path, headers, progress_tracker):
                    try:
                        # 存储相对路径到数据库
                        await repo.mark_video_as_downloaded(
                            aweme_id=aweme_id,
                            download_path=video_relative_path,  # 使用相对路径
                            duration=download_duration
                        )
                    except Exception as e:
                        logger.error(f"Marked video {aweme_id} as downloaded successfully, but failed to update the database: {e}.")
                        result.error = f"Marked video {aweme_id} as downloaded successfully, but failed to update the database: {e}."

                    # 更新结果对象
                    result.video_download_status = DownloadStatus.COMPLETED
                    result.video_path = video_relative_path  # 返回相对路径
                    result.download_duration = download_duration
                    logger.success(f"Video {aweme_id} downloaded successfully and saved to {video_full_path}.")

                    # 记录成功日志
                    if user_id:
                        await log_user_action(
                            user_id=user_id,
                            action="download",
                            message=f"视频下载成功: {video_title[:30]}...",
                            status="success",
                            aweme_id=aweme_id
                        )
                    break

            if result.video_download_status != DownloadStatus.COMPLETED:
                logger.error(f"All download URLs for the video {aweme_id} failed.")
                result.video_download_status = DownloadStatus.FAILED
                result.error = f"All download URLs for the video {aweme_id} failed."

                # 更新数据库状态为"失败"
                await repo.update(aweme_id, {
                    "video_download_status": DownloadStatus.FAILED,
                    "error_message": result.error
                }, user_id=user_id)

                # 记录失败日志
                if user_id:
                    await log_user_action(
                        user_id=user_id,
                        action="download",
                        message=f"视频下载失败: {video_title[:30]}...",
                        status="error",
                        aweme_id=aweme_id,
                        details={"error": result.error}
                    )

            return result

        except Exception as e:
            logger.error(f"Download processing error:: {str(e)}")
            result.video_download_status = DownloadStatus.FAILED
            result.error = str(e)
            return result

    @staticmethod
    async def download_images_by_aweme_id(aweme_id, user_id: str = None):
        """
        只下载图片文件

        Args:
            aweme_id: 视频ID aweme_id
            user_id: 用户ID（用于数据隔离）

        Returns:
            DownloadImagesResult: 下载结果信息
        """
        #todo:获取并叠加error_message

        result = DownloadImagesResult.model_construct()
        headers = Utils.get_headers()

        try:
            # 建立数据库链接
            repo = SupabaseDouyinRepository()

            # 获取视频数据
            video_data = await repo.get_by_aweme_id(aweme_id, user_id=user_id)
            if not video_data:
                logger.error(f"找不到视频数据: {aweme_id}")
                result.video_download_status = DownloadStatus.FAILED
                result.error = f"找不到视频数据: {aweme_id}"
                return result

            logger.info(f"准备下载Douyin {aweme_id} 的图片集")

            # create file path (返回完整路径和相对路径)
            full_path, relative_month = Utils.create_download_folder()
            logger.debug(f"下载基础路径: {full_path}, 相对路径: {relative_month}")

            # generate file name
            video_title = video_data.get("video_title", "undefined")
            file_name = Utils.concat_filename_safe_title(video_title, aweme_id)

            # 完整路径和相对路径
            sub_download_full_path = os.path.join(full_path, file_name)
            sub_download_relative_path = f"{relative_month}/{file_name}"  # 相对路径
            os.makedirs(sub_download_full_path, exist_ok=True)
            logger.success(f"Successfully created download file path: {sub_download_full_path}")

            video_urls = video_data.get("video_download_urls")
            image_urls = video_data.get("image_download_urls")

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
                        task = tg.create_task(DownloaderService.download_single_list_item(i,url_list, sub_download_full_path,file_name, headers))
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
                        task = tg.create_task(DownloaderService.download_single_list_item(i,url_list, sub_download_full_path,file_name, headers))
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
                    "download_path": sub_download_relative_path  # 使用相对路径
                }, user_id=user_id)
                result.video_download_status = DownloadStatus.COMPLETED

                # 记录成功日志
                if user_id:
                    await log_user_action(
                        user_id=user_id,
                        action="download",
                        message=f"图集下载成功: {video_title[:30]}... ({total_downloaded}个文件)",
                        status="success",
                        aweme_id=aweme_id
                    )

            else:
                error_msg = f"Douyin {file_name} 下载失败，共下载了 {video_downloaded_count}/{len(video_urls) if Utils.is_nested_list(video_urls) else 0} 个视频文件和 {image_downloaded_count}/{len(image_urls) if Utils.is_nested_list(image_urls) else 0} 个图片文件。Download Path: {sub_download_full_path}"
                logger.error(error_msg)
                await repo.update(aweme_id, {
                    "video_download_status": DownloadStatus.FAILED,
                    "error_message": error_msg
                }, user_id=user_id)
                result.video_download_status = DownloadStatus.FAILED
                result.error = error_msg

                # 记录失败日志
                if user_id:
                    await log_user_action(
                        user_id=user_id,
                        action="download",
                        message=f"图集下载失败: {video_title[:30]}...",
                        status="error",
                        aweme_id=aweme_id,
                        details={"error": error_msg[:200]}
                    )

        except Exception as e:
            logger.error(f"douyin {aweme_id} 下载处理出错: {str(e)}")
            result.error = str(e)
            result.video_download_status = DownloadStatus.FAILED
            return result
        return result

    @staticmethod
    async def download_music_by_aweme_id(*, aweme_id, user_id: str = None) -> DownloadMusicResult:
        """
        只下载音乐文件

        Args:
            aweme_id: 视频ID aweme_id
            user_id: 用户ID（用于数据隔离）

        Returns:
            DownloadMusicResult: 下载结果信息
        """
        # 初始化结果对象
        result = DownloadMusicResult.model_construct()
        headers = Utils.get_headers()

        try:
            repo = SupabaseDouyinRepository()

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

            # 创建下载路径 (返回完整路径和相对路径)
            full_path, relative_month = Utils.create_download_folder()

            # 从字典中获取音乐名称
            music_name = music_data.get("music_name")
            if not music_name:
                music_name = f"{aweme_id}_music"

            # 生成音乐文件路径 (完整路径和相对路径)
            music_full_path = os.path.join(full_path, f"{music_name}.mp3")
            music_relative_path = f"{relative_month}/{music_name}.mp3"

            # 尝试下载音乐
            for url in music_urls:
                if await DownloaderService.download_file(url, music_full_path, headers):
                    try:
                        await repo.mark_music_as_downloaded(aweme_id)
                    except Exception as e:
                        logger.error(f"Marked music {aweme_id} as downloaded successfully, but failed to update the database: {e}.")
                        result.error += f"Marked music {aweme_id} as downloaded successfully, but failed to update the database: {e}."

                    result.music_path = music_relative_path  # 返回相对路径
                    result.music_download_status = DownloadStatus.COMPLETED

                    logger.success(f"Music for video {aweme_id} downloaded successfully and saved to {music_full_path}.")
                    break

            if result.music_download_status != DownloadStatus.COMPLETED:
                logger.error(f"All download URLs for the music of video {aweme_id} failed.")
                result.music_download_status = DownloadStatus.FAILED
                result.error += f"All download URLs for the music of video {aweme_id} failed."

                # 更新数据库状态为"失败"
                await repo.update(aweme_id, {
                    "music_download_status": DownloadStatus.FAILED,
                    "error_message": result.error
                }, user_id=user_id)

            return result

        except Exception as e:
            logger.error(f"Music download processing error: {str(e)}")
            result.error = str(e)
            return result

    @staticmethod
    async def download_single_list_item(i, url_list, sub_download_full_path, file_name, headers):
        """下载单个列表项（视频或图片），尝试多个URL直到成功"""
        # 确定文件扩展名
        extension = ".mp4" if "mp4" in str(url_list) else ".jpg"
        file_path = os.path.join(sub_download_full_path, f"{file_name}_{i}{extension}")

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

    @staticmethod
    async def download_cover_by_aweme_id(aweme_id: str, user_id: str = None) -> DownloadCoverResult:
        """
        下载视频封面图片

        Args:
            aweme_id: 视频ID
            user_id: 用户ID（用于数据隔离）

        Returns:
            DownloadCoverResult: 下载结果信息
        """
        result = DownloadCoverResult.model_construct()
        headers = Utils.get_headers()

        try:
            repo = SupabaseDouyinRepository()

            # 获取视频数据
            video_data = await repo.get_by_aweme_id(aweme_id, user_id=user_id)
            if not video_data:
                logger.error(f"找不到视频数据: {aweme_id}")
                result.cover_download_status = DownloadStatus.FAILED
                result.error = f"找不到视频数据: {aweme_id}"
                return result

            logger.info(f"准备下载视频 {aweme_id} 的封面")

            # 获取封面 URL 列表
            cover_urls = video_data.get("cover_urls", [])
            if not cover_urls:
                logger.warning(f"视频 {aweme_id} 没有封面 URL")
                result.cover_download_status = DownloadStatus.SKIPPED
                result.error = "没有封面 URL"
                return result

            # 创建下载路径 (返回完整路径和相对路径)
            full_path, relative_month = Utils.create_download_folder()

            # 生成文件名
            video_title = video_data.get("video_title", "undefined")
            file_name = Utils.concat_filename_safe_title(video_title, aweme_id)
            cover_full_path = os.path.join(full_path, f"{file_name}_cover.jpg")
            cover_relative_path = f"{relative_month}/{file_name}_cover.jpg"

            # 尝试下载封面（尝试多个 URL）
            for url in cover_urls:
                if await DownloaderService.download_file(url, cover_full_path, headers):
                    # 更新数据库（存储相对路径）
                    try:
                        await repo.update(aweme_id, {
                            "cover_download_status": DownloadStatus.COMPLETED.value,
                            "cover_download_path": cover_relative_path  # 使用相对路径
                        }, user_id=user_id)
                    except Exception as e:
                        logger.error(f"更新封面下载状态失败: {e}")

                    result.cover_download_status = DownloadStatus.COMPLETED
                    result.cover_path = cover_relative_path  # 返回相对路径
                    logger.success(f"封面 {aweme_id} 下载成功: {cover_full_path}")

                    # 记录成功日志
                    if user_id:
                        await log_user_action(
                            user_id=user_id,
                            action="download",
                            message=f"封面下载成功: {video_title[:30]}...",
                            status="success",
                            aweme_id=aweme_id
                        )
                    return result

            # 所有 URL 都失败
            logger.error(f"视频 {aweme_id} 的所有封面 URL 都下载失败")
            result.cover_download_status = DownloadStatus.FAILED
            result.error = "所有封面 URL 下载失败"

            await repo.update(aweme_id, {
                "cover_download_status": DownloadStatus.FAILED.value,
                "error_message": result.error
            }, user_id=user_id)

            # 记录失败日志
            if user_id:
                await log_user_action(
                    user_id=user_id,
                    action="download",
                    message=f"封面下载失败: {video_title[:30]}...",
                    status="error",
                    aweme_id=aweme_id
                )

            return result

        except Exception as e:
            logger.error(f"下载封面出错: {str(e)}")
            result.cover_download_status = DownloadStatus.FAILED
            result.error = str(e)
            return result

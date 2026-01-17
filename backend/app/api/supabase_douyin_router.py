# backend/app/api/supabase_douyin_router.py

"""
Supabase 抖音视频路由

基于 Supabase 的抖音视频处理 API 端点。
需要认证才能访问（支持 JWT 或 API Key）。
"""

import asyncio
from pathlib import Path
from typing import Optional
from datetime import datetime
from fastapi import APIRouter, HTTPException, Header, Query, BackgroundTasks
from fastapi.responses import FileResponse
from pydantic import BaseModel
from loguru import logger

from app.core.enums import DownloadStatus
from app.core.deps import AuthDep
from app.core.utils import Utils
from app.repositories.supabase_douyin_repository import SupabaseDouyinRepository
from app.repositories.user_logs_repository import UserLogsRepository, log_user_action
from app.services.douyin_analysis import DouyinAnalysis
from app.services.douyin_parser import DouyinParser
from app.services.supabase_douyin_service import SupabaseDouyinService


router = APIRouter(prefix="/douyin")

# 定义 API 分组标签
TAGS_FETCH = ["抖音采集"]      # 从抖音获取并解析视频
TAGS_VIDEOS = ["视频管理"]     # 已存储数据的 CRUD
TAGS_STATS = ["统计分析"]      # 统计和分析
TAGS_DOWNLOAD = ["下载管理"]   # 下载相关操作
TAGS_LOGS = ["日志"]           # 用户操作日志


# ============================================
# 请求/响应模型
# ============================================

class VideoFetchRequest(BaseModel):
    """视频获取请求"""
    url: str
    video_bool: bool = True
    music_bool: bool = False
    cover_bool: bool = True
    video_categories: Optional[str] = None
    use_celery: bool = False  # 是否使用 Celery 异步任务


class VideoSearchRequest(BaseModel):
    """视频搜索请求"""
    keyword: Optional[str] = None
    author: Optional[str] = None
    status: Optional[str] = None
    aweme_type: Optional[str] = None
    category: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None


class BatchFetchRequest(BaseModel):
    """批量获取请求"""
    urls: list[str]
    video_bool: bool = True
    music_bool: bool = False
    cover_bool: bool = True
    video_categories: Optional[str] = None
    use_celery: bool = False  # 是否使用 Celery 异步任务


# ============================================
# 路由端点
# ============================================

@router.post("/fetch", tags=TAGS_FETCH)
async def fetch_video(request: VideoFetchRequest, background_tasks: BackgroundTasks, auth: AuthDep):
    """
    获取单个抖音视频

    从抖音链接解析视频信息并自动下载。

    - **url**: 抖音视频链接（支持分享链接）
    - **video_bool**: 是否下载视频文件
    - **music_bool**: 是否下载背景音乐
    - **video_categories**: 视频分类标签
    - **use_celery**: 是否使用 Celery 异步任务（默认 False）

    需要认证：Bearer Token 或 API Key（需要 `douyin:fetch` 权限）
    """
    try:
        # 从分享文本中提取有效 URL
        try:
            valid_urls = Utils.extract_valid_url(request.url)
            url = valid_urls[0]  # 取第一个有效 URL
        except ValueError:
            raise HTTPException(status_code=400, detail="无法从输入中提取有效的抖音链接")

        logger.info(f"用户 {auth.user_id} 开始获取视频: {url}")

        # 如果使用 Celery 异步任务
        if request.use_celery:
            from app.tasks.parse_tasks import parse_single_link_task

            task = parse_single_link_task.delay(
                url=url,
                user_id=auth.user_id,
                video_bool=request.video_bool,
                music_bool=request.music_bool,
                cover_bool=request.cover_bool,
                categories=request.video_categories
            )

            # 记录日志
            background_tasks.add_task(
                log_user_action,
                user_id=auth.user_id,
                action="fetch",
                message=f"提交 Celery 任务: {url[:30]}...",
                status="pending"
            )

            return {
                "success": True,
                "message": "任务已提交到 Celery 队列",
                "task_id": task.id,
                "url": url,
                "use_celery": True,
            }

        # 默认使用 BackgroundTasks（原有逻辑）
        # 获取视频数据
        aweme_detail = await DouyinAnalysis.fetch_one_video(url)

        if not aweme_detail:
            raise HTTPException(status_code=404, detail="无法获取视频信息")

        # 解析视频数据
        parsed_data = await DouyinParser.parse_aweme_detail(
            aweme_detail=aweme_detail,
            valid_url=url,
            download_video=request.video_bool,
            download_music=request.music_bool,
            download_cover=request.cover_bool,
            categories=request.video_categories
        )

        if not parsed_data:
            raise HTTPException(status_code=500, detail="视频解析失败")

        aweme_id = parsed_data.get("aweme_id")

        # 添加用户 ID
        parsed_data["user_id"] = auth.user_id

        # 后台处理存储和下载
        background_tasks.add_task(
            SupabaseDouyinService.process_video,
            aweme_id,
            parsed_data
        )

        # 记录日志
        video_title = parsed_data.get("video_title", "")[:30]
        background_tasks.add_task(
            log_user_action,
            user_id=auth.user_id,
            action="fetch",
            message=f"获取视频: {video_title}...",
            status="pending",
            aweme_id=aweme_id
        )

        # 处理 datetime 对象转字符串
        video_created_time = parsed_data.get("video_created_time")
        if video_created_time and hasattr(video_created_time, 'isoformat'):
            video_created_time = video_created_time.isoformat()

        # 返回完整的解析数据供前端显示
        return {
            "success": True,
            "message": "视频处理任务已提交",
            "aweme_id": aweme_id,
            "video_title": parsed_data.get("video_title"),
            "author": parsed_data.get("author"),
            "aweme_type": parsed_data.get("aweme_type"),
            # 视频/封面 URL
            "video_download_urls": parsed_data.get("video_download_urls", []),
            "cover_urls": parsed_data.get("cover_urls", []),
            "image_download_urls": parsed_data.get("image_download_urls", []),
            # 统计数据 (使用 video_ 前缀以匹配前端类型)
            "video_digg_count": parsed_data.get("video_digg_count", 0),
            "video_comment_count": parsed_data.get("video_comment_count", 0),
            "video_share_count": parsed_data.get("video_share_count", 0),
            "video_collect_count": parsed_data.get("video_collect_count", 0),
            # 视频信息
            "video_duration": parsed_data.get("video_duration", "0"),
            "video_created_time": video_created_time,
            "video_desc": parsed_data.get("video_desc"),
            "video_categories": parsed_data.get("video_categories"),
            "video_original_url": parsed_data.get("video_original_url"),
            "video_resolution": parsed_data.get("video_resolution"),
            # 下载状态
            "video_download_status": parsed_data.get("video_download_status", "PENDING"),
        }

    except HTTPException:
        # 记录失败日志
        background_tasks.add_task(
            log_user_action,
            user_id=auth.user_id,
            action="fetch",
            message=f"获取视频失败: {request.url[:30]}...",
            status="error"
        )
        raise
    except Exception as e:
        logger.error(f"获取视频失败: {e}")
        # 记录失败日志
        background_tasks.add_task(
            log_user_action,
            user_id=auth.user_id,
            action="fetch",
            message=f"获取视频失败: {str(e)[:50]}",
            status="error"
        )
        raise HTTPException(status_code=500, detail=f"获取视频失败: {str(e)}")


@router.post("/fetch/batch", tags=TAGS_FETCH)
async def fetch_videos_batch(request: BatchFetchRequest, background_tasks: BackgroundTasks, auth: AuthDep):
    """
    批量获取抖音视频

    一次性解析多个抖音链接，适合批量采集。

    - **urls**: 抖音视频链接列表
    - **video_bool**: 是否下载视频文件
    - **music_bool**: 是否下载背景音乐
    - **video_categories**: 视频分类标签
    - **use_celery**: 是否使用 Celery 异步任务（默认 False）

    需要认证：Bearer Token 或 API Key（需要 `douyin:fetch:batch` 权限）
    """
    # 如果使用 Celery 异步任务
    if request.use_celery:
        from app.tasks.parse_tasks import parse_batch_links_task

        task = parse_batch_links_task.delay(
            urls=request.urls,
            user_id=auth.user_id,
            video_bool=request.video_bool,
            music_bool=request.music_bool,
            cover_bool=request.cover_bool,
            categories=request.video_categories
        )

        # 记录日志
        background_tasks.add_task(
            log_user_action,
            user_id=auth.user_id,
            action="fetch_batch",
            message=f"提交批量 Celery 任务: {len(request.urls)} 个链接",
            status="pending"
        )

        return {
            "success": True,
            "message": f"批量任务已提交到 Celery 队列",
            "task_id": task.id,
            "total": len(request.urls),
            "use_celery": True,
        }

    # 默认使用 BackgroundTasks（原有逻辑）
    results = []
    errors = []

    for raw_url in request.urls:
        try:
            # 从分享文本中提取有效 URL
            try:
                valid_urls = Utils.extract_valid_url(raw_url)
                url = valid_urls[0]
            except ValueError:
                errors.append({"url": raw_url, "error": "无法提取有效链接"})
                continue

            aweme_detail = await DouyinAnalysis.fetch_one_video(url)

            if aweme_detail:
                parsed_data = await DouyinParser.parse_aweme_detail(
                    aweme_detail=aweme_detail,
                    valid_url=url,
                    download_video=request.video_bool,
                    download_music=request.music_bool,
                    download_cover=request.cover_bool,
                    categories=request.video_categories
                )

                if parsed_data:
                    aweme_id = parsed_data.get("aweme_id")
                    # 添加用户 ID
                    parsed_data["user_id"] = auth.user_id
                    background_tasks.add_task(
                        SupabaseDouyinService.process_video,
                        aweme_id,
                        parsed_data
                    )

                    # 处理 datetime 对象转字符串
                    video_created_time = parsed_data.get("video_created_time")
                    if video_created_time and hasattr(video_created_time, 'isoformat'):
                        video_created_time = video_created_time.isoformat()

                    # 返回完整数据，供前端立即显示
                    results.append({
                        "url": url,
                        "aweme_id": aweme_id,
                        "status": "submitted",
                        "data": {
                            "aweme_id": aweme_id,
                            "video_title": parsed_data.get("video_title"),
                            "video_desc": parsed_data.get("video_desc"),
                            "author": parsed_data.get("author"),
                            "aweme_type": parsed_data.get("aweme_type"),
                            "video_download_urls": parsed_data.get("video_download_urls", []),
                            "cover_urls": parsed_data.get("cover_urls", []),
                            "video_digg_count": parsed_data.get("video_digg_count", 0),
                            "video_comment_count": parsed_data.get("video_comment_count", 0),
                            "video_share_count": parsed_data.get("video_share_count", 0),
                            "video_collect_count": parsed_data.get("video_collect_count", 0),
                            "video_duration": parsed_data.get("video_duration", "0"),
                            "video_created_time": video_created_time,
                            "image_urls": parsed_data.get("image_urls", []),
                            "sec_uid": parsed_data.get("sec_uid"),
                            "unique_id": parsed_data.get("unique_id"),
                            "valid_url": url,
                            "user_id": auth.user_id,
                        }
                    })
                else:
                    errors.append({"url": url, "error": "解析失败"})
            else:
                errors.append({"url": url, "error": "无法获取视频信息"})

        except Exception as e:
            errors.append({"url": url, "error": str(e)})

    return {
        "success": True,
        "total": len(request.urls),
        "submitted": len(results),
        "failed": len(errors),
        "results": results,
        "errors": errors
    }


@router.get("/videos", tags=TAGS_VIDEOS)
async def list_videos(
    auth: AuthDep,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    order_by: str = Query("created_at"),
    ascending: bool = Query(False)
):
    """
    获取视频列表

    查询已采集并存储的视频数据。

    - **skip**: 跳过记录数（分页）
    - **limit**: 返回记录数（1-100）
    - **order_by**: 排序字段
    - **ascending**: 是否升序

    需要认证：Bearer Token 或 API Key（需要 `douyin:videos:read` 权限）
    """
    try:
        repo = SupabaseDouyinRepository()
        videos = await repo.get_all(
            user_id=auth.user_id,
            skip=skip,
            limit=limit,
            order_by=order_by,
            ascending=ascending
        )
        return {"success": True, "count": len(videos), "videos": videos}
    except Exception as e:
        logger.error(f"获取视频列表失败: {e}")
        raise HTTPException(status_code=500, detail="获取视频列表失败")


@router.get("/videos/{aweme_id}", tags=TAGS_VIDEOS)
async def get_video(aweme_id: str, auth: AuthDep):
    """
    获取视频详情

    根据 aweme_id 获取单个视频的完整信息。

    - **aweme_id**: 视频唯一标识

    需要认证：Bearer Token 或 API Key（需要 `douyin:videos:read` 权限）
    """
    try:
        repo = SupabaseDouyinRepository()
        video = await repo.get_by_aweme_id(aweme_id, user_id=auth.user_id)

        if not video:
            raise HTTPException(status_code=404, detail="视频不存在")

        return {"success": True, "video": video}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取视频详情失败: {e}")
        raise HTTPException(status_code=500, detail="获取视频详情失败")


@router.delete("/videos/{aweme_id}", tags=TAGS_VIDEOS)
async def delete_video(
    aweme_id: str,
    background_tasks: BackgroundTasks,
    auth: AuthDep,
    delete_files: bool = Query(False, description="是否同时删除本地已下载的文件")
):
    """
    删除视频记录

    从数据库中删除视频记录，可选择同时删除本地已下载的文件。

    - **aweme_id**: 视频唯一标识
    - **delete_files**: 是否删除本地文件（默认 False）

    需要认证：Bearer Token 或 API Key（需要 `douyin:videos:write` 权限）
    """
    try:
        repo = SupabaseDouyinRepository()

        # 先获取视频信息用于日志和文件删除
        video = await repo.get_by_aweme_id(aweme_id, user_id=auth.user_id)
        if not video:
            raise HTTPException(status_code=404, detail="视频不存在")

        video_title = video.get("video_title", aweme_id)[:30] if video else aweme_id
        files_deleted = []

        # 删除本地文件
        if delete_files:
            import shutil

            # 删除视频/图片文件
            download_path = video.get("download_path")
            if download_path:
                path = Path(download_path)
                if path.exists():
                    if path.is_dir():
                        shutil.rmtree(path)
                        files_deleted.append(f"目录: {path.name}")
                    else:
                        path.unlink()
                        files_deleted.append(f"文件: {path.name}")

            # 删除封面文件
            cover_path = video.get("cover_download_path")
            if cover_path:
                path = Path(cover_path)
                if path.exists():
                    path.unlink()
                    files_deleted.append(f"封面: {path.name}")

        # 删除数据库记录
        result = await repo.delete(aweme_id, user_id=auth.user_id)

        if not result:
            raise HTTPException(status_code=404, detail="删除数据库记录失败")

        # 构建日志消息
        log_message = f"删除视频: {video_title}..."
        if files_deleted:
            log_message += f" (已删除 {len(files_deleted)} 个本地文件)"

        # 记录删除日志
        background_tasks.add_task(
            log_user_action,
            user_id=auth.user_id,
            action="delete",
            message=log_message,
            status="success",
            aweme_id=aweme_id,
            details={"files_deleted": files_deleted} if files_deleted else None
        )

        return {
            "success": True,
            "message": "视频已删除",
            "files_deleted": files_deleted
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除视频失败: {e}")
        raise HTTPException(status_code=500, detail="删除视频失败")


@router.post("/videos/search", tags=TAGS_VIDEOS)
async def search_videos(
    request: VideoSearchRequest,
    auth: AuthDep,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100)
):
    """
    搜索视频

    支持多条件组合搜索已存储的视频。

    - **keyword**: 关键词（标题/描述）
    - **author**: 作者名称
    - **status**: 下载状态
    - **aweme_type**: 视频类型
    - **category**: 分类标签
    - **start_date/end_date**: 时间范围

    需要认证：Bearer Token 或 API Key（需要 `douyin:search` 权限）
    """
    try:
        repo = SupabaseDouyinRepository()

        status = None
        if request.status:
            try:
                status = DownloadStatus(request.status)
            except ValueError:
                pass

        videos = await repo.search(
            user_id=auth.user_id,
            keyword=request.keyword,
            author=request.author,
            status=status,
            aweme_type=request.aweme_type,
            category=request.category,
            start_date=request.start_date,
            end_date=request.end_date,
            skip=skip,
            limit=limit
        )

        return {"success": True, "count": len(videos), "videos": videos}
    except Exception as e:
        logger.error(f"搜索视频失败: {e}")
        raise HTTPException(status_code=500, detail="搜索视频失败")


@router.get("/statistics", tags=TAGS_STATS)
async def get_statistics(auth: AuthDep):
    """
    获取统计信息

    返回视频总数、下载状态分布、类型分布等统计数据。

    需要认证：Bearer Token 或 API Key（需要 `douyin:statistics` 权限）
    """
    try:
        repo = SupabaseDouyinRepository()
        stats = await repo.get_statistics(user_id=auth.user_id)
        return {"success": True, "statistics": stats}
    except Exception as e:
        logger.error(f"获取统计信息失败: {e}")
        raise HTTPException(status_code=500, detail="获取统计信息失败")


@router.get("/pending", tags=TAGS_DOWNLOAD)
async def get_pending_downloads(auth: AuthDep, limit: int = Query(100, ge=1, le=500)):
    """
    获取待下载列表

    返回等待下载的视频列表。

    - **limit**: 返回记录数（最大500）

    需要认证：Bearer Token 或 API Key（需要 `douyin:videos:read` 权限）
    """
    try:
        repo = SupabaseDouyinRepository()
        videos = await repo.get_pending_downloads(
            user_id=auth.user_id,
            status=DownloadStatus.PENDING,
            limit=limit
        )
        return {"success": True, "count": len(videos), "videos": videos}
    except Exception as e:
        logger.error(f"获取待下载列表失败: {e}")
        raise HTTPException(status_code=500, detail="获取待下载列表失败")


@router.post("/retry/{aweme_id}", tags=TAGS_DOWNLOAD)
async def retry_download(aweme_id: str, background_tasks: BackgroundTasks, auth: AuthDep):
    """
    重试下载

    重新触发下载失败的视频。

    - **aweme_id**: 视频唯一标识

    需要认证：Bearer Token 或 API Key（需要 `douyin:retry` 权限）
    """
    try:
        repo = SupabaseDouyinRepository()
        video = await repo.get_by_aweme_id(aweme_id, user_id=auth.user_id)

        if not video:
            raise HTTPException(status_code=404, detail="视频不存在")

        video_title = video.get("video_title", aweme_id)[:30]

        # 重置下载状态
        await repo.update(aweme_id, {
            "video_download_status": DownloadStatus.PENDING.value,
            "error_message": None
        }, user_id=auth.user_id)

        # 添加后台下载任务（传递 user_id 以实现数据隔离）
        from app.services.downloader import DownloaderService
        background_tasks.add_task(
            DownloaderService.download_video_by_aweme_id,
            aweme_id,
            user_id=auth.user_id
        )

        # 记录重试日志
        background_tasks.add_task(
            log_user_action,
            user_id=auth.user_id,
            action="retry",
            message=f"重试下载: {video_title}...",
            status="pending",
            aweme_id=aweme_id
        )

        return {"success": True, "message": "下载任务已重新提交"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"重试下载失败: {e}")
        raise HTTPException(status_code=500, detail="重试下载失败")


@router.get("/download/{aweme_id}", tags=TAGS_DOWNLOAD)
async def download_video_file(aweme_id: str, auth: AuthDep):
    """
    下载视频文件

    返回视频文件供浏览器下载（设置 Content-Disposition: attachment）。

    - **aweme_id**: 视频唯一标识

    需要认证：Bearer Token 或 API Key（需要 `douyin:videos:read` 权限）
    """
    try:
        repo = SupabaseDouyinRepository()
        video = await repo.get_by_aweme_id(aweme_id, user_id=auth.user_id)

        if not video:
            raise HTTPException(status_code=404, detail="视频不存在")

        download_path = video.get("download_path")
        if not download_path:
            raise HTTPException(status_code=404, detail="视频文件路径不存在")

        file_path = Path(download_path)
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="视频文件不存在")

        # 生成下载文件名
        video_title = video.get("video_title", aweme_id)
        # 清理文件名中的非法字符
        safe_title = "".join(c for c in video_title if c.isalnum() or c in (' ', '-', '_', '.')).strip()
        if not safe_title:
            safe_title = aweme_id
        filename = f"{safe_title}.mp4"

        return FileResponse(
            path=str(file_path),
            filename=filename,
            media_type="video/mp4",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"'
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"下载视频文件失败: {e}")
        raise HTTPException(status_code=500, detail="下载视频文件失败")


@router.get("/download/{aweme_id}/cover", tags=TAGS_DOWNLOAD)
async def download_cover_file(aweme_id: str, auth: AuthDep):
    """
    下载封面文件

    返回封面图片供浏览器下载。

    - **aweme_id**: 视频唯一标识

    需要认证：Bearer Token 或 API Key（需要 `douyin:videos:read` 权限）
    """
    try:
        repo = SupabaseDouyinRepository()
        video = await repo.get_by_aweme_id(aweme_id, user_id=auth.user_id)

        if not video:
            raise HTTPException(status_code=404, detail="视频不存在")

        cover_path = video.get("cover_download_path")
        if not cover_path:
            raise HTTPException(status_code=404, detail="封面文件路径不存在")

        file_path = Path(cover_path)
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="封面文件不存在")

        # 生成下载文件名
        video_title = video.get("video_title", aweme_id)
        safe_title = "".join(c for c in video_title if c.isalnum() or c in (' ', '-', '_', '.')).strip()
        if not safe_title:
            safe_title = aweme_id
        filename = f"{safe_title}_cover.jpg"

        return FileResponse(
            path=str(file_path),
            filename=filename,
            media_type="image/jpeg",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"'
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"下载封面文件失败: {e}")
        raise HTTPException(status_code=500, detail="下载封面文件失败")


@router.get("/logs", tags=TAGS_LOGS)
async def get_user_logs(
    auth: AuthDep,
    limit: int = Query(20, ge=1, le=100),
    action: Optional[str] = Query(None, description="筛选操作类型")
):
    """
    获取用户操作日志

    返回用户最近的操作日志记录。

    - **limit**: 返回数量（1-100）
    - **action**: 筛选特定操作类型（fetch, download, delete, retry, update）

    需要认证：Bearer Token 或 API Key
    """
    try:
        repo = UserLogsRepository()
        logs = await repo.get_recent(
            user_id=auth.user_id,
            limit=limit,
            action=action
        )
        return {"success": True, "count": len(logs), "logs": logs}
    except Exception as e:
        logger.error(f"获取用户日志失败: {e}")
        raise HTTPException(status_code=500, detail="获取用户日志失败")

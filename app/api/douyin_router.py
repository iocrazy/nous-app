from fastapi import APIRouter, Depends, HTTPException, status, Security, BackgroundTasks, Body
from fastapi.security import OAuth2PasswordRequestForm
from typing import Annotated
from loguru import logger
import datetime
from typing import Optional

from app.core.deps import AsyncSessionDep
from app.repositories.douyin_repository import DouyinRepository
from app.core.utils import Utils
from app.services.douyin_analysis import DouyinAnalysis
from app.schemas.douyin import DouyinCreate
from app.core.enums import DownloadStatus
from app.services.downloader import DownloaderService
from app.core.config import settings
from app.services.notion_service import NotionService
from app.services.douyin_parser import DouyinParser
from app.services.douyin_service import DouyinService
from app.schemas.douyin import VideoFetchRequest

router = APIRouter(
    responses={401: {"description": "Unauthorized"}},
)


@router.post("/fetch_one_video")
async def fetch_one_video(*,
                          request_body: VideoFetchRequest = Body(None),
                          url: Optional[str] = None,
                          video_bool: Optional[bool] = True,
                          music_bool: Optional[bool] = False,
                          video_categories: Optional[str] = None,
                          background_tasks: BackgroundTasks):
    """
    获取单个视频
    
    Args:
        request_body: 请求体，包含视频URL和下载选项
        url: 抖音视频URL
        video_bool: 是否下载视频，默认为True
        music_bool: 是否下载音乐，默认为False
        video_categories: 视频分类
        background_tasks: 后台任务
    """
    try:
        # 记录开始时间
        start_time = datetime.datetime.now()
        if request_body:
            video_url = request_body.url
            video_bool = request_body.video_bool
            music_bool = request_body.music_bool
            categories = request_body.video_categories

            logger.debug(f"请求体参数值: video_bool={video_bool}, music_bool={music_bool}, categories={categories}")
        elif url:
            video_url = url
            video_bool = video_bool
            music_bool = music_bool
            categories = video_categories
        else:
            raise HTTPException(status_code=422, detail="Missing required parameter: url")

        # 提取有效URL
        valid_url = Utils.extract_valid_url(video_url)[0]

        # 获取原始数据
        raw_data = await DouyinAnalysis.fetch_one_video(valid_url)

        # 检查数据有效性和aweme_id
        if not raw_data or not raw_data.get("aweme_id"):
            logger.error("获取抖音数据失败或无法获取视频ID")
            return {"success": False, "message": "获取抖音数据失败或无法获取视频ID"}

        # 获取aweme_id
        aweme_id = raw_data.get("aweme_id")

        # 解析数据 - 不包含下载状态检查
        parsed_data = await DouyinParser.parse_aweme_detail(
            raw_data,
            valid_url,
            download_video=video_bool,
            download_music=music_bool,
            categories=categories
        )

        if not parsed_data:
            logger.error("解析抖音数据失败")
            return {"success": False, "message": "解析抖音数据失败"}

        # 生成短视频名称用于日志和消息
        video_title = parsed_data.get("video_title", "")
        short_video_name = video_title[:10] + "…" if len(video_title) > 10 else video_title

        # 记录处理时间
        process_time = (datetime.datetime.now() - start_time).total_seconds()
        logger.info(f"处理视频 {aweme_id} 数据耗时: {process_time:.2f}秒")

        # 将所有数据库操作和下载任务添加到后台任务
        background_tasks.add_task(
            DouyinService.process_video,
            aweme_id=aweme_id,
            parsed_data=parsed_data
        )

        return {
            "success": True,
            "message": f"视频{aweme_id} {short_video_name} 处理请求已接受，正在后台处理",
            "process_time": f"{process_time:.2f}秒"
        }

    except Exception as e:
        # 处理其他所有异常
        logger.error(f"Error fetching video: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching video: {str(e)}")


@router.post("/fetch_all_videos")
async def fetch_all_videos(urls: str, db: AsyncSessionDep):
    # TODO: Implement video fetching logic

    return {"message": "Video fetched successfully"}

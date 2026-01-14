
# app/schemas/douyin.py

"""
抖音视频数据验证模式模块

使用 Pydantic 2.0 定义抖音视频数据的验证模式，用于 API 请求和响应的数据验证。
包含创建、更新、查询等不同操作的数据模式。
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, HttpUrl,  model_validator

from app.core.enums import DownloadStatus


class DouyinBase(BaseModel):
    """抖音视频基础模式"""
    # 视频唯一标识
    aweme_id: str = Field(..., description="视频唯一标识")

    # 用户关联
    user_id: Optional[str] = Field(None, description="用户ID")

    # 视频互动数据
    video_digg_count: Optional[int] = Field(None, description="视频点赞数")
    video_comment_count: Optional[int] = Field(None, description="视频评论数")
    video_share_count: Optional[int] = Field(None, description="视频分享数")
    video_collect_count: Optional[int] = Field(None, description="视频收藏数")

    # 视频元数据
    video_original_url: str = Field(..., description="视频原始链接")
    video_duration: Optional[str] = Field(None, description="视频时长(秒)")
    video_resolution: Optional[str] = Field(None, description="视频分辨率")
    video_datasize: Optional[str] = Field(None, description="视频文件大小(字节)")
    video_hashtag_name: Optional[str] = Field(None, description="视频标签名称")
    video_created_time: Optional[datetime] = Field(None, description="视频创建时间")
    author: Optional[str] = Field(None, description="作者名称")
    video_title: Optional[str] = Field(None, description="视频标题")
    aweme_type: Optional[str] = Field(None, description="媒体类型")
    video_desc: Optional[str] = Field(None, description="视频描述")

    # 视频下载信息
    need_download_video: Optional[bool] = Field(None, description="是否需要下载视频")
    video_download_urls: Optional[list] = Field(None, description="视频下载URL")
    image_download_urls: Optional[list] = Field(None, description="图片下载URL")

    # 音频信息
    music_download_urls: Optional[list] = Field(None, description="音频下载URL")
    music_name: Optional[str] = Field(None, description="音频名称")
    need_download_music: Optional[bool] = Field(None, description="是否需要下载音频")

    # 封面信息
    cover_urls: Optional[list] = Field(None, description="封面URL列表")
    dynamic_cover_url: Optional[str] = Field(None, description="动态封面URL")
    need_download_cover: Optional[bool] = Field(None, description="是否需要下载封面")
    cover_download_status: Optional[DownloadStatus] = Field(None, description="封面下载状态")
    cover_download_path: Optional[str] = Field(None, description="封面下载路径")

    # 下载状态跟踪
    video_download_status: Optional[DownloadStatus] = Field(None, description="视频下载状态")
    music_download_status: Optional[DownloadStatus] = Field(None, description="音频下载状态")
    download_duration: Optional[float] = Field(None, description="下载耗时(秒)")
    download_path: Optional[str] = Field(None, description="下载路径")
    error_message: Optional[str] = Field(None, description="错误信息")
    download_time: Optional[datetime] = Field(None, description="下载时间")
    video_categories: Optional[str] = Field(None, description="视频分类")






class DouyinCreate(DouyinBase):
    """创建抖音视频的数据模式"""

    # 设置默认的下载状态
    video_download_status: Optional[DownloadStatus] = Field(
        default=DownloadStatus.PENDING, 
        description="视频下载状态"
    )

    music_download_status: Optional[DownloadStatus] = Field(
        default=DownloadStatus.SKIPPED,
        description="音频下载状态"
    )
    # todo ： to validate video_download_urls、image_download_urls
    @model_validator(mode='after')
    def validate_urls(self):
        """验证URL格式"""
        # 验证 video_original_url
        if self.video_original_url and not (
            self.video_original_url.startswith('http://') or
            self.video_original_url.startswith('https://')
        ):
            raise ValueError("video_original_url must be a valid HTTP or HTTPS URL")
    #
    #     验证 video_download_urls 中的每个 URL
    #     if self.video_download_urls:
    #         for i, url in enumerate(self.video_download_urls):
    #             if not (url.startswith('http://') or url.startswith('https://')):
    #                 raise ValueError(f"video_download_urls[{i}] must be a valid HTTP or HTTPS URL")
    #
        return self


class DouyinUpdate(DouyinBase):
    """更新抖音视频的数据模式"""
    # 覆盖基类中的必填字段，使其变为可选
    aweme_id: Optional[str] = Field(None, description="视频唯一标识")

    

    @model_validator(mode='after')
    def check_urls(self):
        """验证URL格式"""
        for url_field in ['video_download_url', 'download_url']:
            url = getattr(self, url_field, None)
            if url and not (url.startswith('http://') or url.startswith('https://')):
                raise ValueError(f"{url_field} must be a valid HTTP or HTTPS URL")
        return self


class DouyinInDB(DouyinBase):
    """数据库中的抖音视频数据模式"""
    id: int = Field(..., description="记录ID")
    video_download_status: DownloadStatus = Field(..., description="视频下载状态")
    music_download_status: DownloadStatus = Field(..., description="音频下载状态")
    download_time: Optional[datetime] = Field(None, description="下载时间")
    created_at: datetime = Field(..., description="创建时间")
    updated_at: datetime = Field(..., description="更新时间")
    
    model_config = {
        "from_attributes": True
    }


class DouyinSearchParams(BaseModel):
    """抖音视频搜索参数"""
    keyword: Optional[str] = Field(None, min_length=1, description="搜索关键词")
    author: Optional[str] = Field(None, description="作者名称")
    download_status: Optional[DownloadStatus] = Field(None, description="下载状态")
    is_downloaded: Optional[bool] = Field(None, description="是否已下载")
    created_after: Optional[datetime] = Field(None, description="创建时间晚于")
    created_before: Optional[datetime] = Field(None, description="创建时间早于")
    skip: int = Field(0, ge=0, description="跳过的记录数")
    limit: int = Field(100, ge=1, le=1000, description="返回的最大记录数")


class DownloadVideoResult(BaseModel):
    """下载结果数据模型"""
    video_download_status: DownloadStatus = Field(default=DownloadStatus.PENDING, description="下载状态")
    video_path: Optional[str] = Field(None, description="视频文件路径")
    download_duration: Optional[float] = Field(None, description="下载耗时(秒)")
    error: Optional[str] = Field(None, description="错误信息")
    warning: Optional[str] = Field(None, description="警告信息")

    @property
    def is_successful(self) -> bool:
        """判断下载是否成功"""
        return self.video_download_status == DownloadStatus.COMPLETED and self.error is None



class DownloadMusicResult(BaseModel):
    """下载结果数据模型"""
    music_download_status: DownloadStatus = Field(default=DownloadStatus.PENDING, description="音频下载状态")
    music_path: Optional[str] = Field(None, description="音频文件路径")
    music_downloaded: Optional[bool] = Field(default=False, description="是否已下载音频")
    error: Optional[str] = Field(None, description="错误信息")
    warning: Optional[str] = Field(None, description="警告信息")

    @property
    def is_successful(self) -> bool:
        """判断下载是否成功"""
        return self.music_download_status == DownloadStatus.COMPLETED and self.error is None

class DownloadImagesResult(BaseModel):
    """下载结果数据模型"""
    image_urls_list: list[str] = Field([], description="图片文件路径列表")
    video_download_status: DownloadStatus = Field(default=DownloadStatus.PENDING, description="视频下载状态")
    error: Optional[str] = Field(None, description="错误信息")
    warning: Optional[str] = Field(None, description="警告信息")


class DownloadCoverResult(BaseModel):
    """封面下载结果数据模型"""
    cover_download_status: DownloadStatus = Field(default=DownloadStatus.PENDING, description="封面下载状态")
    cover_path: Optional[str] = Field(None, description="封面文件路径")
    error: Optional[str] = Field(None, description="错误信息")

    @property
    def is_successful(self) -> bool:
        """判断下载是否成功"""
        return self.cover_download_status == DownloadStatus.COMPLETED and self.error is None


class VideoFetchRequest(BaseModel):
    """抖音视频获取请求模型"""
    url: str = Field(..., description="包含抖音视频URL的文本")
    video_bool: bool = Field(default=True, description="是否下载视频")
    music_bool: bool = Field(default=False, description="是否下载音频")
    video_categories: Optional[str] = Field(None, description="视频分类")

    @model_validator(mode='after')
    def validate_url(self):
        """验证URL格式 - 检查文本中是否包含有效URL"""
        if not self.url:
            raise ValueError("url field cannot be empty")

        # 不在这里验证URL格式，而是依赖Utils.extract_valid_url方法
        return self

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "url": "视频描述 https://v.douyin.com/example/ 复制此链接...",
                    "video_bool": True,
                    "music_bool": True,
                    "video_categories": "c1"
                }
            ]
        }
    }

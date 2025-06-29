from sqlalchemy import String, Boolean, Text, Integer, Enum, DateTime, Float, TypeDecorator
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime

from app.models.base import DBModel,JsonList
from app.core.enums import DownloadStatus

# 自定义 SQLAlchemy 类型，用于处理 JSON 列表


class Douyin(DBModel):
    """抖音视频数据模型"""

    __tablename__ = 'douyin_data'

    # 视频唯一标识
    aweme_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True, unique=True)

    # 视频互动数据
    video_digg_count: Mapped[int] = mapped_column(Integer, nullable=True, comment="点赞数")
    video_comment_count: Mapped[int] = mapped_column(Integer, nullable=True, comment="评论数")
    video_share_count: Mapped[int] = mapped_column(Integer, nullable=True, comment="分享数")
    video_collect_count: Mapped[int] = mapped_column(Integer, nullable=True, comment="收藏数")

    # 视频元数据
    video_original_url: Mapped[str] = mapped_column(String(255), nullable=False, comment="视频链接")
    video_duration: Mapped[str] = mapped_column(String(255), nullable=True, comment="视频时长(秒)")
    video_resolution: Mapped[str] = mapped_column(String(255), nullable=True, comment="视频分辨率")
    video_datasize: Mapped[str] = mapped_column(Integer, nullable=True, comment="视频文件大小(字节)")
    video_hashtag_name: Mapped[str] = mapped_column(Text, nullable=True, comment="视频标签名称")
    video_created_time: Mapped[datetime] = mapped_column(DateTime, nullable=True, comment="视频创建时间")
    author: Mapped[str] = mapped_column(String(255), nullable=True, comment="作者名称")
    video_title: Mapped[str] = mapped_column(Text, nullable=True, comment="视频标题")
    aweme_type: Mapped[str] = mapped_column(String(255), nullable=True, comment="媒体类型")

    # 视频下载信息 - 使用自定义类型自动处理 JSON 转换
    need_download_video: Mapped[bool] = mapped_column(Boolean, nullable=True, default=True, comment="是否需要下载视频")
    video_download_urls: Mapped[list[str]] = mapped_column(JsonList, nullable=True, comment="视频下载URL列表")
    image_download_urls: Mapped[list[str]] = mapped_column(JsonList, nullable=True, comment="图片下载URL列表")

    # 音频信息
    music_download_urls: Mapped[list[str]] = mapped_column(JsonList, nullable=True, comment="音频下载URL列表")
    music_name: Mapped[str] = mapped_column(String(255), nullable=True, comment="音频名称")
    need_download_music: Mapped[bool] = mapped_column(Boolean, nullable=True, default=False, comment="是否需要下载音频")


    # 下载状态跟踪
    video_download_status: Mapped[DownloadStatus] = mapped_column(Enum(DownloadStatus), nullable=False, default=DownloadStatus.PENDING, comment="下载状态")
    music_download_status: Mapped[DownloadStatus] = mapped_column(Enum(DownloadStatus), nullable=False, default=DownloadStatus.PENDING, comment="下载状态")
    download_duration: Mapped[float] = mapped_column(Float, nullable=True, comment="下载耗时(秒)")
    download_path: Mapped[str] = mapped_column(Text, nullable=True, comment="下载路径")
    error_message: Mapped[str] = mapped_column(Text, nullable=True, comment="错误信息")
    download_time: Mapped[datetime] = mapped_column(DateTime, nullable=True, comment="下载时间")
    video_categories:Mapped[str] = mapped_column(String(255), nullable=True, comment="视频分类")

    def __repr__(self):
        return f"<Douyin {self.aweme_id}: {self.video_title}>"


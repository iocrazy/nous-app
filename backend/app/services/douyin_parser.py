"""
抖音数据解析服务

负责解析和清洗从抖音API获取的原始数据，转换为应用内部使用的标准格式。
处理不同类型的媒体内容（视频、图文等）的数据提取和格式化。
"""

import datetime
from typing import Dict, Any ,Optional

from loguru import logger

from app.core.utils import Utils


class DouyinParser:
    """抖音数据解析服务，负责清洗和结构化抖音API返回的数据"""

    @staticmethod
    async def parse_aweme_detail(
        aweme_detail: Dict[str, Any],
        valid_url: str,
        download_video: bool = True,
        download_music: bool = False,
        download_cover: bool = True,
        categories: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        解析抖音视频详情数据，根据不同的媒体类型进行不同的处理
        
        Args:
            aweme_detail: 抖音API返回的原始数据
            valid_url: 有效的抖音视频URL
            download_video: 是否下载视频
            download_music: 是否下载音乐
            categories: 视频分类
            
        Returns:
            Dict[str, Any]: 结构化后的视频数据
        """
        if not aweme_detail:
            logger.error("无效的抖音数据")
            return {}
            
        # 提取视频基本信息
        aweme_type = aweme_detail.get("aweme_type")
        aweme_id = aweme_detail.get("aweme_id")

        # 根据不同的媒体类型进行不同的处理
        if aweme_type == 68:  # 图文类型
            logger.info(f"解析图文类型数据:aweme_id={aweme_id}, media_type={aweme_type}")
            return await DouyinParser._parse_image_text(aweme_detail, aweme_id, valid_url, download_video, download_music, download_cover, categories)
        elif aweme_type in (0, 4, 61):  # 视频类型
            logger.info(f"解析视频类型数据:aweme_id={aweme_id}, media_type={aweme_type}")
            return await DouyinParser._parse_video(aweme_detail, aweme_id, valid_url, download_video, download_music, download_cover, categories)
        elif aweme_type == 2:  # 图片合集
            logger.info(f"解析图片合集类型数据:aweme_id={aweme_id}, media_type={aweme_type}")
            return await DouyinParser._parse_image_collection(aweme_detail, aweme_id, valid_url, download_video, download_music, download_cover, categories)
        else:
            logger.warning(f"不支持的媒体类型: {aweme_type}")
            raise ValueError(f"不支持的媒体类型: {aweme_type}")
    
    @staticmethod
    def _extract_cover_urls(aweme_detail: Dict[str, Any]) -> Dict[str, Any]:
        """
        提取封面相关 URL

        Returns:
            Dict 包含 cover_urls 和 dynamic_cover_url
        """
        video_data = aweme_detail.get("video", {})

        # 收集所有封面 URL
        cover_urls = []

        # 原始封面（高清）
        origin_cover = video_data.get("origin_cover", {})
        if origin_cover and origin_cover.get("url_list"):
            cover_urls.extend(origin_cover.get("url_list", []))

        # 普通封面
        cover = video_data.get("cover", {})
        if cover and cover.get("url_list"):
            cover_urls.extend(cover.get("url_list", []))

        # 动态封面（GIF）
        dynamic_cover = video_data.get("dynamic_cover", {})
        dynamic_cover_url = None
        if dynamic_cover and dynamic_cover.get("url_list"):
            dynamic_cover_url = dynamic_cover.get("url_list", [])[0] if dynamic_cover.get("url_list") else None

        return {
            "cover_urls": cover_urls,
            "dynamic_cover_url": dynamic_cover_url
        }

    @staticmethod
    async def _parse_image_text(
        aweme_detail: Dict[str, Any],
        aweme_id: str,
        valid_url: str,
        download_video: bool,
        download_music: bool,
        download_cover: bool,
        categories: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        解析图文类型的抖音数据

        Args:
            aweme_detail: 抖音API返回的原始数据
            aweme_id: 抖音视频ID
            valid_url: 有效的抖音视频URL
            download_video: 是否需要下载视频
            download_music: 是否需要下载音乐
            categories: 视频分类

        Returns:
            Dict[str, Any]: 结构化后的图文数据
        """
        # 提取图片和视频URL
        images = aweme_detail.get("images", [])
        image_download_urls = []
        video_download_urls = []

        
        if images:
            for item in images:
                if not item.get("video", {}):
                    image_urls = item.get("download_url_list", [])
                    image_download_urls.append(image_urls)
                    logger.debug(f"image_url_list: {image_download_urls}")
                if item.get("video", {}):
                    video_urls = item.get("video", {}).get("play_addr", {}).get("url_list", [])
                    video_download_urls.append(video_urls)
                    logger.debug(f"video_url_list: {video_download_urls}")

        logger.debug(f"image_download_urls: {image_download_urls}")
        # 构建音乐名称
        music_author = aweme_detail.get("music", {}).get("author", "undefined")
        music_title = aweme_detail.get("music", {}).get("title", "undefined")
        music_name = f"{aweme_id}_{music_author}-{music_title}"
        video_desc = aweme_detail.get("desc", "undefined")

        # 提取封面 URL
        cover_data = DouyinParser._extract_cover_urls(aweme_detail)

        # 构建返回数据
        return {
            "aweme_id": aweme_id,
            "author": aweme_detail.get("author", {}).get("nickname"),
            "video_original_url": valid_url,
            "video_title": Utils.safe_filename(video_desc, 15),
            "video_desc": video_desc,
            "video_digg_count": aweme_detail.get("statistics", {}).get("digg_count"),
            "video_comment_count": aweme_detail.get("statistics", {}).get("comment_count"),
            "video_share_count": aweme_detail.get("statistics", {}).get("share_count"),
            "video_collect_count": aweme_detail.get("statistics", {}).get("collect_count"),
            "aweme_type": str(aweme_detail.get("aweme_type")),
            "video_created_time": datetime.datetime.fromtimestamp(aweme_detail.get("create_time")),
            "video_hashtag_name": Utils.concat_hashtag_name(aweme_detail),
            "video_datasize_bytes": 0,  # Image/video mixed type doesn't have single file size
            "image_download_urls": image_download_urls,
            "video_download_urls": video_download_urls,
            "music_download_urls": aweme_detail.get("music", {}).get("play_url", {}).get("url_list", None),
            "music_name": music_name,
            "need_download_video": download_video,
            "need_download_music": download_music,
            "need_download_cover": download_cover,
            "cover_urls": cover_data["cover_urls"],
            "dynamic_cover_url": cover_data["dynamic_cover_url"],
            "video_categories": categories,
        }
    
    @staticmethod
    async def _parse_video(
        aweme_detail: Dict[str, Any],
        aweme_id: str,
        original_url: str,
        download_video: bool,
        download_music: bool,
        download_cover: bool,
        categories: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        解析视频类型的抖音数据
        
        Args:
            aweme_detail: 抖音API返回的原始数据
            aweme_id: 抖音视频ID
            original_url: 有效的抖音视频URL
            download_video: 是否下载视频
            download_music: 是否下载音乐
            categories: 视频分类
            
        Returns:
            Dict[str, Any]: 结构化后的视频数据
        """
        # 提取音乐信息
        music_from = aweme_detail.get("music", {}).get("title")
        music_author = aweme_detail.get("music", {}).get("matched_pgc_sound", {}).get("author")
        music_title = aweme_detail.get("music", {}).get("matched_pgc_sound", {}).get("title")
        
        music_name = f"{aweme_id}_{music_author}-{music_title}" if music_author and music_title else f"{aweme_id}:{music_from}"
        video_desc = aweme_detail.get("desc", "undefined")

        # 提取封面 URL
        cover_data = DouyinParser._extract_cover_urls(aweme_detail)

        # 构建返回数据
        return {
            "aweme_id": aweme_id,
            "author": aweme_detail.get("author", {}).get("nickname"),
            "video_original_url": original_url,
            "video_title": Utils.safe_filename(video_desc, 15),
            "video_desc": aweme_detail.get("desc", "undefined"),
            "video_digg_count": aweme_detail.get("statistics", {}).get("digg_count"),
            "video_comment_count": aweme_detail.get("statistics", {}).get("comment_count"),
            "video_share_count": aweme_detail.get("statistics", {}).get("share_count"),
            "video_collect_count": aweme_detail.get("statistics", {}).get("collect_count"),
            "video_hashtag_name": Utils.concat_hashtag_name(aweme_detail),
            "aweme_type": str(aweme_detail.get("aweme_type")),
            "video_created_time": datetime.datetime.fromtimestamp(aweme_detail.get("create_time")),
            "video_datasize": Utils.format_file_size(aweme_detail.get("video", {}).get("bit_rate", [{}])[0].get("play_addr", {}).get("data_size", 0)),
            "video_datasize_bytes": aweme_detail.get("video", {}).get("bit_rate", [{}])[0].get("play_addr", {}).get("data_size", 0) or 0,
            "video_duration": Utils.format_duration(aweme_detail.get("video", {}).get("duration")),
            "video_resolution": f"{aweme_detail.get('video', {}).get('width')}:{aweme_detail.get('video', {}).get('height')}",
            "video_download_urls": aweme_detail.get("video", {}).get("play_addr", {}).get("url_list", None),
            "music_download_urls": aweme_detail.get("music", {}).get("play_url", {}).get("url_list", None),
            "music_name": music_name,
            "need_download_video": download_video,
            "need_download_music": download_music,
            "need_download_cover": download_cover,
            "cover_urls": cover_data["cover_urls"],
            "dynamic_cover_url": cover_data["dynamic_cover_url"],
            "video_categories": categories,
        }
    
    @staticmethod
    async def _parse_image_collection(
        aweme_detail: Dict[str, Any],
        aweme_id: str,
        valid_url: str,
        download_video: bool,
        download_music: bool,
        download_cover: bool,
        categories: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        解析图片合集类型的抖音数据

        Args:
            aweme_detail: 抖音API返回的原始数据
            aweme_id: 抖音视频ID
            valid_url: 有效的抖音视频URL
            download_video: 是否需要下载视频（图片）
            download_music: 是否需要下载音乐
            download_cover: 是否需要下载封面
            categories: 视频分类

        Returns:
            Dict[str, Any]: 结构化后的图片合集数据
        """
        # 提取图片 URL（图片合集）
        images = aweme_detail.get("images", [])
        image_download_urls = []

        if images:
            for item in images:
                image_urls = item.get("download_url_list", [])
                if image_urls:
                    image_download_urls.append(image_urls)

        # 构建音乐名称
        music_author = aweme_detail.get("music", {}).get("author", "undefined")
        music_title = aweme_detail.get("music", {}).get("title", "undefined")
        music_name = f"{aweme_id}_{music_author}-{music_title}"
        video_desc = aweme_detail.get("desc", "undefined")

        # 提取封面 URL
        cover_data = DouyinParser._extract_cover_urls(aweme_detail)

        return {
            "aweme_id": aweme_id,
            "author": aweme_detail.get("author", {}).get("nickname"),
            "video_original_url": valid_url,
            "video_title": Utils.safe_filename(video_desc, 15),
            "video_desc": video_desc,
            "video_digg_count": aweme_detail.get("statistics", {}).get("digg_count"),
            "video_comment_count": aweme_detail.get("statistics", {}).get("comment_count"),
            "video_share_count": aweme_detail.get("statistics", {}).get("share_count"),
            "video_collect_count": aweme_detail.get("statistics", {}).get("collect_count"),
            "aweme_type": str(aweme_detail.get("aweme_type")),
            "video_created_time": datetime.datetime.fromtimestamp(aweme_detail.get("create_time")),
            "video_hashtag_name": Utils.concat_hashtag_name(aweme_detail),
            "video_datasize_bytes": 0,  # Image collections don't have video file size
            "image_download_urls": image_download_urls,
            "music_download_urls": aweme_detail.get("music", {}).get("play_url", {}).get("url_list", None),
            "music_name": music_name,
            "need_download_video": download_video,
            "need_download_music": download_music,
            "need_download_cover": download_cover,
            "cover_urls": cover_data["cover_urls"],
            "dynamic_cover_url": cover_data["dynamic_cover_url"],
            "video_categories": categories,
        }
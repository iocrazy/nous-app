"""
Douyin data parsing service

Responsible for parsing and cleaning raw data from the Douyin API, converting it
to a standardized internal format. Handles different media content types (video,
image-text, etc.) for data extraction and formatting.
"""

import datetime
from typing import Any, Dict

from loguru import logger

from app.core.utils import Utils


class DouyinParser:
    """Douyin data parsing service, responsible for cleaning and structuring Douyin API data"""

    @staticmethod
    async def _extract_music_play_urls(aweme_detail: Dict[str, Any], source: str = "") -> list:
        """Extract music play URLs from aweme_detail, trying multiple paths.

        Douyin's music structure varies between API sources:
        - Full API: music.play_url.url_list = [url1, url2, ...]
        - Full API: music.play_url.uri = "obj/xxx" (build stable URL)
        - LightHTTP: music.play_url may be empty {}, but music.mid exists
        - Some: music.play_url is a string URL directly
        """
        music_obj = aweme_detail.get("music", {})
        if not music_obj:
            logger.info(f"[DouyinParser/{source}] No music object")
            return []

        urls: list = []

        # Path 1: play_url.url_list (full API)
        play_url_obj = music_obj.get("play_url", {})
        if isinstance(play_url_obj, dict):
            urls = play_url_obj.get("url_list", [])
            if not urls:
                # Path 2: play_url.uri → build stable URL
                uri = play_url_obj.get("uri", "")
                if uri:
                    urls = [f"https://sf-tk-sg.ibytedtos.com/obj/{uri}"]
        elif isinstance(play_url_obj, str) and play_url_obj:
            urls = [play_url_obj]

        # Path 3: music.mid → fetch play_url from Douyin mobile API
        if not urls:
            mid = str(music_obj.get("mid", "") or music_obj.get("id_str", "") or music_obj.get("id", ""))
            if mid and mid != "0" and mid != "":
                try:
                    import httpx
                    api_url = f"https://aweme.snssdk.com/aweme/v1/music/detail/?music_id={mid}"
                    api_headers = {"User-Agent": "com.ss.android.ugc.aweme/330101 (Linux; U; Android 14;)"}
                    async with httpx.AsyncClient(timeout=8) as client:
                        resp = await client.get(api_url, headers=api_headers)
                        if resp.status_code == 200:
                            music_info = resp.json().get("music_info", {})
                            api_play_url = music_info.get("play_url", {})
                            if isinstance(api_play_url, dict):
                                urls = api_play_url.get("url_list", [])
                            logger.info(f"[DouyinParser/{source}] Fetched music via API: mid={mid}, urls={len(urls)}")
                except Exception as e:
                    logger.warning(f"[DouyinParser/{source}] Music API fetch failed for mid={mid}: {e}")

        # Path 4: music.url (sometimes present)
        if not urls:
            direct_url = music_obj.get("url", "")
            if direct_url:
                urls = [direct_url]

        logger.info(
            f"[DouyinParser/{source}] music extraction: "
            f"keys={sorted(music_obj.keys())}, "
            f"play_url_type={type(play_url_obj).__name__}, "
            f"mid={music_obj.get('mid', 'N/A')}, "
            f"result_urls={len(urls)}"
        )
        return urls

    @staticmethod
    async def parse_aweme_detail(
        aweme_detail: Dict[str, Any],
        valid_url: str,
        download_video: bool = True,
        download_music: bool = False,
        download_cover: bool = True,
    ) -> Dict[str, Any]:
        """
        Parse Douyin video detail data, handle different media types

        Args:
            aweme_detail: Raw data from Douyin API
            valid_url: Valid Douyin video URL
            download_video: Whether to download video
            download_music: Whether to download music

        Returns:
            Dict[str, Any]: Structured video data
        """
        if not aweme_detail:
            logger.error("无效的抖音数据")
            return {}

        # Extract basic video info
        aweme_type = aweme_detail.get("aweme_type")
        aweme_id = aweme_detail.get("aweme_id")

        # Handle different media types
        if aweme_type == 68:  # Image-text type
            logger.info(
                f"解析图文类型数据:aweme_id={aweme_id}, media_type={aweme_type}"
            )
            return await DouyinParser._parse_image_text(
                aweme_detail,
                aweme_id,
                valid_url,
                download_video,
                download_music,
                download_cover,
            )
        elif aweme_type in (0, 4, 61):  # Video type
            logger.info(
                f"解析视频类型数据:aweme_id={aweme_id}, media_type={aweme_type}"
            )
            return await DouyinParser._parse_video(
                aweme_detail,
                aweme_id,
                valid_url,
                download_video,
                download_music,
                download_cover,
            )
        elif aweme_type == 2:  # Image collection
            logger.info(
                f"解析图片合集类型数据:aweme_id={aweme_id}, media_type={aweme_type}"
            )
            return await DouyinParser._parse_image_collection(
                aweme_detail,
                aweme_id,
                valid_url,
                download_video,
                download_music,
                download_cover,
            )
        else:
            logger.warning(f"不支持的媒体类型: {aweme_type}")
            raise ValueError(f"不支持的媒体类型: {aweme_type}")

    @staticmethod
    def _extract_cover_urls(aweme_detail: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract cover-related URLs

        Returns:
            Dict containing cover_urls and dynamic_cover_url
        """
        video_data = aweme_detail.get("video", {})

        # Collect all cover URLs
        cover_urls = []

        # Original cover (high quality)
        origin_cover = video_data.get("origin_cover", {})
        if origin_cover and origin_cover.get("url_list"):
            cover_urls.extend(origin_cover.get("url_list", []))

        # Standard cover
        cover = video_data.get("cover", {})
        if cover and cover.get("url_list"):
            cover_urls.extend(cover.get("url_list", []))

        # Dynamic cover (GIF)
        dynamic_cover = video_data.get("dynamic_cover", {})
        dynamic_cover_url = None
        if dynamic_cover and dynamic_cover.get("url_list"):
            dynamic_cover_url = (
                dynamic_cover.get("url_list", [])[0]
                if dynamic_cover.get("url_list")
                else None
            )

        return {"cover_urls": cover_urls, "dynamic_cover_url": dynamic_cover_url}

    @staticmethod
    async def _parse_image_text(
        aweme_detail: Dict[str, Any],
        aweme_id: str,
        valid_url: str,
        download_video: bool,
        download_music: bool,
        download_cover: bool,
    ) -> Dict[str, Any]:
        """
        Parse image-text type Douyin data

        Args:
            aweme_detail: Raw data from Douyin API
            aweme_id: Douyin video ID
            valid_url: Valid Douyin video URL
            download_video: Whether to download video
            download_music: Whether to download music

        Returns:
            Dict[str, Any]: Structured image-text data
        """
        # Extract image and video URLs
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
                    video_urls = (
                        item.get("video", {}).get("play_addr", {}).get("url_list", [])
                    )
                    video_download_urls.append(video_urls)
                    logger.debug(f"video_url_list: {video_download_urls}")

        logger.debug(f"image_download_urls: {image_download_urls}")
        # Build music name
        music_author = aweme_detail.get("music", {}).get("author", "undefined")
        music_title = aweme_detail.get("music", {}).get("title", "undefined")
        music_name = f"{aweme_id}_{music_author}-{music_title}"
        video_desc = aweme_detail.get("desc", "undefined")

        # Extract cover URLs
        cover_data = DouyinParser._extract_cover_urls(aweme_detail)

        # Extract standalone music play URL (carousel types have separate audio)
        music_play_urls = await DouyinParser._extract_music_play_urls(aweme_detail, "image_text")

        # Build return data
        return {
            "platform_id": aweme_id,
            "author": aweme_detail.get("author", {}).get("nickname"),
            "original_url": valid_url,
            "title": Utils.safe_filename(video_desc, 15),
            "description": video_desc,
            "like_count": aweme_detail.get("statistics", {}).get("digg_count"),
            "comment_count": aweme_detail.get("statistics", {}).get("comment_count"),
            "share_count": aweme_detail.get("statistics", {}).get("share_count"),
            "favorite_count": aweme_detail.get("statistics", {}).get("collect_count"),
            "media_type": str(aweme_detail.get("aweme_type")),
            "published_at": datetime.datetime.fromtimestamp(
                aweme_detail.get("create_time")
            ),
            "hashtags": Utils.concat_hashtag_name(aweme_detail),
            "datasize_bytes": 0,  # Image/video mixed type doesn't have single file size
            "source_platform": "douyin",
            "image_download_urls": image_download_urls,
            "video_download_urls": video_download_urls,
            "music_name": music_name,
            "music_play_urls": music_play_urls,
            "need_download_video": download_video,
            "need_download_cover": download_cover,
            "cover_urls": cover_data["cover_urls"],
            "dynamic_cover_url": cover_data["dynamic_cover_url"],
        }

    @staticmethod
    async def _parse_video(
        aweme_detail: Dict[str, Any],
        aweme_id: str,
        original_url: str,
        download_video: bool,
        download_music: bool,
        download_cover: bool,
    ) -> Dict[str, Any]:
        """
        Parse video type Douyin data

        Args:
            aweme_detail: Raw data from Douyin API
            aweme_id: Douyin video ID
            original_url: Valid Douyin video URL
            download_video: Whether to download video
            download_music: Whether to download music

        Returns:
            Dict[str, Any]: Structured video data
        """
        # Extract music info
        music_from = aweme_detail.get("music", {}).get("title")
        music_author = (
            aweme_detail.get("music", {}).get("matched_pgc_sound", {}).get("author")
        )
        music_title = (
            aweme_detail.get("music", {}).get("matched_pgc_sound", {}).get("title")
        )

        music_name = (
            f"{aweme_id}_{music_author}-{music_title}"
            if music_author and music_title
            else f"{aweme_id}:{music_from}"
        )
        video_desc = aweme_detail.get("desc", "undefined")

        # Extract cover URLs
        cover_data = DouyinParser._extract_cover_urls(aweme_detail)

        # Safely get bit_rate data (compatible with lightweight parser data structure)
        video_data = aweme_detail.get("video", {}) or {}
        bit_rate_list = video_data.get("bit_rate") or [{}]
        first_bit_rate = bit_rate_list[0] if bit_rate_list else {}
        data_size = (
            first_bit_rate.get("play_addr", {}).get("data_size", 0)
            if first_bit_rate
            else 0
        )

        # Build video download URLs with stable play URL fallback
        video_urls = video_data.get("play_addr", {}).get("url_list", None) or []
        # Append a stable play URL (no expiry) as fallback using the video's uri
        video_uri = video_data.get("play_addr", {}).get("uri", "")
        if video_uri:
            stable_play_url = (
                f"https://aweme.snssdk.com/aweme/v1/play/"
                f"?video_id={video_uri}&ratio=720p&line=0"
            )
            if stable_play_url not in video_urls:
                video_urls.append(stable_play_url)

        # Build return data
        return {
            "platform_id": aweme_id,
            "author": aweme_detail.get("author", {}).get("nickname"),
            "original_url": original_url,
            "title": Utils.safe_filename(video_desc, 15),
            "description": aweme_detail.get("desc", "undefined"),
            "like_count": aweme_detail.get("statistics", {}).get("digg_count"),
            "comment_count": aweme_detail.get("statistics", {}).get("comment_count"),
            "share_count": aweme_detail.get("statistics", {}).get("share_count"),
            "favorite_count": aweme_detail.get("statistics", {}).get("collect_count"),
            "hashtags": Utils.concat_hashtag_name(aweme_detail),
            "media_type": str(aweme_detail.get("aweme_type")),
            "published_at": datetime.datetime.fromtimestamp(
                aweme_detail.get("create_time")
            ),
            "datasize": Utils.format_file_size(data_size),
            "datasize_bytes": data_size or 0,
            "duration": Utils.format_duration(video_data.get("duration")),
            "resolution": f"{video_data.get('width')}x{video_data.get('height')}",
            "source_platform": "douyin",
            "video_download_urls": video_urls or None,
            "music_name": music_name,
            "need_download_video": download_video,
            "need_download_cover": download_cover,
            "cover_urls": cover_data["cover_urls"],
            "dynamic_cover_url": cover_data["dynamic_cover_url"],
        }

    @staticmethod
    async def _parse_image_collection(
        aweme_detail: Dict[str, Any],
        aweme_id: str,
        valid_url: str,
        download_video: bool,
        download_music: bool,
        download_cover: bool,
    ) -> Dict[str, Any]:
        """
        Parse image collection type Douyin data

        Args:
            aweme_detail: Raw data from Douyin API
            aweme_id: Douyin video ID
            valid_url: Valid Douyin video URL
            download_video: Whether to download video (images)
            download_music: Whether to download music
            download_cover: Whether to download cover

        Returns:
            Dict[str, Any]: Structured image collection data
        """
        # Extract image URLs (image collection)
        images = aweme_detail.get("images", [])
        image_download_urls = []

        if images:
            for item in images:
                image_urls = item.get("download_url_list", [])
                if image_urls:
                    image_download_urls.append(image_urls)

        # Build music name
        music_author = aweme_detail.get("music", {}).get("author", "undefined")
        music_title = aweme_detail.get("music", {}).get("title", "undefined")
        music_name = f"{aweme_id}_{music_author}-{music_title}"
        video_desc = aweme_detail.get("desc", "undefined")

        # Extract cover URLs
        cover_data = DouyinParser._extract_cover_urls(aweme_detail)

        # Extract standalone music play URL (carousel types have separate audio)
        music_play_urls = await DouyinParser._extract_music_play_urls(aweme_detail, "image_collection")

        return {
            "platform_id": aweme_id,
            "author": aweme_detail.get("author", {}).get("nickname"),
            "original_url": valid_url,
            "title": Utils.safe_filename(video_desc, 15),
            "description": video_desc,
            "like_count": aweme_detail.get("statistics", {}).get("digg_count"),
            "comment_count": aweme_detail.get("statistics", {}).get("comment_count"),
            "share_count": aweme_detail.get("statistics", {}).get("share_count"),
            "favorite_count": aweme_detail.get("statistics", {}).get("collect_count"),
            "media_type": str(aweme_detail.get("aweme_type")),
            "published_at": datetime.datetime.fromtimestamp(
                aweme_detail.get("create_time")
            ),
            "hashtags": Utils.concat_hashtag_name(aweme_detail),
            "datasize_bytes": 0,  # Image collections don't have video file size
            "source_platform": "douyin",
            "image_download_urls": image_download_urls,
            "music_name": music_name,
            "music_play_urls": music_play_urls,
            "need_download_video": download_video,
            "need_download_cover": download_cover,
            "cover_urls": cover_data["cover_urls"],
            "dynamic_cover_url": cover_data["dynamic_cover_url"],
        }

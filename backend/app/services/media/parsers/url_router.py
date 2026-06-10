# backend/app/services/url_router.py

"""
URL Router Service

Detects source platform from URL and dispatches to the appropriate handler.
Most platforms use yt-dlp as the primary parser; qishui (Soda Music) uses a
dedicated 'soda' handler. Douyin uses the unified ABogus / DrissionPage chain
(douyin_parse.parse_chain).
"""

import re
from urllib.parse import urlparse

from loguru import logger


class URLRouter:
    """Detect URL source platform, dispatch to appropriate handler"""

    PLATFORM_PATTERNS: dict[str, list[str]] = {
        "qishui": ["qishui.douyin.com", "music.douyin.com"],
        "douyin": ["douyin.com", "iesdouyin.com"],
        "youtube": ["youtube.com", "youtu.be"],
        "bilibili": ["bilibili.com", "b23.tv"],
        "twitter": ["twitter.com", "x.com"],
        "tiktok": ["tiktok.com"],
        "instagram": ["instagram.com"],
        "xiaohongshu": ["xiaohongshu.com", "xhslink.com"],
    }

    @staticmethod
    def detect_platform(url: str) -> tuple[str, str]:
        """
        Detect the source platform from a URL.

        Returns:
            tuple[str, str]: (platform_name, handler_type)
                handler_type is 'soda' for qishui (Soda Music) and 'ytdlp' for
                all other platforms.
        """
        try:
            parsed = urlparse(url)
            hostname = parsed.hostname or ""
            hostname = hostname.lower()
        except Exception:
            logger.warning(f"Failed to parse URL: {url}")
            return ("unknown", "ytdlp")

        for platform, domains in URLRouter.PLATFORM_PATTERNS.items():
            for domain in domains:
                if hostname == domain or hostname.endswith(f".{domain}"):
                    handler_type = "soda" if platform == "qishui" else "ytdlp"
                    logger.info(
                        f"[URLRouter] Detected platform: {platform}, handler: {handler_type} for {url}"
                    )
                    return (platform, handler_type)

        # Unknown platform, default to yt-dlp
        logger.info(f"[URLRouter] Unknown platform for {url}, defaulting to ytdlp")
        return ("unknown", "ytdlp")

    @staticmethod
    def is_supported_url(url: str) -> bool:
        """Check if URL looks like a valid video URL"""
        if not url:
            return False

        # Must start with http(s)
        if not re.match(r"^https?://", url):
            return False

        try:
            parsed = urlparse(url)
            # Must have a hostname
            if not parsed.hostname:
                return False
            # Must have at least one dot in hostname (e.g. example.com)
            if "." not in parsed.hostname:
                return False
            return True
        except Exception:
            return False

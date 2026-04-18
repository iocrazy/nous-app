"""Unit tests for URLRouter platform detection + URL validation."""

from __future__ import annotations

import pytest

from app.services.url_router import URLRouter


# ─── detect_platform ────────────────────────────────────────────────


class TestDetectPlatform:
    @pytest.mark.parametrize(
        "url, platform",
        [
            ("https://www.douyin.com/video/7412345", "douyin"),
            ("https://v.douyin.com/abc", "douyin"),
            ("https://www.iesdouyin.com/share/x", "douyin"),
            ("https://www.youtube.com/watch?v=x", "youtube"),
            ("https://youtu.be/abc", "youtube"),
            ("https://www.bilibili.com/video/BV1xx", "bilibili"),
            ("https://b23.tv/abc", "bilibili"),
            ("https://twitter.com/user/status/1", "twitter"),
            ("https://x.com/user/status/1", "twitter"),
            ("https://www.tiktok.com/@user/video/1", "tiktok"),
            ("https://www.instagram.com/p/x", "instagram"),
            ("https://www.xiaohongshu.com/discovery/item/x", "xiaohongshu"),
            ("https://xhslink.com/x", "xiaohongshu"),
        ],
    )
    def test_known_platforms(self, url: str, platform: str) -> None:
        p, handler = URLRouter.detect_platform(url)
        assert p == platform
        assert handler == "ytdlp"

    def test_unknown_platform_defaults_to_unknown_ytdlp(self) -> None:
        p, h = URLRouter.detect_platform("https://example.com/v/1")
        assert p == "unknown"
        assert h == "ytdlp"

    def test_malformed_url_falls_back(self) -> None:
        p, h = URLRouter.detect_platform("not a url")
        assert p == "unknown"
        assert h == "ytdlp"


# ─── is_supported_url ──────────────────────────────────────────────


class TestIsSupportedURL:
    def test_empty_string(self) -> None:
        assert URLRouter.is_supported_url("") is False

    def test_non_http(self) -> None:
        assert URLRouter.is_supported_url("ftp://example.com/x") is False
        assert URLRouter.is_supported_url("file:///tmp/x") is False

    def test_http_without_hostname(self) -> None:
        assert URLRouter.is_supported_url("http:///x") is False

    def test_http_with_tld(self) -> None:
        assert URLRouter.is_supported_url("http://example.com") is True
        assert URLRouter.is_supported_url("https://douyin.com/x") is True

    def test_hostname_without_dot_rejected(self) -> None:
        assert URLRouter.is_supported_url("http://localhost") is False

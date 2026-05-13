"""B9-E — yt-dlp proxy routing.

Originally (commit 162cb74b, 2026-05-02) routed every yt-dlp hop through
the boundary SsrfProxy. After the 2026-05-13 prod incident where the
SsrfProxy CONNECT tunnel stalled for 60s on a silent bilibili upstream
(ssrf_proxy.py::_pipe lacks an idle timeout — kept as a separate
follow-up), we narrowed the proxy scope:

  * URLs whose host is a *known* platform (URLRouter.PLATFORM_PATTERNS)
    skip the SsrfProxy. Their entry URL was validated at parse time
    (app.boundary.validate_url) and yt-dlp's downstream hops then target
    that platform's hardcoded extractor endpoints (api.bilibili.com,
    *.bilivideo.com, googlevideo, ...) which are NOT user-controlled —
    no SSRF surface to defend.
  * Unknown hosts still tunnel through SsrfProxy as defense-in-depth.

External VPN proxy (YT_DLP_PROXY_YOUTUBE / HTTPS_PROXY) still wins for
YouTube/Twitter — that's user-supplied connectivity, not security.
"""

from __future__ import annotations

import pytest

from app.services.media.parsers.ytdlp_service import YtdlpService


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Strip proxy env so each test controls its own state."""
    for var in (
        "YT_DLP_PROXY_YOUTUBE",
        "YT_DLP_PROXY",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "ALL_PROXY",
    ):
        monkeypatch.delenv(var, raising=False)
    yield


@pytest.mark.unit
def test_trusted_platform_douyin_direct_connect(monkeypatch):
    """Douyin URLs bypass SsrfProxy — direct connect, no --proxy."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "SSRF_PROXY_URL", "http://127.0.0.1:55001")
    args = YtdlpService._get_proxy_args("https://www.douyin.com/video/12345")
    assert args == []


@pytest.mark.unit
def test_trusted_platform_bilibili_direct_connect(monkeypatch):
    """Bilibili URLs bypass SsrfProxy — fixes the 2026-05-13 _pipe stall."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "SSRF_PROXY_URL", "http://127.0.0.1:55001")
    args = YtdlpService._get_proxy_args("https://www.bilibili.com/video/BV12345")
    assert args == []


@pytest.mark.unit
def test_trusted_platform_xiaohongshu_direct_connect(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "SSRF_PROXY_URL", "http://127.0.0.1:55001")
    args = YtdlpService._get_proxy_args(
        "https://www.xiaohongshu.com/discovery/item/abc"
    )
    assert args == []


@pytest.mark.unit
def test_external_proxy_wins_for_youtube(monkeypatch):
    """When external proxy is configured for international platforms,
    it takes priority over direct connect (yt-dlp can only have one
    --proxy, and the user explicitly wants VPN for YouTube)."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "SSRF_PROXY_URL", "http://127.0.0.1:55001")
    monkeypatch.setenv("YT_DLP_PROXY_YOUTUBE", "http://user-vpn:8080")
    args = YtdlpService._get_proxy_args("https://www.youtube.com/watch?v=abc")
    assert args == ["--proxy", "http://user-vpn:8080"]


@pytest.mark.unit
def test_youtube_direct_when_no_external(monkeypatch):
    """If no external proxy configured, YouTube falls through to
    trusted-platform direct connect (it's in PLATFORM_PATTERNS). Note
    this is a behavior change from pre-2026-05-13, where YouTube would
    fall back to the boundary SsrfProxy when no VPN was set."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "SSRF_PROXY_URL", "http://127.0.0.1:55001")
    args = YtdlpService._get_proxy_args("https://www.youtube.com/watch?v=abc")
    assert args == []


@pytest.mark.unit
def test_unknown_host_routes_through_ssrf_proxy(monkeypatch):
    """Hosts not in PLATFORM_PATTERNS still tunnel through SsrfProxy
    as defense-in-depth — they could be user-supplied URLs that drop
    yt-dlp into its generic webpage scraper, where SSRF is a real risk."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "SSRF_PROXY_URL", "http://127.0.0.1:55001")
    args = YtdlpService._get_proxy_args("https://example.com/some/video.mp4")
    assert args == ["--proxy", "http://127.0.0.1:55001"]


@pytest.mark.unit
def test_unknown_host_direct_when_ssrf_proxy_not_started(monkeypatch):
    """Boundary URL empty (dev before lifespan) → no --proxy even for
    unknown hosts. Pre-existing degraded-mode behavior."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "SSRF_PROXY_URL", "")
    args = YtdlpService._get_proxy_args("https://example.com/some/video.mp4")
    assert args == []


@pytest.mark.unit
def test_ssrf_proxy_unused_for_trusted_even_when_set(monkeypatch):
    """Trusted-platform direct connect must NOT regress to SsrfProxy
    even if SSRF_PROXY_URL happens to be set. The whole point of the
    bypass is avoiding the SsrfProxy _pipe stall."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "SSRF_PROXY_URL", "http://127.0.0.1:55001")
    for url in (
        "https://www.bilibili.com/video/BV12345",
        "https://b23.tv/abc",
        "https://www.douyin.com/video/12345",
        "https://v.douyin.com/abc",
        "https://www.tiktok.com/@u/video/12345",
    ):
        assert (
            YtdlpService._get_proxy_args(url) == []
        ), f"trusted URL {url!r} leaked --proxy"


@pytest.mark.unit
def test_external_proxy_for_twitter_when_set(monkeypatch):
    """Twitter goes through the external VPN when configured."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "SSRF_PROXY_URL", "http://127.0.0.1:55001")
    monkeypatch.setenv("HTTPS_PROXY", "http://user-vpn:8080")
    args = YtdlpService._get_proxy_args("https://twitter.com/u/status/12345")
    assert args == ["--proxy", "http://user-vpn:8080"]

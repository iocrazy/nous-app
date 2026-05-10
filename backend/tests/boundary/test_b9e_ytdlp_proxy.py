"""B9-E — yt-dlp routes through the boundary SsrfProxy.

The wire-up: settings.SSRF_PROXY_URL is auto-populated by app.main
lifespan; YtdlpService._get_proxy_args reads it and adds --proxy to
every yt-dlp cmd unless an external proxy already wins for this URL.
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
def test_boundary_proxy_used_for_douyin(monkeypatch):
    """Douyin URLs route through the boundary SsrfProxy."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "SSRF_PROXY_URL", "http://127.0.0.1:55001")
    args = YtdlpService._get_proxy_args("https://www.douyin.com/video/12345")
    assert args == ["--proxy", "http://127.0.0.1:55001"]


@pytest.mark.unit
def test_boundary_proxy_used_for_bilibili(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "SSRF_PROXY_URL", "http://127.0.0.1:55001")
    args = YtdlpService._get_proxy_args("https://www.bilibili.com/video/BV12345")
    assert args == ["--proxy", "http://127.0.0.1:55001"]


@pytest.mark.unit
def test_external_proxy_wins_for_youtube(monkeypatch):
    """When external proxy is configured for international platforms,
    it takes priority over the boundary proxy (yt-dlp can only have one)."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "SSRF_PROXY_URL", "http://127.0.0.1:55001")
    monkeypatch.setenv("YT_DLP_PROXY_YOUTUBE", "http://user-vpn:8080")
    args = YtdlpService._get_proxy_args("https://www.youtube.com/watch?v=abc")
    assert args == ["--proxy", "http://user-vpn:8080"]


@pytest.mark.unit
def test_youtube_falls_back_to_boundary_when_no_external(monkeypatch):
    """If no external proxy configured for YouTube, the boundary proxy
    still applies (better than nothing)."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "SSRF_PROXY_URL", "http://127.0.0.1:55001")
    args = YtdlpService._get_proxy_args("https://www.youtube.com/watch?v=abc")
    assert args == ["--proxy", "http://127.0.0.1:55001"]


@pytest.mark.unit
def test_no_proxy_when_boundary_not_started(monkeypatch):
    """Boundary URL empty (proxy not yet started) → no --proxy.
    Behavior matches pre-B9-E: yt-dlp runs unproxied, degraded."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "SSRF_PROXY_URL", "")
    args = YtdlpService._get_proxy_args("https://www.douyin.com/video/12345")
    assert args == []


@pytest.mark.unit
def test_external_proxy_used_when_boundary_empty(monkeypatch):
    """No boundary, external proxy for YouTube → external used."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "SSRF_PROXY_URL", "")
    monkeypatch.setenv("HTTPS_PROXY", "http://user-vpn:8080")
    args = YtdlpService._get_proxy_args("https://www.youtube.com/watch?v=abc")
    assert args == ["--proxy", "http://user-vpn:8080"]

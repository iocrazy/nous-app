"""B9-F — the Camoufox browser routes through SsrfProxy.

Camoufox launches a real Firefox under the hood; we can't realistically
unit-test that. Instead, verify the configuration: when
``settings.SSRF_PROXY_URL`` is set, the launch options handed to Camoufox
carry that proxy.

This replaces the DrissionPage version of the same boundary check
(2026-09-16). The tier changed engines; the boundary did not. A browser
that reaches the network directly is an SSRF hole regardless of which
browser it is, and this tier is the one that opens arbitrary share URLs.
"""

from __future__ import annotations

import pytest

from app.services.media.parsers.douyin_parse.camoufox_parser import CamoufoxParser


@pytest.mark.unit
def test_proxy_configured_when_ssrf_url_set(monkeypatch):
    """Boundary up → launch options name it as the proxy."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "SSRF_PROXY_URL", "http://127.0.0.1:55001")

    kwargs = CamoufoxParser._launch_kwargs("test-ua")

    assert kwargs["proxy"] == {"server": "http://127.0.0.1:55001"}
    # GeoIP only makes sense behind the proxy — it reads the exit IP.
    assert kwargs["geoip"] is True


@pytest.mark.unit
def test_no_proxy_key_when_ssrf_url_unset(monkeypatch):
    """Boundary down → no proxy key at all.

    Passing ``proxy=None`` or an empty string is not the same as omitting
    it; Camoufox would try to parse it. Local dev without SsrfProxy must
    still launch.
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "SSRF_PROXY_URL", "")

    kwargs = CamoufoxParser._launch_kwargs("test-ua")

    assert "proxy" not in kwargs
    assert "geoip" not in kwargs


@pytest.mark.unit
def test_blank_ssrf_url_is_treated_as_unset(monkeypatch):
    """A whitespace-only env var is a config mistake, not a proxy URL."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "SSRF_PROXY_URL", "   ")

    assert "proxy" not in CamoufoxParser._launch_kwargs("test-ua")


@pytest.mark.unit
def test_firefox_chain_ua_is_pinned_into_fingerprint(monkeypatch):
    """One UA for the whole chain — when it is coherent with the engine.

    a_bogus is signed against a UA, the browser sends a UA, and yt-dlp
    later downloads with a UA. Douyin flags sessions where those disagree,
    so the chain picks one and every tier uses it.
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "SSRF_PROXY_URL", "")
    firefox_ua = (
        "Mozilla/5.0 (X11; Linux x86_64; rv:152.0) Gecko/20100101 Firefox/152.0"
    )

    kwargs = CamoufoxParser._launch_kwargs(firefox_ua)

    assert kwargs["config"]["navigator.userAgent"] == firefox_ua


@pytest.mark.unit
def test_chrome_chain_ua_is_refused(monkeypatch):
    """A Chrome UA on a Firefox browser is a self-contradicting fingerprint.

    Measured A/B live on 2026-09-16, same cookies and page back to back:
    letting Camoufox generate its own UA produced no challenge; pinning
    `ua_pool`'s Chrome-on-Windows string produced one every time. An
    incoherent fingerprint is a louder signal to douyin than a UA that
    differs between our own tiers, so coherence wins.
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "SSRF_PROXY_URL", "")
    chrome_ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )

    kwargs = CamoufoxParser._launch_kwargs(chrome_ua)

    assert "config" not in kwargs


@pytest.mark.unit
def test_ua_pool_strings_are_refused_by_the_browser_tier():
    """Pins the actual production input, not a hand-written sample. If the
    pool ever starts serving Firefox UAs this test tells us the guard has
    become a no-op rather than silently passing them through."""
    from app.services.media.parsers.douyin_parse import camoufox_parser as cp
    from app.services.media.parsers.douyin_parse.ua_pool import pick_ua

    # Every UA the pool can serve must be classified consistently.
    verdicts = {cp._looks_like_firefox(pick_ua()) for _ in range(40)}
    assert verdicts == {False}, "ua_pool now serves Firefox UAs — revisit the guard"


@pytest.mark.unit
def test_chrome_ua_containing_like_gecko_is_not_mistaken_for_firefox():
    """`like Gecko` is inside EVERY Chrome UA. A substring check on "Gecko"
    would accept the exact strings this guard exists to reject."""
    from app.services.media.parsers.douyin_parse import camoufox_parser as cp

    assert not cp._looks_like_firefox(
        "Mozilla/5.0 (Macintosh) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )
    assert cp._looks_like_firefox(
        "Mozilla/5.0 (X11; Linux x86_64; rv:152.0) Gecko/20100101 Firefox/152.0"
    )


@pytest.mark.unit
def test_headless_and_locale_defaults(monkeypatch):
    """Server-side defaults. A headed browser on a NAS has no display to
    draw into, and douyin serves a different page to non-zh locales."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "SSRF_PROXY_URL", "")

    kwargs = CamoufoxParser._launch_kwargs(None)

    assert kwargs["headless"] is True
    assert kwargs["locale"] == "zh-CN"
    assert kwargs["block_webrtc"] is True
    # No UA passed → no fingerprint override; Camoufox generates a coherent
    # one rather than us inventing half of it.
    assert "config" not in kwargs

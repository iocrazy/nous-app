"""B9-F — DrissionPage browser routes through SsrfProxy.

DrissionPage launches a real Chromium under the hood; we can't realistically
unit-test that. Instead, verify the configuration call: when settings.
SSRF_PROXY_URL is set, the parser code path invokes ChromiumOptions
.set_proxy(...) with the boundary URL.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.mark.unit
def test_set_proxy_called_when_ssrf_url_set(monkeypatch):
    """When the boundary proxy is up, DrissionPageParser configures
    ChromiumOptions with set_proxy(boundary_url) and disables loopback
    bypass (so even private IPs go through the proxy, where SSRF blocks)."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "SSRF_PROXY_URL", "http://127.0.0.1:55001")

    # Capture all the option calls
    captured: dict[str, list] = {"set_proxy": [], "set_argument": []}

    class _MockOptions:
        def headless(self):
            return self
        def set_proxy(self, url):
            captured["set_proxy"].append(url)
            return self
        def set_argument(self, arg):
            captured["set_argument"].append(arg)
            return self

    # Patch the ChromiumOptions name in the parser module
    import app.services.douyin_parse.drissionpage_parser as parser_mod
    monkeypatch.setattr(parser_mod, "ChromiumOptions", _MockOptions)
    monkeypatch.setattr(parser_mod, "ChromiumPage", MagicMock())

    # Reset the singleton so _initialize() runs fresh for this test
    inst = parser_mod.DrissionPageParser()
    # Force re-init: bypass singleton cache
    inst._initialized = False
    inst._page = None

    try:
        inst._initialize()
    except Exception:
        # Real Chromium likely fails to launch in test env — that's fine.
        # We only need to verify set_proxy was called BEFORE any failure.
        pass

    assert "http://127.0.0.1:55001" in captured["set_proxy"], (
        f"set_proxy not called with boundary URL. captured={captured}"
    )
    # The bypass override is critical — without it Chromium would skip the
    # proxy for localhost / private IPs (defeating the boundary).
    assert "--proxy-bypass-list=<-loopback>" in captured["set_argument"]


@pytest.mark.unit
def test_no_proxy_call_when_ssrf_url_empty(monkeypatch):
    """When SsrfProxy hasn't started yet (dev cold start), the parser must
    NOT call set_proxy — passing an empty string would break Chromium."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "SSRF_PROXY_URL", "")

    captured: dict[str, list] = {"set_proxy": []}

    class _MockOptions:
        def headless(self):
            return self
        def set_proxy(self, url):
            captured["set_proxy"].append(url)
            return self
        def set_argument(self, arg):
            return self

    import app.services.douyin_parse.drissionpage_parser as parser_mod
    monkeypatch.setattr(parser_mod, "ChromiumOptions", _MockOptions)
    monkeypatch.setattr(parser_mod, "ChromiumPage", MagicMock())

    inst = parser_mod.DrissionPageParser()
    inst._initialized = False
    inst._page = None
    try:
        inst._initialize()
    except Exception:
        pass

    assert captured["set_proxy"] == []

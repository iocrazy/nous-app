"""Tests for the Camoufox browser tier (spec §5.3, 2026-09-16).

These drive `CamoufoxParser` against a fake Playwright surface. Launching a
real Firefox per test would make the suite unrunnable in CI, and the things
worth pinning here are ordering and classification decisions, not whether
Playwright works:

* cookies land BEFORE the first navigation (after is a different session as
  far as douyin is concerned),
* an `aweme_list` response yields the video we asked for and never item
  zero,
* a challenge aborts immediately instead of burning the response timeout,
* the downloader's Redis key and TTL survive the engine swap.

The real-browser acceptance is separate and is what actually proves the
tier works end to end.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.media.parsers.douyin_parse.camoufox_parser import CamoufoxParser
from app.services.media.parsers.douyin_parse.failures import (
    DouyinFailure,
    DouyinParseError,
)

TARGET_ID = "7684532239228740849"
OTHER_ID = "1111111111111111111"
DETAIL_URL = "https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id=x"
POST_URL = "https://www.douyin.com/aweme/v1/web/aweme/post/?sec_user_id=x"


class FakeElement:
    """Playwright ElementHandle stand-in. `visible` is the only thing the
    detector reads, and it is the field that distinguishes douyin's always-
    mounted placeholder from a real challenge."""

    def __init__(self, visible: bool = True):
        self._visible = visible

    async def is_visible(self):
        return self._visible


class FakeResponse:
    def __init__(self, url: str, payload: Any):
        self.url = url
        self._payload = payload

    async def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakePage:
    """Minimal Playwright Page: records ordering, replays responses."""

    def __init__(self, journal: list[str], responses: list[FakeResponse], url: str):
        self._journal = journal
        self._responses = responses
        self.url = url
        self._handler = None
        self._selectors: dict[str, Any] = {}
        self._content = "<html></html>"

    def on(self, event: str, handler):
        assert event == "response"
        self._handler = handler

    async def goto(self, url, **kwargs):
        self._journal.append(f"goto:{url}")
        # Douyin fires the detail XHR during initial render, so replay the
        # canned responses as part of navigation rather than after it.
        for response in self._responses:
            self._handler(response)
        await asyncio.sleep(0)
        return None

    async def query_selector(self, selector: str):
        return self._selectors.get(selector)

    async def content(self):
        return self._content


class FakeContext:
    def __init__(self, journal: list[str], page: FakePage, cookies: list[dict]):
        self._journal = journal
        self._page = page
        self._cookies = cookies
        self.closed = False

    async def add_cookies(self, cookies):
        self._journal.append(f"add_cookies:{len(cookies)}")

    async def new_page(self):
        return self._page

    async def cookies(self):
        return self._cookies

    async def close(self):
        self.closed = True
        self._journal.append("context_closed")


class FakeBrowser:
    def __init__(self, context: FakeContext):
        self._context = context

    def is_connected(self):
        return True

    async def new_context(self):
        return self._context


def _install(
    monkeypatch,
    *,
    responses: list[FakeResponse],
    page_url: str = f"https://www.douyin.com/video/{TARGET_ID}",
    browser_cookies: list[dict] | None = None,
    cookie_text: str = "UIFID=abc; sessionid=def",
) -> tuple[list[str], FakeContext, FakePage]:
    journal: list[str] = []
    page = FakePage(journal, responses, page_url)
    context = FakeContext(journal, page, browser_cookies or [])
    browser = FakeBrowser(context)

    monkeypatch.setattr(CamoufoxParser, "_get_browser", AsyncMock(return_value=browser))
    monkeypatch.setattr(
        CamoufoxParser, "_get_user_cookie_text", AsyncMock(return_value=cookie_text)
    )
    # Real sleep would add 1.5s per test for no signal.
    # Small but non-zero: the challenge poller now needs two consecutive
    # samples, and a zero interval would spin instead of yielding.
    monkeypatch.setattr(CamoufoxParser, "CAPTCHA_SETTLE_SEC", 0.01)
    return journal, context, page


# ---------------------------------------------------------------- cookies


@pytest.mark.asyncio
async def test_cookies_injected_before_first_navigation(monkeypatch):
    """Ordering, not merely presence.

    Injecting after `goto` means douyin already saw one anonymous request
    from this browser — which is itself a signal, and costs a reload to
    correct.
    """
    journal, _, _ = _install(
        monkeypatch,
        responses=[FakeResponse(DETAIL_URL, {"aweme_detail": {"aweme_id": TARGET_ID}})],
    )

    await CamoufoxParser.fetch_one_video("https://v.douyin.com/abc/")

    cookie_idx = next(i for i, e in enumerate(journal) if e.startswith("add_cookies"))
    goto_idx = next(i for i, e in enumerate(journal) if e.startswith("goto:"))
    assert cookie_idx < goto_idx, journal


def test_cookie_header_parsed_to_douyin_scoped_cookies():
    cookies = CamoufoxParser._to_playwright_cookies("UIFID=abc; sessionid=def; junk")

    assert [c["name"] for c in cookies] == ["UIFID", "sessionid"]
    assert all(c["domain"] == ".douyin.com" for c in cookies)
    assert all(c["secure"] is True for c in cookies)


def test_empty_cookie_text_yields_no_cookies():
    assert CamoufoxParser._to_playwright_cookies("") == []


# ---------------------------------------------------------------- payload


@pytest.mark.asyncio
async def test_captures_aweme_detail(monkeypatch):
    detail = {"aweme_id": TARGET_ID, "desc": "hi"}
    _install(
        monkeypatch, responses=[FakeResponse(DETAIL_URL, {"aweme_detail": detail})]
    )

    result = await CamoufoxParser.fetch_one_video("https://v.douyin.com/abc/")

    assert result == detail


@pytest.mark.asyncio
async def test_picks_matching_id_out_of_aweme_list(monkeypatch):
    """The list endpoint answers with a feed. Returning item zero would
    hand the user someone else's video and call it success."""
    wanted = {"aweme_id": TARGET_ID, "desc": "wanted"}
    payload = {"aweme_list": [{"aweme_id": OTHER_ID, "desc": "noise"}, wanted]}
    _install(monkeypatch, responses=[FakeResponse(POST_URL, payload)])

    result = await CamoufoxParser.fetch_one_video("https://v.douyin.com/abc/")

    assert result == wanted


@pytest.mark.asyncio
async def test_list_without_target_id_times_out_rather_than_guessing(monkeypatch):
    payload = {"aweme_list": [{"aweme_id": OTHER_ID}]}
    _install(monkeypatch, responses=[FakeResponse(POST_URL, payload)])
    monkeypatch.setattr(CamoufoxParser, "RESPONSE_TIMEOUT_SEC", 0.2)

    assert await CamoufoxParser.fetch_one_video("https://v.douyin.com/abc/") is None


@pytest.mark.asyncio
async def test_unrelated_responses_are_ignored(monkeypatch):
    _install(
        monkeypatch,
        responses=[
            FakeResponse("https://www.douyin.com/aweme/v1/web/user/profile/", {"x": 1}),
            FakeResponse(DETAIL_URL, {"aweme_detail": {"aweme_id": TARGET_ID}}),
        ],
    )

    result = await CamoufoxParser.fetch_one_video("https://v.douyin.com/abc/")

    assert result["aweme_id"] == TARGET_ID


def test_pick_aweme_prefers_detail_over_list():
    detail = {"aweme_id": TARGET_ID}
    payload = {"aweme_detail": detail, "aweme_list": [{"aweme_id": OTHER_ID}]}

    assert CamoufoxParser._pick_aweme(payload, OTHER_ID) == detail


# -------------------------------------------------------------- challenge


@pytest.mark.asyncio
async def test_captcha_selector_aborts_fast_with_typed_failure(monkeypatch):
    """A challenge must not be reported as a generic miss.

    'Douyin wants you to verify' is advice the user can act on; `None`
    reads as 'the link is broken' and invites the retry storm that made
    this failure mode worse in the first place.

    No detail response here, which is what a real challenge looks like:
    douyin blocks the page, so the XHR never returns anything usable. The
    parser deliberately prefers DATA over the DOM — see
    `test_visible_captcha_does_not_override_a_response_that_arrived`.
    """
    _, _, page = _install(monkeypatch, responses=[])
    page._selectors['iframe[src*="verifycenter"]'] = FakeElement(visible=True)
    # Long timeout on purpose: if the parser waited for it, this test hangs
    # instead of passing, which is the failure we care about.
    monkeypatch.setattr(CamoufoxParser, "RESPONSE_TIMEOUT_SEC", 60.0)

    with pytest.raises(DouyinParseError) as excinfo:
        await asyncio.wait_for(
            CamoufoxParser.fetch_one_video("https://v.douyin.com/abc/"), timeout=5
        )

    assert excinfo.value.kind is DouyinFailure.CAPTCHA


@pytest.mark.asyncio
async def test_captcha_detected_from_verification_url(monkeypatch):
    _install(
        monkeypatch,
        responses=[],
        page_url="https://www.douyin.com/verifycenter/challenge?x=1",
    )

    with pytest.raises(DouyinParseError) as excinfo:
        await CamoufoxParser.fetch_one_video("https://v.douyin.com/abc/")

    assert excinfo.value.kind is DouyinFailure.CAPTCHA


@pytest.mark.asyncio
async def test_captcha_detected_from_slider_html(monkeypatch):
    _, _, page = _install(monkeypatch, responses=[])
    page._content = '<div class="x">请拖动下方滑块完成验证</div>'

    with pytest.raises(DouyinParseError) as excinfo:
        await CamoufoxParser.fetch_one_video("https://v.douyin.com/abc/")

    assert excinfo.value.kind is DouyinFailure.CAPTCHA


@pytest.mark.asyncio
async def test_detection_error_is_not_treated_as_a_challenge(monkeypatch):
    """A failed check means we don't know, and 'we don't know' must not
    abort a parse that would have worked."""
    _, _, page = _install(
        monkeypatch,
        responses=[FakeResponse(DETAIL_URL, {"aweme_detail": {"aweme_id": TARGET_ID}})],
    )
    page.content = AsyncMock(side_effect=RuntimeError("detached"))
    page.query_selector = AsyncMock(side_effect=RuntimeError("detached"))

    result = await CamoufoxParser.fetch_one_video("https://v.douyin.com/abc/")

    assert result["aweme_id"] == TARGET_ID


# ------------------------------------------------------------------ redis


@pytest.mark.asyncio
async def test_browser_cookies_cached_to_original_redis_key(monkeypatch):
    """Key and TTL are the downloader's contract, not ours to renumber —
    yt-dlp reads this or douyin's CDN answers 403."""
    fake_redis = MagicMock()
    _install(
        monkeypatch,
        responses=[FakeResponse(DETAIL_URL, {"aweme_detail": {"aweme_id": TARGET_ID}})],
        browser_cookies=[
            {"name": "sessionid", "value": "v1", "domain": ".douyin.com"},
            {"name": "ttwid", "value": "v2", "domain": "www.douyin.com"},
            {"name": "other", "value": "v3", "domain": ".example.com"},
        ],
    )

    with patch("app.core.redis.get_sync_redis", return_value=fake_redis):
        await CamoufoxParser.fetch_one_video("https://v.douyin.com/abc/")

    fake_redis.setex.assert_called_once()
    key, ttl, value = fake_redis.setex.call_args.args
    assert key == f"douyin_browser_cookies:{TARGET_ID}"
    assert ttl == 600
    # Only douyin cookies; the foreign-domain one must not leak into the
    # header we hand to the downloader.
    assert value == "sessionid=v1; ttwid=v2"


@pytest.mark.asyncio
async def test_redis_failure_does_not_fail_the_parse(monkeypatch):
    """Caching is an optimisation. Losing it should not lose the video."""
    _install(
        monkeypatch,
        responses=[FakeResponse(DETAIL_URL, {"aweme_detail": {"aweme_id": TARGET_ID}})],
        browser_cookies=[{"name": "sessionid", "value": "v", "domain": ".douyin.com"}],
    )

    with patch("app.core.redis.get_sync_redis", side_effect=RuntimeError("redis down")):
        result = await CamoufoxParser.fetch_one_video("https://v.douyin.com/abc/")

    assert result["aweme_id"] == TARGET_ID


# --------------------------------------------------------------- teardown


@pytest.mark.asyncio
async def test_context_closed_even_when_parse_fails(monkeypatch):
    """Contexts hold cookies and a page. Leaking one per failed parse is
    how a fallback tier becomes a memory leak."""
    _, context, _ = _install(monkeypatch, responses=[])
    monkeypatch.setattr(CamoufoxParser, "RESPONSE_TIMEOUT_SEC", 0.2)

    await CamoufoxParser.fetch_one_video("https://v.douyin.com/abc/")

    assert context.closed is True


@pytest.mark.asyncio
async def test_context_closed_when_challenge_raises(monkeypatch):
    _, context, _ = _install(
        monkeypatch, responses=[], page_url="https://www.douyin.com/verifycenter/x"
    )

    with pytest.raises(DouyinParseError):
        await CamoufoxParser.fetch_one_video("https://v.douyin.com/abc/")

    assert context.closed is True


@pytest.mark.asyncio
async def test_close_releases_browser_and_is_idempotent():
    """Shutdown runs once, but teardown chains get re-entered on crash
    paths; a second close must not explode."""
    manager = AsyncMock()
    CamoufoxParser._context_manager = manager
    CamoufoxParser._browser = MagicMock()

    await CamoufoxParser.close()

    manager.__aexit__.assert_awaited_once()
    assert CamoufoxParser._browser is None
    assert CamoufoxParser._context_manager is None

    await CamoufoxParser.close()  # no-op, must not raise
    manager.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_survives_a_browser_that_is_already_gone():
    manager = AsyncMock()
    manager.__aexit__.side_effect = RuntimeError("browser already dead")
    CamoufoxParser._context_manager = manager
    CamoufoxParser._browser = MagicMock()

    await CamoufoxParser.close()

    # State cleared regardless, so the next parse relaunches instead of
    # reusing a handle to a dead process.
    assert CamoufoxParser._browser is None
    assert CamoufoxParser._context_manager is None


@pytest.mark.asyncio
async def test_hidden_nocaptcha_placeholder_is_not_a_challenge(monkeypatch):
    """Regression, measured live 2026-09-16 on aweme 7684532239228740849.

    Douyin mounts a permanent, zero-sized iframe on ordinary video pages::

        <iframe name="nocaptcha-container"
                src="…/obj/rc-verifycenter/rmc-nocaptcha/1.0.0.52/index.html">

    Both `iframe[src*="verifycenter"]` and `iframe[src*="captcha"]` matched
    it, `is_visible()` was False for both, and the `aweme_detail` response
    arrived normally. A presence-only detector therefore aborted 100% of
    SUCCESSFUL parses while reporting them to the user as "Douyin asked for
    human verification" — the most expensive kind of wrong answer, because
    it tells the user to stop trying something that was working.
    """
    _, _, page = _install(
        monkeypatch,
        responses=[FakeResponse(DETAIL_URL, {"aweme_detail": {"aweme_id": TARGET_ID}})],
    )
    page._selectors['iframe[src*="verifycenter"]'] = FakeElement(visible=False)
    page._selectors['iframe[src*="captcha"]'] = FakeElement(visible=False)

    result = await CamoufoxParser.fetch_one_video("https://v.douyin.com/abc/")

    assert result["aweme_id"] == TARGET_ID


@pytest.mark.asyncio
async def test_visible_captcha_container_still_detected(monkeypatch):
    """The inverse of the regression above: making the check visibility-aware
    must not make it blind. A visible widget with no data arriving is a real
    challenge and must be reported as one."""
    _, _, page = _install(monkeypatch, responses=[])
    page._selectors["#captcha_container"] = FakeElement(visible=True)

    with pytest.raises(DouyinParseError) as excinfo:
        await CamoufoxParser.fetch_one_video("https://v.douyin.com/abc/")

    assert excinfo.value.kind is DouyinFailure.CAPTCHA


@pytest.mark.asyncio
async def test_visible_captcha_does_not_override_a_response_that_arrived(monkeypatch):
    """Data beats the DOM.

    The parser races the payload against the challenge poller instead of
    deciding up front. Measured live 2026-09-16: `aweme/detail` lands at
    t≈3.6s while a one-shot check fires at t≈1.5s, so any widget present
    mid-render pre-empted a parse that was two seconds from succeeding.
    If the video arrived, we have what the user asked for — reporting a
    captcha instead would be throwing away a completed result.
    """
    _, _, page = _install(
        monkeypatch,
        responses=[FakeResponse(DETAIL_URL, {"aweme_detail": {"aweme_id": TARGET_ID}})],
    )
    page._selectors["#captcha_container"] = FakeElement(visible=True)

    result = await CamoufoxParser.fetch_one_video("https://v.douyin.com/abc/")

    assert result["aweme_id"] == TARGET_ID


@pytest.mark.asyncio
async def test_single_sample_flicker_is_not_a_challenge(monkeypatch):
    """Two consecutive samples required.

    Douyin's verification widgets mount and unmount during page init. A
    detector that fired on one sighting would abort on ordinary page churn.
    """
    _, _, page = _install(monkeypatch, responses=[])

    looks = {"n": 0}

    class Flicker:
        async def is_visible(self):
            looks["n"] += 1
            # Visible on the first sample only, gone by the second.
            return looks["n"] == 1

    page._selectors["#captcha_container"] = Flicker()
    monkeypatch.setattr(CamoufoxParser, "RESPONSE_TIMEOUT_SEC", 0.2)

    # Seen once, gone next → timeout (None), not a captcha claim.
    result = await CamoufoxParser.fetch_one_video("https://v.douyin.com/abc/")

    assert result is None
    assert looks["n"] >= 2, "detector must take a second look before deciding"


@pytest.mark.asyncio
async def test_nocaptcha_in_page_url_is_not_a_challenge(monkeypatch):
    """`nocaptcha` contains `captcha`. Substring matching without the
    exclusion would read the absence of a challenge as its presence."""
    _install(
        monkeypatch,
        responses=[FakeResponse(DETAIL_URL, {"aweme_detail": {"aweme_id": TARGET_ID}})],
        page_url="https://www.douyin.com/video/1?from=rmc-nocaptcha",
    )

    result = await CamoufoxParser.fetch_one_video("https://v.douyin.com/abc/")

    assert result["aweme_id"] == TARGET_ID


def test_verifycenter_is_not_an_html_marker():
    """Pins the removal. `verifycenter` appears in the placeholder iframe's
    CDN path on every normal page, and raw-HTML matching has no visibility
    check available to rescue it."""
    from app.services.media.parsers.douyin_parse import camoufox_parser as cp

    assert "verifycenter" not in cp._CAPTCHA_HTML_MARKERS
    # The specific ones stay — they only appear on a real challenge.
    assert "captcha-verify-container" in cp._CAPTCHA_HTML_MARKERS
    assert "拖动下方滑块" in cp._CAPTCHA_HTML_MARKERS

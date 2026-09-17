# backend/app/services/media/parsers/douyin_parse/camoufox_parser.py

"""Browser-tier douyin parser, on Camoufox instead of DrissionPage.

Why the tier changed engines (2026-09-16)
-----------------------------------------
DrissionPage drove a real Chrome over CDP. Douyin's anti-bot stack reads the
CDP instrumentation itself, not just the fingerprint surface, so every
stealth patch we layered on top was answering the wrong question — the
`Runtime.enable` family of signals fires regardless of how clean
`navigator.webdriver` looks. Camoufox is Firefox driven through Playwright's
own protocol, so that entire detection surface simply does not apply, and the
fingerprint spoofing happens in C++ below the JS layer where page scripts
cannot read it back.

The trade we accepted: Firefox gives us no CDP `Debugger` domain, so there
are no native breakpoints. This tier never used them — it listens for a
network response and reads JSON — so the cost lands entirely on interactive
reverse-engineering work, not on this parser.

Position in the chain
---------------------
This is the FALLBACK tier. `ABogusDouyinParser` is tried first and, when it
works, no browser is started at all — see `parse_chain.fetch_douyin_detail`.
We get here when the HTTP signature path returned nothing or failed, which
in practice means douyin rejected the signature or the saved cookie went
stale.

The MCP server of the same name (`camoufox-reverse-mcp`) is a *development*
tool for reverse-engineering sessions. Production talks to the Camoufox
Python API directly; there is no MCP dependency in this module.
"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from app.services.media.parsers.douyin_parse.failures import (
    DouyinFailure,
    DouyinParseError,
)

#: Response URLs that carry the payload we want.
_DETAIL_URL_MARKERS: tuple[str, ...] = ("aweme/detail", "aweme/post")

#: Redis key the downloader already reads. Kept byte-identical to the
#: DrissionPage era: yt-dlp needs these cookies or douyin's CDN answers 403,
#: and the reader side is not part of this migration.
_COOKIE_REDIS_KEY = "douyin_browser_cookies:{aweme_id}"
_COOKIE_TTL_SECONDS = 600

#: DOM handles for douyin's verification challenge. The slider is injected
#: asynchronously ~500ms after the page settles, so a single synchronous
#: check right after navigation misses it.
#:
#: PRESENCE IS NOT ENOUGH — every one of these must also be VISIBLE. Douyin
#: ships a permanently-mounted, zero-sized placeholder iframe on ordinary
#: video pages:
#:
#:     <iframe name="nocaptcha-container"
#:             src="…/obj/rc-verifycenter/rmc-nocaptcha/1.0.0.52/index.html">
#:
#: Note the path: `rc-verifycenter/rmc-**nocaptcha**`. It is the container
#: that exists so a challenge *could* be shown, and it matches a naive
#: `src*="verifycenter"` selector on a page with no challenge at all.
#: Measured 2026-09-16 on aweme 7684532239228740849: both iframe selectors
#: matched, `is_visible()` was False for both, and the `aweme_detail`
#: response arrived normally — i.e. a presence-only check fails 100% of
#: successful parses.
_CAPTCHA_SELECTORS: tuple[str, ...] = (
    "#captcha_container",
    ".captcha-verify-container",
    ".captcha_verify_container",
    ".secsdk-captcha-wrapper",
    ".captcha_wrapper",
    'iframe[src*="verifycenter"]',
    'iframe[src*="captcha"]',
)

#: Fallback markers for when the challenge renders without a stable selector.
#:
#: Deliberately does NOT include a bare "verifycenter": that substring is in
#: the placeholder iframe's CDN path on every normal page, so matching it
#: against raw HTML is the same false positive as above with no visibility
#: check available to save us.
_CAPTCHA_HTML_MARKERS: tuple[str, ...] = (
    "captcha-verify-container",
    "secsdk-captcha",
    "拖动下方滑块",
)

#: A challenge can also arrive as a navigation to a verification host. This
#: reads the PAGE url, not a subresource, so the placeholder iframe cannot
#: trip it — but `nocaptcha` is excluded anyway for the same reason.
_CAPTCHA_URL_MARKERS: tuple[str, ...] = ("verifycenter", "captcha")
_CAPTCHA_URL_EXCLUSIONS: tuple[str, ...] = ("nocaptcha",)


def _looks_like_firefox(user_agent: str) -> bool:
    """Is this UA consistent with the engine Camoufox actually runs?

    Deliberately strict about Chrome: "like Gecko" appears inside every
    Chrome UA string, so a naive `"Gecko" in ua` check would accept the
    exact strings this guard exists to reject.
    """
    ua = (user_agent or "").lower()
    if "chrome" in ua or "chromium" in ua or "edg/" in ua:
        return False
    return "firefox" in ua or "gecko/" in ua


class CamoufoxParser:
    """Open the share link in Camoufox and read the detail API response.

    One browser is shared process-wide. Launching Camoufox costs seconds and
    hundreds of MB; a parser that spawned one per request would turn the
    fallback tier into a resource incident under any real queue depth.
    """

    #: Navigation budget. Douyin's SPA keeps streaming long after the detail
    #: response lands, so we wait on `domcontentloaded`, never on `load`.
    NAV_TIMEOUT_MS = 30_000

    #: How long to wait for the detail response once the page is up.
    RESPONSE_TIMEOUT_SEC = 25.0

    #: Grace period for an async-injected captcha to appear.
    CAPTCHA_SETTLE_SEC = 1.5

    _browser: Any = None
    _context_manager: Any = None
    _lock: asyncio.Lock | None = None

    # ------------------------------------------------------------------
    # Browser lifecycle
    # ------------------------------------------------------------------

    @classmethod
    def _get_lock(cls) -> asyncio.Lock:
        # Created lazily: a module-level `asyncio.Lock()` binds to whichever
        # loop imported the module, which is not necessarily the loop that
        # runs the request.
        if cls._lock is None:
            cls._lock = asyncio.Lock()
        return cls._lock

    @classmethod
    def _launch_kwargs(cls, user_agent: str | None) -> dict[str, Any]:
        """Camoufox launch options, including the SSRF boundary.

        Every outbound request from this browser must go through Nous's
        SsrfProxy the same way yt-dlp and the HTTP tier do. Handing the
        browser a direct route would open a hole that the rest of the
        codebase spent real effort closing.
        """
        from app.core.config import settings

        kwargs: dict[str, Any] = {
            "headless": True,
            "os": "linux",
            "locale": "zh-CN",
            "humanize": True,
            "block_webrtc": True,
        }

        proxy_url = (getattr(settings, "SSRF_PROXY_URL", "") or "").strip()
        if proxy_url:
            kwargs["proxy"] = {"server": proxy_url}
            # GeoIP inference reads the proxy's exit IP. Without a proxy it
            # would hit the network at launch for no benefit.
            kwargs["geoip"] = True
            logger.debug("[Camoufox] routing through SSRF proxy")

        # The chain pins ONE User-Agent across a_bogus signing, the browser
        # and the later download, so douyin never sees a session whose UA
        # changes mid-flight. Honouring that here is conditional, and the
        # condition is not cosmetic:
        #
        # `ua_pool` serves Chrome-on-Windows strings — correct for the HTTP
        # tier and for the Chrome-based tier this replaced. Pinning one onto
        # Camoufox produces a browser whose `navigator.userAgent` claims
        # Chrome/Windows while every other surface (Gecko internals, the
        # platform, the generated device profile) says Firefox/Linux.
        #
        # Measured A/B on aweme 7684532239228740849, 2026-09-16, same
        # cookies, same page, back to back:
        #
        #   no override → UA "Firefox/152.0 (X11; Linux x86_64)" → NO captcha
        #   override    → UA "Chrome (Windows NT 10.0; Win64)"   → CAPTCHA
        #
        # Camoufox warns about exactly this ("Manually setting navigator
        # properties is not recommended"). An incoherent fingerprint is a
        # louder signal than a UA that differs between tiers, so we take the
        # coherent one and say so.
        if user_agent and _looks_like_firefox(user_agent):
            kwargs["config"] = {"navigator.userAgent": user_agent}
        elif user_agent:
            logger.debug(
                "[Camoufox] ignoring non-Firefox chain UA; using the browser's "
                "own coherent fingerprint instead"
            )

        return kwargs

    @classmethod
    async def _get_browser(cls, user_agent: str | None) -> Any:
        """Return the shared browser, launching it on first use."""
        async with cls._get_lock():
            if cls._browser is not None and cls._browser.is_connected():
                return cls._browser

            # A disconnected handle means the browser died; drop it before
            # relaunching so we never leak the old process.
            if cls._browser is not None:
                logger.warning("[Camoufox] browser disconnected, relaunching")
                await cls._shutdown_locked()

            from camoufox.async_api import AsyncCamoufox

            logger.info("[Camoufox] launching browser")
            cls._context_manager = AsyncCamoufox(**cls._launch_kwargs(user_agent))
            cls._browser = await cls._context_manager.__aenter__()
            logger.info("[Camoufox] browser ready")
            return cls._browser

    @classmethod
    async def _shutdown_locked(cls) -> None:
        """Tear the browser down. Caller must hold the lock."""
        manager, cls._context_manager = cls._context_manager, None
        cls._browser = None
        if manager is None:
            return
        try:
            await manager.__aexit__(None, None, None)
        except Exception as err:  # pragma: no cover - best-effort teardown
            logger.warning(f"[Camoufox] browser teardown failed: {err}")

    @classmethod
    async def close(cls) -> None:
        """Release the browser. Called from the app shutdown chain."""
        async with cls._get_lock():
            if cls._context_manager is None:
                return
            logger.info("[Camoufox] closing browser")
            await cls._shutdown_locked()
            logger.info("[Camoufox] browser closed")

    # ------------------------------------------------------------------
    # Cookies
    # ------------------------------------------------------------------

    @classmethod
    async def _get_user_cookie_text(cls, user_id: str | None) -> str:
        """Fetch the user's saved douyin cookie string."""
        if not user_id:
            return ""
        try:
            from app.repositories.cookies_repository import get_cookies_repository

            row = await get_cookies_repository().get_by_user_and_platform(
                user_id, "douyin"
            )
            if row:
                return (row.get("cookie_text") or row.get("cookie_file") or "").strip()
        except Exception as err:
            # Never log the cookie itself — only why the read failed.
            logger.debug(f"[Camoufox] failed to load user cookie: {err}")
        return ""

    @staticmethod
    def _to_playwright_cookies(cookie_text: str) -> list[dict[str, Any]]:
        """`k=v; k=v` header → Playwright cookie dicts scoped to douyin."""
        cookies: list[dict[str, Any]] = []
        for part in (cookie_text or "").split(";"):
            name, sep, value = part.strip().partition("=")
            if not sep or not name:
                continue
            cookies.append(
                {
                    "name": name.strip(),
                    "value": value.strip(),
                    "domain": ".douyin.com",
                    "path": "/",
                    "secure": True,
                }
            )
        return cookies

    @classmethod
    async def _cache_cookies(cls, context: Any, aweme_id: str) -> None:
        """Publish the authenticated session for the downloader to reuse."""
        if not aweme_id:
            return
        try:
            parts = [
                f"{c['name']}={c.get('value', '')}"
                for c in await context.cookies()
                if "douyin" in (c.get("domain") or "").lower()
            ]
            if not parts:
                return
            from app.core.redis import get_sync_redis

            key = _COOKIE_REDIS_KEY.format(aweme_id=aweme_id)
            get_sync_redis().setex(key, _COOKIE_TTL_SECONDS, "; ".join(parts))
            # Count only. The values are session credentials.
            logger.info(f"[Camoufox] cached {len(parts)} cookies to Redis (key={key})")
        except Exception as err:
            logger.warning(f"[Camoufox] failed to cache cookies: {err}")

    # ------------------------------------------------------------------
    # Challenge detection
    # ------------------------------------------------------------------

    @classmethod
    async def _detect_captcha(cls, page: Any) -> str:
        """Return a short reason string when a challenge is ON SCREEN, else "".

        "On screen" is the operative word. See `_CAPTCHA_SELECTORS` for why a
        presence-only check reports a challenge on every successful parse.
        """
        try:
            current_url = (page.url or "").lower()
            if not any(x in current_url for x in _CAPTCHA_URL_EXCLUSIONS):
                for marker in _CAPTCHA_URL_MARKERS:
                    if marker in current_url:
                        return f"url:{marker}"

            for selector in _CAPTCHA_SELECTORS:
                try:
                    element = await page.query_selector(selector)
                    if element is None:
                        continue
                    # The visibility call is the whole check. A hidden or
                    # zero-sized node is douyin's placeholder, not a demand
                    # for the user to solve anything.
                    if await element.is_visible():
                        return f"selector:{selector}"
                except Exception:
                    continue

            html = (await page.content() or "").lower()
            for marker in _CAPTCHA_HTML_MARKERS:
                if marker.lower() in html:
                    return f"html:{marker}"
        except Exception as err:
            # A detection failure is not a challenge. Saying otherwise would
            # abort parses that would have succeeded.
            logger.warning(f"[Camoufox] captcha detection failed: {err}")
        return ""

    # ------------------------------------------------------------------
    # Response handling
    # ------------------------------------------------------------------

    @staticmethod
    def _is_detail_response(url: str) -> bool:
        return any(marker in url for marker in _DETAIL_URL_MARKERS)

    @classmethod
    async def _await_detail(cls, responses: Any, target_aweme_id: str) -> dict:
        """Consume responses until one actually contains the target video.

        Loops rather than taking the first payload: douyin's feed endpoints
        answer on the same URL patterns, so an early response can be a list
        that simply does not contain what we asked for. No timeout here —
        the caller owns the deadline so the challenge poller can race it.
        """
        while True:
            payload = await responses.get()
            aweme = cls._pick_aweme(payload, target_aweme_id)
            if aweme is not None:
                return aweme

    @classmethod
    async def _poll_captcha(cls, page: Any) -> str:
        """Watch for a challenge for as long as the caller lets us run.

        Requires the SAME reason twice in a row before reporting it. A
        verification widget that appears for one sample and is gone by the
        next is page-init churn, not a demand for the user to solve
        anything — and calling that a captcha tells the user to give up on
        a parse that was about to succeed.
        """
        previous = ""
        while True:
            await asyncio.sleep(cls.CAPTCHA_SETTLE_SEC)
            reason = await cls._detect_captcha(page)
            if reason and reason == previous:
                return reason
            previous = reason

    @staticmethod
    def _pick_aweme(payload: dict[str, Any], target_aweme_id: str) -> dict | None:
        """`aweme_detail` outright, else the `aweme_list` entry we asked for.

        The list branch must not fall back to "first item": douyin's feed
        endpoints answer with whatever is trending, so returning item zero
        would hand the user a video they never asked for while reporting
        success.
        """
        detail = payload.get("aweme_detail")
        if isinstance(detail, dict) and detail:
            return detail

        for item in payload.get("aweme_list") or []:
            if isinstance(item, dict) and str(item.get("aweme_id")) == target_aweme_id:
                return item
        return None

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    @classmethod
    async def fetch_one_video(
        cls,
        url: str,
        user_id: str | None = None,
        user_agent: str | None = None,
    ) -> dict | None:
        """Parse one douyin video through a real browser.

        Signature matches `ABogusDouyinParser.parse` and
        the other tier entry points so `parse_chain` can call any
        tier through the same reference.

        Raises `DouyinParseError(CAPTCHA)` when douyin serves a challenge —
        an outcome the user can act on ("wait, or refresh the cookie"),
        which a bare `None` would erase.
        """
        from app.core.utils import Utils

        if not user_agent:
            from app.services.media.parsers.douyin_parse.ua_pool import pick_ua

            user_agent = pick_ua()

        logger.info(f"[Camoufox] parsing {str(url)[:80]}")
        cookie_text = await cls._get_user_cookie_text(user_id)

        browser = await cls._get_browser(user_agent)
        context = await browser.new_context()
        try:
            # Before the first navigation, so the very first request to
            # douyin already carries the session. Injecting afterwards costs
            # a reload, and the un-authenticated first load is itself a
            # signal douyin can act on.
            if cookie_text:
                cookies = cls._to_playwright_cookies(cookie_text)
                if cookies:
                    await context.add_cookies(cookies)
                    logger.info(f"[Camoufox] injected {len(cookies)} cookies")

            page = await context.new_page()

            # Collect detail responses as they arrive. Registering before
            # navigation matters: douyin fires the detail XHR during initial
            # render, and a listener attached afterwards misses it.
            responses: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

            async def _on_response(response: Any) -> None:
                if not cls._is_detail_response(response.url):
                    return
                try:
                    body = await response.json()
                except Exception:
                    return
                if isinstance(body, dict):
                    await responses.put(body)

            page.on(
                "response",
                lambda r: asyncio.create_task(_on_response(r)),
            )

            await page.goto(
                url, wait_until="domcontentloaded", timeout=cls.NAV_TIMEOUT_MS
            )

            target_aweme_id = Utils.match_aweme_id(page.url) or ""
            logger.debug(f"[Camoufox] target aweme_id={target_aweme_id}")

            # RACE the payload against the challenge, rather than deciding
            # "is there a captcha?" up front and only then waiting.
            #
            # Measured 2026-09-16 on aweme 7684532239228740849: the
            # `aweme/detail` response lands at t≈3.6s, while a one-shot
            # challenge check fires at t≈1.5s. Anything transiently in the
            # DOM at 1.5s — and douyin's verification widgets mount and
            # unmount during page init — killed a parse whose successful
            # response was still two seconds from arriving.
            #
            # Racing makes the answer self-evident instead of predictive:
            # if the data arrives, there was no blocking challenge, whatever
            # the DOM looked like mid-render. If a challenge is real, the
            # response never comes and the poller reports it well inside the
            # timeout — so we still fail fast, we just stop guessing early.
            detail_task = asyncio.create_task(
                cls._await_detail(responses, target_aweme_id)
            )
            captcha_task = asyncio.create_task(cls._poll_captcha(page))
            try:
                done, _pending = await asyncio.wait(
                    {detail_task, captcha_task},
                    timeout=cls.RESPONSE_TIMEOUT_SEC,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                for task in (detail_task, captcha_task):
                    if not task.done():
                        task.cancel()

            if detail_task in done and not detail_task.cancelled():
                aweme = detail_task.result()
                resolved_id = str(aweme.get("aweme_id") or target_aweme_id)
                logger.info(f"[Camoufox] got aweme_detail for {resolved_id}")
                await cls._cache_cookies(context, resolved_id)
                return aweme

            if captcha_task in done and not captcha_task.cancelled():
                reason = captcha_task.result()
                if reason:
                    logger.warning(f"[Camoufox] challenge detected ({reason})")
                    raise DouyinParseError(
                        DouyinFailure.CAPTCHA,
                        f"Douyin served a verification challenge ({reason})",
                    )

            logger.warning("[Camoufox] timed out waiting for detail response")
            return None
        except DouyinParseError:
            raise
        except Exception as err:
            logger.error(f"[Camoufox] parse failed: {err}")
            return None
        finally:
            # The context owns the cookies and the page; the browser stays
            # up for the next request.
            try:
                await context.close()
            except Exception as err:  # pragma: no cover - best-effort
                logger.debug(f"[Camoufox] context close failed: {err}")

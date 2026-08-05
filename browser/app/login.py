"""Generic QR-login machinery: page snapshots, the driver, the flow spec.

Same split as `validation.py`. A platform contributes a `LoginFlowSpec` - where
to navigate, which selectors hold the QR code, which on-page texts mean what,
and a **pure** judge - while this module owns everything platform-neutral:
driving Playwright, bounding every wait, turning failures into typed statuses.

The judge is pure on purpose. "What state is this page in?" is the part most
likely to be wrong and most expensive to test against a real platform, so it is
`LoginPageSnapshot -> LoginJudgement` with no Playwright types anywhere near it.
Reading the page (async, flaky, needs a browser) and interpreting the page
(sync, total, needs nothing) are separate jobs.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from .browser_runtime import build_launch_kwargs
from .config import get_settings
from .dom import (
    first_text,
    first_visible_attribute,
    is_selector_visible,
    visible_marker_texts,
)
from .schemas import EnvironmentConfig, SessionStatus


@dataclass(frozen=True)
class LoginPageSnapshot:
    """Everything the judge is allowed to look at. Pure data."""

    url: str
    # Visible markers meaning "still sitting on the login screen".
    login_texts: tuple[str, ...] = ()
    # Visible markers meaning "the code was scanned, confirm on your phone".
    scanned_texts: tuple[str, ...] = ()
    # Visible markers meaning "this QR code is dead".
    expired_texts: tuple[str, ...] = ()
    sms_input_visible: bool = False
    qrcode_visible: bool = False


@dataclass(frozen=True)
class LoginJudgement:
    status: SessionStatus
    reason: str


@dataclass(frozen=True)
class LoginProfile:
    """Who just logged in. All fields best-effort."""

    platform_user_id: str = ""
    username: str = ""
    avatar_url: str | None = None


@dataclass(frozen=True)
class LoginFlowSpec:
    platform: str
    # Page that renders the QR code.
    login_url: str
    # Where to read profile fields after login. Empty = stay put.
    profile_url: str
    # Selector candidates for the QR <img>, priority order.
    qrcode_selectors: tuple[str, ...]
    login_markers: tuple[str, ...]
    scanned_markers: tuple[str, ...]
    expired_markers: tuple[str, ...]
    # Clicking this re-issues an expired code.
    refresh_selectors: tuple[str, ...]
    sms_input_selectors: tuple[str, ...]
    sms_submit_selectors: tuple[str, ...]
    # Pure: what state is this page in?
    judge: Callable[[LoginPageSnapshot], LoginJudgement]
    # Pure: (scraped fields, cookies) -> who this is.
    parse_profile: Callable[
        [Mapping[str, str], Sequence[Mapping[str, Any]]], LoginProfile
    ]
    # field name -> selector candidates, read as text.
    profile_text_selectors: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    # field name -> (selector candidates, attribute name).
    profile_attr_selectors: Mapping[str, tuple[tuple[str, ...], str]] = field(
        default_factory=dict
    )


# --- driver -----------------------------------------------------------------


class LoginDriver:
    """Owns one live browser + context + page for the length of a login.

    Deliberately *not* an async context manager: its whole reason to exist is
    outliving the request that created it. Release is explicit, and the session
    registry is the only thing allowed to call it.
    """

    def __init__(self, spec: LoginFlowSpec, playwright: Any, browser: Any, context: Any, page: Any):
        self._spec = spec
        self._playwright = playwright
        self._browser = browser
        self._context = context
        self._page = page

    @property
    def spec(self) -> LoginFlowSpec:
        return self._spec

    async def snapshot(self) -> LoginPageSnapshot:
        spec = self._spec
        page = self._page
        return LoginPageSnapshot(
            url=page.url,
            login_texts=tuple(await visible_marker_texts(page, spec.login_markers)),
            scanned_texts=tuple(await visible_marker_texts(page, spec.scanned_markers)),
            expired_texts=tuple(await visible_marker_texts(page, spec.expired_markers)),
            sms_input_visible=await _any_selector_visible(page, spec.sms_input_selectors),
            qrcode_visible=await _any_selector_visible(page, spec.qrcode_selectors),
        )

    async def read_qrcode(self) -> str | None:
        """Current QR code as a `data:image/...` URL, or None.

        Bounded wait for the node to appear, because the login card is injected
        by client-side JS after `domcontentloaded` and reading immediately gets
        nothing on a cold page.
        """
        settings = get_settings()
        attempts = settings.login_qrcode_attempts
        found: str | None = None
        # Bounded poll - a fixed number of attempts, never `while True`
        # (spec 7.2). An unbounded wait here would hold a browser forever on a
        # page that simply never renders a code.
        for attempt in range(attempts):
            src = await first_visible_attribute(
                self._page, self._spec.qrcode_selectors, "src"
            )
            if src and src.startswith("data:image"):
                return src
            # A non-data src (remote image, placeholder) is still worth
            # reporting over nothing, but does not end the poll.
            found = src or found
            if attempt + 1 < attempts:
                await asyncio.sleep(settings.login_qrcode_poll_s)
        return found

    async def refresh_qrcode(self) -> str | None:
        """Click the platform's own "refresh" affordance and re-read the code.

        Reloading the whole page would also work but throws away any 2FA state
        the page is holding; the in-page refresh is what a human would click.
        """
        clicked = False
        for selector in self._spec.refresh_selectors:
            try:
                locator = self._page.locator(selector).first
                if not await locator.count() or not await locator.is_visible():
                    continue
                await locator.click(timeout=get_settings().login_click_timeout_ms)
                clicked = True
                break
            except Exception:
                continue
        if not clicked:
            return None
        return await self.read_qrcode()

    async def submit_sms_code(self, code: str) -> bool:
        """Type the code and submit. False = no input to type into."""
        settings = get_settings()
        target = None
        for selector in self._spec.sms_input_selectors:
            try:
                locator = self._page.locator(selector).first
                if await locator.count() and await locator.is_visible():
                    target = locator
                    break
            except Exception:
                continue
        if target is None:
            return False

        await target.fill(code, timeout=settings.login_click_timeout_ms)
        for selector in self._spec.sms_submit_selectors:
            try:
                button = self._page.locator(selector).first
                if await button.count() and await button.is_visible():
                    await button.click(timeout=settings.login_click_timeout_ms)
                    return True
            except Exception:
                continue
        # No explicit submit control found - platforms commonly auto-submit on
        # the last digit, and Enter is the fallback a human would use.
        await target.press("Enter", timeout=settings.login_click_timeout_ms)
        return True

    async def read_profile(self) -> LoginProfile:
        """Best effort. A missing display name must never fail a good login."""
        spec = self._spec
        if spec.profile_url:
            try:
                await self._page.goto(
                    spec.profile_url,
                    wait_until="domcontentloaded",
                    timeout=get_settings().nav_timeout_ms,
                )
                await self._page.wait_for_timeout(get_settings().settle_ms)
            except Exception:
                # Stay on whatever page we are on and read what we can.
                pass

        fields: dict[str, str] = {}
        for name, selectors in spec.profile_text_selectors.items():
            value = await first_text(self._page, selectors)
            if value:
                fields[name] = value
        for name, (selectors, attribute) in spec.profile_attr_selectors.items():
            value = await first_visible_attribute(self._page, selectors, attribute)
            if value:
                fields[name] = value

        cookies = await self._safe_cookies()
        return spec.parse_profile(fields, cookies)

    async def _safe_cookies(self) -> list[dict[str, Any]]:
        try:
            return list(await self._context.cookies())
        except Exception:
            return []

    async def storage_state(self) -> dict[str, Any]:
        """Plaintext session material. Returned as a dict, never a path.

        `storage_state(path=...)` would write credentials to disk, which spec
        7.6 forbids outright; the overload must not be used here.
        """
        return await self._context.storage_state()

    async def close(self) -> None:
        """Release every resource, best effort, in dependency order.

        Each step is guarded separately: a page that already crashed must not
        stop the browser process from being reaped, or a failed login leaks a
        Chromium for the container's lifetime.
        """
        for closer in (
            getattr(self._page, "close", None),
            getattr(self._context, "close", None),
            getattr(self._browser, "close", None),
            getattr(self._playwright, "stop", None),
        ):
            if closer is None:
                continue
            try:
                await closer()
            except Exception:
                continue


async def _any_selector_visible(page: Any, selectors: Sequence[str]) -> bool:
    for selector in selectors:
        if await is_selector_visible(page, selector):
            return True
    return False


async def open_login_driver(
    spec: LoginFlowSpec, environment: EnvironmentConfig | None
) -> LoginDriver:
    """Launch a browser, open the login page, hand back a live driver.

    Raises on failure - the caller classifies. A partially-built driver is torn
    down here rather than leaked, since there is no handle to release it with.

    Unlike validation, the context gets **no** storage_state: a QR login starts
    from a clean profile by definition, and seeding it with a dead session is
    how you land on a logged-in-looking shell with no QR code on it.
    """
    from playwright.async_api import async_playwright

    settings = get_settings()
    playwright = await async_playwright().start()
    browser = None
    context = None
    try:
        browser = await playwright.chromium.launch(**build_launch_kwargs(environment))
        context = await browser.new_context(**_login_context_kwargs(environment))
        page = await context.new_page()
        await page.goto(
            spec.login_url,
            wait_until="domcontentloaded",
            timeout=settings.nav_timeout_ms,
        )
        return LoginDriver(spec, playwright, browser, context, page)
    except Exception:
        for closer in (
            getattr(context, "close", None),
            getattr(browser, "close", None),
            playwright.stop,
        ):
            if closer is None:
                continue
            try:
                await closer()
            except Exception:
                continue
        raise


def _login_context_kwargs(environment: EnvironmentConfig | None) -> dict[str, Any]:
    """Same environment isolation as validation, minus storage_state.

    The account's environment is pinned at bind time on purpose: the fingerprint
    the platform sees while the account is created should be the one it sees
    forever after (design doc 3.2).
    """
    kwargs: dict[str, Any] = {}
    if environment is None:
        return kwargs
    if environment.user_agent:
        kwargs["user_agent"] = environment.user_agent
    if environment.locale:
        kwargs["locale"] = environment.locale
    if environment.timezone_id:
        kwargs["timezone_id"] = environment.timezone_id
    if environment.geo_lat is not None and environment.geo_lng is not None:
        kwargs["geolocation"] = {
            "latitude": environment.geo_lat,
            "longitude": environment.geo_lng,
        }
        kwargs["permissions"] = ["geolocation"]
    return kwargs

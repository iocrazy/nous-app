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
import logging
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Mapping, Sequence

from .browser_runtime import apply_stealth, build_launch_kwargs
from .config import get_settings
from .dom import (
    first_text,
    first_visible_attribute,
    is_selector_visible,
    visible_marker_texts,
)
from .schemas import EnvironmentConfig, SessionStatus

logger = logging.getLogger("nous_browser.login")


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
    """Who just logged in.

    Two classes of field, and the split is the whole point (2026-08-09):

    * ``platform_user_id`` is the **identity key**. The backend upserts
      ``social_accounts`` on ``(scope, platform, platform_user_id)``, so a value
      from a different namespace than last time does not degrade the account —
      it *forks* it into a second row and cuts the publish history in half.
      It is therefore resolved by this module from the spec's one declared
      cookie (``identity_cookie``) and **never** from a scraped field. A
      platform module cannot express a DOM identity path any more, because
      ``parse_profile`` is not handed the cookies and its return value's
      ``platform_user_id`` is overwritten below.
    * ``username`` / ``avatar_url`` / ``platform_handle`` are **display**.
      Best-effort as before: a console redesign that breaks a selector must
      leave a nameless account, never fail a login the user already completed.

    ``platform_handle`` is the human-facing account name (抖音号 / 小红书号).
    It used to be fed into ``platform_user_id`` whenever its selector happened
    to hit, which is exactly how one Douyin account ended up bound twice
    (``41cf16…`` from the cookie on 08-06, ``miopoo`` from the DOM on 08-09).
    Users rename it at will, so it is display material and nothing else.
    """

    platform_user_id: str = ""
    username: str = ""
    avatar_url: str | None = None
    platform_handle: str = ""


class IdentityUnresolved(Exception):
    """The identity cookie is not in the jar, so we do not know who this is.

    Deliberately **not** best-effort. Everything else about a profile degrades;
    this one fails the login. The alternative — carrying on with a blank or
    substituted identity — creates a duplicate account row whose publish history
    starts empty, and the user has no way to tell it apart from a fresh bind.
    """

    reason = "identity_unresolved"

    def __init__(self, platform: str, cookie: str) -> None:
        super().__init__(
            f"cannot identify the {platform} account: "
            f"cookie {cookie!r} was not present after login"
        )
        self.platform = platform
        self.cookie = cookie


def identity_from_cookies(
    identity_cookie: str, cookies: Sequence[Mapping[str, Any]]
) -> str:
    """The account's identity key, or "" when the cookie is absent.

    One cookie name, no fallback list, no DOM. A fallback would have to come
    from another namespace, and mixing namespaces under one unique key is the
    duplicate-row bug this function exists to make unrepresentable.
    """
    for cookie in cookies:
        if cookie.get("name") != identity_cookie:
            continue
        value = (cookie.get("value") or "").strip()
        if value:
            return value
    return ""


@dataclass(frozen=True)
class LoginFlowSpec:
    platform: str
    # THE identity source for this platform: the single cookie whose value is
    # the account's stable id. Required, and required to be exactly one — see
    # `LoginProfile`. Verified per platform against a live session; a guess here
    # is worse than a hard failure, because a value that changes between logins
    # silently forks the account.
    identity_cookie: str
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
    # Pure: scraped fields -> the DISPLAY half of the profile. Cookies are not
    # passed on purpose: identity comes from `identity_cookie` and nowhere else,
    # and any `platform_user_id` this returns is discarded.
    parse_profile: Callable[[Mapping[str, str]], LoginProfile]
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
        """Display fields best effort; the identity key is not negotiable.

        A missing display name must never fail a good login — but a missing
        *identity* must, and this is the boundary where that difference is
        enforced. The login path is the one caller that turns a profile into a
        durable account row, so it is the one caller that cannot accept
        "whoever this is". Session *validation* calls
        ``read_profile_from_page`` directly and keeps its old best-effort
        contract: a refresh that cannot identify the account simply refreshes
        nothing.

        Raises ``IdentityUnresolved`` — typed, so the caller can say *why* the
        login failed instead of reporting a generic scrape error.
        """
        cookies = await self._safe_cookies()
        try:
            profile = await read_profile_from_page(self._page, self._spec, cookies)
        except Exception as exc:  # noqa: BLE001 - display scrape is best-effort
            logger.warning(
                "profile scrape failed for platform=%s: %s",
                self._spec.platform,
                type(exc).__name__,
            )
            # The cookies are already in hand and owe nothing to the DOM, so a
            # broken page still yields a correctly-identified account.
            profile = LoginProfile(
                platform_user_id=identity_from_cookies(
                    self._spec.identity_cookie, cookies
                )
            )
        if not profile.platform_user_id:
            raise IdentityUnresolved(self._spec.platform, self._spec.identity_cookie)
        return profile

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


async def read_profile_from_page(
    page: Any, spec: LoginFlowSpec, cookies: Sequence[Mapping[str, Any]]
) -> LoginProfile:
    """Navigate to the profile page and scrape the account identity. Best effort.

    Module-level rather than a `LoginDriver` method because the session
    *validation* path needs the same scrape and has no driver: it holds its own
    page from a storage_state-seeded context. Duplicating the logic there is how
    the two copies drift, and this scrape is already fragile enough — the
    selectors are CSS-Modules hashes that move on every console deploy.

    Every step swallows its failure by design: a console redesign that breaks a
    display-name selector must degrade to a nameless account, never fail a login
    the user already completed or condemn a session that is actually alive.

    **The identity key is not part of that bargain.** It is resolved here, from
    the spec's single declared cookie, and stamped over whatever
    ``parse_profile`` returned — so no platform can accidentally (or
    deliberately) source it from the DOM. "" means the cookie was absent;
    turning that into a failure is the *caller's* call, because the login path
    and the validation path want opposite things from it (see
    ``LoginDriver.read_profile``).
    """
    if spec.profile_url:
        try:
            await page.goto(
                spec.profile_url,
                wait_until="domcontentloaded",
                timeout=get_settings().nav_timeout_ms,
            )
            await page.wait_for_timeout(get_settings().settle_ms)
        except Exception:
            # Stay on whatever page we are on and read what we can.
            pass

    fields: dict[str, str] = {}
    for name, selectors in spec.profile_text_selectors.items():
        value = await first_text(page, selectors)
        if value:
            fields[name] = value
    for name, (selectors, attribute) in spec.profile_attr_selectors.items():
        value = await first_visible_attribute(page, selectors, attribute)
        if value:
            fields[name] = value

    return replace(
        spec.parse_profile(fields),
        platform_user_id=identity_from_cookies(spec.identity_cookie, cookies),
    )


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
    # patchright，不是 playwright：drop-in fork，补 CDP 层泄露。
    # 四个 import 点必须一致 —— test_patchright_everywhere 会失败。
    from patchright.async_api import async_playwright

    settings = get_settings()
    playwright = await async_playwright().start()
    browser = None
    context = None
    try:
        browser = await playwright.chromium.launch(**build_launch_kwargs(environment))
        context = await browser.new_context(**_login_context_kwargs(environment))
        await apply_stealth(context)
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

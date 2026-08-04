"""Playwright launch/context plumbing plus the real browser health probe.

Everything that translates an `EnvironmentConfig` into Playwright arguments is a
pure function here, so the per-account isolation wiring is unit-testable without
a browser. `_probe_once` in validation.py is the only place that drives a real
Chromium.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any
from urllib.parse import urlsplit, unquote

from .config import get_settings
from .schemas import EnvironmentConfig

# Headed only. headless (even headless=new) leaves detectable traces and Douyin
# bounces the upload page to login, which reads as "cookie expired" - the exact
# intermittent false negative recorded in the reference implementation. See
# design doc 2.4: this is a decided item, not a tunable.
HEADLESS = False

LAUNCH_ARGS = (
    # Required in containers without a tuned seccomp profile.
    "--no-sandbox",
    "--disable-dev-shm-usage",
    # Drops the `navigator.webdriver` flag that Blink otherwise sets.
    "--disable-blink-features=AutomationControlled",
)

_ALLOWED_PROXY_SCHEMES = ("http", "https", "socks5")


class ProxyConfigError(ValueError):
    """Raised when proxy_url cannot be turned into Playwright proxy options."""


def parse_proxy_url(proxy_url: str) -> dict[str, str]:
    """`http://user:pass@host:port` -> Playwright proxy options.

    Credentials must be split out of the server URL: Playwright does not
    reliably honour inline userinfo, and leaving them in `server` also means
    they show up verbatim in Chromium's error strings.
    """
    raw = (proxy_url or "").strip()
    if not raw:
        raise ProxyConfigError("proxy_url is empty")

    parts = urlsplit(raw)
    if parts.scheme not in _ALLOWED_PROXY_SCHEMES:
        raise ProxyConfigError(
            f"unsupported proxy scheme {parts.scheme or '(none)'}; "
            f"expected one of {', '.join(_ALLOWED_PROXY_SCHEMES)}"
        )
    if not parts.hostname:
        raise ProxyConfigError("proxy_url has no host")

    server = f"{parts.scheme}://{parts.hostname}"
    if parts.port:
        server = f"{server}:{parts.port}"

    options: dict[str, str] = {"server": server}
    if parts.username:
        options["username"] = unquote(parts.username)
    if parts.password:
        options["password"] = unquote(parts.password)
    return options


def build_launch_kwargs(env: EnvironmentConfig | None) -> dict[str, Any]:
    """Options for `chromium.launch()`.

    The proxy is set at launch rather than per-context on purpose: we launch a
    dedicated browser per validation, so launch scope *is* account scope, and it
    sidesteps Chromium's caveat that per-context proxying needs a global proxy
    to have been declared up front. When S6 moves to a long-lived browser this
    has to move down to new_context().
    """
    kwargs: dict[str, Any] = {
        "headless": HEADLESS,
        "args": list(LAUNCH_ARGS),
    }
    if env is not None and env.proxy_url:
        kwargs["proxy"] = parse_proxy_url(env.proxy_url)
    return kwargs


def build_context_kwargs(
    env: EnvironmentConfig | None, storage_state: dict[str, Any]
) -> dict[str, Any]:
    """Options for `browser.new_context()`.

    storage_state is passed as a dict. Never a path - that is the whole point of
    "credentials do not touch disk" (spec 7.6).
    """
    kwargs: dict[str, Any] = {"storage_state": storage_state}
    if env is None:
        return kwargs

    if env.user_agent:
        kwargs["user_agent"] = env.user_agent
    if env.locale:
        kwargs["locale"] = env.locale
    if env.timezone_id:
        kwargs["timezone_id"] = env.timezone_id
    if env.geo_lat is not None and env.geo_lng is not None:
        kwargs["geolocation"] = {"latitude": env.geo_lat, "longitude": env.geo_lng}
        # Without the grant the page gets a permission prompt instead of coords.
        kwargs["permissions"] = ["geolocation"]
    return kwargs


def xvfb_socket_path(display: str) -> str | None:
    """Unix socket X creates for `display` (`:99` -> /tmp/.X11-unix/X99).

    Returns None for a display we cannot map to a local socket (e.g. a remote
    `host:0` form), which callers treat as "cannot confirm".
    """
    if not display or not display.startswith(":"):
        return None
    number = display[1:].split(".", 1)[0]
    if not number.isdigit():
        return None
    return f"/tmp/.X11-unix/X{number}"


def xvfb_ready(display: str | None = None) -> bool:
    settings = get_settings()
    path = xvfb_socket_path(display or settings.display)
    return bool(path) and os.path.exists(path)


# --- real browser probe -----------------------------------------------------
#
# /healthz must not hardcode readiness. The project has a scar from exactly that
# (a /health returning a literal {"status":"healthy"} while DBOS was down for
# three days). So we actually launch Chromium - cached, because a docker
# healthcheck would otherwise start a browser every 30s.

_probe_lock = asyncio.Lock()
_probe_cache: tuple[float, bool] | None = None


async def _launch_and_close() -> bool:
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=HEADLESS, args=list(LAUNCH_ARGS)
        )
        try:
            # Touching .version proves we are talking to a live browser process,
            # not just holding a handle that failed to start.
            return bool(browser.version)
        finally:
            await browser.close()


async def probe_browser_ready(*, force: bool = False) -> bool:
    """Can we actually start a headed Chromium right now?

    Also transitively covers Xvfb: headed Chromium cannot start without a
    working DISPLAY, so a dead Xvfb turns this False.
    """
    global _probe_cache
    settings = get_settings()
    now = time.monotonic()

    cached = _probe_cache
    if not force and cached is not None and now - cached[0] < settings.health_probe_ttl_s:
        return cached[1]

    async with _probe_lock:
        # Re-check: a concurrent caller may have refreshed while we queued.
        cached = _probe_cache
        now = time.monotonic()
        if not force and cached is not None and now - cached[0] < settings.health_probe_ttl_s:
            return cached[1]
        try:
            ready = await _launch_and_close()
        except Exception:
            ready = False
        _probe_cache = (time.monotonic(), ready)
        return ready


def reset_probe_cache() -> None:
    global _probe_cache
    _probe_cache = None

"""Read-only reconnaissance of a creator console page (spec §3.2, task T0).

Why this module exists
======================
Every platform adapter in this tree was written against a page nobody could
look at from code: the only way to learn "how many nodes carry this caption",
"does this file input accept several files", "what is the maxlength of that
box" was to have a human open DevTools. That is why the design spec is full of
`[TO-VERIFY]` markers, and why the xiaohongshu module still carries two
`[GUESS] UNVERIFIED` constants — nobody has ever had a tool that could answer
those questions with a number.

This module is that tool. It opens ONE allow-listed page with a real account's
cookies, optionally hands a probe file to a file input so a form that only
renders after a transfer will render, and answers with **counts** — never with
prose, never with the raw document.

What it deliberately cannot do
==============================
There is no code path here that activates a control, types into a field,
confirms a dialog, or hands anything to a platform. The Playwright surface this
module touches is exactly: `goto`, `wait_for`, `wait_for_timeout`,
`set_input_files`, `count`, `is_visible`, `nth`, `title`, `evaluate` (with the
two constant read-only scripts below), and `storage_state`.

That is not a promise in a runbook — `tests/test_inspect_units.py` reads this
file and fails if a word from the interaction vocabulary appears anywhere in
it, including in comments. A guard nobody can forget to run beats a note
nobody reads. It is also why the timeouts below come from `inspect_*` settings
rather than reusing the posting flow's: sharing those names would put the
forbidden vocabulary back into this file for no behavioural gain.

Two more constraints follow the same rule:

* **The URL is allow-listed against the platform's own creator hosts.** An
  endpoint that opens an arbitrary URL while holding a user's live cookies is
  a credentialed SSRF, and "only we call it today" is not a property of the
  code.
* **Nothing leaves here unbounded.** Counts are capped, the visible-text
  excerpt is truncated and scrubbed, and the document itself is never
  returned. A recon answer carrying the page source would be a copy of
  someone's account contents sitting in a log or a terminal buffer.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Sequence
from urllib.parse import urlsplit

from .assets import AssetError, StagedAsset, stage_assets
from .browser_runtime import (
    ProxyConfigError,
    apply_stealth,
    build_context_kwargs,
    build_launch_kwargs,
)
from .config import get_settings
from .dom import visible_marker_texts
from .redaction import scrub
from .schemas import (
    MAX_EXCERPT_CHARS,
    InputSummary,
    InspectRequest,
    InspectResponse,
    SelectorCount,
    SessionStatus,
    TextCount,
)
from .validation import ProbeKind, classify_playwright_error, storage_state_is_empty

logger = logging.getLogger("nous_browser.inspect")

# Read-side ceilings. The request-side ones (how many probes, how long an
# excerpt) live on the wire contract in `schemas.py`, so there is exactly one
# place a caller's limits are declared.
MAX_INPUT_SUMMARY = 80
# How many matches of one probe we bother testing for visibility. A caption
# matching 200 nodes has already told us what we needed to know.
MAX_VISIBILITY_SAMPLE = 20

SEED_ROLE_PREFIX = "seed"

# https only. The creator consoles are https-only, and allowing http would let
# a typo downgrade a request that carries session cookies.
ALLOWED_SCHEME = "https"

_KIND_TO_STATUS = {
    ProbeKind.INVALID: SessionStatus.SESSION_INVALID,
    ProbeKind.PROXY_FAILED: SessionStatus.PROXY_FAILED,
    ProbeKind.TIMEOUT: SessionStatus.TIMEOUT,
    ProbeKind.ERROR: SessionStatus.FAILED,
}


@dataclass(frozen=True)
class InspectSpec:
    """What a platform contributes: where we may look, and how "logged out" reads.

    `allowed_hosts` is the platform module's own `CREATOR_HOSTS` tuple,
    imported rather than restated. A second copy of an allow-list is a second
    thing to forget when a platform moves a host.

    Absence is meaningful, exactly as it is for the other registries: a
    platform that registers no spec cannot be inspected at all, rather than
    falling back to "anything goes".
    """

    platform: str
    allowed_hosts: tuple[str, ...]
    login_text_markers: tuple[str, ...]


def url_refusal(url: str, allowed_hosts: Sequence[str]) -> str | None:
    """Reason this URL may not be opened, or None. Pure.

    Runs before a browser exists, so a refused URL costs nothing and — more to
    the point — is never fetched. Order is deliberate: scheme, then embedded
    credentials, then host. `https://creator.douyin.com@evil.example/` has a
    hostname of `evil.example`, so the host check alone would already refuse
    it; the userinfo check exists so the refusal *reason* says what actually
    happened instead of blaming a host the caller never meant to reach.
    """
    raw = (url or "").strip()
    if not raw:
        return "url is empty"
    parts = urlsplit(raw)
    scheme = parts.scheme.lower()
    if scheme != ALLOWED_SCHEME:
        return (
            f"url scheme '{scheme or '(none)'}' is not allowed; "
            f"expected {ALLOWED_SCHEME}"
        )
    if parts.username or parts.password:
        return "url carries embedded credentials in its userinfo"
    host = (parts.hostname or "").lower()
    if not host:
        return "url has no host"
    if host not in tuple(allowed_hosts):
        return (
            f"host '{host}' is outside this platform's creator hosts; "
            f"allowed: {', '.join(allowed_hosts)}"
        )
    return None


def _session_lost(
    url: str, allowed_hosts: Sequence[str], visible_login: list[str]
) -> str | None:
    """Why this is not a usable console page, or None. Pure.

    Host first, then login text — same order and same reason as
    `verify._session_lost`: a logged-out redirect keeps the original path in a
    `redirect_url` query parameter, so a page that still mentions the path can
    be the login screen.
    """
    host = (urlsplit(url or "").hostname or "").lower()
    if host not in tuple(allowed_hosts):
        return f"navigated away from the creator host (host={host or 'unknown'})"
    if visible_login:
        return "login prompt visible on the page: " + ", ".join(visible_login)
    return None


# Read-only. Reads attributes and geometry; it never reads `.value`, because an
# input's value is whatever the account owner last typed and has no business
# leaving this process.
_INPUT_SUMMARY_JS = """
(limit) => {
  const nodes = document.querySelectorAll(
    'input, textarea, [contenteditable="true"], [contenteditable=""]'
  );
  const out = [];
  for (const el of nodes) {
    if (out.length >= limit) break;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    out.push({
      tag: el.tagName.toLowerCase(),
      type: el.getAttribute('type'),
      accept: el.getAttribute('accept'),
      multiple: el.hasAttribute('multiple'),
      name: el.getAttribute('name'),
      elem_id: el.getAttribute('id'),
      placeholder: el.getAttribute('placeholder')
        || el.getAttribute('data-placeholder'),
      maxlength: el.getAttribute('maxlength'),
      contenteditable: el.getAttribute('contenteditable'),
      class_name: (el.getAttribute('class') || '').slice(0, 160),
      visible: !!(rect.width || rect.height)
        && style.visibility !== 'hidden'
        && style.display !== 'none'
    });
  }
  return {total: nodes.length, items: out};
}
"""

_BODY_TEXT_JS = "() => (document.body && document.body.innerText) || ''"


def _safe_locator(build: Any) -> Any | None:
    """Build a locator, or None if even *constructing* it fails.

    Locator construction is not obviously fallible, which is exactly why it is
    wrapped: Playwright raises there for a malformed selector, and a page that
    detached mid-read raises for everything. One bad probe out of forty must not
    cost the whole page read — this call is at the end of a browser launch, a
    navigation and possibly an upload.
    """
    try:
        return build()
    except Exception:  # noqa: BLE001 - a probe we cannot build is a probe error
        return None


async def _safe_count(locator: Any) -> int | None:
    if locator is None:
        return None
    try:
        return int(await locator.count())
    except Exception:  # noqa: BLE001 - an unusable locator is not a crash
        return None


async def _visible_count(locator: Any, total: int) -> int:
    """How many of the first `MAX_VISIBILITY_SAMPLE` matches are on screen.

    Visibility is reported separately from presence because the two answer
    different questions, and conflating them is a documented trap here:
    platforms keep hidden login nodes in the authenticated app shell, so a
    presence-only check calls a healthy session logged out.
    """
    seen = 0
    for index in range(min(total, MAX_VISIBILITY_SAMPLE)):
        try:
            if await locator.nth(index).is_visible():
                seen += 1
        except Exception:  # noqa: BLE001 - a re-render is not evidence
            continue
    return seen


async def _count_text(page: Any, probe: str) -> TextCount:
    """Exact / exact-visible / substring counts for one caption.

    All three, because each answers a question the others cannot. `exact` is
    the number the calibration standard asks for ("不等于 1 就是错的").
    `substring` exists because 「允许」 is a substring of 「不允许」 — the two
    numbers differing IS the finding. `exact_visible` separates "in the DOM"
    from "on screen".
    """
    exact_locator = _safe_locator(lambda: page.get_by_text(probe, exact=True))
    exact = await _safe_count(exact_locator)
    if exact is None:
        return TextCount(error="probe could not be evaluated")
    substring = await _safe_count(
        _safe_locator(lambda: page.get_by_text(probe, exact=False))
    )
    return TextCount(
        exact=exact,
        exact_visible=await _visible_count(exact_locator, exact),
        substring=substring if substring is not None else 0,
    )


async def _count_selector(page: Any, selector: str) -> SelectorCount:
    locator = _safe_locator(lambda: page.locator(selector))
    total = await _safe_count(locator)
    if total is None:
        # A malformed selector is a caller mistake worth reporting as such,
        # not a reason to lose the rest of an expensive page read.
        return SelectorCount(error="selector could not be evaluated")
    return SelectorCount(total=total, visible=await _visible_count(locator, total))


def _clip(value: Any) -> Any:
    """Bound one attribute value. Page-controlled strings are never unbounded."""
    if isinstance(value, str):
        return scrub(value, max_len=200)
    return value


@dataclass(frozen=True)
class _Observation:
    """One page read. Pure data — no Playwright objects escape the driver."""

    url_after: str
    page_title: str
    texts: dict[str, TextCount]
    selectors: dict[str, SelectorCount]
    body_text_excerpt: str
    body_text_truncated: bool
    input_summary: list[InputSummary]
    input_total: int


async def _observe(page: Any, request: InspectRequest) -> _Observation:
    texts: dict[str, TextCount] = {}
    for probe in request.text_probes:
        texts[probe] = await _count_text(page, probe)

    selectors: dict[str, SelectorCount] = {}
    for selector in request.selector_probes:
        selectors[selector] = await _count_selector(page, selector)

    try:
        raw_body = str(await page.evaluate(_BODY_TEXT_JS))
    except Exception:  # noqa: BLE001
        raw_body = ""
    limit = min(request.excerpt_chars, MAX_EXCERPT_CHARS)
    collapsed = " ".join(raw_body.split())
    excerpt = scrub(collapsed, max_len=limit) if limit else ""

    try:
        summary_raw = await page.evaluate(_INPUT_SUMMARY_JS, MAX_INPUT_SUMMARY)
    except Exception:  # noqa: BLE001
        summary_raw = {}
    if not isinstance(summary_raw, dict):
        summary_raw = {}
    items = [
        InputSummary(**{key: _clip(value) for key, value in item.items()})
        for item in (summary_raw.get("items") or [])[:MAX_INPUT_SUMMARY]
        if isinstance(item, dict)
    ]

    try:
        page_title = str(await page.title())
    except Exception:  # noqa: BLE001
        page_title = ""

    return _Observation(
        url_after=scrub(page.url),
        page_title=scrub(page_title, max_len=200),
        texts=texts,
        selectors=selectors,
        body_text_excerpt=excerpt,
        body_text_truncated=len(collapsed) > limit,
        input_summary=items,
        input_total=int(summary_raw.get("total") or 0),
    )


async def _seed(page: Any, request: InspectRequest, paths: list[str]) -> None:
    """Hand the probe files to one file input, so a lazy form renders.

    `state="attached"` rather than visible: these inputs sit hidden behind
    styled drop zones, and waiting for visibility waits forever — the same
    finding `douyin_uploader` records at its own file input.

    The input is addressed by selector + index rather than "the first one",
    because the first has already been the wrong one on this platform: the
    cover dialog exposes four hidden inputs and index 0 is an AI reference
    image, which accepts a file and silently does nothing with it.
    """
    settings = get_settings()
    locator = page.locator(request.seed_selector).nth(request.seed_input_index)
    await locator.wait_for(state="attached", timeout=settings.inspect_form_timeout_ms)
    await locator.set_input_files(paths, timeout=settings.inspect_upload_wait_s * 1000)
    await page.wait_for_timeout(request.seed_wait_ms)


@asynccontextmanager
async def _staged(request: InspectRequest) -> AsyncIterator[list[StagedAsset]]:
    """The probe files on local disk, in request order; nothing when there are none."""
    if not request.seed_files:
        yield []
        return
    items = [
        (f"{SEED_ROLE_PREFIX}:{index}", item)
        for index, item in enumerate(request.seed_files)
    ]
    async with stage_assets(items) as staged:
        yield [staged[role] for role, _ in items]


def _failure(kind: ProbeKind, message: str, detail: dict[str, Any]) -> InspectResponse:
    body = dict(detail)
    body.setdefault("reason", kind.value)
    return InspectResponse(
        success=False,
        status=_KIND_TO_STATUS[kind],
        message=message,
        detail=body,
    )


async def _run_once(spec: InspectSpec, request: InspectRequest) -> InspectResponse:
    # patchright, not playwright: drop-in fork covering the CDP-layer leaks.
    # All import sites must agree — `test_patchright_everywhere` enforces it.
    from patchright.async_api import async_playwright

    settings = get_settings()
    proxy_configured = bool(
        request.environment is not None and request.environment.proxy_url
    )

    try:
        launch_kwargs = build_launch_kwargs(request.environment)
    except ProxyConfigError as exc:
        return _failure(
            ProbeKind.PROXY_FAILED,
            scrub(str(exc)),
            {"stage": "proxy_config", "platform": spec.platform},
        )

    context_kwargs = build_context_kwargs(request.environment, request.storage_state)
    started = time.monotonic()

    try:
        async with _staged(request) as staged:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(**launch_kwargs)
                try:
                    context = await browser.new_context(**context_kwargs)
                    await apply_stealth(context)
                    page = await context.new_page()
                    await page.goto(
                        request.url,
                        wait_until="domcontentloaded",
                        timeout=settings.nav_timeout_ms,
                    )
                    await page.wait_for_timeout(request.settle_ms)

                    visible_login = await visible_marker_texts(
                        page, spec.login_text_markers
                    )
                    lost = _session_lost(page.url, spec.allowed_hosts, visible_login)
                    if lost is not None:
                        return _failure(
                            ProbeKind.INVALID,
                            lost,
                            {
                                "stage": "session",
                                "platform": spec.platform,
                                "url_after": scrub(page.url),
                                "login_markers": visible_login,
                            },
                        )

                    if staged:
                        await _seed(page, request, [asset.path for asset in staged])

                    observation = await _observe(page, request)

                    # Harvest the renewed session before the context dies. The
                    # platform rotates cookies on any authenticated page load,
                    # so discarding them would make a recon run a net DRAIN on
                    # session lifetime (same argument as `verify._read_once`).
                    try:
                        fresh = await context.storage_state()
                    except Exception:  # noqa: BLE001
                        fresh = None

                    return InspectResponse(
                        success=True,
                        status=SessionStatus.SESSION_VALID,
                        message="page read",
                        detail={
                            "platform": spec.platform,
                            "stage": "observe",
                            "elapsed_s": round(time.monotonic() - started, 1),
                            "seeded": len(staged),
                        },
                        url_after=observation.url_after,
                        page_title=observation.page_title,
                        texts=observation.texts,
                        selectors=observation.selectors,
                        body_text_excerpt=observation.body_text_excerpt,
                        body_text_truncated=observation.body_text_truncated,
                        input_summary=observation.input_summary,
                        input_total=observation.input_total,
                        seeded_files=[asset.filename for asset in staged],
                        updated_storage_state=fresh or None,
                    )
                finally:
                    # Explicit teardown on every path (spec §3.2 "勘探结束显式
                    # 调 close"): a context left behind holds live cookies.
                    await browser.close()
    except AssetError as exc:
        return _failure(
            ProbeKind.ERROR,
            exc.message,
            {**exc.detail, "stage": "assets", "platform": spec.platform},
        )
    except Exception as exc:  # noqa: BLE001 - every failure becomes a typed status
        raw = f"{type(exc).__name__}: {exc}"
        return _failure(
            classify_playwright_error(raw),
            scrub(raw),
            {
                "stage": "navigate",
                "platform": spec.platform,
                "proxy_configured": proxy_configured,
            },
        )


async def run_inspect(spec: InspectSpec, request: InspectRequest) -> InspectResponse:
    """Open one allow-listed page and report what is on it.

    One attempt, no retries. The session check and the read-back both retry
    because they run unattended on a schedule; a recon run has a human waiting
    on the answer and re-running it is a keystroke, whereas each retry is
    another automated hit on a creator console — the thing §D8 asks us to spend
    sparingly.

    Total by construction: every failure below becomes a typed status, so a
    caller never has to parse a traceback to find out whether the account is
    dead or our container is.
    """
    if storage_state_is_empty(request.storage_state):
        return InspectResponse(
            success=False,
            status=SessionStatus.SESSION_INVALID,
            message="storage_state contains no cookies or origins",
            detail={
                "platform": spec.platform,
                "stage": "fail_fast",
                "reason": "empty_storage_state",
            },
        )

    result = await _run_once(spec, request)
    logger.info(
        "[inspect] platform=%s status=%s seeded=%d",
        spec.platform,
        result.status.value,
        len(request.seed_files),
    )
    return result


__all__ = [
    "ALLOWED_SCHEME",
    "InspectSpec",
    "MAX_INPUT_SUMMARY",
    "MAX_VISIBILITY_SAMPLE",
    "run_inspect",
    "url_refusal",
]

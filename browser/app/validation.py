"""Generic DOM-based session validation runner.

A platform contributes a `DomValidationSpec` (where to navigate, what counts as
"still logged in", which on-page texts mean "logged out") and this module owns
everything platform-neutral: retries, budgets, error classification, credential
hygiene. Splitting it this way keeps the per-platform validator a *single*
function - the reference implementation's worst bug was having two copies of
Douyin's check, only one of which ever got fixed.

Platforms that do not validate via DOM at all (e.g. a signing-machine style
integration) register their own async validator instead; the registry stores
callables, not specs.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Sequence

from .browser_runtime import (
    ProxyConfigError,
    apply_stealth,
    build_context_kwargs,
    build_launch_kwargs,
)
from .config import get_settings
from .dom import visible_marker_texts
from .redaction import scrub
from .schemas import EnvironmentConfig, SessionResult, SessionStatus

logger = logging.getLogger(__name__)


class ProbeKind(str, Enum):
    VALID = "valid"
    INVALID = "invalid"
    PROXY_FAILED = "proxy_failed"
    TIMEOUT = "timeout"
    ERROR = "error"


@dataclass(frozen=True)
class Judgement:
    """Outcome of reading a settled page. Pure data - no Playwright types."""

    valid: bool
    reason: str


@dataclass(frozen=True)
class ProbeOutcome:
    kind: ProbeKind
    reason: str
    detail: dict[str, Any]


@dataclass(frozen=True)
class DomValidationSpec:
    platform: str
    target_url: str
    # Texts whose presence on the settled page means "logged out".
    login_text_markers: tuple[str, ...]
    # Pure: (final_url, visible_login_texts) -> Judgement.
    judge: Callable[[str, Sequence[str]], Judgement]


# Chromium net errors that mean "the proxy itself is the problem". Surfacing
# these as session_invalid would send the user off re-scanning a QR code to fix
# a proxy outage, which is why the status enum separates them.
PROXY_ERROR_TOKENS = (
    "ERR_PROXY_CONNECTION_FAILED",
    "ERR_TUNNEL_CONNECTION_FAILED",
    "ERR_SOCKS_CONNECTION_FAILED",
    "ERR_SOCKS_CONNECTION_HOST_UNREACHABLE",
    "ERR_PROXY_AUTH_REQUESTED",
    "ERR_PROXY_AUTH_UNSUPPORTED",
    "ERR_UNEXPECTED_PROXY_AUTH",
    "ERR_NO_SUPPORTED_PROXIES",
    "ERR_PROXY_CERTIFICATE_INVALID",
)

_TIMEOUT_TOKENS = ("timeout", "timed out", "exceeded")


def classify_playwright_error(message: str) -> ProbeKind:
    """Map a Playwright/Chromium error string to a probe outcome kind.

    Pure and table-driven so the classification can be tested without provoking
    real network failures.

    A timeout stays a timeout even when a proxy is configured. A blackholing
    proxy does surface as a timeout, but so does a merely slow platform, and
    guessing "proxy_failed" from a timeout would make that status untrustworthy
    - it is supposed to mean "stop retrying and go fix the proxy". Callers get
    `proxy_configured` in `detail` instead and can word their message
    accordingly.
    """
    text = message or ""
    if any(token in text.upper() for token in PROXY_ERROR_TOKENS):
        return ProbeKind.PROXY_FAILED

    if any(token in text.lower() for token in _TIMEOUT_TOKENS):
        return ProbeKind.TIMEOUT

    return ProbeKind.ERROR


def storage_state_is_empty(storage_state: dict[str, Any]) -> bool:
    """No cookies and no origins - cannot possibly be a live session.

    Fail fast (spec 7.7): a browser launch costs seconds, this costs nothing.
    """
    if not storage_state:
        return True
    return not storage_state.get("cookies") and not storage_state.get("origins")


async def _read_profile_best_effort(
    platform: str, page: Any, context: Any
) -> dict[str, Any] | None:
    """Scrape the account identity off an already-authenticated page.

    Returns None whenever anything at all goes wrong, and callers must treat
    that as "no profile this time", never as a bad session. **A failed scrape
    must not change a validation verdict** — the selectors are CSS-Modules
    hashes that move on every console deploy, and letting a stale selector
    condemn a live session would be the worst possible trade.

    Imports are deferred: `login` and the platform registry pull in the
    platform modules, which import this one.
    """
    try:
        from .login import read_profile_from_page
        from .platforms import get_login_flow

        spec = get_login_flow(platform)
        if spec is None:
            return None

        try:
            cookies = list(await context.cookies())
        except Exception:
            cookies = []

        profile = await read_profile_from_page(page, spec, cookies)
        fields = {
            # Reported, never written back to the identity column: the backend's
            # `update_profile` refuses `platform_user_id` on purpose (correcting
            # an id is a re-bind, not a profile refresh). It is here so an
            # operator can see what a live session says it is.
            "platform_user_id": profile.platform_user_id or None,
            "username": profile.username or None,
            "avatar_url": profile.avatar_url or None,
            "platform_handle": profile.platform_handle or None,
        }
        # All-empty is indistinguishable from "selectors all missed"; sending
        # `{}` would let a caller overwrite a good stored name with nothing.
        return fields if any(v for v in fields.values()) else None
    except Exception:  # noqa: BLE001 - best effort by contract
        logger.debug("profile scrape failed for platform=%s", platform, exc_info=True)
        return None


async def _probe_once(
    spec: DomValidationSpec,
    storage_state: dict[str, Any],
    env: EnvironmentConfig | None,
) -> ProbeOutcome:
    """One full launch -> navigate -> settle -> judge cycle."""
    # patchright，不是 playwright：drop-in fork，补 CDP 层泄露。
    # 四个 import 点必须一致 —— test_patchright_everywhere 会失败。
    from patchright.async_api import async_playwright

    settings = get_settings()
    proxy_configured = bool(env is not None and env.proxy_url)

    try:
        launch_kwargs = build_launch_kwargs(env)
    except ProxyConfigError as exc:
        return ProbeOutcome(
            kind=ProbeKind.PROXY_FAILED,
            reason=scrub(str(exc)),
            detail={"stage": "proxy_config"},
        )

    context_kwargs = build_context_kwargs(env, storage_state)

    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(**launch_kwargs)
            try:
                context = await browser.new_context(**context_kwargs)
                await apply_stealth(context)
                page = await context.new_page()
                await page.goto(
                    spec.target_url,
                    wait_until="domcontentloaded",
                    timeout=settings.nav_timeout_ms,
                )
                # Deliberately a fixed settle instead of wait_for_url(exact, 5s):
                # the platform sometimes lands on the target and *then* bounces,
                # and sometimes arrives late. Sampling after a settle beats
                # racing a strict matcher (that matcher is the documented source
                # of intermittent false "expired" verdicts upstream).
                await page.wait_for_timeout(settings.settle_ms)

                final_url = page.url
                visible = await visible_marker_texts(page, spec.login_text_markers)
                judgement = spec.judge(final_url, visible)
                # final_url is platform navigation state, not credential
                # material, and it is what makes a misjudgement debuggable.
                detail: dict[str, Any] = {
                    "final_url": scrub(final_url),
                    "login_markers": visible,
                }
                if judgement.valid:
                    # Read the account identity while we already hold a live
                    # authenticated page. Before this, profile was scraped ONLY
                    # during the login flow, so an account bound before a
                    # selector fix kept showing its fallback cookie id forever —
                    # nothing in the system could ever refresh it. Callers get
                    # `detail["profile"]` and can write it back.
                    #
                    # Strictly after `judge`: this navigates away from the page
                    # the verdict was read from.
                    profile = await _read_profile_best_effort(spec.platform, page, context)
                    if profile is not None:
                        detail["profile"] = profile
                return ProbeOutcome(
                    kind=ProbeKind.VALID if judgement.valid else ProbeKind.INVALID,
                    reason=judgement.reason,
                    detail=detail,
                )
            finally:
                await browser.close()
    except Exception as exc:  # noqa: BLE001 - every failure becomes a typed status
        raw = f"{type(exc).__name__}: {exc}"
        return ProbeOutcome(
            kind=classify_playwright_error(raw),
            reason=scrub(raw),
            # A timeout on a proxied account is ambiguous between "slow platform"
            # and "proxy blackholing traffic"; surfacing the flag lets the caller
            # say so without the status having to guess.
            detail={"stage": "navigate", "proxy_configured": proxy_configured},
        )


_KIND_TO_STATUS = {
    ProbeKind.INVALID: SessionStatus.SESSION_INVALID,
    ProbeKind.PROXY_FAILED: SessionStatus.PROXY_FAILED,
    ProbeKind.TIMEOUT: SessionStatus.TIMEOUT,
    ProbeKind.ERROR: SessionStatus.FAILED,
}


def _result_from_outcome(
    spec: DomValidationSpec, outcome: ProbeOutcome, attempts: int
) -> SessionResult:
    status = _KIND_TO_STATUS[outcome.kind]
    detail = dict(outcome.detail)
    detail["attempts"] = attempts
    detail["platform"] = spec.platform
    return SessionResult(
        success=False, status=status, message=outcome.reason, detail=detail
    )


async def run_dom_session_validation(
    spec: DomValidationSpec,
    storage_state: dict[str, Any],
    env: EnvironmentConfig | None = None,
) -> SessionResult:
    """Validate a session by loading the platform's authenticated page.

    Three-piece set from the design doc (7.1):
      1. headed browser        - enforced in browser_runtime.HEADLESS
      2. retry N times         - the loop below
      3. lenient judgement     - spec.judge, after a settle rather than a strict
                                 wait_for_url
    """
    settings = get_settings()

    if storage_state_is_empty(storage_state):
        return SessionResult(
            success=False,
            status=SessionStatus.SESSION_INVALID,
            message="storage_state contains no cookies or origins",
            detail={"platform": spec.platform, "attempts": 0, "stage": "fail_fast"},
        )

    deadline = time.monotonic() + settings.validate_total_timeout_s
    last: ProbeOutcome | None = None
    attempts = 0

    # Bounded loop, never `while True` (spec 7.2).
    for attempt in range(1, settings.validate_attempts + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0 or (attempt > 1 and remaining < settings.settle_ms / 1000):
            break

        attempts = attempt
        budget = min(remaining, settings.validate_attempt_timeout_s)
        try:
            outcome = await asyncio.wait_for(
                _probe_once(spec, storage_state, env), timeout=budget
            )
        except asyncio.TimeoutError:
            last = ProbeOutcome(
                kind=ProbeKind.TIMEOUT,
                reason=f"validation attempt exceeded {budget:.0f}s budget",
                detail={"stage": "attempt_budget"},
            )
            continue

        if outcome.kind is ProbeKind.VALID:
            detail = dict(outcome.detail)
            detail["attempts"] = attempt
            detail["platform"] = spec.platform
            return SessionResult(
                success=True,
                status=SessionStatus.SESSION_VALID,
                message=outcome.reason,
                detail=detail,
            )

        if outcome.kind is ProbeKind.PROXY_FAILED:
            # Retrying a dead proxy just burns the caller's budget, and the
            # caller can retry cheaply once the proxy is fixed.
            return _result_from_outcome(spec, outcome, attempt)

        last = outcome

    if last is None:
        last = ProbeOutcome(
            kind=ProbeKind.TIMEOUT,
            reason="no validation attempt could run within the total budget",
            detail={"stage": "total_budget"},
        )
    return _result_from_outcome(spec, last, attempts)

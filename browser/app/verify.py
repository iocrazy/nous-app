"""Generic publish read-back runner (P1-3).

A platform contributes a `VerifySpec` — where its works list lives, which hosts
count as "still inside the console", which texts mean "logged out", and a pure
reader that turns a settled page into a `ReadbackJudgement`. Everything
platform-neutral (launch, budget, error classification, status mapping,
credential hygiene) lives here, exactly as `validation.py` does it for the
session check.

What a read-back is for
=======================
`run_publish` can only report that the platform's editor accepted us. For a
scheduled post that is hours away from the truth, and `douyin_publish` says as
much: its outcome carries `published_url=None` because the post-publish redirect
has no identifier in it. Something has to go back later and look.

The three-way answer is the design
==================================
`SessionStatus.PUBLISHED` (it is live), `SessionStatus.NOT_PUBLISHED` (we read
the platform and it is not live), and everything else (we did not find out).
Collapsing the last two — the obvious shortcut, since both are "not success" —
is precisely the bug this exists to prevent: it would let one container outage
mark a batch of perfectly good posts as failed, and it would make the honest
"the platform refused this" indistinguishable from "our browser fell over".

Only ONE navigation happens per read-back, and it goes straight to the works
list. There is deliberately no separate `validate_session` call first: that
would double the page loads against the platform for a job that runs on a
schedule, and doubling automated hits on a creator console is a risk-control
signal in its own right. The session verdict instead reuses the platform's own
`login_text_markers` / `creator_hosts` **data** (Douyin's come straight out of
`platforms/douyin.py`), so there is no second implementation of "is this session
alive" to rot out of sync — which is the failure this repo's one-validator rule
actually guards against.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

from .browser_runtime import (
    ProxyConfigError,
    apply_stealth,
    build_context_kwargs,
    build_launch_kwargs,
)
from .config import get_settings
from .dom import visible_marker_texts
from .redaction import scrub
from .schemas import EnvironmentConfig, SessionStatus, VerifyProbe, VerifyPublishResponse
from .validation import ProbeKind, classify_playwright_error, storage_state_is_empty

logger = logging.getLogger("nous_browser.verify")


class ReadbackVerdict(str, Enum):
    """The neutral conclusion of one read-back.

    `INCONCLUSIVE` is a first-class member rather than an absence, because the
    caller's behaviour for it is specific and different from both others:
    retry, then — if the budget runs out — ask a human. Silence is not on the
    menu (CLAUDE.md: silent no-op is not acceptable on a trigger path).
    """

    LIVE = "live"
    NOT_LIVE = "not_live"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class ReadbackJudgement:
    """Outcome of reading a settled works page. Pure data, no Playwright types.

    `reason` is a stable machine code and lands in `detail["reason"]` — it is
    what the blocked work item shows the user, so it has to survive the trip
    intact rather than being rebuilt from prose.
    """

    verdict: ReadbackVerdict
    reason: str
    message: str
    item_id: str | None = None
    published_url: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


# (page, title) -> judgement. A coroutine rather than a description of DOM
# steps, for the same reason the publisher registry stores callables: a platform
# whose works list is an API call rather than a page must be expressible without
# reshaping the registry.
ReadbackReader = Callable[[Any, str], Awaitable[ReadbackJudgement]]


@dataclass(frozen=True)
class VerifySpec:
    platform: str
    # The account's own works list.
    works_url: str
    # Hosts that still count as "inside the creator console". Landing anywhere
    # else means the session bounced us out.
    creator_hosts: tuple[str, ...]
    # Texts whose presence means "logged out". Same tuple the session validator
    # uses — see the module docstring.
    login_text_markers: tuple[str, ...]
    read: ReadbackReader


def _session_lost(url: str, creator_hosts: tuple[str, ...], visible_login: list[str]):
    """Why this page is not a usable works list, or None if it is. Pure.

    Host first, then login text: a logged-out redirect keeps the original path
    in a `redirect_url` query parameter, so a page that still *mentions* the
    works path can be the login screen (the exact trap
    `judge_douyin_session` documents).
    """
    host = (urlsplit(url or "").hostname or "").lower()
    if host not in creator_hosts:
        return f"navigated away from the creator host (host={host or 'unknown'})"
    if visible_login:
        return "login prompt visible on the works page: " + ", ".join(visible_login)
    return None


@dataclass(frozen=True)
class _ReadOutcome:
    """One attempt's result. `judgement` is non-None only when the page was
    actually read; every other path is a typed failure and must never be turned
    into a verdict about the post."""

    kind: ProbeKind
    detail: dict[str, Any]
    message: str
    judgement: ReadbackJudgement | None = None
    storage_state: dict[str, Any] | None = None


async def _read_once(
    spec: VerifySpec,
    storage_state: dict[str, Any],
    env: EnvironmentConfig | None,
    title: str,
) -> _ReadOutcome:
    """One launch → navigate → settle → read cycle."""
    # patchright, not playwright: drop-in fork covering the CDP-layer leaks.
    # All four import sites must agree — `test_patchright_everywhere` enforces it.
    from patchright.async_api import async_playwright

    settings = get_settings()
    proxy_configured = bool(env is not None and env.proxy_url)

    try:
        launch_kwargs = build_launch_kwargs(env)
    except ProxyConfigError as exc:
        return _ReadOutcome(
            ProbeKind.PROXY_FAILED, {"stage": "proxy_config"}, scrub(str(exc))
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
                    spec.works_url,
                    wait_until="domcontentloaded",
                    timeout=settings.nav_timeout_ms,
                )
                # Same fixed settle as the validator, and for the same reason:
                # the console sometimes lands and then bounces. A works list
                # additionally renders its cards asynchronously, so sampling
                # before the settle would read zero cards off a healthy page —
                # which this design reads as "could not read the list".
                await page.wait_for_timeout(settings.settle_ms)

                final_url = page.url
                visible_login = await visible_marker_texts(
                    page, spec.login_text_markers
                )
                lost = _session_lost(final_url, spec.creator_hosts, visible_login)
                if lost is not None:
                    return _ReadOutcome(
                        ProbeKind.INVALID,
                        {
                            "stage": "session",
                            "final_url": scrub(final_url),
                            "login_markers": visible_login,
                        },
                        lost,
                    )

                judgement = await spec.read(page, title)
                detail = dict(judgement.detail)
                detail["final_url"] = scrub(final_url)
                # Harvest the refreshed session before the context dies. The
                # platform rotates cookies on any authenticated page load, so a
                # read-back that discarded them would make the extra job a net
                # DRAIN on session lifetime — paying a page load and throwing
                # away the renewal it earned. Best-effort: a state we cannot
                # read is no reason to lose a verdict we already have.
                try:
                    fresh = await context.storage_state()
                except Exception:  # noqa: BLE001
                    fresh = None
                return _ReadOutcome(
                    ProbeKind.VALID,
                    detail,
                    judgement.message,
                    judgement=judgement,
                    storage_state=fresh or None,
                )
            finally:
                await browser.close()
    except Exception as exc:  # noqa: BLE001 — every failure becomes a typed status
        raw = f"{type(exc).__name__}: {exc}"
        return _ReadOutcome(
            classify_playwright_error(raw),
            {"stage": "navigate", "proxy_configured": proxy_configured},
            scrub(raw),
        )


_KIND_TO_STATUS = {
    ProbeKind.INVALID: SessionStatus.SESSION_INVALID,
    ProbeKind.PROXY_FAILED: SessionStatus.PROXY_FAILED,
    ProbeKind.TIMEOUT: SessionStatus.TIMEOUT,
    ProbeKind.ERROR: SessionStatus.FAILED,
}


def response_for_judgement(
    judgement: ReadbackJudgement,
    platform: str,
    detail: dict[str, Any],
    storage_state: dict[str, Any] | None = None,
) -> VerifyPublishResponse:
    """Judgement → wire response. Pure, so the mapping is testable on its own.

    The only place `NOT_PUBLISHED` is minted, and the only place `success` is
    allowed to be True. Note that `INCONCLUSIVE` maps to `FAILED`, NOT to
    `NOT_PUBLISHED`: "I could not tell" must never reach the caller wearing the
    clothes of "it is not live", because the caller blocks a user's work item on
    the latter.
    """
    body = dict(detail)
    body["reason"] = judgement.reason
    body.setdefault("platform", platform)
    body["verdict"] = judgement.verdict.value

    if judgement.verdict is ReadbackVerdict.LIVE:
        return VerifyPublishResponse(
            success=True,
            status=SessionStatus.PUBLISHED,
            message=judgement.message,
            detail=body,
            platform_item_id=judgement.item_id,
            published_url=judgement.published_url,
            updated_storage_state=storage_state,
        )
    if judgement.verdict is ReadbackVerdict.NOT_LIVE:
        return VerifyPublishResponse(
            success=False,
            status=SessionStatus.NOT_PUBLISHED,
            message=judgement.message,
            detail=body,
            # Carried even here: a rejected post still has an id, and the link
            # is how the user goes and looks at what the platform said.
            platform_item_id=judgement.item_id,
            published_url=judgement.published_url,
            updated_storage_state=storage_state,
        )
    return VerifyPublishResponse(
        success=False,
        status=SessionStatus.FAILED,
        message=judgement.message,
        detail=body,
        updated_storage_state=storage_state,
    )


async def run_verify(
    spec: VerifySpec,
    storage_state: dict[str, Any],
    environment: EnvironmentConfig | None,
    probe: VerifyProbe,
) -> VerifyPublishResponse:
    """Read the platform back and report whether the post is live.

    Bounded exactly like `run_dom_session_validation` (spec 7.2): a total
    budget, a per-attempt budget, and a fixed attempt count — never `while
    True`. Retries here are cheap-looking and are not: each one is a real
    browser hitting a real creator console, so the caller's own retry budget
    (`publish_readback`) is the outer, slower loop and this one only covers a
    single flaky navigation.
    """
    settings = get_settings()

    if storage_state_is_empty(storage_state):
        # Fail fast, and as a SESSION verdict rather than a post verdict: an
        # empty state tells us about the account, nothing about the post.
        return VerifyPublishResponse(
            success=False,
            status=SessionStatus.SESSION_INVALID,
            message="storage_state contains no cookies or origins",
            detail={
                "platform": spec.platform,
                "attempts": 0,
                "stage": "fail_fast",
                "reason": "empty_storage_state",
            },
        )

    deadline = time.monotonic() + settings.validate_total_timeout_s
    last: tuple[ProbeKind, dict[str, Any], str] | None = None
    attempts = 0

    for attempt in range(1, settings.validate_attempts + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0 or (attempt > 1 and remaining < settings.settle_ms / 1000):
            break

        attempts = attempt
        budget = min(remaining, settings.validate_attempt_timeout_s)
        try:
            outcome = await asyncio.wait_for(
                _read_once(spec, storage_state, environment, probe.title),
                timeout=budget,
            )
        except asyncio.TimeoutError:
            last = (
                ProbeKind.TIMEOUT,
                {"stage": "attempt_budget"},
                f"read-back attempt exceeded {budget:.0f}s budget",
            )
            continue

        if outcome.kind is ProbeKind.VALID and outcome.judgement is not None:
            detail = dict(outcome.detail)
            detail["attempts"] = attempt
            return response_for_judgement(
                outcome.judgement, spec.platform, detail, outcome.storage_state
            )

        if outcome.kind is ProbeKind.PROXY_FAILED:
            # Same call as the validator makes: retrying a dead proxy only
            # burns budget, and the caller can come back once it is fixed.
            last = (outcome.kind, outcome.detail, outcome.message)
            break

        last = (outcome.kind, outcome.detail, outcome.message)

    if last is None:
        last = (
            ProbeKind.TIMEOUT,
            {"stage": "total_budget"},
            "no read-back attempt could run within the total budget",
        )
    kind, detail, message = last
    body = dict(detail)
    body["attempts"] = attempts
    body["platform"] = spec.platform
    body.setdefault("reason", kind.value)
    return VerifyPublishResponse(
        success=False,
        status=_KIND_TO_STATUS[kind],
        message=message,
        detail=body,
    )


__all__ = [
    "ReadbackJudgement",
    "ReadbackReader",
    "ReadbackVerdict",
    "VerifySpec",
    "response_for_judgement",
    "run_verify",
]

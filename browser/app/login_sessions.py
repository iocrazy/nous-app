"""Keep-alive registry for QR login sessions. Platform-neutral.

A QR code is not data the caller can hold onto - it is a view of a live browser
context, and the moment that context dies the code is worthless. So unlike every
other operation in this service, login cannot be request-scoped: `start` leaves
a browser running and hands back a handle, and four later requests drive it.

That makes leak prevention the central problem, not an afterthought. A user who
opens the modal and wanders off must not cost a Chromium for the container's
lifetime. Three independent mechanisms, because the point of defence in depth is
that no single one has to be perfect:

1. **Every session has a hard deadline** (`BROWSER_LOGIN_TTL_S`), fixed at
   creation. Nothing renews it except a successful login, and that extension is
   itself bounded and one-shot.
2. **A background reaper** releases whatever is past its deadline, and actually
   closes the browser - dropping the registry entry alone would leave the
   Chromium running with nothing pointing at it, which is a worse leak than the
   one we set out to fix.
3. **Every request re-checks the deadline inline.** If the reaper task ever dies
   the sessions still expire, they just expire lazily. A cleanup path whose
   correctness depends on a background task staying alive is the pattern this
   project already has scars from.

Released sessions leave a **tombstone** behind for a grace period: the caller
polling one more time gets a typed `timeout` instead of a bare 404 it has to
guess the meaning of.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Awaitable, Callable

from .config import get_settings
from .login import (
    IdentityUnresolved,
    LoginDriver,
    LoginFlowSpec,
    LoginProfile,
    open_login_driver,
)
from .redaction import scrub
from .schemas import EnvironmentConfig, SessionStatus
from .validation import ProbeKind, classify_playwright_error

logger = logging.getLogger("nous_browser.login")

DriverFactory = Callable[
    [LoginFlowSpec, "EnvironmentConfig | None"], Awaitable[LoginDriver]
]

# Statuses after which polling again cannot tell the caller anything new.
TERMINAL_STATUSES = frozenset(
    {
        SessionStatus.SUCCESS,
        SessionStatus.TIMEOUT,
        SessionStatus.FAILED,
        SessionStatus.PROXY_FAILED,
    }
)

_KIND_TO_STATUS = {
    ProbeKind.PROXY_FAILED: SessionStatus.PROXY_FAILED,
    ProbeKind.TIMEOUT: SessionStatus.TIMEOUT,
    ProbeKind.ERROR: SessionStatus.FAILED,
}

# What a submitted-but-not-accepted code reports as its message. Deliberately
# not "wrong code": see `LoginSession.submit_sms` for what the page can and
# cannot tell us apart.
CODE_NOT_ACCEPTED_MESSAGE = (
    "the verification code was not accepted; the page is still asking for one"
)


class LoginError(Exception):
    """A typed failure. Carries the wire status so no caller has to guess."""

    def __init__(self, status: SessionStatus, message: str, **detail: Any):
        super().__init__(message)
        self.status = status
        self.message = message
        self.detail: dict[str, Any] = detail


class LoginCapacityError(LoginError):
    """No room for another concurrent login."""


class SessionPhase(str, Enum):
    ACTIVE = "active"
    RELEASED = "released"


@dataclass
class StatusSnapshot:
    """What an endpoint returns. Flat, already scrubbed."""

    status: SessionStatus
    qrcode_data_url: str | None
    message: str
    detail: dict[str, Any] = field(default_factory=dict)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class LoginSession:
    """One live login. All page work is serialised behind a lock.

    Two requests driving one page concurrently is not a theoretical worry: a
    status poll and a code submission overlap naturally, and Playwright will
    happily interleave them into a page state neither one asked for.
    """

    def __init__(
        self,
        session_id: str,
        spec: LoginFlowSpec,
        driver: LoginDriver,
        qrcode_data_url: str,
    ):
        settings = get_settings()
        self.id = session_id
        self.spec = spec
        self.platform = spec.platform
        self._driver: LoginDriver | None = driver
        self.phase = SessionPhase.ACTIVE
        self.created_at = _now()
        self.expires_at = self.created_at + timedelta(seconds=settings.login_ttl_s)
        self.purge_at: datetime | None = None
        self.qrcode_data_url: str | None = qrcode_data_url
        self.status = SessionStatus.WAITING_SCAN
        self.message = "QR code ready; waiting for a scan"
        self.detail: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._state_extended = False

    # --- lifecycle ---------------------------------------------------------

    @property
    def is_active(self) -> bool:
        return self.phase is SessionPhase.ACTIVE

    def is_expired(self, now: datetime | None = None) -> bool:
        return (now or _now()) >= self.expires_at

    def should_purge(self, now: datetime | None = None) -> bool:
        return self.purge_at is not None and (now or _now()) >= self.purge_at

    async def release(self, status: SessionStatus, message: str) -> None:
        """Close the browser and become a tombstone. Idempotent.

        The recorded status is what later polls see, so a session reaped for
        age answers `timeout` rather than vanishing.
        """
        driver, self._driver = self._driver, None
        self.phase = SessionPhase.RELEASED
        self.purge_at = _now() + timedelta(seconds=get_settings().login_terminal_grace_s)
        self.status = status
        self.message = message
        # Overwrite the reason too, or the tombstone keeps explaining the state
        # the page was in before it was torn down ("QR code displayed" next to
        # "closed by caller"). Any stage marker set by the failing operation is
        # preserved.
        self.detail = {**self.detail, "reason": message}
        self.qrcode_data_url = None
        if driver is not None:
            await driver.close()

    # --- operations --------------------------------------------------------

    async def poll_status(self) -> StatusSnapshot:
        """Read the page once and return. Never waits for the user.

        Single-shot by design: the polling loop lives in the backend workflow,
        which is the side that owns the task record, writes heartbeats and can
        be observed. An endpoint that blocked until the user scanned would hold
        an HTTP worker for minutes and hide all of that.
        """
        async with self._operate() as driver:
            if driver is None:
                return self._tombstone_snapshot()

            snapshot = await driver.snapshot()
            judgement = self.spec.judge(snapshot)
            detail: dict[str, Any] = {"reason": judgement.reason}

            if judgement.status is SessionStatus.QRCODE_EXPIRED:
                # Contract: an expired code must come back refreshed *in this
                # same response*. Returning "expired" and leaving the caller to
                # ask again is how a user ends up staring at a dead image.
                refreshed = await driver.refresh_qrcode()
                # None means the refresh affordance was not found or the new
                # code never rendered. Reporting the stale code would be worse
                # than reporting nothing - the user would scan a dead image.
                self.qrcode_data_url = refreshed
                detail["refreshed"] = refreshed is not None
            elif judgement.status in (
                SessionStatus.WAITING_SCAN,
                SessionStatus.SCANNED,
            ):
                # The platform re-renders the code on its own schedule; re-read
                # rather than serving a cached image that may already be stale.
                current = await driver.read_qrcode()
                if current:
                    self.qrcode_data_url = current
            else:
                self.qrcode_data_url = None

            self._record(judgement.status, judgement.reason, detail)
            return self._snapshot()

    async def submit_sms(self, code: str) -> StatusSnapshot:
        """Type a code into the live page and report what the page does next.

        The interesting case is the one that used to be invisible: the code was
        **not** accepted. `sms_required` is then the answer to two different
        questions - "we need a code from you" (nobody has typed one yet) and
        "the one you typed did not get you past this screen" - and they are the
        same value on the wire. A caller that only sees the status therefore
        cannot tell a fresh challenge from a rejection, which is exactly how a
        wrong code came to produce **no feedback at all**: the backend read
        `sms_required` as a pending state, called the operation a success, and
        the modal cleared the field without a word.

        So the distinction is carried explicitly, in `detail["code_rejected"]`.
        The evidence for it is structural, not textual: *we submitted a code,
        we waited for the page to settle, and the page is still asking for
        one.* That is falsifiable from the same snapshot the judge already
        reads, and it does not depend on selectors for a platform's error
        toast - a matcher that silently stops matching after a copy change is
        precisely the kind of probe this repo has been burned by.

        The honest limit of that evidence, stated because the copy is written
        to it: a platform that *accepted* the code and immediately issued a
        **second** challenge also leaves a code field on screen, and this
        snapshot cannot separate that from a rejection. Both readings share one
        remedy - enter the code the platform is showing you now - so the user
        is told the code was not accepted and the page is still asking, which
        is true either way, rather than "wrong code", which would not be.
        """
        async with self._operate() as driver:
            if driver is None:
                return self._tombstone_snapshot()

            submitted = await driver.submit_sms_code(code)
            if not submitted:
                # Not an error on our side: the page is simply not asking for a
                # code (yet, or any more). Answer with what it *is* asking for.
                # No `code_rejected` here on purpose - nothing was typed, so
                # nothing was refused.
                snapshot = await driver.snapshot()
                judgement = self.spec.judge(snapshot)
                self._record(
                    judgement.status,
                    "no verification code input is present: " + judgement.reason,
                    {"reason": judgement.reason, "submitted": False},
                )
                return self._snapshot()

            # Give the platform a moment to accept or reject the code before
            # sampling, otherwise the answer is just the pre-submit state.
            await asyncio.sleep(get_settings().login_sms_settle_s)
            snapshot = await driver.snapshot()
            judgement = self.spec.judge(snapshot)
            detail: dict[str, Any] = {"reason": judgement.reason, "submitted": True}
            message = judgement.reason
            if judgement.status is SessionStatus.SMS_REQUIRED:
                detail["code_rejected"] = True
                # The judge's own reason ("the platform is asking for a
                # verification code") reads as a first-time prompt, so it must
                # not be what a rejection reports - the message is shown to
                # people.
                message = CODE_NOT_ACCEPTED_MESSAGE
            self._record(judgement.status, message, detail)
            return self._snapshot()

    async def collect_state(self) -> tuple[dict[str, Any], LoginProfile]:
        """Plaintext storage_state plus whatever profile we can scrape.

        Re-judges first rather than trusting a previously recorded `success`:
        this is the one call whose output gets persisted, and handing back a
        half-finished session's cookies would create an account row that never
        works.
        """
        async with self._operate() as driver:
            if driver is None:
                raise LoginError(
                    self.status,
                    f"login session is no longer active ({self.status.value})",
                    terminal=True,
                )

            if self.status is not SessionStatus.SUCCESS:
                snapshot = await driver.snapshot()
                judgement = self.spec.judge(snapshot)
                self._record(judgement.status, judgement.reason, {"reason": judgement.reason})
                if judgement.status is not SessionStatus.SUCCESS:
                    raise LoginError(
                        judgement.status,
                        "login has not completed: " + judgement.reason,
                        terminal=judgement.status in TERMINAL_STATUSES,
                    )
                self._extend_for_state_fetch()

            # Order matters: profile scraping navigates, and a navigation that
            # goes wrong must not cost us the cookies we came for.
            state = await driver.storage_state()
            try:
                profile = await driver.read_profile()
            except IdentityUnresolved as exc:
                # NOT degradable. Every other profile failure costs a display
                # field; this one would cost the account's continuity — the
                # backend keys `social_accounts` on this value, so binding with
                # a blank or substituted id forks the account into a second row
                # and leaves its publish history on the first. Fail the login
                # and say why, so the user rescans instead of quietly owning
                # two half-accounts (repo rule: 触发路径必须类型化失败回显).
                logger.warning(
                    "identity unresolved for platform=%s (cookie=%s)",
                    self.platform,
                    exc.cookie,
                )
                raise LoginError(
                    SessionStatus.FAILED,
                    exc.args[0],
                    reason=IdentityUnresolved.reason,
                    identity_cookie=exc.cookie,
                    terminal=True,
                ) from exc
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "profile scrape failed for platform=%s: %s",
                    self.platform,
                    scrub(f"{type(exc).__name__}: {exc}"),
                )
                profile = LoginProfile()
            return state, profile

    # --- internals ---------------------------------------------------------

    def _operate(self):
        return _SessionOperation(self)

    def _record(
        self, status: SessionStatus, reason: str, detail: dict[str, Any]
    ) -> None:
        self.status = status
        self.message = scrub(reason)
        self.detail = detail
        if status is SessionStatus.SUCCESS:
            self._extend_for_state_fetch()

    def _extend_for_state_fetch(self) -> None:
        """Buy a bounded window for the caller to come fetch storage_state.

        A login that completes at 4:58 of a 5:00 TTL would otherwise be reaped
        before anyone could collect it - the user scanned, and the account still
        fails to bind. One-shot and short: this is a grace window, not a way for
        a session to live indefinitely by staying successful.
        """
        if self._state_extended:
            return
        self._state_extended = True
        floor = _now() + timedelta(seconds=get_settings().login_state_grace_s)
        if floor > self.expires_at:
            self.expires_at = floor

    def _snapshot(self) -> StatusSnapshot:
        detail = dict(self.detail)
        detail.setdefault("platform", self.platform)
        # `terminal` tells the polling caller when to stop. Without it the
        # backend has to hardcode its own copy of which statuses are final,
        # which is exactly the kind of duplicated enum knowledge that drifts.
        detail["terminal"] = self.status in TERMINAL_STATUSES
        detail["expires_at"] = self.expires_at.isoformat()
        return StatusSnapshot(
            status=self.status,
            qrcode_data_url=self.qrcode_data_url,
            message=self.message,
            detail=detail,
        )

    def _tombstone_snapshot(self) -> StatusSnapshot:
        snapshot = self._snapshot()
        snapshot.detail["released"] = True
        snapshot.detail["terminal"] = True
        return snapshot


class _SessionOperation:
    """Async context manager wrapping one page operation.

    Yields the driver, or None when the session is no longer usable. Owns the
    three things every operation needs and none should re-implement: the
    inline TTL check, the per-session lock (bounded), and turning any Playwright
    explosion into a typed status instead of a 500.
    """

    def __init__(self, session: LoginSession):
        self._session = session
        self._held = False

    async def __aenter__(self) -> LoginDriver | None:
        session = self._session
        settings = get_settings()

        if not session.is_active:
            return None

        # Inline expiry, before taking the lock: correctness must not depend on
        # the reaper being alive.
        if session.is_expired():
            await session.release(
                SessionStatus.TIMEOUT, "login session expired before it completed"
            )
            return None

        try:
            await asyncio.wait_for(
                session._lock.acquire(), timeout=settings.login_lock_wait_s
            )
        except asyncio.TimeoutError:
            raise LoginError(
                SessionStatus.TIMEOUT,
                "another operation on this login session is still running",
                stage="lock",
            ) from None
        self._held = True

        # Re-check: the operation we queued behind may have released it.
        if not session.is_active:
            return None
        return session._driver

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        session = self._session
        if self._held:
            session._lock.release()
            self._held = False

        if exc is None or isinstance(exc, LoginError):
            return False

        # Any other exception is a Playwright/browser failure. Typed status in
        # the body, never a traceback over HTTP (spec 7.8).
        raw = f"{type(exc).__name__}: {exc}"
        status = _KIND_TO_STATUS.get(
            classify_playwright_error(raw), SessionStatus.FAILED
        )
        logger.warning(
            "login session %s failed on platform=%s: %s",
            session.id,
            session.platform,
            scrub(raw),
        )
        session._record(status, scrub(raw), {"stage": "driver"})
        # The page is in an unknown state; keeping the browser around only
        # leaks it. Release, but keep the tombstone so the caller learns why.
        await session.release(status, scrub(raw))
        raise LoginError(status, scrub(raw), stage="driver") from exc


class LoginSessionRegistry:
    """Owns every live login session in this process."""

    def __init__(self, open_driver: DriverFactory | None = None):
        self._sessions: dict[str, LoginSession] = {}
        self._open_driver: DriverFactory = open_driver or open_login_driver
        self._reaper: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    def get(self, session_id: str) -> LoginSession | None:
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if session.should_purge():
            self._sessions.pop(session_id, None)
            return None
        return session

    def active_count(self) -> int:
        return sum(1 for s in self._sessions.values() if s.is_active)

    async def start(
        self, spec: LoginFlowSpec, environment: EnvironmentConfig | None
    ) -> LoginSession:
        settings = get_settings()

        # Purge first: a stale tombstone must not count against capacity, and
        # neither must a session whose deadline has already passed.
        await self.sweep()

        if self.active_count() >= settings.login_max_sessions:
            # Typed refusal rather than opening browser number N+1 and hoping.
            # Headed Chromium is a memory hog; the container OOMs long before
            # the platform complains.
            raise LoginCapacityError(
                SessionStatus.FAILED,
                "too many concurrent login sessions; try again shortly",
                reason="login_capacity",
                active=self.active_count(),
                limit=settings.login_max_sessions,
            )

        try:
            driver = await asyncio.wait_for(
                self._open_driver(spec, environment),
                timeout=settings.login_start_timeout_s,
            )
        except asyncio.TimeoutError:
            raise LoginError(
                SessionStatus.TIMEOUT,
                f"login page did not open within {settings.login_start_timeout_s}s",
                stage="open",
            ) from None
        except LoginError:
            raise
        except Exception as exc:  # noqa: BLE001
            raw = f"{type(exc).__name__}: {exc}"
            status = _KIND_TO_STATUS.get(
                classify_playwright_error(raw), SessionStatus.FAILED
            )
            raise LoginError(status, scrub(raw), stage="open") from exc

        try:
            qrcode = await driver.read_qrcode()
        except Exception as exc:  # noqa: BLE001
            await driver.close()
            raise LoginError(
                SessionStatus.FAILED,
                scrub(f"{type(exc).__name__}: {exc}"),
                stage="qrcode",
            ) from exc

        if not qrcode:
            # No point keeping a browser open around a page with no code on it.
            await driver.close()
            raise LoginError(
                SessionStatus.FAILED,
                "login page rendered no QR code",
                stage="qrcode",
                selectors_tried=len(spec.qrcode_selectors),
            )

        session = LoginSession(uuid.uuid4().hex, spec, driver, qrcode)
        self._sessions[session.id] = session
        return session

    async def close(self, session_id: str) -> bool:
        session = self.get(session_id)
        if session is None:
            return False
        if session.is_active:
            # A caller closing a *completed* login is the normal happy path
            # (step 5 of the workflow), so it must not be recorded as a failure.
            final = (
                SessionStatus.SUCCESS
                if session.status is SessionStatus.SUCCESS
                else SessionStatus.FAILED
            )
            await session.release(final, "login session closed by caller")
        return True

    async def sweep(self) -> int:
        """Release everything past its deadline; drop expired tombstones."""
        now = _now()
        released = 0
        for session_id, session in list(self._sessions.items()):
            if session.is_active and session.is_expired(now):
                await session.release(
                    SessionStatus.TIMEOUT, "login session expired before it completed"
                )
                released += 1
            elif session.should_purge(now):
                self._sessions.pop(session_id, None)
        return released

    # --- background reaper -------------------------------------------------

    async def _reap_loop(self) -> None:
        interval = get_settings().login_reaper_interval_s
        # Condition-bound, not `while True` (spec 7.2): shutdown flips the
        # event and the loop leaves rather than being cancelled mid-close.
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
            if self._stop.is_set():
                return
            try:
                released = await self.sweep()
                if released:
                    logger.info("reaped %d expired login session(s)", released)
            except Exception:
                # A reaper that dies on one bad session stops protecting the
                # rest, and every request would then be relying on its own
                # inline check alone.
                logger.exception("login session sweep failed")

    def start_reaper(self) -> None:
        if self._reaper is None or self._reaper.done():
            self._stop.clear()
            self._reaper = asyncio.create_task(self._reap_loop())

    async def shutdown(self) -> None:
        self._stop.set()
        reaper, self._reaper = self._reaper, None
        if reaper is not None:
            try:
                await asyncio.wait_for(reaper, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                reaper.cancel()

        # Close browsers explicitly. Process exit would strand the Chromium
        # children of a container that is only restarting, not going away.
        for session in list(self._sessions.values()):
            if session.is_active:
                await session.release(SessionStatus.FAILED, "service shutting down")
        self._sessions.clear()


_registry: LoginSessionRegistry | None = None


def get_registry() -> LoginSessionRegistry:
    global _registry
    if _registry is None:
        _registry = LoginSessionRegistry()
    return _registry


def reset_registry() -> None:
    """Test seam. Never called by the service."""
    global _registry
    _registry = None

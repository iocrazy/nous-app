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
from typing import Any, Awaitable, Callable, Mapping, Sequence

from .config import get_settings
from .login import (
    IdentityUnresolved,
    LoginDriver,
    LoginFlowSpec,
    LoginJudgement,
    LoginPageSnapshot,
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

# How many polls may go by with the identity chooser still on screen before the
# login is called off. At the backend's 3s poll interval this is ~36s.
#
# It was 5 (~15s) until a real bind failed inside it (2026-08-12): the click
# landed, the screen did not move, and the login was called off 27 seconds after
# it began — while the user was still reading the screen. 15s is a fine budget
# for a click plus a repaint and a poor one for "platform sends an SMS, then
# re-renders", which is the step actually being waited on.
#
# There has to be a ceiling at all for the reason this whole change exists:
# "keep polling and hope" is indistinguishable, from the outside, from the bug
# where nobody ever clicked. A run of failures must end in something the user
# can see and act on — so this may be raised, and may not be removed.
MAX_IDENTITY_CHALLENGE_POLLS = 12

# `detail` keys for the identity challenge, so the backend and the UI branch on
# a contract instead of on prose.
#
# `code_requested` is the important one, and it is deliberately narrow: it means
# **we clicked something that asks the platform to send a code**, never "a code
# field is on screen". The false claim it replaces ("the platform sent a code to
# the phone number on this account", printed whenever `sms_required` showed up)
# is what left the user waiting on a message nobody had requested.
CODE_REQUESTED_KEY = "code_requested"
# Terminal `detail["reason"]` values. The UI keys its copy off these — a user
# whose platform only offers the manual option needs a different sentence from
# one whose login screen changed shape.
IDENTITY_CHALLENGE_UNCLICKABLE = "identity_challenge_unclickable"
IDENTITY_CHALLENGE_STALLED = "identity_challenge_stalled"
# The platform escalated to something no unattended browser may complete — a
# slider, a jigsaw. Split from `stalled` because the remedy is different and
# non-obvious: rescanning produces the same screen, and the user has to finish
# the sign-in somewhere we are not driving.
IDENTITY_CHALLENGE_BLOCKED = "identity_challenge_blocked"

# The key carrying "what was on that page", written on every terminal identity
# failure. Whitelisted through to `task_tracking.metadata.login.detail`, because
# the alternative is what the previous attempt at this bug produced: a failure
# reason with no way to tell which of four explanations it was
# (`backend/app/workflows/session_login.py::_PUBLIC_DETAIL_KEYS`).
PAGE_EVIDENCE_KEY = "page_evidence"


def blocking_challenge_marker(
    evidence: Mapping[str, Any], markers: Sequence[str]
) -> str | None:
    """Which "we cannot do this" caption is in the captured page text, if any.

    Pure, and deliberately used for one thing only: choosing the wording of a
    failure that has already been decided. It cannot fail a login that would
    otherwise succeed, so a false positive costs one wrong sentence on a screen
    that was broken anyway — while a miss costs nothing at all beyond the
    generic copy. That asymmetry is why substring matching is acceptable here
    and nowhere near the captions we *click*.
    """
    text = evidence.get("visible_text") or ""
    if not isinstance(text, str):
        return None
    for marker in markers:
        if marker and marker in text:
            return marker
    return None


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
        # --- identity challenge bookkeeping ---------------------------------
        # Counted, latched and capped rather than re-derived per poll, because
        # every one of these drives a *click*: re-deriving would mean clicking
        # "send me a code" once every poll interval, i.e. a text message every
        # three seconds. Each anchor is clicked at most once per login.
        self._identity_polls = 0
        self._identity_option: str | None = None
        self._code_request_click: str | None = None
        self._sms_code_requested = False
        # What the page did after the click landed, as reported by
        # `wait_for_challenge_progress`. `None` = we have not clicked yet;
        # `[]` = we clicked and the page did not move, which is the state the
        # one-shot escalation below exists for.
        self._challenge_progress: list[str] | None = None
        # The escalated re-click: `None` = not attempted, `""` = attempted and
        # there was nothing to escalate to. Both are recorded, because "no
        # button ancestor exists" is a finding about the page, not a no-op.
        self._challenge_escalation: str | None = None

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

            if judgement.status is SessionStatus.IDENTITY_CHALLENGE:
                # Answer the chooser inside this same response, for the reason
                # `qrcode_expired` refreshes inside its own: reporting the
                # screen and waiting for someone else to act on it is how a
                # user ends up staring at a step nothing is driving. Returns a
                # snapshot when the screen is still up (or has run out of
                # patience); None once the click landed and the page moved.
                stuck = await self._answer_identity_challenge(
                    driver, snapshot, judgement
                )
                if stuck is not None:
                    return stuck
                snapshot = await driver.snapshot()
                judgement = self.spec.judge(snapshot)

            detail: dict[str, Any] = {"reason": judgement.reason}
            if self._identity_option:
                detail["identity_option"] = self._identity_option

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
                if judgement.status is SessionStatus.SMS_REQUIRED:
                    # The second anchor. The chooser hands over to a form that
                    # sends nothing until its own button is pressed, and this
                    # is also the path for a code screen reached with no
                    # chooser at all — "click whichever is on screen, walk on
                    # if neither is" rather than a fixed sequence.
                    await self._request_sms_code(driver)
                    if self._code_request_click:
                        detail["code_request_click"] = self._code_request_click

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

    async def _answer_identity_challenge(
        self,
        driver: LoginDriver,
        snapshot: LoginPageSnapshot,
        judgement: LoginJudgement,
    ) -> StatusSnapshot | None:
        """Pick "receive an SMS" on the chooser. None = it worked, read again.

        Three outcomes, and the two that are not "it worked" both have to be
        *visible*, because the state they replace — sitting on the chooser
        forever — is the bug:

        * **clicked** → latch it (one click per login: this button texts a real
          phone), wait for the page to move, and return None so the caller
          re-reads whatever it moved to.
        * **still there** → report `identity_challenge`, which the UI renders
          as "verifying identity", never as "enter the code we sent you".
        * **out of patience** → a terminal `failed` carrying *why*, and — since
          2026-08-12 — **what the page looked like when it gave up**. The
          previous version of this failure reported a reason and nothing else,
          which was enough to know the login had stalled and not enough to know
          why: the click may never have reached a handler, the platform may have
          moved to a screen our captions still match, or it may have escalated
          to a challenge nothing here can complete. Those need opposite fixes
          and looked identical on the wire.
        """
        self._identity_polls += 1
        offered = list(snapshot.identity_challenge_texts)

        if self._identity_option is None:
            clicked = await driver.choose_sms_challenge()
            if clicked:
                self._identity_option = clicked
                # Clicking the card captioned 接收短信验证码 *is* the request:
                # from here the platform is the one sending. Latched on the
                # click landing, never on anything read off the page — a flag
                # set by observation is a flag that can lie the way the old
                # copy did.
                self._sms_code_requested = True
                # Ask the page whether it moved, rather than sleeping a fixed
                # 3s and letting the next poll's timing decide. `[]` means it
                # did not, and that is what licenses the escalation below.
                self._challenge_progress = await driver.wait_for_challenge_progress()
                if self._challenge_progress:
                    return None
        elif self._challenge_escalation is None and not self._challenge_progress:
            # One re-click, on the next poll rather than in the same one: the
            # platform gets a beat, and a single status request never has to
            # fit two click timeouts plus two waits inside the backend's 20s
            # read budget. See `LoginDriver.escalate_challenge_click` — it is a
            # hypothesis being tried, not a diagnosis being acted on.
            self._challenge_escalation = (
                await driver.escalate_challenge_click(self._identity_option) or ""
            )
            if self._challenge_escalation:
                self._challenge_progress = await driver.wait_for_challenge_progress()
                if self._challenge_progress:
                    return None

        self.qrcode_data_url = None

        if self._identity_polls >= MAX_IDENTITY_CHALLENGE_POLLS:
            evidence = await self._page_evidence(driver)
            blocking = blocking_challenge_marker(
                evidence, self.spec.blocking_challenge_markers
            )
            if blocking:
                reason = IDENTITY_CHALLENGE_BLOCKED
                message = (
                    "the platform escalated to a challenge this service cannot "
                    f"complete ({blocking})"
                )
            elif self._identity_option:
                reason = IDENTITY_CHALLENGE_STALLED
                message = (
                    "selected the SMS verification option, but the platform is "
                    "still showing the identity check"
                )
            else:
                reason = IDENTITY_CHALLENGE_UNCLICKABLE
                message = (
                    "the platform is asking to verify your identity and the "
                    "SMS option could not be selected"
                )
            detail = self._challenge_detail(reason, offered)
            detail[PAGE_EVIDENCE_KEY] = evidence
            if blocking:
                detail["blocking_marker"] = blocking
            self._record(SessionStatus.FAILED, message, detail)
            return self._snapshot()

        detail = self._challenge_detail(judgement.reason, offered)
        self._record(SessionStatus.IDENTITY_CHALLENGE, judgement.reason, detail)
        return self._snapshot()

    def _challenge_detail(self, reason: str, offered: list[str]) -> dict[str, Any]:
        """The per-poll bookkeeping every challenge report carries.

        Separate from the page evidence because it answers a different
        question: this is *what we did*, the evidence is *what we were looking
        at*. Reading a failure means lining the two up — "we clicked, nothing
        moved, and here is the screen that did not move".
        """
        detail: dict[str, Any] = {
            "reason": reason,
            "options_seen": offered,
            "polls": self._identity_polls,
        }
        if self._identity_option:
            detail["identity_option"] = self._identity_option
        if self._challenge_progress is not None:
            detail["progress_signals"] = self._challenge_progress
        if self._challenge_escalation is not None:
            # "" records an attempted escalation with no button ancestor to
            # escalate to — a fact about the page, not an absence of action.
            detail["escalated_click"] = self._challenge_escalation
        return detail

    async def _page_evidence(self, driver: LoginDriver) -> dict[str, Any]:
        """Capture the page, and never let capturing it become the failure.

        This runs on a path that is already failing. An exception here would
        replace a typed, explained failure with a driver error whose message is
        about the diagnostics — losing both the reason and the evidence.
        """
        try:
            return await driver.page_evidence(self.spec.sms_challenge_option_texts)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "page evidence capture failed for platform=%s: %s",
                self.platform,
                scrub(f"{type(exc).__name__}: {exc}"),
            )
            return {"evidence_error": type(exc).__name__}

    async def _request_sms_code(self, driver: LoginDriver) -> None:
        """Press the platform's own "send me the code" button, at most once.

        Re-attempted every poll until it lands, because the button may render a
        beat after the code field does — but capped at one *successful* click,
        since each one is a real text message to a real phone.
        """
        if self._code_request_click:
            return
        clicked = await driver.request_sms_code()
        if clicked:
            self._code_request_click = clicked
            self._sms_code_requested = True

    def _record(
        self, status: SessionStatus, reason: str, detail: dict[str, Any]
    ) -> None:
        # `sms_required` alone never licensed "a code was sent to you", and
        # saying it anyway is the bug. The flag rides along on every report of
        # that status once one of our clicks has actually asked for a code, so
        # the UI can say "we asked the platform to text you" only when it is
        # true, and something neutral otherwise.
        if status is SessionStatus.SMS_REQUIRED and self._sms_code_requested:
            detail = {**detail, CODE_REQUESTED_KEY: True}
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

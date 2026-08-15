"""A supply channel for a code the publish page asks for while it is running.

The platform can interrupt a publish to demand an SMS verification code. Until
now that was a dead end: the publisher raised `sms_verification_required` and
the post was simply lost, with an error message that honestly said there was no
way to supply one.

--- why this is not shaped like the login channel ---------------------------

Login solves the same problem with a *session registry*: `POST /login/start`
returns immediately, leaves a browser running, and hands back an id that four
later requests drive (`login_sessions.py`). Publish cannot copy that. A publish
is one blocking HTTP call — the browser is launched inside the request handler
and torn down when it returns — so there is no id to hand back, because the
response that would carry it is exactly what we are waiting on.

Inverting the direction is what makes it work: **the caller supplies the id up
front.** `nous-backend` mints a `correlation_id`, passes it in the publish
request, and can therefore address the challenge while the publish it belongs to
is still in flight. Nothing about the browser lifecycle changes — the publish
coroutine holds its own context and simply stops on an `await`.

The part of the login design that *is* copied is the part that matters: the code
is only meaningful to one live context, so it goes straight to it, and the
platform's verdict comes back in the same response instead of the user having to
watch a field flip somewhere else. That is why `submit` blocks on a verdict
rather than firing and forgetting — see `SmsVerdict`.

--- the three bounds ---------------------------------------------------------

Every wait here is bounded, because the failure mode this module could
introduce is worse than the bug it fixes: a publish parked forever holds a
browser slot (`BROWSER_MAX_CONCURRENT` is 4), and four parked publishes stop
the queue for everyone.

1. **The window** (`SmsWindow`) — one budget for the whole challenge, shared by
   every retry inside it. Not per-attempt: three attempts must not silently
   become three times the ceiling the budget arithmetic promised.
2. **The attempts** — a wrong code must not end the publish, but it must not be
   retryable forever either.
3. **The verdict wait** — `submit` never outlives its own bound, so a publish
   coroutine that dies mid-verification cannot strand an HTTP worker.

Nothing here is left to a background reaper. Challenges are registered and
removed by the `async with` that owns them, so a challenge cannot outlive the
publish it belongs to even if this module has a bug — the same reasoning as
`login_sessions`' inline deadline re-check, arrived at by removing the
background task instead of double-checking it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

logger = logging.getLogger("nous_browser.publish.sms")

# --- verdicts ---------------------------------------------------------------
#
# Stable machine codes. The backend branches on these and the UI writes copy
# against them, so they are a contract: `rejected` and `expired` license
# opposite sentences ("that code did not work, try again" vs "we stopped
# waiting"), and collapsing them is how a user ends up retyping a code into a
# publish that is already over.
ACCEPTED = "accepted"
REJECTED = "rejected"
EXHAUSTED = "exhausted"
EXPIRED = "expired"
ABANDONED = "abandoned"
NOT_PENDING = "not_pending"

# How long a finished challenge stays addressable, so a code submitted one
# moment too late gets a typed answer instead of a bare 404 the caller has to
# guess the meaning of. Same rationale as `login_sessions`' tombstones.
TOMBSTONE_GRACE_S = 60.0

# Ceiling on how long `submit` waits for the publish coroutine to judge a code.
# Generous next to the ~2s settle it actually takes, because overshooting costs
# one slow HTTP response and undershooting reports `abandoned` for a code that
# was about to be accepted.
VERDICT_WAIT_S = 30.0


@dataclass(frozen=True)
class SmsVerdict:
    """What the *page* did with a submitted code. Returned to the submitter.

    `attempts_left` rides along because it is the difference between "try
    again" and "that was the last one" — a UI that has to infer that from a
    counter it maintains itself will eventually disagree with the browser.
    """

    outcome: str
    message: str
    attempts_left: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "message": self.message,
            "attempts_left": self.attempts_left,
        }

    @property
    def retryable(self) -> bool:
        return self.outcome == REJECTED and self.attempts_left > 0


class SmsWindow:
    """The human's time budget. Separate from the publish's own deadline.

    A publish waiting for a code is not making progress, and charging that wait
    to the work budget would mean a correctly supplied code could still lose the
    post to a deadline spent waiting for the person who supplied it. So the wait
    is measured here and handed back to `Deadline.extend` afterwards.
    """

    def __init__(self, budget_s: float):
        self.budget_s = max(0.0, float(budget_s))
        self._expires_at = time.monotonic() + self.budget_s

    def remaining(self) -> float:
        return max(0.0, self._expires_at - time.monotonic())

    def exhausted(self) -> bool:
        return self.remaining() <= 0


@dataclass
class _Submission:
    code: str
    verdict: "asyncio.Future[SmsVerdict]"


class PublishSmsChallenge:
    """One live "the page wants a code" moment, addressable by correlation id."""

    def __init__(
        self,
        correlation_id: str,
        platform: str,
        window: SmsWindow,
        max_attempts: int,
    ):
        self.correlation_id = correlation_id
        self.platform = platform
        self.window = window
        self.max_attempts = max(1, int(max_attempts))
        self.attempts_used = 0
        self.opened_at = time.monotonic()
        self.closed_outcome: str | None = None
        self.closed_message: str = ""
        self._inbox: asyncio.Queue[_Submission] = asyncio.Queue()
        self._purge_at: float | None = None

    # --- state ------------------------------------------------------------

    @property
    def attempts_left(self) -> int:
        return max(0, self.max_attempts - self.attempts_used)

    @property
    def is_open(self) -> bool:
        return self.closed_outcome is None

    def should_purge(self, now: float | None = None) -> bool:
        return self._purge_at is not None and (now or time.monotonic()) >= self._purge_at

    def snapshot(self) -> dict[str, Any]:
        """What the poll endpoint reports. Never contains a code.

        `waiting` is the field the backend keys the user-visible prompt off,
        and it is deliberately narrow: it means *this publish is parked on an
        await right now*, not "a code field was seen on a page". The looser
        reading is the one that had login telling users a code had been sent
        when nobody had asked for one.
        """
        return {
            "correlation_id": self.correlation_id,
            "platform": self.platform,
            "waiting": self.is_open,
            "outcome": self.closed_outcome,
            "message": self.closed_message,
            "attempts_left": self.attempts_left,
            "max_attempts": self.max_attempts,
            "seconds_remaining": round(self.window.remaining(), 1),
        }

    # --- consumer side (the publish coroutine) ----------------------------

    async def await_code(self) -> _Submission | None:
        """Block until someone supplies a code. `None` = the window ran out.

        Bounded by the window, never by a fixed sleep: retries draw from the
        same budget as the first attempt, so a user who mistypes twice does not
        get three full windows' worth of a browser slot.
        """
        remaining = self.window.remaining()
        if remaining <= 0:
            return None
        try:
            return await asyncio.wait_for(self._inbox.get(), timeout=remaining)
        except asyncio.TimeoutError:
            return None

    def resolve(self, submission: _Submission, verdict: SmsVerdict) -> None:
        """Hand the page's verdict back to whoever is blocked in `submit`."""
        if not submission.verdict.done():
            submission.verdict.set_result(verdict)

    def close(self, outcome: str, message: str) -> None:
        """Become a tombstone and release anyone still waiting on a verdict.

        Called from the owning `async with`, on every path out including a
        raised `StepFailure`. Draining the inbox is not tidiness: a submission
        that arrived in the same tick the publish gave up would otherwise hold
        its HTTP worker until `VERDICT_WAIT_S`, reporting nothing.
        """
        if self.closed_outcome is None:
            self.closed_outcome = outcome
            self.closed_message = message
        self._purge_at = time.monotonic() + TOMBSTONE_GRACE_S
        # Condition-bound, not `while True` (spec 7.2). The queue only shrinks
        # here — nothing can enqueue once `closed_outcome` is set — so this
        # terminates on the queue's own length.
        while not self._inbox.empty():
            try:
                pending = self._inbox.get_nowait()
            except asyncio.QueueEmpty:  # pragma: no cover - drained concurrently
                break
            if not pending.verdict.done():
                pending.verdict.set_result(
                    SmsVerdict(outcome=outcome, message=message, attempts_left=0)
                )

    def consume_attempt(self) -> None:
        self.attempts_used += 1

    # --- producer side (the HTTP request carrying the user's code) --------

    async def submit(self, code: str) -> SmsVerdict:
        """Hand a code to the live page and wait for what the page did with it.

        Blocking is the design, not an oversight. The alternative — accept the
        code, return 202, let the user watch a metadata field — is the exact
        shape this repo keeps getting burned by, and login already rejected it
        for this same reason: the code is only meaningful to one context, so
        that context's answer is the only useful thing to return.
        """
        if not self.is_open:
            return SmsVerdict(
                outcome=self.closed_outcome or NOT_PENDING,
                message=self.closed_message
                or "this publish is no longer waiting for a verification code",
                attempts_left=0,
            )
        if self.window.exhausted():
            return SmsVerdict(
                outcome=EXPIRED,
                message="the window for supplying a verification code has closed",
                attempts_left=0,
            )

        loop = asyncio.get_running_loop()
        submission = _Submission(code=code, verdict=loop.create_future())
        await self._inbox.put(submission)
        try:
            return await asyncio.wait_for(submission.verdict, timeout=VERDICT_WAIT_S)
        except asyncio.TimeoutError:
            # The publish coroutine did not judge the code in time. Reported as
            # `abandoned` rather than `rejected`: we do not know that the code
            # was wrong, and telling a user their correct code was refused is a
            # worse lie than telling them we lost track of the publish.
            return SmsVerdict(
                outcome=ABANDONED,
                message="the publish did not respond to the code in time",
                attempts_left=self.attempts_left,
            )


class PublishSmsRegistry:
    """Process-wide index of live challenges. One per in-flight publish."""

    def __init__(self) -> None:
        self._challenges: dict[str, PublishSmsChallenge] = {}

    def get(self, correlation_id: str) -> PublishSmsChallenge | None:
        self._purge()
        return self._challenges.get(correlation_id)

    def waiting_count(self) -> int:
        """How many publishes are parked on a human right now.

        Reported in the `pool_saturated` body so a queued batch says *why* it
        is queued. "Waiting for something" and "waiting for four other people
        to type their codes" are the same spinner and completely different
        problems, and only one of them is fixed by waiting.
        """
        self._purge()
        return sum(1 for c in self._challenges.values() if c.is_open)

    def _purge(self) -> None:
        now = time.monotonic()
        stale = [k for k, c in self._challenges.items() if c.should_purge(now)]
        for key in stale:
            self._challenges.pop(key, None)

    @asynccontextmanager
    async def open(
        self,
        correlation_id: str,
        platform: str,
        *,
        window_s: float,
        max_attempts: int,
    ) -> AsyncIterator[PublishSmsChallenge]:
        """Register a challenge for the body of a `with`, then always close it.

        Ownership is the point: registration and removal are the same lexical
        construct, so a challenge cannot outlive its publish through any exit
        path — return, `StepFailure`, or cancellation.
        """
        self._purge()
        challenge = PublishSmsChallenge(
            correlation_id=correlation_id,
            platform=platform,
            window=SmsWindow(window_s),
            max_attempts=max_attempts,
        )
        # Last writer wins by design: a correlation id is minted per publish
        # attempt, so a collision means the previous holder is gone.
        self._challenges[correlation_id] = challenge
        logger.info(
            "publish parked on an SMS challenge platform=%s window=%.0fs attempts=%d",
            platform,
            window_s,
            challenge.max_attempts,
        )
        try:
            yield challenge
        finally:
            if challenge.is_open:
                challenge.close(
                    ABANDONED, "the publish stopped waiting for a verification code"
                )
            logger.info(
                "SMS challenge closed platform=%s outcome=%s attempts_used=%d",
                platform,
                challenge.closed_outcome,
                challenge.attempts_used,
            )


_REGISTRY = PublishSmsRegistry()


def get_sms_registry() -> PublishSmsRegistry:
    return _REGISTRY

# backend/app/services/media/parsers/douyin_parse/failures.py

"""Why a douyin parse failed, in a form that survives the trip to the user.

2026-09-15: a user retried the same link fifteen times and every attempt died
the same way, while all the product ever told them was::

    RuntimeError: All enabled Douyin parse methods failed

The reason was in the logs the whole time, and it was not a bug of ours:

* ``abogus``       → ``Blocked by ArgusSecurityPlugin Uifid Not Found`` — douyin
                     now requires a device-fingerprint field our signed request
                     does not carry.
* ``drissionpage`` → cookies injected, page opened, then
                     ``Captcha detected via iframe[src*="verifycenter"]`` —
                     douyin decided the session looked suspicious.

Nothing the user could do about the generic message, and nothing they could
even find out. Both are throttling, and both clear on their own — the 14-day log shows douyin
blocking and serving the same signed path on the same day, 1-10 successes
against 0-16 blocks. What the user needs to know is therefore "this is douyin
rate-limiting you, wait a few minutes" and NOT "something is broken, keep
clicking". The old single sentence said neither, so the reported session was
fifteen retries in forty-five minutes — each one another flagged request
inside the window that was already hot.

CLAUDE.md, "触发路径必须类型化失败回显": a user-triggered path must return a
typed result — success, or failure WITH a reason. ``parse_chain`` used to
``return None`` from every branch, which is the silent no-op that rule forbids.

The kinds are deliberately coarse. They exist to answer one question — "is
retrying worth my time?" — not to mirror every upstream error string.
"""

from __future__ import annotations

from enum import Enum


class DouyinFailure(str, Enum):
    """Coarse reason a douyin parse could not produce a detail."""

    #: Douyin served a verification challenge. Session-scoped and usually
    #: transient — retrying later, or refreshing the saved cookie, can work.
    CAPTCHA = "douyin_captcha"

    #: Argus anti-bot rejection. Intermittent (same path succeeds 1-10×/day
    #: while being blocked), so "wait a few minutes" is the honest advice.
    SIGNATURE_REJECTED = "douyin_signature_rejected"

    #: Logged in as nobody / cookie expired.
    AUTH_REQUIRED = "douyin_auth_required"

    #: Everything else. Kept explicit so "we don't know" is a stated answer
    #: rather than an empty string that reads like "nothing went wrong".
    UNKNOWN = "douyin_unknown"


class DouyinParseError(RuntimeError):
    """A parse failure that knows why it failed.

    Subclasses ``RuntimeError`` so every existing ``except RuntimeError`` /
    ``raise RuntimeError`` site keeps working unchanged — this adds a reason
    to the failure, it does not re-plumb who catches it.
    """

    def __init__(self, kind: DouyinFailure, message: str) -> None:
        super().__init__(message)
        self.kind = kind


#: User-facing sentence per kind. Stored on the task row, so it is what the
#: task detail modal shows in place of the old exception text. Written to say
#: what happened AND what to do — "parse failed" leaves the reader with no
#: next move, which is what made the original message useless.
FAILURE_MESSAGES: dict[DouyinFailure, str] = {
    DouyinFailure.CAPTCHA: (
        "Douyin asked for human verification and blocked this parse. "
        "This is on Douyin's side, not the link — try again in a few minutes, "
        "or refresh your saved Douyin cookie in Settings."
    ),
    DouyinFailure.SIGNATURE_REJECTED: (
        "Douyin's anti-bot check rejected this request. It is intermittent, "
        "not a permanent block — the same link usually works later. Retrying "
        "immediately makes it more likely to happen again, so give it a few "
        "minutes."
    ),
    DouyinFailure.AUTH_REQUIRED: (
        "Douyin needs a signed-in session for this link. "
        "Add or refresh your Douyin cookie in Settings and try again."
    ),
    DouyinFailure.UNKNOWN: (
        "Every Douyin parse method failed and none of them said why. "
        "The application log for this task has the raw responses."
    ),
}


def message_for(kind: DouyinFailure) -> str:
    return FAILURE_MESSAGES.get(kind, FAILURE_MESSAGES[DouyinFailure.UNKNOWN])

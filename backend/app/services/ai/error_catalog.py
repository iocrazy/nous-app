"""AI error catalog — raw provider/engine exceptions → stable error codes.

WHY THIS EXISTS
---------------
When an AI task fails the user is shown ``task_tracking.error_msg``, which
is whatever the engine happened to raise. A provider 401 reaches the UI as::

    DBOSMaxStepRetriesExceeded: Step ai_caption_via_provider has exceeded
    its maximum of 3 retries

which tells the user nothing about what to do (the real cause was an
invalid API key, fixable in Settings → AI). ``error_msg`` is trigger-owned
(CLAUDE.md 路线 C rule 2) so the backend must not rewrite it; instead we
classify the failure into a stable code and merge it into the
business-owned ``metadata`` jsonb (rule 3). The frontend maps the code to
actionable copy via ``frontend/utils/errorCatalog.ts``.

DBOS WRAPPING
-------------
``DBOSMaxStepRetriesExceeded``'s own message never carries the underlying
provider error — the real exceptions live in ``.errors``. Classification
therefore walks the whole chain (``.errors``, ``__cause__``,
``__context__``) rather than looking at ``str(exc)`` alone, which is what
makes it usable from a workflow-level tail-catch.

ADDING A NEW WORKFLOW
---------------------
One line in the workflow's failure path, before the existing
``record_workflow_failure`` / ``raise``::

    await record_ai_error_code(wf_id, e)

It never raises and never changes control flow — a failure to record the
code must not turn into a different failure.
"""

from __future__ import annotations

import re
from typing import Final, Optional, Union

from loguru import logger

# ── Codes ─────────────────────────────────────────────────────────────
# UPPER_SNAKE, stable across releases — the frontend keys i18n copy off
# these strings (``errors.<CODE>.title`` / ``errors.<CODE>.hint``), so
# renaming one silently degrades every already-failed task to raw text.

PROVIDER_AUTH: Final = "PROVIDER_AUTH"
PROVIDER_RATE_LIMIT: Final = "PROVIDER_RATE_LIMIT"
PROVIDER_QUOTA_CAP: Final = "PROVIDER_QUOTA_CAP"
PROVIDER_UNREACHABLE: Final = "PROVIDER_UNREACHABLE"
PROVIDER_BAD_MODEL: Final = "PROVIDER_BAD_MODEL"
OUTPUT_PARSE: Final = "OUTPUT_PARSE"
TASK_TIMEOUT: Final = "TASK_TIMEOUT"
INTERNAL: Final = "INTERNAL"

# codex-local (per-user daemon) failures. Lowercase on purpose: unlike the
# codes above — which travel through ``task_tracking.metadata`` and are keyed
# for i18n by the frontend — these are also the literal HTTP/SSE ``code``
# strings the client reads (see ``core/provider_errors._MAPPING``), and those
# are lowercase across the whole API surface. One string, one meaning.
LOCAL_DAEMON_OFFLINE: Final = "local_daemon_offline"
LOCAL_CLI_MISSING: Final = "local_cli_missing"
LOCAL_TOOLS_UNSUPPORTED: Final = "local_tools_unsupported"
LOCAL_CODEX_NOT_LOGGED_IN: Final = "local_codex_not_logged_in"
LOCAL_REF_REJECTED: Final = "local_ref_rejected"
LOCAL_CODEX_FAILED: Final = "local_codex_failed"

ALL_ERROR_CODES: Final[tuple[str, ...]] = (
    PROVIDER_AUTH,
    PROVIDER_RATE_LIMIT,
    PROVIDER_QUOTA_CAP,
    PROVIDER_UNREACHABLE,
    PROVIDER_BAD_MODEL,
    OUTPUT_PARSE,
    TASK_TIMEOUT,
    INTERNAL,
    LOCAL_DAEMON_OFFLINE,
    LOCAL_CLI_MISSING,
    LOCAL_TOOLS_UNSUPPORTED,
    LOCAL_CODEX_NOT_LOGGED_IN,
    LOCAL_REF_REJECTED,
    LOCAL_CODEX_FAILED,
)

# ── Provider error codes that outrank the HTTP status carrying them ───
# Normally a status code beats prose (see below). This one pattern is the
# exception, and it earns it: Volcengine Ark ships an account-level inference
# cap as HTTP 429 — the same status as a transient burst limit — so reading
# the status alone gives "wait a moment and retry" for something that does not
# clear on its own. doubao-seed-2-0-pro returned it on 126 consecutive hourly
# health probes across five days (2026-08-14 → 19) while every ai_summary run
# failed. The body's own error code is strictly more specific than the
# transport status it arrived in, so it is checked first.
_QUOTA_CAP_PATTERN: Final["re.Pattern[str]"] = re.compile(
    r"setlimitexceeded|reached the set inference limit",
    re.IGNORECASE,
)

# ── Structured signal: HTTP status ────────────────────────────────────
# Read before the text rules — an SDK exception that exposes a status code
# is more trustworthy than string matching against a serialized body.
_STATUS_TO_CODE: Final[dict[int, str]] = {
    401: PROVIDER_AUTH,
    403: PROVIDER_AUTH,
    404: PROVIDER_BAD_MODEL,
    429: PROVIDER_RATE_LIMIT,
    502: PROVIDER_UNREACHABLE,
    503: PROVIDER_UNREACHABLE,
    504: PROVIDER_UNREACHABLE,
}

# ── Text rules ────────────────────────────────────────────────────────
# Order matters: the first match wins, so the specific rows sit above the
# broad ones. Three orderings are load-bearing:
#   - QUOTA_CAP before RATE_LIMIT, so a configured account cap isn't read as
#     a transient burst limit the user should wait out.
#   - BAD_MODEL before UNREACHABLE, so a 404 "model does not exist" isn't
#     read as a dead endpoint.
#   - UNREACHABLE before TASK_TIMEOUT, so a *connect* timeout is reported
#     as "can't reach the provider" rather than "your task ran too long".
# ── codex-local marker rules ──────────────────────────────────────────
# Spliced onto the FRONT of ``_RULES`` (they are emitted by our own code and
# are strictly more specific than anything below — a "codex_not_logged_in"
# message would otherwise be swallowed by the PROVIDER_AUTH prose rule) AND
# used on their own by the marker fast-path in ``classify_ai_error``.
#
# One code per cause, deliberately: these four failures need four different
# user actions. Collapsing ``cli_missing`` into "your daemon is not connected"
# sends the user to restart a daemon that is connected and working — they
# would restart, fail, restart, and the copy would never mention the one thing
# that fixes it (installing the CLI). Same for a timeout: the daemon is
# connected, it just did not finish in time.
#
# ``timeout`` maps to the EXISTING ``TASK_TIMEOUT`` rather than a new local
# code — "the model took too long, try again" is already the right advice and
# is already translated; a codex-local-specific string would say the same
# thing in different words.
_CODEX_LOCAL_RULES: Final[tuple[tuple[str, "re.Pattern[str]"], ...]] = (
    (LOCAL_DAEMON_OFFLINE, re.compile(r"\[codex-local:daemon_offline\]")),
    (LOCAL_CLI_MISSING, re.compile(r"\[codex-local:cli_missing\]")),
    (TASK_TIMEOUT, re.compile(r"\[codex-local:timeout\]")),
    (LOCAL_TOOLS_UNSUPPORTED, re.compile(r"\[codex-local:tools_unsupported\]")),
    (
        LOCAL_CODEX_NOT_LOGGED_IN,
        re.compile(r"\[codex-local:codex_not_logged_in\]"),
    ),
    (LOCAL_REF_REJECTED, re.compile(r"\[codex-local:ref_rejected\]")),
    (
        LOCAL_CODEX_FAILED,
        re.compile(r"\[codex-local:(codex_no_output|codex_failed)\]"),
    ),
)

_RULES: Final[tuple[tuple[str, "re.Pattern[str]"], ...]] = _CODEX_LOCAL_RULES + (
    (
        PROVIDER_AUTH,
        re.compile(
            # NB: no bare "permission denied" — that is also errno 13 from
            # the filesystem, and telling a user to check their API key
            # because a temp dir wasn't writable is worse than raw text.
            r"\b401\b|\b403\b|unauthorized|forbidden"
            r"|invalid[ _-]?api[ _-]?key|incorrect api key|invalid_api_key"
            r"|api key not valid|no api key|missing api key"
            r"|authentication[ _-]?(failed|error)|auth[ _-]?failed"
            r"|invalid (access )?token",
            re.IGNORECASE,
        ),
    ),
    # Same pattern the pre-status check uses — declared once above so the
    # two paths can never drift apart.
    (PROVIDER_QUOTA_CAP, _QUOTA_CAP_PATTERN),
    (
        PROVIDER_RATE_LIMIT,
        re.compile(
            r"\b429\b|rate[ _-]?limit|too many requests"
            r"|insufficient[ _-]?quota|quota exceeded|exceeded your current quota"
            r"|requests per (minute|second|day)|throttl",
            re.IGNORECASE,
        ),
    ),
    (
        PROVIDER_BAD_MODEL,
        re.compile(
            r"model[ _-]?not[ _-]?found|model_not_found"
            r"|unknown model|invalid model|no such model|unsupported model"
            r"|model[^\n]{0,60}?"
            r"(does not exist|is not (available|supported)|not found)"
            r"|does not exist, or you do not have access"
            r"|no active grant",
            re.IGNORECASE,
        ),
    ),
    (
        PROVIDER_UNREACHABLE,
        re.compile(
            r"connection (refused|reset|aborted|error)|connect(ion)?error"
            r"|failed to establish a new connection|network is unreachable"
            r"|name or service not known|temporary failure in name resolution"
            r"|nodename nor servname|getaddrinfo|\bdns\b"
            r"|connect[ _-]?timeout|timeout connecting|connecttimeout"
            r"|\b50[234]\b|bad gateway|service unavailable|gateway time-?out"
            r"|ssl.{0,20}(error|handshake)",
            re.IGNORECASE,
        ),
    ),
    (
        OUTPUT_PARSE,
        re.compile(
            r"jsondecodeerror|expecting value|expecting ['\"].['\"] delimiter"
            r"|unterminated string|extra data"
            r"|failed to parse|could not parse|cannot parse|unable to parse"
            r"|invalid json|not valid json|malformed json"
            r"|validationerror|returned neither an en nor a zh prompt",
            re.IGNORECASE,
        ),
    ),
    (
        TASK_TIMEOUT,
        re.compile(
            r"timeouterror|timed out|\btimeout\b|deadline exceeded",
            re.IGNORECASE,
        ),
    ),
)

# Depth bound so a self-referential __context__ chain can't spin forever.
_MAX_UNWRAP_DEPTH: Final = 8


def _walk(exc: BaseException) -> list[BaseException]:
    """Flatten an exception into itself + everything it wraps.

    Covers all three wrapping mechanisms in play: DBOS's
    ``DBOSMaxStepRetriesExceeded.errors`` list, explicit ``raise ... from``
    (``__cause__``) and implicit re-raise inside ``except`` (``__context__``).
    """
    out: list[BaseException] = []
    seen: set[int] = set()
    queue: list[tuple[BaseException, int]] = [(exc, 0)]
    while queue:
        cur, depth = queue.pop(0)
        if cur is None or id(cur) in seen or depth > _MAX_UNWRAP_DEPTH:
            continue
        seen.add(id(cur))
        out.append(cur)
        nested = getattr(cur, "errors", None)
        if isinstance(nested, (list, tuple)):
            queue.extend((e, depth + 1) for e in nested if isinstance(e, BaseException))
        for attr in ("__cause__", "__context__"):
            nxt = getattr(cur, attr, None)
            if isinstance(nxt, BaseException):
                queue.append((nxt, depth + 1))
    return out


def _status_code(exc: BaseException) -> Optional[int]:
    """HTTP status off an SDK exception (openai / httpx / requests shapes)."""
    code = getattr(exc, "status_code", None)
    if isinstance(code, int):
        return code
    response = getattr(exc, "response", None)
    if response is not None:
        code = getattr(response, "status_code", None)
        if isinstance(code, int):
            return code
    return None


def classify_ai_error(exc_or_message: Union[BaseException, str, None]) -> Optional[str]:
    """Map a raw failure to one of ``ALL_ERROR_CODES``, or ``None``.

    ``None`` means "no confident classification" — callers should then
    write no code at all, so the UI falls back to showing the raw error
    instead of a generic catch-all that hides information.

    Accepts either an exception (chain-aware, see ``_walk``) or a bare
    message string (for callers that only kept the text, e.g. a
    ``task_tracking.error_msg`` re-classification).
    """
    if exc_or_message is None:
        return None

    if isinstance(exc_or_message, BaseException):
        chain = _walk(exc_or_message)
        # The DBOS wrapper's own message ("… exceeded its maximum of N
        # retries") never classifies; joining the whole chain means the
        # underlying provider text still gets its shot at the rules.
        text = "\n".join(f"{type(m).__name__}: {m}" for m in chain)
        # Built BEFORE the status scan on purpose: a provider error code in
        # the body outranks the status class it was transported in. Without
        # this, a 429 short-circuits to PROVIDER_RATE_LIMIT and an account
        # cap is reported as something that clears on its own.
        if _QUOTA_CAP_PATTERN.search(text):
            return PROVIDER_QUOTA_CAP
        # Same reasoning as the cap pattern, one step stronger: a
        # ``CodexLocalError`` carries BOTH a marker and a ``status_code``, and
        # the status is deliberately borrowed from the HTTP class (401 for a
        # logged-out CLI) — letting the status scan win would report the
        # user's own machine problem as "the provider rejected our
        # credentials". The marker is ours and unambiguous, so it goes first.
        if "[codex-local:" in text:
            for code, pattern in _CODEX_LOCAL_RULES:
                if pattern.search(text):
                    return code
        for member in chain:
            status = _status_code(member)
            if status is not None and status in _STATUS_TO_CODE:
                return _STATUS_TO_CODE[status]
    else:
        text = str(exc_or_message)

    if not text.strip():
        return None

    for code, pattern in _RULES:
        if pattern.search(text):
            return code
    return None


async def record_ai_error_code(
    workflow_id: Optional[str],
    error: Union[BaseException, str, None],
) -> Optional[str]:
    """Classify ``error`` and merge ``{"error_code": code}`` into the task's
    ``metadata``. Returns the code written, or ``None`` if nothing was.

    Best-effort by contract: an unclassifiable error, a missing workflow id
    or a failed write are all swallowed. This runs on a path that is
    already failing — it must never become the reason a workflow fails
    differently, and it must never alter control flow (the caller's
    ``raise`` / ``record_workflow_failure`` stays exactly as it was).

    Writes ``metadata`` only. ``error_msg`` / ``phase`` / ``status`` are
    trigger-owned (CLAUDE.md 路线 C rule 2) and are not touched here.
    """
    code = classify_ai_error(error)
    if not code or not workflow_id:
        return None
    try:
        from app.services.infra.unified_task_manager import get_task_manager

        await get_task_manager().patch_metadata(workflow_id, {"error_code": code})
        return code
    except Exception as e:  # noqa: BLE001
        logger.warning(
            f"[error_catalog] failed to record error_code={code} "
            f"for wf={workflow_id}: {e!r}"
        )
        return None


__all__ = [
    "ALL_ERROR_CODES",
    "INTERNAL",
    "LOCAL_CLI_MISSING",
    "LOCAL_CODEX_FAILED",
    "LOCAL_CODEX_NOT_LOGGED_IN",
    "LOCAL_DAEMON_OFFLINE",
    "LOCAL_REF_REJECTED",
    "LOCAL_TOOLS_UNSUPPORTED",
    "OUTPUT_PARSE",
    "PROVIDER_AUTH",
    "PROVIDER_BAD_MODEL",
    "PROVIDER_QUOTA_CAP",
    "PROVIDER_RATE_LIMIT",
    "PROVIDER_UNREACHABLE",
    "TASK_TIMEOUT",
    "classify_ai_error",
    "record_ai_error_code",
]

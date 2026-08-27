"""LLM retry middleware — wraps adapter.call() with classified retry.

Decisions locked in plan-eng-review 2026-04-25:
- 401 / 403 / 400 / 422 → NO retry, fail fast (bad credentials or bad
  request, retrying won't change anything).
- 429 / 502 / 503 / 504 / network errors / timeouts → retry with
  exponential backoff + jitter.
- 200 with empty body → NOT retried (treated as success — adapter
  decides whether empty content is valid).

Cancel signal:
- During backoff sleep, the middleware checks a cancel callback every
  ``_CANCEL_POLL_INTERVAL_S`` seconds. If cancel observed, raises
  :class:`RunCancelled` so caller can short-circuit.

Usage:

    middleware = LLMRetryMiddleware(adapter, cancel_check=recorder.check_cancelled)
    response = await middleware.call(composed, messages)

The middleware does NOT know about the FALLBACK chain — that lives in
the adapter layer (``ai_provider.py`` walks ``ai_agents.fallback_models``
after the middleware exhausts retries on each model). Order of escalation:

    primary model: middleware retries N times -> raises -> adapter catches ->
       fallback[0]: middleware retries N times -> raises -> adapter catches ->
          fallback[1]: ... -> all exhausted -> caller sees AllModelsFailed
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from loguru import logger

from app.schemas.ai_library import ComposedSystemPrompt

# ----------------------------------------------------------------------
# Error classification
# ----------------------------------------------------------------------

# HTTP status codes that mean "the call will never succeed; don't retry".
_NON_RETRYABLE_STATUSES = frozenset({400, 401, 403, 404, 422})

# HTTP status codes that signal transient failure — retry with backoff.
_RETRYABLE_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})

# Default retry parameters. Caller can override per-instance.
DEFAULT_MAX_RETRIES = 3
DEFAULT_BASE_DELAY_S = 1.0
DEFAULT_MAX_DELAY_S = 30.0
DEFAULT_JITTER_RATIO = 0.25  # ±25% jitter to avoid thundering herd

# How often we wake up from a backoff sleep to check cancel.
_CANCEL_POLL_INTERVAL_S = 1.0


class LLMCallError(Exception):
    """Adapter raised an exception — non-retryable case."""


class LLMRetryExhausted(Exception):
    """All retries used up. Caller (FALLBACK chain) decides next step."""


class RunCancelled(Exception):
    """Cancel observed during retry backoff — caller should terminate run."""


CancelCheck = Callable[[], Awaitable[bool]]

# Called once per retry, BEFORE the backoff sleep. Receives the event payload.
# Deliberately a plain callback rather than a recorder handle: the middleware
# must stay provider- and storage-agnostic, and a telemetry sink must never be
# able to fail a model call (the caller wraps, this module contains).
RetryObserver = Callable[[dict], Awaitable[None]]


def classify_error(exc: BaseException) -> str:
    """Return ``'retryable'`` / ``'non_retryable'`` / ``'unknown'``.

    Heuristics:
      - Reads ``status_code`` attribute if present (httpx, openai, etc.).
      - Reads ``response.status_code`` if nested.
      - Falls back to exception type name (``TimeoutError`` → retryable).
      - Unknown exceptions are RETRYABLE by default (fail-open philosophy
        is wrong for cost reasons; default to retry but log loudly).

    No model dispatch logic here — middleware is provider-agnostic. The
    adapter wraps provider exceptions in ones that expose ``status_code``.
    """
    status = _extract_status_code(exc)
    if status is not None:
        if status in _NON_RETRYABLE_STATUSES:
            return "non_retryable"
        if status in _RETRYABLE_STATUSES:
            return "retryable"
        # 5xx not in the explicit set — retry. 2xx/3xx shouldn't reach here.
        if 500 <= status < 600:
            return "retryable"
        if 400 <= status < 500:
            return "non_retryable"

    name = type(exc).__name__
    if "Timeout" in name or "Connection" in name or "Network" in name:
        return "retryable"

    return "unknown"


def _extract_status_code(exc: BaseException) -> Optional[int]:
    code = getattr(exc, "status_code", None)
    if isinstance(code, int):
        return code
    response = getattr(exc, "response", None)
    if response is not None:
        code = getattr(response, "status_code", None)
        if isinstance(code, int):
            return code
    return None


# How much of a provider error body to keep. Long enough for a JSON error
# envelope's code+message, short enough that task_tracking.error_msg (500
# chars, and it also carries the class name + model list) isn't crowded out.
def retry_after_seconds(exc: BaseException) -> Optional[float]:
    """The provider's own ``Retry-After``, in seconds, or ``None``.

    ``None`` means "no usable guidance — use our own backoff". It is
    deliberately distinct from ``0.0`` ("retry immediately"): a garbled header
    must not be read as permission to hammer.

    Only the delta-seconds form is supported. The HTTP-date form is left
    unparsed on purpose — resolving it correctly needs the server's clock, and
    a wrong absolute time is more dangerous than no guidance at all.
    """
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) if response is not None else None
    if not headers:
        return None
    raw = None
    try:
        # Header names are case-insensitive and providers disagree in
        # practice; httpx maps are already case-insensitive but a plain dict
        # (every test double, and some SDKs) is not.
        raw = headers.get("Retry-After")
        if raw is None:
            for k, v in dict(headers).items():
                if str(k).lower() == "retry-after":
                    raw = v
                    break
    except Exception:
        return None
    if raw is None:
        return None
    try:
        seconds = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    if seconds != seconds or seconds < 0:  # NaN or negative
        return None
    return seconds


_BODY_SNIPPET_MAX = 220


def _response_body(exc: BaseException) -> str:
    """Provider response body off an httpx/requests-shaped exception, or ""."""
    response = getattr(exc, "response", None)
    if response is None:
        return ""
    try:
        text = getattr(response, "text", "")
    except Exception:  # noqa: BLE001 — a body that can't be read is not fatal
        return ""
    return text if isinstance(text, str) else ""


def describe_llm_error(exc: BaseException) -> str:
    """One self-contained line: exception type, HTTP status, provider body.

    Load-bearing for post-mortems, for two independent reasons:

    1. ``str(httpx.HTTPStatusError)`` stops at "Client error '429 Too Many
       Requests' for url '…'" — which cannot tell an account-level spend cap
       ("SetLimitExceeded", fatal until someone raises it in the provider
       console) apart from a transient burst limit (clears by itself). Only
       the response BODY carries that distinction, and acting on the two is
       opposite.
    2. DBOS pickles an exception's ``args`` and nothing else — no
       ``__cause__``, no ``__context__``. Whatever is not inside this string
       by the time :class:`AllModelsFailed` is constructed is unrecoverable
       once the workflow row is all that's left.

    Redacted before it is returned: the string ends up in a persisted DB row
    (``task_tracking.error_msg``), and some providers echo request headers
    back in error envelopes.
    """
    from app.boundary.log_redact import redact

    status = _extract_status_code(exc)
    head = type(exc).__name__ + (f" HTTP {status}" if status is not None else "")
    detail = _response_body(exc) or str(exc)
    # Redact BEFORE truncating, never after. Every pattern in log_redact
    # requires a known prefix plus a minimum length ("sk-" + >=20 chars,
    # "Bearer " + >=20), so a cut that lands mid-secret leaves a stub too
    # short to match and the tail goes to task_tracking.error_msg in the
    # clear. Redacting first replaces the whole secret with "***" while it is
    # still intact, and the truncation afterwards can only shorten a mask.
    detail = redact(" ".join(detail.split()))[:_BODY_SNIPPET_MAX]
    line = f"{head}: {detail}" if detail else head
    chain = _cause_chain(exc)
    return f"{line} <- {chain}" if chain else line


_CAUSE_CHAIN_MAX = _BODY_SNIPPET_MAX  # the chain gets the same budget as the body


def _cause_chain(exc: BaseException) -> str:
    """``__cause__`` links, outermost first, as ``Type: msg <- Type: msg``.

    "fetch failed" on top hides the DNS error three levels down, and DBOS
    keeps only ``args`` — so the chain has to be inside the string. Cycles
    (possible when exceptions are re-raised across retries) terminate.
    """
    from app.boundary.log_redact import redact

    parts: list[str] = []
    seen = {id(exc)}
    cur = exc.__cause__
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        parts.append(f"{type(cur).__name__}: {' '.join(str(cur).split())}")
        cur = cur.__cause__
    if not parts:
        return ""
    return redact(" <- ".join(parts))[:_CAUSE_CHAIN_MAX]


# ----------------------------------------------------------------------
# Backoff
# ----------------------------------------------------------------------


def compute_backoff(
    attempt: int,
    *,
    base_delay_s: float = DEFAULT_BASE_DELAY_S,
    max_delay_s: float = DEFAULT_MAX_DELAY_S,
    jitter_ratio: float = DEFAULT_JITTER_RATIO,
    rng: Optional[Callable[[], float]] = None,
) -> float:
    """Exponential backoff with multiplicative jitter.

    attempt is 1-indexed (first retry = 1).
    Formula: delay = min(base * 2^(attempt-1), max) * (1 ± jitter_ratio)
    """
    raw = min(base_delay_s * (2 ** (attempt - 1)), max_delay_s)
    rand = (rng or random.random)()  # noqa: S311 — not crypto-sensitive
    jitter = 1.0 + (rand * 2.0 - 1.0) * jitter_ratio  # in [1-jr, 1+jr]
    return max(0.0, raw * jitter)


# ----------------------------------------------------------------------
# Middleware
# ----------------------------------------------------------------------


@dataclass
class LLMRetryMiddleware:
    """Wraps an adapter, retrying retryable failures with backoff.

    Not threadsafe — instantiate one per run (or per call). State in
    ``_attempts`` is per-call.
    """

    adapter: Any
    cancel_check: Optional[CancelCheck] = None
    # W1: emit one durable event per retry so the count survives a process
    # restart and the UI can render progress. None = no telemetry (unchanged
    # behaviour).
    on_retry: Optional[RetryObserver] = None
    max_retries: int = DEFAULT_MAX_RETRIES
    base_delay_s: float = DEFAULT_BASE_DELAY_S
    max_delay_s: float = DEFAULT_MAX_DELAY_S
    jitter_ratio: float = DEFAULT_JITTER_RATIO

    # AI-007: hard ceiling on total wall-time across all retry attempts +
    # backoff sleeps. None = no ceiling (legacy behavior). When set, the loop
    # stops retrying once the deadline is reached and never sleeps past it, so
    # worst-case retry time is bounded regardless of max_retries × backoff.
    total_deadline_seconds: Optional[float] = None

    # Test seam: override the random source for deterministic backoff.
    rng: Optional[Callable[[], float]] = None

    # Test seam: override sleep so tests don't actually sleep seconds.
    # default_factory (NOT default): a plain Python function stored as a
    # dataclass default becomes a class attribute, and function class
    # attributes are descriptors — ``self.sleep(x)`` would bind ``self`` and
    # call ``asyncio.sleep(<middleware>, x)``, crashing every backoff sleep
    # with "'<=' not supported between 'LLMRetryMiddleware' and 'int'".
    # default_factory assigns the function as an INSTANCE attribute, which
    # does not bind. (time.monotonic is a C builtin — not a descriptor — so
    # it never had the bug, but gets the same treatment for consistency.)
    sleep: Callable[[float], Awaitable[None]] = field(
        default_factory=lambda: asyncio.sleep, init=False
    )
    # Test seam: override the monotonic clock for deterministic deadline tests.
    monotonic: Callable[[], float] = field(
        default_factory=lambda: time.monotonic, init=False
    )

    def policy_key(self) -> str:
        """A stable fingerprint of the retry policy currently in force.

        Retry counts are only meaningful under one policy. Carrying a count
        across a config change would report "attempt 3 of 3" under rules that
        allowed 5 — so the key changes when the policy does, and the count
        naturally starts over.

        Only policy participates. The adapter, the cancel hook and the test
        seams are not policy; keying on them would restart the count for
        reasons that have nothing to do with the rules.
        """
        canonical = json.dumps(
            {
                "max_retries": self.max_retries,
                "base_delay_s": self.base_delay_s,
                "max_delay_s": self.max_delay_s,
                "jitter_ratio": self.jitter_ratio,
                "total_deadline_seconds": self.total_deadline_seconds,
                "retryable_statuses": sorted(_RETRYABLE_STATUSES),
                "non_retryable_statuses": sorted(_NON_RETRYABLE_STATUSES),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha1(canonical.encode()).hexdigest()[:16]  # noqa: S324

    async def _emit_retry(self, event: dict) -> None:
        """Never let telemetry fail a model call."""
        if self.on_retry is None:
            return
        try:
            await self.on_retry(event)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[LLMRetry] retry observer failed (ignored): {exc!r}")

    async def call(
        self, composed: ComposedSystemPrompt, messages: list[dict]
    ) -> dict[str, Any]:
        last_exc: Optional[BaseException] = None
        start = self.monotonic()
        # Audit #18: an "unknown" classification (no status_code, not a
        # Timeout/Connection/Network name) might be a transient blip OR a hard
        # error (parse/ValueError/adapter bug). Give it ONE retry so we don't
        # drop a recoverable transient, but fail fast after that instead of
        # burning the full max_retries budget amplifying a hard failure.
        unknown_failures = 0
        for attempt in range(0, self.max_retries + 1):  # attempt=0 is first try
            # AI-007: stop before a fresh attempt once the deadline is hit.
            if attempt > 0 and self._deadline_remaining(start) <= 0:
                logger.warning(
                    f"[LLMRetry] global deadline "
                    f"({self.total_deadline_seconds:.1f}s) reached before "
                    f"attempt {attempt + 1}"
                )
                break
            try:
                return await self.adapter.call(composed, messages)
            except Exception as exc:  # noqa: BLE001
                classification = classify_error(exc)
                logger.warning(
                    f"[LLMRetry] model={getattr(composed, 'model', '?')} "
                    f"attempt {attempt + 1}/{self.max_retries + 1} failed "
                    f"({classification}): {describe_llm_error(exc)}"
                )
                last_exc = exc
                if classification == "non_retryable":
                    raise LLMCallError(f"non-retryable: {exc}") from exc
                if classification == "unknown":
                    unknown_failures += 1
                    # First unknown → retry once; second → give up (don't
                    # spend the whole budget on a likely-hard failure).
                    if unknown_failures > 1:
                        raise LLMCallError(
                            f"unknown error retried once, giving up: {exc}"
                        ) from exc
                if attempt >= self.max_retries:
                    break  # exhausted; raise after loop

                # The provider's own Retry-After outranks our guess — it knows
                # when its bucket refills. Two rules, and the second is the one
                # that matters: a value beyond our ceiling means give up NOW.
                # Sleeping an hour inside one turn is worse than failing —
                # it pins a connection, a task row and the user's attention,
                # while every caller above has its own deadline. Without this,
                # one provider header can hang the run.
                provider_delay = retry_after_seconds(exc)
                if provider_delay is not None and provider_delay > self.max_delay_s:
                    raise LLMRetryExhausted(
                        f"provider asked to wait {provider_delay:.0f}s, beyond "
                        f"the {self.max_delay_s:.0f}s ceiling for a single "
                        f"turn; giving up without retrying. last={exc}"
                    ) from exc

                # Sleep before next try, polling cancel every poll-interval.
                delay = (
                    provider_delay
                    if provider_delay is not None
                    else compute_backoff(
                        attempt + 1,
                        base_delay_s=self.base_delay_s,
                        max_delay_s=self.max_delay_s,
                        jitter_ratio=self.jitter_ratio,
                        rng=self.rng,
                    )
                )
                # Emit BEFORE the wait: a UI that only learns of a backoff
                # once it is over cannot show it, and a crash mid-sleep would
                # otherwise leave no trace that we were waiting at all.
                # ⚠️ Same-model transient retry. Cross-model fallback stays
                # prohibited — this must never become the seam where one
                # quietly turns into the other.
                await self._emit_retry(
                    {
                        "attempt": attempt + 1,
                        "max_retries": self.max_retries,
                        "model": getattr(composed, "model", None),
                        "policy_key": self.policy_key(),
                        "delay_ms": int(delay * 1000),
                        "classification": classification,
                        "failure": describe_llm_error(exc)[:300],
                    }
                )

                # AI-007: never sleep past the deadline; if the backoff would
                # blow it, give up now rather than wake up already-expired.
                if self.total_deadline_seconds is not None:
                    remaining = self._deadline_remaining(start)
                    if remaining <= 0:
                        break
                    delay = min(delay, remaining)
                await self._sleep_with_cancel(delay)

        raise LLMRetryExhausted(
            f"all {self.max_retries + 1} attempts exhausted; last="
            + (describe_llm_error(last_exc) if last_exc is not None else "unknown")
        ) from last_exc

    def _deadline_remaining(self, start: float) -> float:
        """Seconds left before the global deadline; +inf when no deadline set."""
        if self.total_deadline_seconds is None:
            return float("inf")
        return self.total_deadline_seconds - (self.monotonic() - start)

    async def _sleep_with_cancel(self, total_seconds: float) -> None:
        """Sleep ``total_seconds`` in poll-interval chunks; raise if cancelled."""
        remaining = total_seconds
        while remaining > 0:
            if self.cancel_check is not None:
                try:
                    if await self.cancel_check():
                        raise RunCancelled("cancel observed during retry backoff")
                except RunCancelled:
                    raise
                except Exception:  # noqa: BLE001
                    # Cancel-check failure is non-fatal; keep sleeping.
                    logger.exception("[LLMRetry] cancel_check raised; ignoring")
            chunk = min(_CANCEL_POLL_INTERVAL_S, remaining)
            await self.sleep(chunk)
            remaining -= chunk


__all__ = [
    "DEFAULT_BASE_DELAY_S",
    "DEFAULT_JITTER_RATIO",
    "DEFAULT_MAX_DELAY_S",
    "DEFAULT_MAX_RETRIES",
    "CancelCheck",
    "LLMCallError",
    "LLMRetryExhausted",
    "LLMRetryMiddleware",
    "RunCancelled",
    "classify_error",
    "compute_backoff",
    "describe_llm_error",
]

"""Typed end-of-turn reason (harness phase 2, dsh TurnEndReason).

A turn ends in exactly one of a handful of ways, and the Task Center used to
learn only "completed" or "failed" — a run that hit the tool-loop ceiling or
was cut off by the provider's output limit looked identical to one that
finished. This module names the ways and classifies the runner's existing
return shapes into them, in one place.

Design note (deviates from the plan's "tag every exit"): ``_run_turn_inner``
and ``_stream_turn_inner`` have ~25 terminal points between them, and every
one already carries a distinguishing marker in the value it returns/yields
(``cancelled`` / ``aborted`` / ``awaiting_approval`` / ``error_code`` /
``usage.hook_decision`` / ``usage.warning`` / ``finish_reason``). Tagging each
exit by hand would restate that marker a second time and drift. Instead the
wrappers classify the *outcome*; the exhaustiveness guard in the tests scans
the runner's source for every marker literal and asserts this module knows
it — a new exit shape that the classifier does not recognise turns red.
"""

from __future__ import annotations

import asyncio
from enum import Enum
from typing import Any, Optional

from app.services.ai.runner.events import emit

TURN_END_EVENT_TYPE = "turn_end"


class TurnEndReason(str, Enum):
    COMPLETED = "completed"  # natural stop (finish_reason=stop)
    PROVIDER_LENGTH = (
        "provider_length"  # provider cut the output (finish_reason=length)
    )
    CONTEXT_REJECTED = "context_rejected"  # preflight budget refused the turn
    MAX_ITERATIONS = "max_iterations"  # tool-loop ceiling
    CANCELLED = "cancelled"  # user cancel / abort (incl. hook abort)
    AWAITING_APPROVAL = "awaiting_approval"  # paused for a human decision
    ERROR = "error"  # exception or error result (incl. run timeout)
    INTERRUPTED = (
        "interrupted"  # closed after the fact by the sweeper (crash / lost heartbeat)
    )
    PAUSED = "paused"  # target-level pause (phase 2)
    AWAITING_INPUT = "awaiting_input"  # parked on a typed question (phase 2a)


# ``StepContext.stop(reason)`` literals → the turn-end word. One table, and
# ``StepContext.stop`` refuses a reason that is not in it, so a hook cannot
# invent a stop the transcript would misfile.
STOP_REASON_TO_TURN_END: dict[str, "TurnEndReason"] = {
    "cancelled": TurnEndReason.CANCELLED,
    "paused": TurnEndReason.PAUSED,
    "awaiting_input": TurnEndReason.AWAITING_INPUT,
}


# ── the markers the runner's exits carry, and what each one means ─────────
# Keys of the run_turn result dict that are flags (value True).
FLAG_MARKERS: dict[str, TurnEndReason] = {
    "cancelled": TurnEndReason.CANCELLED,
    "aborted": TurnEndReason.CANCELLED,
    "awaiting_approval": TurnEndReason.AWAITING_APPROVAL,
    "awaiting_input": TurnEndReason.AWAITING_INPUT,
}
# run_turn ``error_code`` / stream ``usage.error_code`` literals.
ERROR_CODE_MARKERS: dict[str, TurnEndReason] = {
    "context_budget_exceeded": TurnEndReason.CONTEXT_REJECTED,
    "run_timeout": TurnEndReason.ERROR,
}
# run_turn ``error`` string literals that are not free text.
ERROR_LITERAL_MARKERS: dict[str, TurnEndReason] = {
    "max_tool_iterations_exceeded": TurnEndReason.MAX_ITERATIONS,
}
# stream ``usage.hook_decision`` literals.
HOOK_DECISION_MARKERS: dict[str, TurnEndReason] = {
    "abort": TurnEndReason.CANCELLED,
    "await_approval": TurnEndReason.AWAITING_APPROVAL,
}
# stream ``usage.warning`` literals.
WARNING_MARKERS: dict[str, TurnEndReason] = {
    "timeout_sec_exceeded": TurnEndReason.ERROR,
    "max_stream_iterations_exceeded": TurnEndReason.MAX_ITERATIONS,
}


def _finish_reason_of(raw: Any) -> Optional[str]:
    try:
        return (raw or {}).get("choices", [{}])[0].get("finish_reason") or None
    except (AttributeError, IndexError, TypeError):
        return None


def classify_run_result(result: dict[str, Any]) -> tuple[TurnEndReason, dict[str, Any]]:
    """Reason + extra payload fields for a ``run_turn`` result dict."""
    stop_reason = result.get("stop_reason")
    if stop_reason in STOP_REASON_TO_TURN_END:
        return STOP_REASON_TO_TURN_END[stop_reason], {}
    for key, reason in FLAG_MARKERS.items():
        if result.get(key):
            return reason, {}
    code = result.get("error_code")
    if code in ERROR_CODE_MARKERS:
        return ERROR_CODE_MARKERS[code], {"error_code": code}
    err = result.get("error")
    if err in ERROR_LITERAL_MARKERS:
        return ERROR_LITERAL_MARKERS[err], {"error_code": err}
    if err or code:
        return TurnEndReason.ERROR, {"error_code": code or str(err)[:80]}
    finish = _finish_reason_of(result.get("raw"))
    extra: dict[str, Any] = {}
    if finish:
        extra["finish_reason"] = finish
    calls = result.get("tool_calls")
    if isinstance(calls, list):
        extra["tool_calls"] = len(calls)
    if finish == "length":
        return TurnEndReason.PROVIDER_LENGTH, extra
    return TurnEndReason.COMPLETED, extra


def classify_stream_end(last_terminal: Any) -> tuple[TurnEndReason, dict[str, Any]]:
    """Reason for a stream that finished iterating.

    ``last_terminal`` is the last chunk that carried a ``finish_reason``, or
    ``None`` when the generator returned without one — which is what the
    cooperative cancel checks do (they ``return`` silently).
    """
    if last_terminal is None:
        return TurnEndReason.CANCELLED, {"finish_reason": None}
    usage = getattr(last_terminal, "usage", None) or {}
    finish = getattr(last_terminal, "finish_reason", None)
    extra: dict[str, Any] = {"finish_reason": finish}
    trace = getattr(last_terminal, "tool_call_trace", None)
    if isinstance(trace, list):
        extra["tool_calls"] = len(trace)
    stop_reason = usage.get("stop_reason")
    if stop_reason in STOP_REASON_TO_TURN_END:
        return STOP_REASON_TO_TURN_END[stop_reason], extra
    code = usage.get("error_code")
    if code in ERROR_CODE_MARKERS:
        return ERROR_CODE_MARKERS[code], {**extra, "error_code": code}
    if code in ERROR_LITERAL_MARKERS:  # buffered fallback forwards run_turn's
        return ERROR_LITERAL_MARKERS[code], {**extra, "error_code": code}
    hook = usage.get("hook_decision")
    if hook in HOOK_DECISION_MARKERS:
        return HOOK_DECISION_MARKERS[hook], extra
    warning = usage.get("warning")
    if warning in WARNING_MARKERS:
        return WARNING_MARKERS[warning], {**extra, "error_code": warning}
    if code:
        return TurnEndReason.ERROR, {**extra, "error_code": code}
    if finish == "length":
        return TurnEndReason.PROVIDER_LENGTH, extra
    return TurnEndReason.COMPLETED, extra


def classify_exception(exc: BaseException) -> tuple[TurnEndReason, dict[str, Any]]:
    if isinstance(exc, asyncio.CancelledError):
        return TurnEndReason.CANCELLED, {"error_code": "cancelled_error"}
    return TurnEndReason.ERROR, {"error": f"{type(exc).__name__}: {exc!s:.200}"}


async def emit_turn_end(
    recorder: Any, reason: TurnEndReason, extra: dict[str, Any]
) -> None:
    """One typed ``turn_end`` per turn, through the single event entry."""
    await emit(recorder, TURN_END_EVENT_TYPE, {"reason": reason.value, **extra}, turn=1)


__all__ = [
    "STOP_REASON_TO_TURN_END",
    "TURN_END_EVENT_TYPE",
    "TurnEndReason",
    "classify_exception",
    "classify_run_result",
    "classify_stream_end",
    "emit_turn_end",
]

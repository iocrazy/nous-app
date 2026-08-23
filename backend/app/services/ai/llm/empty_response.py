"""Why was this model reply empty?

An assistant turn that carries neither text nor a tool call ends the run with
nothing to show the user. Production, 30 days to 2026-08-23:
``doubao-seed-2-0-lite-260428`` closed 36 runs, **8 of them (22%) exactly like
that**, and 7 of those had billed completion tokens — one of them 649. The
model generated output. We stored none of it.

And then the trail stops: no transcript event is written for those runs, so
nothing anywhere records what the response actually contained. Two causes that
demand opposite fixes are indistinguishable from what we keep:

* the text went to a field we do not read — ``reasoning_content`` is the
  obvious suspect for a "seed-2.0" reasoning model — in which case retrying
  just buys more of the same, or
* the model genuinely produced nothing, in which case retrying is exactly
  right (nothing durable was produced, so a retry is safe).

This module does not decide between them. It records the evidence needed to
decide — the message's key names and value sizes, the finish reason, and
whether we were billed — so the next occurrence answers the question instead
of raising it again.

**Sizes and key names only, never content.** A diagnostic that quietly
accumulates model output becomes a second transcript nobody audited.
"""

from __future__ import annotations

from typing import Any, Optional


def _sizeof(value: Any) -> int:
    """A comparable magnitude for a field's payload, without keeping it."""
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value)
    if isinstance(value, (list, tuple, dict)):
        return len(value)
    return len(str(value))


def diagnose_empty_response(resp: Any) -> Optional[dict[str, Any]]:
    """``None`` when the reply carried text or a tool call; otherwise the
    evidence bundle.

    Never raises. A malformed envelope is itself worth reporting — turning a
    recoverable turn into an outage because the diagnosis blew up would be a
    strictly worse failure than the one being diagnosed.
    """
    try:
        choices = (resp or {}).get("choices") or []
        first = choices[0] if choices else {}
        message = first.get("message") or {}
        if not isinstance(message, dict):
            message = {}

        content = message.get("content")
        has_text = bool(isinstance(content, str) and content.strip()) or bool(
            isinstance(content, list) and content
        )
        has_tool_calls = bool(message.get("tool_calls"))
        if has_text or has_tool_calls:
            return None

        usage = (resp or {}).get("usage") or {}
        completion_tokens = usage.get("completion_tokens")
        try:
            completion_tokens = int(completion_tokens or 0)
        except (TypeError, ValueError):
            completion_tokens = 0

        return {
            # Key names + sizes: enough to spot `reasoning_content` carrying
            # 400 characters while `content` carries 0, and nothing more.
            "message_keys": {k: _sizeof(v) for k, v in message.items()},
            "finish_reason": first.get("finish_reason"),
            "completion_tokens": completion_tokens,
            # The pairing that makes this worth chasing at all.
            "billed_but_empty": completion_tokens > 0,
        }
    except Exception as exc:  # never let the diagnosis break the turn
        return {
            "message_keys": {},
            "finish_reason": None,
            "completion_tokens": 0,
            "billed_but_empty": False,
            "diagnosis_error": f"{type(exc).__name__}: {exc}",
        }


__all__ = ["diagnose_empty_response"]

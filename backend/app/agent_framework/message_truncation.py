"""Per-message size cap — defend against single-turn overruns.

Wave 5a (A2). The compactor handles message-list growth, but a SINGLE
oversized message (user paste of a 200k-line file, tool result with a
50MB JSON, etc.) bypasses it entirely — the message goes straight to
the model and trips a 400 "input too long" before compaction even runs.

This module gives ``cap_message_tokens()`` and the convenience
``cap_messages_tokens()`` for batch use. Both:
  - Measure with the real tokenizer (``count_tokens`` from A1)
  - Truncate ONLY when over budget — short messages pass through unchanged
  - Insert an explicit marker so the model knows truncation happened
    (silent truncation is an injection vector — agent can't tell apart
    "user said X" from "user said X+lots truncated")
  - Preserve the FRONT of the message by default — that's where the
    user's actual question usually is; tail is often a long paste/log

Truncation strategy:
  - For string content: keep first N tokens, append marker
  - For multi-part content (Anthropic style): truncate the largest text
    part first, repeat until under budget
  - For tool_call arguments: NEVER truncated (would corrupt the call) —
    instead the WHOLE call is replaced with a placeholder pointing at
    the original tool_call_id
"""

from __future__ import annotations

from dataclasses import dataclass

from app.agent_framework.tokenizer import count_tokens

# Default per-message cap. 50k tokens ≈ 200KB of text; well above any
# reasonable single-paste use case but well under any model's window
# (qwen-max is 32k, qwen-plus 128k — single message > 50k means the
# user is likely pasting machine-generated noise).
DEFAULT_PER_MESSAGE_TOKEN_CAP = 50_000

# Marker text inserted into truncated content. The agent should treat
# the wrapped block as a notice that data was elided.
TRUNCATION_MARKER = (
    "\n\n[... truncated for length: {dropped} tokens removed by boundary ...]"
)

# Placeholder for tool_calls whose arguments overran the cap — we can't
# truncate JSON args without breaking the call shape.
TOOL_CALL_PLACEHOLDER = (
    "[tool_call replaced — arguments exceeded {cap} tokens "
    "(was {actual} tokens). Original tool_call_id: {tcid}]"
)


@dataclass(frozen=True)
class TruncationOutcome:
    """What happened to a single message."""

    message: dict
    truncated: bool
    tokens_before: int
    tokens_after: int


def cap_message_tokens(
    message: dict,
    *,
    cap: int = DEFAULT_PER_MESSAGE_TOKEN_CAP,
    model: str = "",
) -> TruncationOutcome:
    """Truncate ``message`` if its token count exceeds ``cap``.

    Returns the (possibly-truncated) message + before/after token counts.
    Original message is NOT mutated.
    """
    content = message.get("content")
    tool_calls = message.get("tool_calls") or []

    before = _measure_message(message, model)
    if before <= cap:
        return TruncationOutcome(
            message=message,
            truncated=False,
            tokens_before=before,
            tokens_after=before,
        )

    new_msg = dict(message)

    # Step 1: truncate string/multi-part content.
    if isinstance(content, str):
        new_msg["content"] = _truncate_string(content, cap, model)
    elif isinstance(content, list):
        new_msg["content"] = _truncate_multipart(content, cap, model)

    # Step 2: replace overruning tool_calls.
    if tool_calls:
        new_calls = []
        for call in tool_calls:
            fn = call.get("function") or {}
            args = str(fn.get("arguments") or "")
            arg_tokens = count_tokens(args, model)
            if arg_tokens > cap:
                tcid = call.get("id") or "?"
                placeholder = TOOL_CALL_PLACEHOLDER.format(
                    cap=cap, actual=arg_tokens, tcid=tcid
                )
                new_calls.append(
                    {
                        **call,
                        "function": {**fn, "arguments": placeholder},
                    }
                )
            else:
                new_calls.append(call)
        new_msg["tool_calls"] = new_calls

    after = _measure_message(new_msg, model)
    return TruncationOutcome(
        message=new_msg,
        truncated=True,
        tokens_before=before,
        tokens_after=after,
    )


def cap_messages_tokens(
    messages: list[dict],
    *,
    cap: int = DEFAULT_PER_MESSAGE_TOKEN_CAP,
    model: str = "",
) -> list[TruncationOutcome]:
    """Apply ``cap_message_tokens`` to each message; returns parallel
    list of outcomes. Use ``[o.message for o in result]`` for the new
    messages list."""
    return [cap_message_tokens(m, cap=cap, model=model) for m in messages]


# ─── Internals ────────────────────────────────────────────────────────


def _measure_message(message: dict, model: str) -> int:
    """Count tokens in one message (content + tool_call args)."""
    n = 0
    content = message.get("content")
    if isinstance(content, str):
        n += count_tokens(content, model)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict):
                n += count_tokens(part.get("text") or str(part), model)
            else:
                n += count_tokens(str(part), model)
    for call in message.get("tool_calls") or []:
        fn = call.get("function") or {}
        n += count_tokens(str(fn.get("arguments") or ""), model)
    return n


def _truncate_string(text: str, cap: int, model: str) -> str:
    """Keep first ~cap tokens, append marker.

    Token-perfect truncation requires the actual tokenizer's encode/decode
    cycle (which we may not have). We binary-search the byte boundary
    that gets us close to the cap, then append the marker.
    """
    if not text:
        return text
    full = count_tokens(text, model)
    if full <= cap:
        return text

    # Binary-search character index that yields ~cap tokens
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if count_tokens(text[:mid], model) <= cap - 50:  # leave headroom for marker
            lo = mid
        else:
            hi = mid - 1
    head = text[:lo]
    dropped = full - count_tokens(head, model)
    return head + TRUNCATION_MARKER.format(dropped=dropped)


def _truncate_multipart(parts: list, cap: int, model: str) -> list:
    """Anthropic-style multi-part content. Truncate the LARGEST text
    part first, repeat until under budget."""
    out = [dict(p) if isinstance(p, dict) else p for p in parts]
    while True:
        total = sum(
            (
                count_tokens(p.get("text") or str(p), model)
                if isinstance(p, dict)
                else count_tokens(str(p), model)
            )
            for p in out
        )
        if total <= cap:
            return out
        # Find largest text part
        largest_idx, largest_size = -1, 0
        for i, p in enumerate(out):
            if isinstance(p, dict) and isinstance(p.get("text"), str):
                size = count_tokens(p["text"], model)
                if size > largest_size:
                    largest_size, largest_idx = size, i
        if largest_idx < 0:
            # No truncatable part found — bail to avoid infinite loop
            return out
        target_part = out[largest_idx]
        target_text = target_part["text"]
        # Truncate this part to fit the remaining budget
        other_total = total - largest_size
        per_part_budget = max(100, cap - other_total)
        out[largest_idx] = {
            **target_part,
            "text": _truncate_string(target_text, per_part_budget, model),
        }
        # Loop will recheck — defensive against rounding


__all__ = [
    "DEFAULT_PER_MESSAGE_TOKEN_CAP",
    "TRUNCATION_MARKER",
    "TOOL_CALL_PLACEHOLDER",
    "TruncationOutcome",
    "cap_message_tokens",
    "cap_messages_tokens",
]

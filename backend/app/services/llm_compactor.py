"""LLM message compaction with tool_use / tool_result pair preservation.

★ P0 critical path (plan-eng-review 2026-04-25, Issue 3.1):
A naive truncation that splits between an assistant ``tool_calls`` message
and its corresponding ``tool`` reply causes the next API call to fail
with ``400 orphaned tool_result`` (Anthropic) or equivalent OpenAI errors.
That breaks ChatPanel mid-conversation. THIS module exists to make sure
that never happens.

Algorithm (mirrors claw-code ``runtime/src/compact.rs``):

1. Estimate prompt tokens via cheap chars/4 heuristic.
2. If under ``max_input_tokens`` → no compaction.
3. Pick an initial split: keep at least ``keep_floor_turns`` tail messages.
4. Walk the split index backward into the head until tail contains NO
   tool reply whose paired ``tool_use`` is on the head side. This is the
   pair-preservation step.
5. If walk-back consumes all messages → cannot compact safely (return as-is).
6. Otherwise summarise the head via the injected ``summarizer``, return
   ``[system_summary, *tail]``.

Public entrypoint: :func:`compact_messages`. Pure async function with
all I/O injected (summarizer + token counter). Unit-testable without
any LLM calls.

Threshold: defaults to 100,000 input tokens (CLAUDE_CODE_AUTO_COMPACT_
INPUT_TOKENS in claw-code). Override per-call.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


# Default trigger threshold. Mirror claw-code constant.
DEFAULT_AUTO_COMPACTION_INPUT_TOKENS = 100_000

# Don't compact if the conversation is already small. Below this floor of
# preserved tail messages, we skip compaction entirely (nothing useful to
# summarise).
DEFAULT_KEEP_FLOOR_TURNS = 6


# Cheap token estimator: ~4 chars per token. Good enough for the threshold
# decision; not used for billing.
def estimate_tokens(messages: list[dict]) -> int:
    total_chars = 0
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            # Multi-part content (e.g. tool_use blocks). Stringify items.
            for part in content:
                total_chars += len(str(part))
        # tool_calls also contribute (function name + JSON args).
        for call in msg.get("tool_calls") or []:
            fn = call.get("function") or {}
            total_chars += len(str(fn.get("name") or "")) + len(
                str(fn.get("arguments") or "")
            )
    return total_chars // 4


# Summariser injected by caller. Receives the messages to summarise and
# returns a single string. Typically wraps a cheap auxiliary LLM call
# (Qwen-Turbo / GPT-4o-mini etc.).
Summarizer = Callable[[list[dict]], Awaitable[str]]


@dataclass(frozen=True)
class CompactionResult:
    """What :func:`compact_messages` returns."""

    messages: list[dict]
    compacted: bool
    head_message_count: int = 0  # how many messages were rolled into summary
    summary: Optional[str] = None
    estimated_input_tokens_before: int = 0
    estimated_input_tokens_after: int = 0


async def compact_messages(
    messages: list[dict],
    *,
    summarizer: Summarizer,
    max_input_tokens: int = DEFAULT_AUTO_COMPACTION_INPUT_TOKENS,
    keep_floor_turns: int = DEFAULT_KEEP_FLOOR_TURNS,
) -> CompactionResult:
    """Compact ``messages`` if they exceed ``max_input_tokens``.

    The returned ``messages`` is always API-safe: every tool reply has its
    paired ``tool_use`` on the same side of the boundary.

    No-op cases (returned ``compacted=False``):
      - Token estimate under threshold.
      - Fewer than ``keep_floor_turns`` messages total.
      - Pair-preservation walk-back consumed everything (rare; defensive).
    """
    estimated_before = estimate_tokens(messages)
    if estimated_before < max_input_tokens:
        return CompactionResult(
            messages=messages,
            compacted=False,
            estimated_input_tokens_before=estimated_before,
            estimated_input_tokens_after=estimated_before,
        )

    if len(messages) <= keep_floor_turns:
        # Nothing to drop without going under the floor.
        return CompactionResult(
            messages=messages,
            compacted=False,
            estimated_input_tokens_before=estimated_before,
            estimated_input_tokens_after=estimated_before,
        )

    # Initial candidate split: keep last ``keep_floor_turns`` messages.
    candidate = len(messages) - keep_floor_turns
    safe_split = _safe_split_index(messages, candidate)

    if safe_split <= 0:
        # Walking back to preserve pairs swallowed the entire history —
        # nothing to summarise. Return as-is and log; caller may bail out.
        logger.warning(
            "[Compactor] cannot find safe split point; pairs span entire history"
        )
        return CompactionResult(
            messages=messages,
            compacted=False,
            estimated_input_tokens_before=estimated_before,
            estimated_input_tokens_after=estimated_before,
        )

    head = messages[:safe_split]
    tail = messages[safe_split:]

    summary_text = await summarizer(head)

    summary_message = {
        "role": "system",
        "content": (
            "<conversation_summary>\n" f"{summary_text}\n" "</conversation_summary>"
        ),
    }
    new_messages = [summary_message, *tail]
    estimated_after = estimate_tokens(new_messages)

    return CompactionResult(
        messages=new_messages,
        compacted=True,
        head_message_count=len(head),
        summary=summary_text,
        estimated_input_tokens_before=estimated_before,
        estimated_input_tokens_after=estimated_after,
    )


# ---------------------------------------------------------------------------
# Pair-preservation: the heart of the P0 contract.
# ---------------------------------------------------------------------------


def _safe_split_index(messages: list[dict], candidate: int) -> int:
    """Walk ``candidate`` backwards until tail has no orphaned tool replies.

    "Orphaned" = a tail message with role='tool' whose ``tool_call_id``
    appears in an assistant ``tool_calls`` block that lives in the head.

    Repeats until stable: moving the boundary back may expose new orphans
    (the now-included assistant turn may have its own tool replies that
    were previously in head and now... wait, no — moving backward only
    moves messages from head INTO tail. We need to re-check that the
    newly-included assistant message's tool_calls all have replies in tail.

    More precisely:
      - If we move boundary back by 1, the message at the new boundary
        joins the tail.
      - If that message is an assistant with tool_calls, those calls'
        replies must ALSO be in the tail. They were originally further
        along the array (tool reply comes AFTER its tool_use), so they
        are. Safe.
      - If that message is a tool reply, its paired tool_use is earlier
        in head — we'd need to walk further back. Hence the loop.

    Returns 0 if pairs span the entire history (cannot safely split).
    """
    while candidate > 0:
        tail = messages[candidate:]
        head = messages[:candidate]

        # tool_call_ids appearing as replies in the tail.
        tail_reply_ids = {
            m.get("tool_call_id")
            for m in tail
            if m.get("role") == "tool" and m.get("tool_call_id")
        }
        if not tail_reply_ids:
            return candidate  # no tool replies in tail → safe

        # tool_call_ids issued by assistants in the head.
        head_call_ids: set[str] = set()
        for m in head:
            if m.get("role") == "assistant":
                for call in m.get("tool_calls") or []:
                    cid = call.get("id")
                    if cid:
                        head_call_ids.add(cid)

        orphans = tail_reply_ids & head_call_ids
        if not orphans:
            return candidate  # all tail replies have their tool_use in tail too

        # Walk back to BEFORE the head assistant message that owns any
        # orphan tool_call_id. That moves it (and any earlier replies it
        # may have produced) into the tail.
        new_candidate = candidate - 1
        while new_candidate >= 0:
            msg = messages[new_candidate]
            if msg.get("role") == "assistant":
                msg_call_ids = {c.get("id") for c in msg.get("tool_calls") or []}
                if msg_call_ids & orphans:
                    candidate = new_candidate
                    break
            new_candidate -= 1
        else:
            # No assistant owning the orphans found in head → defensive 0.
            return 0

    return candidate


__all__ = [
    "DEFAULT_AUTO_COMPACTION_INPUT_TOKENS",
    "DEFAULT_KEEP_FLOOR_TURNS",
    "CompactionResult",
    "Summarizer",
    "compact_messages",
    "estimate_tokens",
]

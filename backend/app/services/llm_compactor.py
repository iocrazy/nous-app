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


# Wave 5a (A1): use the model-aware tokenizer instead of chars/4.
# Old chars/4 under-counted Chinese 4x (1 CJK char = 1 token) — caused
# compaction to trigger LATE on Chinese-heavy conversations and fixed-tail
# budgeting to be wildly wrong. Routes through a proper tokenizer when
# the provider package is installed; falls back to language-aware
# heuristic otherwise.
from app.agent_framework.tokenizer import count_messages_tokens


def estimate_tokens(messages: list[dict], model: str = "") -> int:
    """Estimate prompt-side tokens for ``messages``. ``model`` selects
    the provider tokenizer (qwen / openai / etc.); empty model = heuristic.

    Kept under the original name + signature so callers don't need to
    pass model immediately — passing model when known just makes the
    estimate more accurate.
    """
    return count_messages_tokens(messages, model)


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
    # Wave 5b (B5): True when the head summary came from the cached
    # session-memory doc (no LLM call), False when summarizer was invoked.
    used_session_memory: bool = False


async def compact_messages(
    messages: list[dict],
    *,
    summarizer: Summarizer,
    max_input_tokens: int = DEFAULT_AUTO_COMPACTION_INPUT_TOKENS,
    keep_floor_turns: int = DEFAULT_KEEP_FLOOR_TURNS,
    model: str = "",
    tail_token_budget: Optional[int] = None,
    session_memory_loader: Optional[Callable[[], Awaitable[Optional[str]]]] = None,
    prune_tool_results: bool = True,
    prune_aging_after_turns: int = 10,
) -> CompactionResult:
    """Compact ``messages`` if they exceed ``max_input_tokens``.

    The returned ``messages`` is always API-safe: every tool reply has its
    paired ``tool_use`` on the same side of the boundary.

    Wave 5a (A3): tail size is now token-budget-aware. ``tail_token_budget``
    overrides the legacy ``keep_floor_turns`` when provided. We walk
    backwards from the end accumulating turns until the budget is hit;
    minimum tail of 2 turns regardless. ``model`` selects the tokenizer.
    Legacy fixed-N path stays as fallback when ``tail_token_budget`` is None.

    No-op cases (returned ``compacted=False``):
      - Token estimate under threshold.
      - Fewer than ``keep_floor_turns`` messages total (legacy path).
      - Pair-preservation walk-back consumed everything (rare; defensive).
    """
    estimated_before = estimate_tokens(messages, model)

    # Wave F (F2): cheap pre-pass — dedupe + age tool_results BEFORE the
    # threshold check. Two big wins:
    #   1. Many "near-overflow" conversations drop back UNDER threshold
    #      after dedupe (no compaction LLM call needed at all).
    #   2. When compaction does fire, head/tail are already ~30% smaller
    #      so the LLM summary is cheaper + tighter.
    # Pure functions; safe to skip via prune_tool_results=False.
    if prune_tool_results and estimated_before >= int(max_input_tokens * 0.7):
        from app.agent_framework.tool_result_pruner import prune as _prune

        pruned, prune_stats = _prune(
            messages, aging_after_turns=prune_aging_after_turns
        )
        if prune_stats.duplicates_replaced or prune_stats.aged_results:
            messages = pruned
            estimated_before = estimate_tokens(messages, model)
            from app.agent_framework._metrics_helper import inc_metric
            inc_metric("compaction_pre_pass_pruned",
                       by=prune_stats.duplicates_replaced + prune_stats.aged_results)
            logger.info(
                "[Compactor] pre-pass pruned %d dups + %d aged "
                "(%d chars dropped); new estimate=%d",
                prune_stats.duplicates_replaced,
                prune_stats.aged_results,
                prune_stats.chars_dropped,
                estimated_before,
            )

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

    # Wave 5a (A3): pick split point — token-budget mode preferred when
    # caller supplied a budget; legacy fixed-N otherwise.
    if tail_token_budget is not None and tail_token_budget > 0:
        candidate = _candidate_split_by_token_budget(
            messages, tail_token_budget, model=model, min_tail_turns=2
        )
    else:
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

    # Wave 5b (B5): if a session_memory_loader is provided, prefer the
    # already-maintained session-memory document over a fresh LLM call.
    # The loader returns the cached body_md (or None if no memory yet).
    summary_text: Optional[str] = None
    used_session_memory = False
    if session_memory_loader is not None:
        try:
            cached = await session_memory_loader()
            if cached:
                summary_text = cached
                used_session_memory = True
        except Exception as exc:
            logger.warning(
                "[Compactor] session_memory_loader failed: %s — falling back to fresh summary",
                exc,
            )

    if summary_text is None:
        summary_text = await summarizer(head)

    summary_message = {
        "role": "system",
        "content": (
            "<conversation_summary>\n" f"{summary_text}\n" "</conversation_summary>"
        ),
    }
    new_messages = [summary_message, *tail]
    estimated_after = estimate_tokens(new_messages, model)

    # Wave I (I3) + J1: telemetry via helper.
    from app.agent_framework._metrics_helper import inc_metric
    inc_metric("compaction_triggered")
    inc_metric(
        "compaction_used_session_memory"
        if used_session_memory
        else "compaction_used_fresh_summarizer"
    )

    return CompactionResult(
        messages=new_messages,
        compacted=True,
        head_message_count=len(head),
        summary=summary_text,
        estimated_input_tokens_before=estimated_before,
        estimated_input_tokens_after=estimated_after,
        used_session_memory=used_session_memory,
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


def _candidate_split_by_token_budget(
    messages: list[dict],
    tail_token_budget: int,
    *,
    model: str,
    min_tail_turns: int = 2,
) -> int:
    """Wave 5a (A3): pick split index so tail fits in ``tail_token_budget``.

    Walks backwards from end accumulating token counts; returns the
    first index where adding the next-older message would push over
    budget, BUT never returns an index that would leave fewer than
    ``min_tail_turns`` messages in tail.

    Returns 0 if the entire conversation fits in budget (caller will
    then bail to legacy path or return as-is).
    """
    if not messages:
        return 0
    n = len(messages)
    accumulated = 0
    # Walk from the end; tail_start is the index of the FIRST tail msg
    tail_start = n
    for i in range(n - 1, -1, -1):
        msg_tokens = estimate_tokens([messages[i]], model)
        # min_tail_turns: force at least min_tail_turns into tail even
        # if they exceed budget (any single huge msg already capped by A2)
        included_so_far = n - i
        if accumulated + msg_tokens > tail_token_budget and included_so_far > min_tail_turns:
            break
        accumulated += msg_tokens
        tail_start = i
    return tail_start


__all__ = [
    "DEFAULT_AUTO_COMPACTION_INPUT_TOKENS",
    "DEFAULT_KEEP_FLOOR_TURNS",
    "CompactionResult",
    "Summarizer",
    "compact_messages",
    "estimate_tokens",
]

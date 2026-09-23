"""Tiered context compaction — keep the agent loop running long.

When a long conversation approaches the model's context window, the
runner used to either reject the turn (`check_context_budget` raises
ContextWindowError) or pass through a request that the provider would
later truncate / refuse. Neither lets the agent keep working.

This module installs a 4-tier policy that runs at the start of every
``AgentRunner.run_turn``:

    | budget_used   | tier       | action                                |
    |---------------|------------|---------------------------------------|
    | < 60 %        | green      | noop                                  |
    | 60-80 %       | yellow     | `prune` tool results (dedupe + age)   |
    | 80-90 %       | orange     | yellow + emergency cap (Phase 1 stub) |
    | > 90 %        | red        | yellow + emergency cap, more aggressive |

Phase 1 (this file) ships only ``yellow`` properly. The ``orange`` and
``red`` tiers fall through to a lossy emergency truncate using the
existing ``cap_messages_tokens`` helper. Phase 2 replaces those tiers
with an LLM-driven head summarizer; the public API of this module
(``ContextCompactor.maybe_compact``) is the seam that doesn't change.

A compaction never edits ``system_message`` and never drops the most
recent ``keep_recent_turns`` messages — those carry intent that an
LLM-summary cannot faithfully reconstruct.

Telemetry: ``CompactionStats`` is returned alongside the new messages
list so the caller can persist counts to ``agent_runs.metadata`` for
observability + cost analysis.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from loguru import logger

from app.agent_framework._metrics_helper import inc_metric
from app.agent_framework.context_window import resolve_model_window
from app.agent_framework.message_truncation import cap_messages_tokens
from app.agent_framework.tokenizer import count_messages_tokens, count_tokens
from app.agent_framework.tool_result_pruner import PruneStats, prune


class CompactionTier(str, Enum):
    GREEN = "green"
    YELLOW = "yellow"
    ORANGE = "orange"
    RED = "red"


@dataclass(frozen=True)
class CompactionThresholds:
    """Token-budget percentages that move us between tiers.

    Defaults follow the "Anthropic effective harnesses" recommendation
    (60/80/90). Conservative on purpose — burning a small amount of
    cheap-model spend on early compaction is much cheaper than losing
    a turn to a context-window provider error.
    """

    yellow_pct: float = 0.60
    orange_pct: float = 0.80
    red_pct: float = 0.90


@dataclass(frozen=True)
class CompactionStats:
    """Per-turn telemetry. Persist via agent_runs.metadata.

    ``notes`` is a tuple — a list with ``frozen=True`` would lie about
    immutability since the list itself stays mutable.
    """

    tier: CompactionTier
    tokens_before: int
    tokens_after: int
    tokens_saved: int
    yellow_prune: Optional[PruneStats] = None
    emergency_dropped_chars: int = 0
    notes: tuple[str, ...] = ()


class NoSafeSplitError(RuntimeError):
    """No head/tail boundary keeps every tool call with its replies.

    Raised before any model call, so the caller's emergency-cap fallback
    reports ``attempts=0`` rather than pretending it tried to summarize.
    """


def _safe_split_index(messages: list[dict], candidate: int) -> int:
    """Walk ``candidate`` back until the tail holds no orphaned tool replies.

    Summarization drops ``messages[:split]`` and keeps ``messages[split:]``
    verbatim. A tail ``role=tool`` reply whose ``tool_call_id`` was issued by
    an assistant ``tool_calls`` message in the head would be orphaned — the
    call is summarized away, the reply answers nothing, and Anthropic-shaped
    providers reject the request outright.

    Moving the boundary back only moves messages from head into tail. An
    assistant message that joins the tail brings no new orphan (its replies
    come after it, so they are already in the tail); a tool reply that joins
    the tail may, hence the loop until stable.

    Returns 0 when no boundary is safe (one exchange spans the history).
    Ported from ``llm_compactor._safe_split_index`` when the chat path moved
    onto this compactor.
    """
    while candidate > 0:
        tail_reply_ids = {
            m.get("tool_call_id")
            for m in messages[candidate:]
            if m.get("role") == "tool" and m.get("tool_call_id")
        }
        if not tail_reply_ids:
            return candidate
        head_call_ids = {
            call.get("id")
            for m in messages[:candidate]
            if m.get("role") == "assistant"
            for call in m.get("tool_calls") or []
            if call.get("id")
        }
        orphans = tail_reply_ids & head_call_ids
        if not orphans:
            return candidate
        owner = _last_owner_before(messages, candidate, orphans)
        if owner is None:
            return 0
        candidate = owner
    return candidate


def _last_owner_before(
    messages: list[dict], candidate: int, call_ids: set[Any]
) -> int | None:
    """Index of the last assistant before ``candidate`` issuing any of
    ``call_ids``; ``None`` if none does."""
    for idx in range(candidate - 1, -1, -1):
        msg = messages[idx]
        if msg.get("role") != "assistant":
            continue
        if {c.get("id") for c in msg.get("tool_calls") or []} & call_ids:
            return idx
    return None


async def _emit(recorder: Any, event_type: str, payload: dict[str, Any]) -> None:
    """Thin alias onto the single transcript entry (runner/events.py)."""
    from app.services.ai.runner.events import emit

    await emit(recorder, event_type, payload)


class ContextCompactor:
    """Tiered token-budget enforcer. Holds no per-run state — safe to
    instantiate once or per-turn.
    """

    # Phase 2 will swap this for an LLM summarizer. Keeping the constant
    # here means the integration point in agent_runner doesn't change.
    EMERGENCY_KEEP_RECENT_TURNS = 4
    RED_KEEP_RECENT_TURNS = 2

    # Total summarization attempts before giving up and letting the caller
    # fall back to the deterministic emergency cap. Low on purpose: retrying
    # a model that is echoing its input costs real tokens each time, and the
    # fallback is free and guaranteed to shrink.
    SUMMARY_ATTEMPTS = 2

    # Emergency cap targets this fraction of the window for messages
    # (rest reserved for system prompt). 0.80 lands a compacted run at
    # the orange/red boundary — enough headroom for the next turn's
    # tool output without immediately re-triggering compaction.
    EMERGENCY_TARGET_PCT = 0.80

    # Hard-stop floor when even the "keep recent" budget can't fit the
    # window. Below this we give up and let the existing
    # check_context_budget raise — same behaviour as before this module.
    EMERGENCY_FLOOR_PCT = 0.95

    def __init__(self, thresholds: Optional[CompactionThresholds] = None):
        self.thresholds = thresholds or CompactionThresholds()

    @staticmethod
    def is_enabled() -> bool:
        """Master kill-switch. Default ON; set AGENT_AUTO_COMPACT=false
        in env to bypass the whole module (e.g., for debugging a
        regression on a single tenant)."""
        return os.environ.get("AGENT_AUTO_COMPACT", "true").lower() != "false"

    async def maybe_compact(
        self,
        *,
        system_message: str,
        user_messages: list[dict],
        model: str,
        adapter: Any = None,
        tools: Optional[list] = None,
        recorder: Any = None,
        user_id: str | None = None,
    ) -> tuple[list[dict], CompactionStats]:
        """Return possibly-compacted messages + stats.

        ``recorder`` (anything with ``async record_event(type, payload)``)
        receives the compaction bracket — ``compaction_start`` /
        ``compaction_summary`` / ``compaction_end`` — on the orange/red path
        only. Telemetry: ``None`` is silent, a throwing recorder is a warning.

        ``user_id`` is routing context for the legacy maintenance-model
        summary (``codex-local`` runs on the user's own paired machine); the
        warm path doesn't need it — it reuses the already-routed ``adapter``.

        Async because Phase 2 may make an LLM call to summarize the
        head when the budget is tight. The yellow path is still
        synchronous internally; the await on green is a no-op.

        ``system_message`` is read for token counting only — never edited
        or returned. Caller keeps the original.

        ``user_messages`` is treated as immutable; this method always
        returns a new list (or the input unchanged if tier == green).
        """
        if not self.is_enabled():
            # Kill switch: skip even tokenization. Caller asked for the
            # cheapest possible no-op; tokens=0 is a sentinel meaning
            # "not measured" — caller's recorder.note_compaction guard
            # already short-circuits on tokens_saved == 0.
            return user_messages, CompactionStats(
                tier=CompactionTier.GREEN,
                tokens_before=0,
                tokens_after=0,
                tokens_saved=0,
                notes=("AGENT_AUTO_COMPACT=false",),
            )

        window, window_known = resolve_model_window(model)
        # Every tier below divides by `window`. When the table didn't know the
        # model, that denominator is a default, and a wrong denominator makes
        # a confidently-wrong tier — so it rides every return path, including
        # green, which is where a mis-measured model is most likely to sit.
        window_notes: tuple[str, ...] = (
            ()
            if window_known
            else (
                f"window is a fallback ({window} tokens): model={model!r} is "
                "not in _MODEL_WINDOWS, so tier thresholds are relative to a "
                "default, not to this model's real context window",
            )
        )
        if window <= 0:
            # Unknown model → bail out with noop. Same sentinel semantics
            # as the kill-switch path: don't pay for tokenization just to
            # populate stats nobody will read.
            return user_messages, CompactionStats(
                tier=CompactionTier.GREEN,
                tokens_before=0,
                tokens_after=0,
                tokens_saved=0,
                notes=(f"unknown model={model}, compaction skipped",),
            )

        sys_tokens = count_tokens(system_message, model) if system_message else 0
        msg_tokens = count_messages_tokens(user_messages, model)
        total = sys_tokens + msg_tokens
        used_pct = total / window if window > 0 else 0.0

        tier = self._tier_for(used_pct)

        if tier == CompactionTier.GREEN:
            return user_messages, CompactionStats(
                tier=tier,
                tokens_before=total,
                tokens_after=total,
                tokens_saved=0,
                notes=window_notes,
            )

        # Yellow tier: prune tool results (dedupe + age old bodies).
        # Cheap, no LLM call. This alone usually drops 30-60 % when an
        # agent has been re-reading the same files.
        pruned, prune_stats = prune(user_messages)
        new_total = sys_tokens + count_messages_tokens(pruned, model)

        # Yellow alone may already pull us under the orange threshold —
        # check before paying the emergency cost.
        new_pct = new_total / window
        if tier == CompactionTier.YELLOW or new_pct < self.thresholds.orange_pct:
            return pruned, CompactionStats(
                tier=CompactionTier.YELLOW,
                tokens_before=total,
                tokens_after=new_total,
                tokens_saved=total - new_total,
                yellow_prune=prune_stats,
                notes=window_notes,
            )

        # Orange / Red: head summarization. Replace older turns with a
        # single [Earlier conversation summary] system message produced
        # by a cheap model (default Haiku 4.5). Falls back to lossy
        # character truncation if the summarizer fails / times out so
        # context_compactor stays in control of the policy.
        keep = (
            self.RED_KEEP_RECENT_TURNS
            if tier == CompactionTier.RED
            else self.EMERGENCY_KEEP_RECENT_TURNS
        )
        notes: list[str] = list(window_notes)
        capped: list[dict]
        dropped_chars = 0
        inc_metric("compaction_triggered")
        # The bracket: start lands BEFORE the summarizer is awaited, end lands
        # in ``finally``. A crash in between leaves an orphan start — the
        # crash scene — never an end that claims a completion it did not see.
        await _emit(
            recorder,
            "compaction_start",
            {"tier": tier.value, "tokens_before": total, "window": window},
        )
        end_payload: dict[str, Any] = {}
        try:
            try:
                capped = await self._compact_with_summary(
                    messages=pruned,
                    keep_recent_turns=keep,
                    model=model,
                    system_message=system_message,
                    tools=tools,
                    adapter=adapter,
                    recorder=recorder,
                    user_id=user_id,
                )
                notes.append("compacted via LLM head summary")
            except Exception as exc:
                logger.warning(
                    "[compactor] summarizer failed, falling back to "
                    "emergency-cap truncation: {}",
                    exc,
                )
                await _emit(
                    recorder,
                    "compaction_summary",
                    {
                        "path": "emergency_cap",
                        "attempts": (
                            0
                            if isinstance(exc, NoSafeSplitError)
                            else self.SUMMARY_ATTEMPTS
                        ),
                        "error": f"{exc!s:.200}",
                    },
                )
                capped, dropped_chars = self._emergency_cap(
                    messages=pruned,
                    model=model,
                    window=window,
                    sys_tokens=sys_tokens,
                    keep_recent_turns=keep,
                )
                reason = (
                    "no safe split"
                    if isinstance(exc, NoSafeSplitError)
                    else "summarizer failed"
                )
                notes.append(f"emergency-cap fallback ({reason}: {exc!s:.120})")
            final_total = sys_tokens + count_messages_tokens(capped, model)
            end_payload = {
                "tokens_after": final_total,
                "tokens_saved": total - final_total,
            }
        except BaseException as exc:
            end_payload = {"error": f"{type(exc).__name__}: {exc!s:.200}"}
            raise
        finally:
            await _emit(recorder, "compaction_end", end_payload)
        if final_total / window > self.EMERGENCY_FLOOR_PCT:
            notes.append(
                f"still over emergency floor {self.EMERGENCY_FLOOR_PCT:.0%} "
                f"after compaction; downstream budget check will reject"
            )

        logger.info(
            "[compactor] tier={} model={} window={} before={} after={} saved={}",
            tier.value,
            model,
            window,
            total,
            final_total,
            total - final_total,
        )

        return capped, CompactionStats(
            tier=tier,
            tokens_before=total,
            tokens_after=final_total,
            tokens_saved=total - final_total,
            yellow_prune=prune_stats,
            emergency_dropped_chars=dropped_chars,
            notes=tuple(notes),
        )

    async def _compact_with_summary(
        self,
        *,
        messages: list[dict],
        keep_recent_turns: int,
        model: str,
        system_message: Optional[str] = None,
        tools: Optional[list] = None,
        adapter: Any = None,
        recorder: Any = None,
        user_id: str | None = None,
    ) -> list[dict]:
        """Replace messages[:-keep_recent_turns] with a single
        [Earlier conversation summary] system message produced by the
        configured cheap model.

        Convergence is enforced here, not assumed: a summary is accepted only
        if it is genuinely smaller than the messages it replaces. A model that
        echoes its input back — a real failure mode for small models handed a
        long transcript — would otherwise GROW the context and be recorded as
        a successful compaction. Rejected drafts are retried up to
        ``SUMMARY_ATTEMPTS`` in total, then this raises.

        The boundary is pulled back so no assistant ``tool_calls`` message is
        summarized away while its ``role=tool`` replies stay in the tail
        (``_safe_split_index``); the tail may therefore exceed
        ``keep_recent_turns``.

        Raises ``RuntimeError`` (from the summarizer, or from the shrink
        check) so the caller can fall back to the lossy ``_emergency_cap``
        path — which is deterministic and always shrinks. Raises
        ``NoSafeSplitError`` (a ``RuntimeError``) without calling any model
        when every boundary would orphan a tool reply.
        """
        if len(messages) <= keep_recent_turns:
            return list(messages)  # nothing to summarize

        split = _safe_split_index(messages, len(messages) - keep_recent_turns)
        if split == 0:
            raise NoSafeSplitError(
                "no safe split point: every head/tail boundary would separate "
                "a tool call from its replies"
            )
        head = messages[:split]
        tail = messages[split:]
        head_tokens = count_messages_tokens(head, model)

        last_summary_tokens: Optional[int] = None
        for attempt in range(1, self.SUMMARY_ATTEMPTS + 1):
            summary_text, summary_path = await self._produce_summary(
                head,
                system_message=system_message,
                tools=tools,
                adapter=adapter,
                model=model,
                user_id=user_id,
            )
            summary_message = {
                "role": "system",
                "content": "[Earlier conversation summary]\n" + summary_text,
            }
            summary_tokens = count_messages_tokens([summary_message], model)
            if summary_tokens < head_tokens:
                await _emit(
                    recorder,
                    "compaction_summary",
                    {
                        "summary_tokens": summary_tokens,
                        "head_tokens": head_tokens,
                        "attempts": attempt,
                        "path": summary_path,
                        # phase 2b-1: replay.messages_from_events rebuilds
                        # the post-compaction history from this text; the
                        # emergency-cap row above stays metrics-only.
                        "summary": summary_text,
                    },
                )
                if attempt > 1:
                    logger.info(
                        "[compactor] summary accepted on attempt {} "
                        "({} → {} tokens)",
                        attempt,
                        head_tokens,
                        summary_tokens,
                    )
                return [summary_message] + list(tail)

            last_summary_tokens = summary_tokens
            logger.warning(
                "[compactor] summary did not shrink its source on attempt "
                "{}/{}: {} tokens in, {} tokens out",
                attempt,
                self.SUMMARY_ATTEMPTS,
                head_tokens,
                summary_tokens,
            )

        raise RuntimeError(
            f"summary did not shrink its source after {self.SUMMARY_ATTEMPTS} "
            f"attempts ({head_tokens} tokens in, {last_summary_tokens} out)"
        )

    async def _produce_summary(
        self,
        head: list[dict],
        *,
        system_message: Optional[str],
        tools: Optional[list],
        adapter: Any,
        model: str,
        user_id: str | None = None,
    ) -> tuple[str, str]:
        """Warm-prefix first, legacy cheap-model second.

        Returns ``(summary_text, path)`` with ``path`` in ``{"warm", "legacy"}``
        so the transcript can say which one actually produced the summary.

        W3-1 (user-approved cost shift): replaying the conversation's own
        prefix on its own adapter lets the provider's KV cache cover every
        input token but the appended instruction. The chain is
        warm → legacy → (caller's) emergency cap — a warm hiccup must not skip
        straight to lossy truncation. No adapter / no system message (the
        background paths that never had one) → straight to legacy.

        Calls go through the module attribute (`summarizer.summarize`), not a
        from-import: tests patch that attribute, and a from-import would pin
        the original function object here and make the patch invisible.
        """
        from app.agent_framework import summarizer

        if adapter is not None and system_message:
            try:
                text = await summarizer.summarize_warm_prefix(
                    adapter=adapter,
                    system_message=system_message,
                    tools=tools,
                    head=head,
                    model=model,
                )
                return text, "warm"
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[compactor] warm-prefix summarize failed, falling back "
                    "to the maintenance model: {}",
                    exc,
                )
        return await summarizer.summarize(head, user_id=user_id), "legacy"

    def _tier_for(self, used_pct: float) -> CompactionTier:
        if used_pct >= self.thresholds.red_pct:
            return CompactionTier.RED
        if used_pct >= self.thresholds.orange_pct:
            return CompactionTier.ORANGE
        if used_pct >= self.thresholds.yellow_pct:
            return CompactionTier.YELLOW
        return CompactionTier.GREEN

    def _emergency_cap(
        self,
        *,
        messages: list[dict],
        model: str,
        window: int,
        sys_tokens: int,
        keep_recent_turns: int,
    ) -> tuple[list[dict], int]:
        """Truncate older message bodies to fit the window.

        Phase 1 implementation: keep the most recent ``keep_recent_turns``
        messages verbatim; cap each older message body to a fair share of
        the remaining budget. Phase 2 will replace this with an LLM
        summary of the older head.

        Pair-safe without ``_safe_split_index``: this path only truncates
        bodies (``cap_messages_tokens`` copies each message, keeping
        ``tool_calls`` / ``tool_call_id``) and never removes a message, so no
        boundary here can orphan a tool reply.

        Returns (new_messages, total_chars_dropped).
        """
        if len(messages) <= keep_recent_turns:
            return messages, 0

        # Reserve EMERGENCY_TARGET_PCT of the window for the
        # conversation; system prompt gets the remainder.
        # cap_messages_tokens enforces a per-message cap, not a total —
        # we hand it a per-message budget that the math says will fit.
        target_msg_tokens = int(window * self.EMERGENCY_TARGET_PCT) - sys_tokens
        if target_msg_tokens <= 0:
            # System prompt alone overflows — nothing this module can do.
            return messages, 0

        head = messages[:-keep_recent_turns]
        tail = messages[-keep_recent_turns:]
        tail_tokens = count_messages_tokens(tail, model)
        head_budget = max(0, target_msg_tokens - tail_tokens)

        if not head:
            return messages, 0

        per_msg_cap = max(200, head_budget // len(head))

        # cap_messages_tokens returns parallel list of TruncationOutcome
        # — pull .message out for the new messages list.
        outcomes = cap_messages_tokens(head, cap=per_msg_cap, model=model)
        capped_head = [o.message for o in outcomes]

        before_chars = sum(len(str(m.get("content") or "")) for m in head)
        after_chars = sum(len(str(m.get("content") or "")) for m in capped_head)

        return capped_head + tail, max(0, before_chars - after_chars)


__all__ = [
    "ContextCompactor",
    "NoSafeSplitError",
    "CompactionTier",
    "CompactionStats",
    "CompactionThresholds",
]

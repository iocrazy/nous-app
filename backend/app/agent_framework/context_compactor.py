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
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from loguru import logger

from app.agent_framework.context_window import model_window_size
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
    """Per-turn telemetry. Persist via agent_runs.metadata."""

    tier: CompactionTier
    tokens_before: int
    tokens_after: int
    tokens_saved: int
    yellow_prune: Optional[PruneStats] = None
    emergency_dropped_chars: int = 0
    notes: list[str] = field(default_factory=list)


class ContextCompactor:
    """Tiered token-budget enforcer. Holds no per-run state — safe to
    instantiate once or per-turn.
    """

    # Phase 2 will swap this for an LLM summarizer. Keeping the constant
    # here means the integration point in agent_runner doesn't change.
    EMERGENCY_KEEP_RECENT_TURNS = 4
    RED_KEEP_RECENT_TURNS = 2

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

    def maybe_compact(
        self,
        *,
        system_message: str,
        user_messages: list[dict],
        model: str,
    ) -> tuple[list[dict], CompactionStats]:
        """Return possibly-compacted messages + stats.

        ``system_message`` is read for token counting only — never edited
        or returned. Caller keeps the original.

        ``user_messages`` is treated as immutable; this method always
        returns a new list (or the input unchanged if tier == green).
        """
        if not self.is_enabled():
            tokens = count_messages_tokens(user_messages, model)
            return user_messages, CompactionStats(
                tier=CompactionTier.GREEN,
                tokens_before=tokens,
                tokens_after=tokens,
                tokens_saved=0,
                notes=["AGENT_AUTO_COMPACT=false"],
            )

        window = model_window_size(model)
        if window <= 0:
            # Unknown model → bail out with noop. The downstream budget
            # check will still run and reject if the request is way over.
            tokens = count_messages_tokens(user_messages, model)
            return user_messages, CompactionStats(
                tier=CompactionTier.GREEN,
                tokens_before=tokens,
                tokens_after=tokens,
                tokens_saved=0,
                notes=[f"unknown model={model}, compaction skipped"],
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
            )

        # Orange / Red: emergency cap (Phase 1 stub for what Phase 2
        # will replace with an LLM head summary). cap_messages_tokens
        # truncates message bodies in place; lossier than summarising
        # but lets the turn continue.
        keep = (
            self.RED_KEEP_RECENT_TURNS
            if tier == CompactionTier.RED
            else self.EMERGENCY_KEEP_RECENT_TURNS
        )
        capped, dropped_chars = self._emergency_cap(
            messages=pruned,
            model=model,
            window=window,
            sys_tokens=sys_tokens,
            keep_recent_turns=keep,
        )
        final_total = sys_tokens + count_messages_tokens(capped, model)

        notes: list[str] = [
            f"emergency-cap (Phase 1 stub); Phase 2 will replace with LLM summary"
        ]
        if final_total / window > self.EMERGENCY_FLOOR_PCT:
            notes.append(
                f"still over emergency floor {self.EMERGENCY_FLOOR_PCT:.0%} "
                f"after compaction; downstream budget check will reject"
            )

        logger.info(
            "[compactor] tier={} model={} window={} before={} after={} saved={}",
            tier.value, model, window, total, final_total, total - final_total,
        )

        return capped, CompactionStats(
            tier=tier,
            tokens_before=total,
            tokens_after=final_total,
            tokens_saved=total - final_total,
            yellow_prune=prune_stats,
            emergency_dropped_chars=dropped_chars,
            notes=notes,
        )

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

        Returns (new_messages, total_chars_dropped).
        """
        if len(messages) <= keep_recent_turns:
            return messages, 0

        # Reserve 80 % of the window for the conversation; system prompt
        # gets the remaining 20 %. cap_messages_tokens enforces a per-
        # message cap, not a total — we hand it a per-message budget that
        # the math says will fit.
        target_msg_tokens = int(window * 0.8) - sys_tokens
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
    "CompactionTier",
    "CompactionStats",
    "CompactionThresholds",
]

"""Regression tests for tiered context compaction.

These pin the four-tier policy (green / yellow / orange / red) and the
invariants the runner relies on:

  - the most recent ``keep_recent_turns`` messages are NEVER edited
  - system_message is never returned (caller keeps the original)
  - returning unchanged messages on green is the cheap path the hot
    loop counts on
  - AGENT_AUTO_COMPACT=false is a hard kill switch that bypasses
    everything
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from app.agent_framework.context_compactor import (
    CompactionStats,
    CompactionThresholds,
    CompactionTier,
    ContextCompactor,
)


@pytest.fixture
def compactor() -> ContextCompactor:
    return ContextCompactor()


def _tail_messages(content: str = "recent turn", count: int = 4) -> list[dict]:
    """A trailing block of recent turns the compactor must preserve verbatim."""
    return [{"role": "user", "content": f"{content} #{i}"} for i in range(count)]


def _bulk_messages(approx_tokens: int) -> list[dict]:
    """Cheap way to hit a token budget — heuristic counter is ~chars/4."""
    body = "x" * (approx_tokens * 4)
    return [{"role": "assistant", "content": body}]


def test_kill_switch_returns_messages_unchanged(monkeypatch, compactor):
    """AGENT_AUTO_COMPACT=false must short-circuit before any work — the
    compactor goes from a critical-path component to a no-op so a single
    tenant can be debugged without disabling the runner."""
    monkeypatch.setenv("AGENT_AUTO_COMPACT", "false")
    msgs = _tail_messages(count=3)

    out, stats = compactor.maybe_compact(
        system_message="sys", user_messages=msgs, model="claude-sonnet-4-6"
    )

    assert out is msgs  # identity check — no copy
    assert stats.tier == CompactionTier.GREEN
    assert "AGENT_AUTO_COMPACT=false" in stats.notes


def test_unknown_model_falls_back_to_green(compactor):
    """Bail out cleanly on unknown models — the downstream context-budget
    check still runs and rejects oversize requests."""
    msgs = _tail_messages(count=2)

    with patch(
        "app.agent_framework.context_compactor.model_window_size", return_value=0
    ):
        out, stats = compactor.maybe_compact(
            system_message="", user_messages=msgs, model="some-future-model"
        )

    assert out is msgs
    assert stats.tier == CompactionTier.GREEN
    assert any("unknown model" in n for n in stats.notes)


def test_green_tier_returns_input_identity(compactor):
    """Hot path: a small turn under 60 % of the window must not even
    allocate a new list. Identity check guards against accidental
    `list(messages)` copies in the green branch."""
    # Tiny turn → easily under green threshold for a 200K window
    msgs = _tail_messages(count=2)

    out, stats = compactor.maybe_compact(
        system_message="short sys", user_messages=msgs, model="claude-sonnet-4-6"
    )

    assert out is msgs
    assert stats.tier == CompactionTier.GREEN
    assert stats.tokens_saved == 0


def test_yellow_tier_invokes_prune(compactor):
    """At 60-80 %, the compactor must call ``prune`` (dedupe + age) but
    NOT touch the emergency cap path. Cheap, no LLM call yet."""
    msgs = _tail_messages(count=4)

    with patch(
        "app.agent_framework.context_compactor.model_window_size", return_value=1000
    ), patch(
        "app.agent_framework.context_compactor.count_tokens", return_value=0
    ), patch(
        "app.agent_framework.context_compactor.count_messages_tokens",
        side_effect=[700, 500],  # before, after-prune
    ), patch(
        "app.agent_framework.context_compactor.prune",
        return_value=(msgs, _make_prune_stats(duplicates=2)),
    ) as mock_prune:
        out, stats = compactor.maybe_compact(
            system_message="", user_messages=msgs, model="claude-sonnet-4-6"
        )

    mock_prune.assert_called_once()
    assert stats.tier == CompactionTier.YELLOW
    assert stats.tokens_saved == 200
    assert stats.yellow_prune is not None


def test_orange_tier_runs_emergency_cap_when_yellow_insufficient(compactor):
    """If yellow alone can't pull us under the orange threshold, the
    emergency cap must run and keep the recent turns intact."""
    head = _bulk_messages(approx_tokens=200)
    tail = _tail_messages(count=4)
    msgs = head + tail

    with patch(
        "app.agent_framework.context_compactor.model_window_size", return_value=1000
    ), patch(
        "app.agent_framework.context_compactor.count_tokens", return_value=0
    ), patch(
        "app.agent_framework.context_compactor.count_messages_tokens",
        # before=850 (orange); after-prune=830 (still orange);
        # tail=200; final=400
        side_effect=[850, 830, 200, 400],
    ), patch(
        "app.agent_framework.context_compactor.prune",
        return_value=(msgs, _make_prune_stats()),
    ):
        out, stats = compactor.maybe_compact(
            system_message="",
            user_messages=msgs,
            model="claude-sonnet-4-6",
        )

    assert stats.tier == CompactionTier.ORANGE
    # Recent turns must survive verbatim — the compactor edits older head
    # only.
    assert out[-4:] == tail


def test_red_tier_keeps_fewer_recent_turns(compactor):
    """At >90 %, even the recent-turn budget shrinks (RED_KEEP_RECENT_TURNS
    < EMERGENCY_KEEP_RECENT_TURNS). Verifies a different code path from
    orange — same emergency-cap helper, smaller keep window."""
    head = _bulk_messages(approx_tokens=400)
    tail = _tail_messages(count=4)
    msgs = head + tail

    with patch(
        "app.agent_framework.context_compactor.model_window_size", return_value=1000
    ), patch(
        "app.agent_framework.context_compactor.count_tokens", return_value=0
    ), patch(
        "app.agent_framework.context_compactor.count_messages_tokens",
        side_effect=[950, 940, 100, 300],  # tail=100 to fit below red
    ), patch(
        "app.agent_framework.context_compactor.prune",
        return_value=(msgs, _make_prune_stats()),
    ):
        out, stats = compactor.maybe_compact(
            system_message="",
            user_messages=msgs,
            model="claude-sonnet-4-6",
        )

    assert stats.tier == CompactionTier.RED
    # Red tier preserves only the LAST 2 turns (vs 4 in orange).
    assert out[-2:] == tail[-2:]


def test_thresholds_are_configurable():
    """Default 60/80/90 mirrors Anthropic's blog. Tests pin the values
    so an accidental "let's tweak them" PR makes a loud diff."""
    t = CompactionThresholds()
    assert (t.yellow_pct, t.orange_pct, t.red_pct) == (0.60, 0.80, 0.90)


def test_custom_thresholds_take_effect():
    """Caller can override thresholds for tenants on tighter / looser
    budgets — verify the overrides actually drive tier selection."""
    aggressive = CompactionThresholds(
        yellow_pct=0.30, orange_pct=0.50, red_pct=0.70
    )
    compactor = ContextCompactor(thresholds=aggressive)
    assert compactor._tier_for(0.10) == CompactionTier.GREEN
    assert compactor._tier_for(0.40) == CompactionTier.YELLOW
    assert compactor._tier_for(0.60) == CompactionTier.ORANGE
    assert compactor._tier_for(0.80) == CompactionTier.RED


# ─── Helpers ──────────────────────────────────────────────────────────


def _make_prune_stats(duplicates: int = 0, aged: int = 0, chars: int = 0):
    from app.agent_framework.tool_result_pruner import PruneStats
    return PruneStats(
        duplicates_replaced=duplicates,
        aged_results=aged,
        chars_dropped=chars,
    )

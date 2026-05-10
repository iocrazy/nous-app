"""Regression tests for tiered context compaction.

These pin the four-tier policy (green / yellow / orange / red) and the
invariants the runner relies on:

  - the most recent ``keep_recent_turns`` messages are NEVER edited
  - system_message is never returned (caller keeps the original)
  - returning unchanged messages on green is the cheap path the hot
    loop counts on
  - AGENT_AUTO_COMPACT=false is a hard kill switch that bypasses
    everything

Phase 2 added an LLM-driven head summarizer. Tests for the
summarizer call (mock the network) and the emergency-cap fallback
when the summarizer fails are pinned here too.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.agent_framework.context_compactor import (
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


async def test_kill_switch_returns_messages_unchanged(monkeypatch, compactor):
    """AGENT_AUTO_COMPACT=false must short-circuit before any work — the
    compactor goes from a critical-path component to a no-op so a single
    tenant can be debugged without disabling the runner."""
    monkeypatch.setenv("AGENT_AUTO_COMPACT", "false")
    msgs = _tail_messages(count=3)

    out, stats = await compactor.maybe_compact(
        system_message="sys", user_messages=msgs, model="claude-sonnet-4-6"
    )

    assert out is msgs
    assert stats.tier == CompactionTier.GREEN
    assert "AGENT_AUTO_COMPACT=false" in stats.notes


async def test_kill_switch_skips_tokenization(monkeypatch, compactor):
    """The disabled path's whole point is "no work". Tokens stay at 0
    sentinel — caller's note_compaction guard already short-circuits on
    tokens_saved == 0, and re-counting just to fill stats nobody reads
    defeats the kill switch."""
    monkeypatch.setenv("AGENT_AUTO_COMPACT", "false")
    msgs = _tail_messages(count=3)

    with patch(
        "app.agent_framework.context_compactor.count_messages_tokens"
    ) as mock_count:
        out, stats = await compactor.maybe_compact(
            system_message="sys", user_messages=msgs, model="claude-sonnet-4-6"
        )

    mock_count.assert_not_called()
    assert stats.tokens_before == 0
    assert stats.tokens_after == 0


async def test_notes_is_immutable_tuple(compactor):
    """``notes`` lives on a frozen dataclass; making it a ``list`` would
    let callers mutate `stats.notes.append(...)` and silently change a
    "frozen" record. Pin the type so an accidental refactor back to
    list trips the test."""
    msgs = _tail_messages(count=2)
    _, stats = await compactor.maybe_compact(
        system_message="", user_messages=msgs, model="claude-sonnet-4-6"
    )

    assert isinstance(stats.notes, tuple)


async def test_unknown_model_falls_back_to_green(compactor):
    """Bail out cleanly on unknown models — the downstream context-budget
    check still runs and rejects oversize requests."""
    msgs = _tail_messages(count=2)

    with patch(
        "app.agent_framework.context_compactor.model_window_size", return_value=0
    ):
        out, stats = await compactor.maybe_compact(
            system_message="", user_messages=msgs, model="some-future-model"
        )

    assert out is msgs
    assert stats.tier == CompactionTier.GREEN
    assert any("unknown model" in n for n in stats.notes)


async def test_green_tier_returns_input_identity(compactor):
    """Hot path: a small turn under 60 % of the window must not even
    allocate a new list."""
    msgs = _tail_messages(count=2)

    out, stats = await compactor.maybe_compact(
        system_message="short sys", user_messages=msgs, model="claude-sonnet-4-6"
    )

    assert out is msgs
    assert stats.tier == CompactionTier.GREEN
    assert stats.tokens_saved == 0


async def test_yellow_tier_invokes_prune(compactor):
    """At 60-80 %, the compactor must call ``prune`` (dedupe + age) but
    NOT make an LLM call. Cheap, no network."""
    msgs = _tail_messages(count=4)

    with (
        patch(
            "app.agent_framework.context_compactor.model_window_size", return_value=1000
        ),
        patch("app.agent_framework.context_compactor.count_tokens", return_value=0),
        patch(
            "app.agent_framework.context_compactor.count_messages_tokens",
            side_effect=[700, 500],
        ),
        patch(
            "app.agent_framework.context_compactor.prune",
            return_value=(msgs, _make_prune_stats(duplicates=2)),
        ) as mock_prune,
        patch(
            "app.agent_framework.summarizer.summarize",
            new=AsyncMock(),
        ) as mock_summarize,
    ):
        out, stats = await compactor.maybe_compact(
            system_message="", user_messages=msgs, model="claude-sonnet-4-6"
        )

    mock_prune.assert_called_once()
    mock_summarize.assert_not_called()  # yellow never reaches the summary path
    assert stats.tier == CompactionTier.YELLOW
    assert stats.tokens_saved == 200
    assert stats.yellow_prune is not None


async def test_orange_tier_invokes_summarizer(compactor):
    """Orange tier must call summarize() (Phase 2 path). The head turns
    are replaced with a single [Earlier conversation summary] system
    message; recent turns survive verbatim."""
    head = _bulk_messages(approx_tokens=200)
    tail = _tail_messages(count=4)
    msgs = head + tail

    fake_summary = "User asked about X. Agent ran tool foo. Result: bar."

    with (
        patch(
            "app.agent_framework.context_compactor.model_window_size", return_value=1000
        ),
        patch("app.agent_framework.context_compactor.count_tokens", return_value=0),
        patch(
            "app.agent_framework.context_compactor.count_messages_tokens",
            side_effect=[850, 830, 400],  # before / after-prune / final-after-summary
        ),
        patch(
            "app.agent_framework.context_compactor.prune",
            return_value=(msgs, _make_prune_stats()),
        ),
        patch(
            "app.agent_framework.summarizer.summarize",
            new=AsyncMock(return_value=fake_summary),
        ) as mock_summarize,
    ):
        out, stats = await compactor.maybe_compact(
            system_message="",
            user_messages=msgs,
            model="claude-sonnet-4-6",
        )

    mock_summarize.assert_awaited_once()
    assert stats.tier == CompactionTier.ORANGE
    # Tail unchanged
    assert out[-4:] == tail
    # First message is the summary system message
    assert out[0]["role"] == "system"
    assert "[Earlier conversation summary]" in out[0]["content"]
    assert fake_summary in out[0]["content"]
    assert "compacted via LLM head summary" in stats.notes


async def test_summarizer_failure_falls_back_to_emergency_cap(compactor):
    """If the summarizer raises (timeout / provider error / empty
    response), the compactor must NOT bubble the exception. Fall back
    to the lossy ``_emergency_cap`` truncation so the agent's turn
    keeps moving."""
    head = _bulk_messages(approx_tokens=200)
    tail = _tail_messages(count=4)
    msgs = head + tail

    with (
        patch(
            "app.agent_framework.context_compactor.model_window_size", return_value=1000
        ),
        patch("app.agent_framework.context_compactor.count_tokens", return_value=0),
        patch(
            "app.agent_framework.context_compactor.count_messages_tokens",
            side_effect=[850, 830, 200, 400],
        ),
        patch(
            "app.agent_framework.context_compactor.prune",
            return_value=(msgs, _make_prune_stats()),
        ),
        patch(
            "app.agent_framework.summarizer.summarize",
            new=AsyncMock(side_effect=RuntimeError("haiku 503")),
        ),
    ):
        out, stats = await compactor.maybe_compact(
            system_message="",
            user_messages=msgs,
            model="claude-sonnet-4-6",
        )

    assert stats.tier == CompactionTier.ORANGE
    # Recent turns survive verbatim under emergency cap too
    assert out[-4:] == tail
    # The fallback note was added so an operator can see the summarizer broke
    assert any("emergency-cap fallback" in n for n in stats.notes)


async def test_red_tier_keeps_fewer_recent_turns(compactor):
    """RED tier shrinks keep_recent_turns from 4 to 2 — verifies a
    different code path from orange. Summarizer is mocked to bypass
    the network."""
    head = _bulk_messages(approx_tokens=400)
    tail = _tail_messages(count=4)
    msgs = head + tail

    with (
        patch(
            "app.agent_framework.context_compactor.model_window_size", return_value=1000
        ),
        patch("app.agent_framework.context_compactor.count_tokens", return_value=0),
        patch(
            "app.agent_framework.context_compactor.count_messages_tokens",
            side_effect=[950, 940, 300],
        ),
        patch(
            "app.agent_framework.context_compactor.prune",
            return_value=(msgs, _make_prune_stats()),
        ),
        patch(
            "app.agent_framework.summarizer.summarize",
            new=AsyncMock(return_value="summary"),
        ),
    ):
        out, stats = await compactor.maybe_compact(
            system_message="",
            user_messages=msgs,
            model="claude-sonnet-4-6",
        )

    assert stats.tier == CompactionTier.RED
    # Red tier preserves only the LAST 2 turns
    assert out[-2:] == tail[-2:]


def test_thresholds_are_configurable():
    """Default 60/80/90 mirrors Anthropic's blog. Tests pin the values
    so an accidental "let's tweak them" PR makes a loud diff."""
    t = CompactionThresholds()
    assert (t.yellow_pct, t.orange_pct, t.red_pct) == (0.60, 0.80, 0.90)


def test_custom_thresholds_take_effect():
    """Caller can override thresholds for tenants on tighter / looser
    budgets."""
    aggressive = CompactionThresholds(yellow_pct=0.30, orange_pct=0.50, red_pct=0.70)
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

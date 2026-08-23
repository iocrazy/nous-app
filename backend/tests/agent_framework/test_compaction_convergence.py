"""Compaction has to actually compact — and has to skip itself when free.

Two properties the tiered compactor did not have:

**Zero-LLM rescue.** The pruner runs before summarization and is free. If it
alone pulls the budget back under the orange threshold, the LLM summary is
pure waste. The code returns early, but the only test covering that early
return sat in the YELLOW tier — where `summarize()` is unreachable anyway, so
its `assert_not_called()` could never fail. The paying case is ORANGE-or-RED
in, rescued by the pruner, out with no model call.

**Convergence.** A "summary" longer than the text it replaces makes the
problem worse while charging for the privilege. Nothing checked. A model that
echoes its input back — a real failure mode for small models handed a long
transcript — would grow the context and be recorded as a successful compaction.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.agent_framework.context_compactor import (
    CompactionTier,
    ContextCompactor,
)
from app.agent_framework.tool_result_pruner import PruneStats
from tests.agent_framework.compaction_stubs import token_stub


def _stats(**kw):
    defaults = dict(duplicates_replaced=0, aged_results=0, chars_dropped=0)
    defaults.update(kw)
    return PruneStats(**defaults)


def _msgs(n=8):
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i}"}
        for i in range(n)
    ]


@pytest.fixture
def compactor():
    return ContextCompactor()


# ── the pruner rescue: no LLM call when it isn't needed ──────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    "before, after, tier_in",
    [
        (850, 700, "orange"),  # 85% → prune → 70%, back under 80%
        (960, 750, "red"),  # 96% → prune → 75%
    ],
)
async def test_pruner_alone_rescues_and_the_model_is_never_called(
    compactor, before, after, tier_in
):
    msgs = _msgs()
    with (
        patch(
            "app.agent_framework.context_compactor.model_window_size",
            return_value=1000,
        ),
        patch("app.agent_framework.context_compactor.count_tokens", return_value=0),
        patch(
            "app.agent_framework.context_compactor.count_messages_tokens",
            side_effect=[before, after],
        ),
        patch(
            "app.agent_framework.context_compactor.prune",
            return_value=(msgs, _stats(duplicates_replaced=3)),
        ),
        patch(
            "app.agent_framework.summarizer.summarize", new=AsyncMock()
        ) as mock_summarize,
    ):
        out, stats = await compactor.maybe_compact(
            system_message="", user_messages=msgs, model="claude-sonnet-4-6"
        )

    assert mock_summarize.await_count == 0, (
        f"entered as {tier_in} but the pruner already brought it under "
        "threshold — paying for a summary here is pure waste"
    )
    assert stats.tokens_saved == before - after
    assert out is msgs


@pytest.mark.unit
async def test_pruner_that_does_not_rescue_still_pays_for_the_summary():
    """The negative half. Without it, a compactor that never summarizes at
    all would pass the test above."""
    compactor = ContextCompactor()
    msgs = _msgs()
    with (
        patch(
            "app.agent_framework.context_compactor.model_window_size",
            return_value=1000,
        ),
        patch("app.agent_framework.context_compactor.count_tokens", return_value=0),
        patch(
            "app.agent_framework.context_compactor.count_messages_tokens",
            new=token_stub([850, 840], summary_tokens=50),
        ),
        patch(
            "app.agent_framework.context_compactor.prune",
            return_value=(msgs, _stats()),
        ),
        patch(
            "app.agent_framework.summarizer.summarize",
            new=AsyncMock(return_value="short summary"),
        ) as mock_summarize,
    ):
        await compactor.maybe_compact(
            system_message="", user_messages=msgs, model="claude-sonnet-4-6"
        )

    assert mock_summarize.await_count == 1


# ── convergence: a summary that doesn't shrink is not a summary ──────────


@pytest.mark.unit
async def test_a_summary_longer_than_its_source_is_rejected(compactor):
    """Echoing the input back must not be recorded as a compaction."""
    head = _msgs(6)
    tail = _msgs(4)

    with patch(
        "app.agent_framework.summarizer.summarize",
        new=AsyncMock(return_value="x " * 5000),  # far longer than the head
    ):
        with pytest.raises(RuntimeError, match="did not shrink"):
            await compactor._compact_with_summary(
                messages=head + tail,
                keep_recent_turns=4,
                model="claude-sonnet-4-6",
            )


@pytest.mark.unit
async def test_a_non_shrinking_summary_is_retried_before_giving_up(compactor):
    """One bad draft should not cost the turn — retry, then accept a good one."""
    msgs = _msgs(10)
    attempts = ["x " * 5000, "genuinely short summary"]

    with patch(
        "app.agent_framework.summarizer.summarize",
        new=AsyncMock(side_effect=attempts),
    ) as mock_summarize:
        out = await compactor._compact_with_summary(
            messages=msgs, keep_recent_turns=4, model="claude-sonnet-4-6"
        )

    assert mock_summarize.await_count == 2
    assert "genuinely short summary" in out[0]["content"]


@pytest.mark.unit
async def test_retries_are_bounded(compactor):
    """A model stuck echoing must not be retried forever — the caller has a
    cheap deterministic fallback and should get to it."""
    msgs = _msgs(10)

    with patch(
        "app.agent_framework.summarizer.summarize",
        new=AsyncMock(return_value="x " * 5000),
    ) as mock_summarize:
        with pytest.raises(RuntimeError, match="did not shrink"):
            await compactor._compact_with_summary(
                messages=msgs, keep_recent_turns=4, model="claude-sonnet-4-6"
            )

    assert mock_summarize.await_count == compactor.SUMMARY_ATTEMPTS
    assert compactor.SUMMARY_ATTEMPTS <= 3, "retrying a stuck model is not free"


@pytest.mark.unit
async def test_a_shrinking_summary_is_accepted_on_the_first_try(compactor):
    msgs = _msgs(10)
    with patch(
        "app.agent_framework.summarizer.summarize",
        new=AsyncMock(return_value="tight summary"),
    ) as mock_summarize:
        out = await compactor._compact_with_summary(
            messages=msgs, keep_recent_turns=4, model="claude-sonnet-4-6"
        )
    assert mock_summarize.await_count == 1
    assert out[0]["role"] == "system"
    assert len(out) == 5  # summary + 4 retained turns


@pytest.mark.unit
async def test_rejection_falls_back_to_the_emergency_cap_end_to_end(compactor):
    """The reject must reach the caller's existing fallback, not blow up the
    turn — otherwise a stuck summarizer becomes an outage."""
    msgs = _msgs(12)
    with (
        patch(
            "app.agent_framework.context_compactor.model_window_size",
            return_value=1000,
        ),
        patch("app.agent_framework.context_compactor.count_tokens", return_value=0),
        patch(
            "app.agent_framework.context_compactor.count_messages_tokens",
            # summary_tokens > head_tokens → every draft is rejected
            new=token_stub([900, 890], summary_tokens=999),
        ),
        patch(
            "app.agent_framework.context_compactor.prune",
            return_value=(msgs, _stats()),
        ),
        patch(
            "app.agent_framework.summarizer.summarize",
            new=AsyncMock(return_value="x " * 5000),
        ),
    ):
        out, stats = await compactor.maybe_compact(
            system_message="", user_messages=msgs, model="claude-sonnet-4-6"
        )

    assert out, "the turn must still have messages to send"
    assert any("emergency-cap fallback" in n for n in stats.notes), stats.notes

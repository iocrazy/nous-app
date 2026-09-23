"""``compaction_triggered`` counts every time the compactor actually compacts.

The Prometheus counter used to be fed only by the chat-only ``llm_compactor``;
the harness compactor emitted transcript events and run metadata but no
counter, so once chat moved onto it the ``CompactorOverActive`` alert would
read zero forever. Only a summarizing tier (orange / red) counts — green is a
no-op and yellow is a free prune, matching what the counter meant before.
"""

import contextlib
from unittest.mock import AsyncMock, patch

import pytest

from app.agent_framework.context_compactor import ContextCompactor
from app.agent_framework.tool_result_pruner import PruneStats
from tests.agent_framework.compaction_stubs import token_stub


def _msgs(n=12):
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i}"}
        for i in range(n)
    ]


async def _run(tier_counts):
    msgs = _msgs()
    with contextlib.ExitStack() as st:
        st.enter_context(
            patch(
                "app.agent_framework.context_compactor.resolve_model_window",
                return_value=(1000, True),
            )
        )
        st.enter_context(
            patch("app.agent_framework.context_compactor.count_tokens", return_value=0)
        )
        st.enter_context(
            patch(
                "app.agent_framework.context_compactor.count_messages_tokens",
                new=token_stub(tier_counts, summary_tokens=50),
            )
        )
        st.enter_context(
            patch(
                "app.agent_framework.context_compactor.prune",
                return_value=(
                    msgs,
                    PruneStats(duplicates_replaced=0, aged_results=0, chars_dropped=0),
                ),
            )
        )
        st.enter_context(
            patch(
                "app.agent_framework.summarizer.summarize",
                new=AsyncMock(return_value="short summary"),
            )
        )
        inc = st.enter_context(
            patch("app.agent_framework.context_compactor.inc_metric")
        )
        _, stats = await ContextCompactor().maybe_compact(
            system_message="sys", user_messages=msgs, model="claude-sonnet-4-6"
        )
    return inc, stats


@pytest.mark.unit
async def test_orange_compaction_counts_once():
    inc, stats = await _run([850, 840])
    assert stats.tier.value == "orange"
    inc.assert_called_once_with("compaction_triggered")


@pytest.mark.unit
@pytest.mark.parametrize(
    "tier_counts, tier", [([100], "green"), ([700, 650], "yellow")]
)
async def test_green_and_yellow_do_not_count(tier_counts, tier):
    inc, stats = await _run(tier_counts)
    assert stats.tier.value == tier
    inc.assert_not_called()

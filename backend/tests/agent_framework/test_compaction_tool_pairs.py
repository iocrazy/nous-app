"""The head/tail split must never separate a tool call from its replies.

Summarization DROPS the head and keeps the tail verbatim. If the boundary
lands between an assistant ``tool_calls`` message (head) and its ``role=tool``
replies (tail), the summary swallows the call and the tail keeps replies that
answer nothing — an orphaned ``tool_call_id`` that Anthropic-shaped providers
reject with a hard 400. Before this, the only pair protection in the repo lived
in the chat-only ``llm_compactor`` (retired in the compactor unification);
the harness compactor split at a fixed ``-keep`` offset and no test noticed.

When no safe split exists at all (one exchange spans the whole history), the
compactor must not summarize: it falls back to the emergency cap — which only
truncates bodies and never removes a message — and says so in ``notes``.
"""

import contextlib
from unittest.mock import AsyncMock, patch

import pytest

from app.agent_framework.context_compactor import CompactionTier, ContextCompactor
from app.agent_framework.tool_result_pruner import PruneStats
from tests.agent_framework.compaction_stubs import token_stub


def _call(cid: str) -> dict:
    return {"id": cid, "type": "function", "function": {"name": "Read"}}


def _assistant_calls(*cids: str) -> dict:
    return {"role": "assistant", "content": "", "tool_calls": [_call(c) for c in cids]}


def _reply(cid: str) -> dict:
    return {"role": "tool", "tool_call_id": cid, "content": f"result {cid}"}


def _assert_pairs_intact(messages: list[dict]) -> None:
    call_ids = {
        c["id"]
        for m in messages
        if m.get("role") == "assistant"
        for c in m.get("tool_calls") or []
    }
    reply_ids = {m["tool_call_id"] for m in messages if m.get("role") == "tool"}
    assert (
        reply_ids - call_ids == set()
    ), f"orphaned tool replies: {reply_ids - call_ids}"
    assert (
        call_ids - reply_ids == set()
    ), f"tool calls without replies: {call_ids - reply_ids}"


def _orange_patches(msgs, summarize, warm):
    return (
        patch(
            "app.agent_framework.context_compactor.resolve_model_window",
            return_value=(1000, True),
        ),
        patch("app.agent_framework.context_compactor.count_tokens", return_value=0),
        patch(
            "app.agent_framework.context_compactor.count_messages_tokens",
            new=token_stub([850, 840], summary_tokens=50),
        ),
        patch(
            "app.agent_framework.context_compactor.prune",
            return_value=(
                msgs,
                PruneStats(duplicates_replaced=0, aged_results=0, chars_dropped=0),
            ),
        ),
        patch("app.agent_framework.summarizer.summarize", new=summarize),
        patch("app.agent_framework.summarizer.summarize_warm_prefix", new=warm),
    )


async def _compact(msgs, summarize, warm):
    with contextlib.ExitStack() as st:
        for p in _orange_patches(msgs, summarize, warm):
            st.enter_context(p)
        return await ContextCompactor().maybe_compact(
            system_message="sys",
            user_messages=msgs,
            model="claude-sonnet-4-6",
            adapter=object(),
        )


@pytest.mark.unit
async def test_orange_split_moves_the_boundary_before_a_split_tool_exchange():
    # keep=4 (orange) puts the naive boundary at index 6: head would end on
    # the assistant that issued c2/c3 while their replies sit in the tail.
    msgs = [
        {"role": "user", "content": "q1"},
        _assistant_calls("c1"),
        _reply("c1"),
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"},
        _assistant_calls("c2", "c3"),
        _reply("c2"),
        _reply("c3"),
        {"role": "assistant", "content": "done"},
        {"role": "user", "content": "latest"},
    ]
    summarize = AsyncMock(return_value="short summary")
    warm = AsyncMock(side_effect=RuntimeError("no warm"))

    out, stats = await _compact(msgs, summarize, warm)

    assert stats.tier == CompactionTier.ORANGE
    assert out[0]["content"].startswith("[Earlier conversation summary]")
    _assert_pairs_intact(out)
    # The whole c2/c3 exchange is retained verbatim, not summarized away.
    assert out[1:] == msgs[5:]
    summarized_head = summarize.await_args.args[0]
    assert summarized_head == msgs[:5]


@pytest.mark.unit
async def test_no_safe_split_skips_summary_and_falls_back_to_emergency_cap():
    # One assistant issued every call; its replies run into the retained tail,
    # so any boundary would orphan something. Nothing may be dropped.
    msgs = [_assistant_calls("c1", "c2", "c3", "c4", "c5")] + [
        _reply(c) for c in ("c1", "c2", "c3", "c4", "c5")
    ]
    summarize = AsyncMock(return_value="short summary")
    warm = AsyncMock(return_value="warm summary")

    out, stats = await _compact(msgs, summarize, warm)

    summarize.assert_not_awaited()
    warm.assert_not_awaited()
    assert len(out) == len(msgs)
    _assert_pairs_intact(out)
    assert any("no safe split" in n for n in stats.notes), stats.notes

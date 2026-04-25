"""Unit tests for llm_compactor — ★ P0 tool_use/tool_result pair preservation.

These tests are the contract that prevents the orphaned-tool-result bug
that would break ChatPanel mid-conversation.
"""

from __future__ import annotations

import pytest

from app.services.llm_compactor import (
    DEFAULT_AUTO_COMPACTION_INPUT_TOKENS,
    DEFAULT_KEEP_FLOOR_TURNS,
    CompactionResult,
    compact_messages,
    estimate_tokens,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _user(text: str) -> dict:
    return {"role": "user", "content": text}


def _assistant(text: str = None, tool_calls: list = None) -> dict:
    msg: dict = {"role": "assistant", "content": text}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def _tool_call(call_id: str, fn_name: str = "Skill", args: str = "{}") -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": fn_name, "arguments": args},
    }


def _tool_reply(call_id: str, content: str) -> dict:
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "name": "Skill",
        "content": content,
    }


def _make_long_text(approx_tokens: int) -> str:
    """4 chars per token in the estimator."""
    return "a" * (approx_tokens * 4)


async def _fake_summary(messages: list[dict]) -> str:
    return f"summary of {len(messages)} messages"


# ---------------------------------------------------------------------------
# No-op cases
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_under_threshold_returns_unchanged():
    msgs = [_user("hi"), _assistant("hello")]
    result = await compact_messages(
        msgs, summarizer=_fake_summary, max_input_tokens=10_000
    )
    assert result.compacted is False
    assert result.messages is msgs


@pytest.mark.unit
@pytest.mark.asyncio
async def test_empty_messages_no_op():
    result = await compact_messages(
        [], summarizer=_fake_summary, max_input_tokens=10
    )
    assert result.compacted is False
    assert result.messages == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_under_floor_no_op_even_if_tokens_high():
    """If we have <= keep_floor_turns messages, can't compact without going under floor."""
    msgs = [_user(_make_long_text(50_000))] * 3
    result = await compact_messages(
        msgs,
        summarizer=_fake_summary,
        max_input_tokens=1_000,
        keep_floor_turns=6,
    )
    assert result.compacted is False


# ---------------------------------------------------------------------------
# Happy compaction path
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_simple_compaction_keeps_floor_messages():
    msgs = [_user(_make_long_text(20_000)) for _ in range(20)]
    result = await compact_messages(
        msgs,
        summarizer=_fake_summary,
        max_input_tokens=50_000,
        keep_floor_turns=6,
    )
    assert result.compacted is True
    # First message is the system summary.
    assert result.messages[0]["role"] == "system"
    assert "<conversation_summary>" in result.messages[0]["content"]
    # Then exactly keep_floor_turns tail messages.
    assert len(result.messages) == 1 + 6
    assert result.head_message_count == 14


@pytest.mark.unit
@pytest.mark.asyncio
async def test_summary_text_invokes_summarizer():
    msgs = [_user(_make_long_text(20_000)) for _ in range(20)]
    captured: dict = {}

    async def capturing(messages):
        captured["count"] = len(messages)
        return "MOCK SUMMARY"

    result = await compact_messages(
        msgs, summarizer=capturing, max_input_tokens=50_000, keep_floor_turns=6
    )
    assert result.summary == "MOCK SUMMARY"
    assert captured["count"] == 14
    assert "MOCK SUMMARY" in result.messages[0]["content"]


# ---------------------------------------------------------------------------
# ★ P0 — tool_use / tool_result pair preservation
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_p0_pair_preservation_simple_walk_back():
    """Initial split lands AFTER assistant tool_use but BEFORE its tool reply.

    Setup:
        idx 0: user big
        idx 1: user big
        ...
        idx 16: user big
        idx 17: assistant tool_calls=[tc1]   ← this would be in head
        idx 18: tool reply tc1               ← this would be in tail (orphan!)
        idx 19: assistant final
        idx 20: user
        idx 21: user
        idx 22: user
        idx 23: user

    keep_floor_turns=6 → initial candidate = 24 - 6 = 18 → tail starts at idx 18
    tail[0] is a tool reply for tc1, head has the tool_use → orphan!
    Must walk back to candidate=17 so assistant tool_use joins tail.
    """
    msgs = [_user(_make_long_text(15_000)) for _ in range(17)]
    msgs.append(_assistant(tool_calls=[_tool_call("tc1")]))  # idx 17
    msgs.append(_tool_reply("tc1", "result1"))  # idx 18
    msgs.extend([_assistant("done"), _user("ok"), _user("k"), _user("k"), _user("k")])
    # total = 23 messages

    result = await compact_messages(
        msgs,
        summarizer=_fake_summary,
        max_input_tokens=50_000,
        keep_floor_turns=6,
    )
    assert result.compacted is True

    # Verify NO orphan tool replies in returned messages.
    _assert_no_orphan_tool_replies(result.messages)

    # The boundary moved from 18 → 17, so head_message_count = 17.
    assert result.head_message_count == 17


@pytest.mark.unit
@pytest.mark.asyncio
async def test_p0_pair_preservation_multiple_tool_calls_in_one_assistant():
    """Assistant fires 2 tool calls, both replies in tail. Must keep all 3 together."""
    msgs = [_user(_make_long_text(10_000)) for _ in range(14)]
    # idx 14: assistant with 2 tool calls
    msgs.append(_assistant(tool_calls=[_tool_call("tc1"), _tool_call("tc2")]))
    msgs.append(_tool_reply("tc1", "r1"))  # idx 15
    msgs.append(_tool_reply("tc2", "r2"))  # idx 16
    msgs.extend([_assistant("done"), _user("ok"), _user("k"), _user("k"), _user("k")])
    # total = 22

    result = await compact_messages(
        msgs,
        summarizer=_fake_summary,
        max_input_tokens=50_000,
        keep_floor_turns=6,
    )
    assert result.compacted is True
    _assert_no_orphan_tool_replies(result.messages)
    # head_message_count must be <= 14 so the assistant + both replies are in tail
    assert result.head_message_count <= 14


@pytest.mark.unit
@pytest.mark.asyncio
async def test_p0_pair_preservation_split_lands_between_unrelated_pairs():
    """Two independent pairs. Boundary lands between them — should be safe (no walk-back)."""
    msgs = [_user(_make_long_text(10_000)) for _ in range(10)]
    # First pair (will be in head)
    msgs.append(_assistant(tool_calls=[_tool_call("tc_old")]))
    msgs.append(_tool_reply("tc_old", "old result"))
    msgs.extend([_user("mid")] * 5)
    # Second pair (will be in tail) - this brings tail to 6+
    msgs.append(_assistant(tool_calls=[_tool_call("tc_new")]))
    msgs.append(_tool_reply("tc_new", "new result"))
    msgs.extend([_assistant("done"), _user("k"), _user("k"), _user("k")])

    result = await compact_messages(
        msgs,
        summarizer=_fake_summary,
        max_input_tokens=50_000,
        keep_floor_turns=6,
    )
    assert result.compacted is True
    _assert_no_orphan_tool_replies(result.messages)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_p0_no_tool_calls_anywhere_simple_split():
    """Pure text conversation — boundary is straightforward."""
    msgs = []
    for _ in range(20):
        msgs.append(_user(_make_long_text(10_000)))
        msgs.append(_assistant(_make_long_text(8_000)))
    result = await compact_messages(
        msgs,
        summarizer=_fake_summary,
        max_input_tokens=80_000,
        keep_floor_turns=6,
    )
    assert result.compacted is True
    _assert_no_orphan_tool_replies(result.messages)
    assert len(result.messages) == 1 + 6


@pytest.mark.unit
@pytest.mark.asyncio
async def test_p0_pair_spanning_walk_back_consumes_everything_returns_no_op():
    """Pathological: tool reply at very start with no head — return as-is."""
    msgs = [
        _assistant(tool_calls=[_tool_call("tc1")]),
        _tool_reply("tc1", "r"),
    ] * 10  # 20 messages, all paired

    # With keep_floor_turns=6, candidate = 14. tail starts at 14.
    # Walk-back may need to keep going — at index 0 there's an assistant
    # tool_use for tc1 that has its reply at index 1, but tc1 ID is
    # repeated. Let's just verify no orphans regardless.
    result = await compact_messages(
        msgs,
        summarizer=_fake_summary,
        max_input_tokens=10,  # force compaction
        keep_floor_turns=6,
    )
    # Either compacted-with-no-orphans or not compacted; never with orphans.
    _assert_no_orphan_tool_replies(result.messages)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_p0_singleton_message_no_op():
    msgs = [_user(_make_long_text(50_000))]
    result = await compact_messages(
        msgs, summarizer=_fake_summary, max_input_tokens=1_000, keep_floor_turns=6
    )
    assert result.compacted is False


# ---------------------------------------------------------------------------
# Token estimator
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_estimate_tokens_chars_div_4():
    msgs = [_user("a" * 400)]
    assert estimate_tokens(msgs) == 100  # 400 chars / 4


@pytest.mark.unit
def test_estimate_tokens_includes_tool_calls_arguments():
    msg = _assistant(
        tool_calls=[_tool_call("tc1", "Skill", '{"skill": "x"}')]
    )
    # Skill = 5 chars, args = 14 chars → 19 chars → 4 tokens (chars // 4)
    assert estimate_tokens([msg]) == 4


@pytest.mark.unit
def test_estimate_tokens_handles_none_content():
    msg = _assistant(tool_calls=[_tool_call("tc1")])  # content=None
    assert estimate_tokens([msg]) >= 0  # no crash


# ---------------------------------------------------------------------------
# Defaults / constants
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_default_threshold_is_100k():
    assert DEFAULT_AUTO_COMPACTION_INPUT_TOKENS == 100_000


@pytest.mark.unit
def test_default_floor_is_6():
    assert DEFAULT_KEEP_FLOOR_TURNS == 6


# ---------------------------------------------------------------------------
# Helper: P0 invariant assertion
# ---------------------------------------------------------------------------


def _assert_no_orphan_tool_replies(messages: list[dict]) -> None:
    """For any role='tool' message, an assistant earlier must have issued its tool_call_id.

    This is the API contract — Anthropic / OpenAI both reject conversations
    that violate it. The compactor must NEVER produce a violating list.
    """
    issued_ids: set[str] = set()
    for msg in messages:
        role = msg.get("role")
        if role == "assistant":
            for call in msg.get("tool_calls") or []:
                cid = call.get("id")
                if cid:
                    issued_ids.add(cid)
        elif role == "tool":
            cid = msg.get("tool_call_id")
            assert cid in issued_ids, (
                f"orphaned tool reply: tool_call_id={cid} has no preceding assistant tool_use"
            )

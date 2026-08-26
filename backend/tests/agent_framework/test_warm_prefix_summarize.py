"""Compaction summarization replays the conversation's own warm prefix.

The old path builds a SEPARATE request — its own system prompt, the head
flattened into one user message, routed to a cheap maintenance model. Every
input token is cold: nothing about that request matches anything the provider
has cached for this conversation.

The dsh-derived path (W3-1, user-approved cost shift): the summary request is
the conversation's own request replayed byte-for-byte — same adapter, same
model, same system message, same tools, the shadowed head messages verbatim —
with the compaction instruction appended as ONE final user message. The
provider's prefix cache from the conversation's previous turns then covers
everything except that instruction, so the summary's input tokens are mostly
cache reads. Nothing before the appended instruction may differ, or the cache
key diverges and the whole point is lost — several tests below pin exactly
that byte-identity.
"""

from unittest.mock import AsyncMock

import pytest

from app.agent_framework.summarizer import summarize_warm_prefix

SYSTEM = "# Identity\nI am the Script AI."
TOOLS = [{"type": "function", "function": {"name": "Skill", "parameters": {}}}]
HEAD = [
    {"role": "user", "content": "write scene 1"},
    {"role": "assistant", "content": "INT. OFFICE — DAY ..."},
    {"role": "user", "content": "now scene 2"},
    {"role": "assistant", "content": "EXT. STREET — NIGHT ..."},
]


def _adapter(text="a tight summary", tool_calls=None):
    message = {"role": "assistant", "content": text}
    if tool_calls:
        message["tool_calls"] = tool_calls
    a = AsyncMock()
    a.call = AsyncMock(
        return_value={"choices": [{"message": message, "finish_reason": "stop"}]}
    )
    return a


@pytest.mark.unit
@pytest.mark.asyncio
async def test_replays_the_conversation_prefix_byte_for_byte():
    adapter = _adapter()
    await summarize_warm_prefix(
        adapter=adapter, system_message=SYSTEM, tools=TOOLS, head=HEAD, model="qwen-max"
    )
    composed, messages = adapter.call.await_args.args
    # The prefix IS the conversation: same system text, same tools, same model.
    assert composed.system_message == SYSTEM
    assert composed.tools == TOOLS
    assert composed.model == "qwen-max"
    # Head verbatim, instruction appended as exactly one final user message.
    assert messages[: len(HEAD)] == HEAD
    assert len(messages) == len(HEAD) + 1
    assert messages[-1]["role"] == "user"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_head_messages_are_the_same_objects_not_copies_with_edits():
    """Any rewrite — trimming, re-keying, flattening — changes the bytes the
    provider hashes, and the cache misses silently. Identity is the cheapest
    proof no rewrite happened."""
    adapter = _adapter()
    await summarize_warm_prefix(
        adapter=adapter, system_message=SYSTEM, tools=TOOLS, head=HEAD, model="m"
    )
    _, messages = adapter.call.await_args.args
    for sent, original in zip(messages, HEAD):
        assert sent is original


@pytest.mark.unit
@pytest.mark.asyncio
async def test_uses_the_conversation_adapter_not_the_maintenance_model():
    """The whole cost case rests on same-account, same-model routing — the
    maintenance model has no cache of this conversation to hit."""
    adapter = _adapter()
    await summarize_warm_prefix(
        adapter=adapter, system_message=SYSTEM, tools=TOOLS, head=HEAD, model="m"
    )
    adapter.call.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_returns_only_text_never_reasoning():
    """Qwen3-style <think> blocks are private reasoning — a checkpoint that
    quotes them leaks it into every later turn."""
    adapter = _adapter(text="<think>secret chain</think>the actual summary")
    out = await summarize_warm_prefix(
        adapter=adapter, system_message=SYSTEM, tools=TOOLS, head=HEAD, model="m"
    )
    assert out == "the actual summary"
    assert "secret" not in out


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_tool_calling_reply_is_a_failure_not_a_summary():
    """Tools ride the replayed prefix, so the model MAY try to call one. A
    tool call stored as a checkpoint becomes an orphaned call replayed into
    every later request — reject and let the caller retry/fall back."""
    adapter = _adapter(
        text=None, tool_calls=[{"id": "c1", "function": {"name": "Skill"}}]
    )
    with pytest.raises(RuntimeError, match="tool call"):
        await summarize_warm_prefix(
            adapter=adapter, system_message=SYSTEM, tools=TOOLS, head=HEAD, model="m"
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_empty_text_raises():
    adapter = _adapter(text="   ")
    with pytest.raises(RuntimeError, match="empty"):
        await summarize_warm_prefix(
            adapter=adapter, system_message=SYSTEM, tools=TOOLS, head=HEAD, model="m"
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_trailing_tool_call_pair_is_not_split():
    """If the head boundary lands right after an assistant tool_calls message
    (its tool result sits in the retained tail), replaying the head and then
    appending a USER instruction produces an orphaned tool_use — a hard 400
    on Anthropic-shaped providers. The boundary must retreat so the head ends
    on a complete exchange; the displaced messages join the retained tail."""
    head = HEAD + [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "t1", "function": {"name": "Skill"}}],
        }
    ]
    adapter = _adapter()
    await summarize_warm_prefix(
        adapter=adapter, system_message=SYSTEM, tools=TOOLS, head=head, model="m"
    )
    _, messages = adapter.call.await_args.args
    assert not any(
        m.get("tool_calls") for m in messages[:-1][len(HEAD) :]
    ), "the orphaned tool_call message was replayed"
    assert messages[: len(HEAD)] == HEAD


# ── wiring: the compactor prefers warm, falls back to legacy ─────────────


def _stub_counts(monkeypatch_ctx=None):
    pass


@pytest.mark.unit
@pytest.mark.asyncio
async def test_compactor_uses_warm_prefix_when_it_has_an_adapter():
    from unittest.mock import patch

    from app.agent_framework.context_compactor import ContextCompactor
    from tests.agent_framework.compaction_stubs import token_stub

    adapter = _adapter("warm summary")
    legacy = AsyncMock()
    c = ContextCompactor()
    with (
        patch(
            "app.agent_framework.context_compactor.count_messages_tokens",
            new=token_stub([300], summary_tokens=10),
        ),
        patch("app.agent_framework.summarizer.summarize", legacy),
    ):
        out = await c._compact_with_summary(
            messages=HEAD + [{"role": "user", "content": "latest"}],
            keep_recent_turns=1,
            model="qwen-max",
            system_message=SYSTEM,
            tools=TOOLS,
            adapter=adapter,
        )
    adapter.call.assert_awaited_once()
    legacy.assert_not_awaited()
    assert "warm summary" in out[0]["content"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_warm_failure_falls_back_to_legacy_then_succeeds():
    """The chain is warm → legacy cheap model → (caller's) emergency cap.
    A warm hiccup must not skip straight to lossy truncation."""
    from unittest.mock import patch

    from app.agent_framework.context_compactor import ContextCompactor
    from tests.agent_framework.compaction_stubs import token_stub

    adapter = _adapter(text="")  # warm path raises on empty
    legacy = AsyncMock(return_value="legacy summary")
    c = ContextCompactor()
    with (
        patch(
            "app.agent_framework.context_compactor.count_messages_tokens",
            new=token_stub([300], summary_tokens=10),
        ),
        patch("app.agent_framework.summarizer.summarize", legacy),
    ):
        out = await c._compact_with_summary(
            messages=HEAD + [{"role": "user", "content": "latest"}],
            keep_recent_turns=1,
            model="qwen-max",
            system_message=SYSTEM,
            tools=TOOLS,
            adapter=adapter,
        )
    legacy.assert_awaited()
    assert "legacy summary" in out[0]["content"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_adapter_means_legacy_path_unchanged():
    from unittest.mock import patch

    from app.agent_framework.context_compactor import ContextCompactor
    from tests.agent_framework.compaction_stubs import token_stub

    legacy = AsyncMock(return_value="legacy summary")
    c = ContextCompactor()
    with (
        patch(
            "app.agent_framework.context_compactor.count_messages_tokens",
            new=token_stub([300], summary_tokens=10),
        ),
        patch("app.agent_framework.summarizer.summarize", legacy),
    ):
        out = await c._compact_with_summary(
            messages=HEAD + [{"role": "user", "content": "latest"}],
            keep_recent_turns=1,
            model="qwen-max",
        )
    legacy.assert_awaited()
    assert "legacy summary" in out[0]["content"]


@pytest.mark.unit
def test_runner_threads_its_adapter_into_the_compactor():
    """helper 存在 ≠ helper 被调：the runner's preflight must hand the
    compactor its adapter + the composed tools, or the warm path is dead code
    on every real route."""
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[2]
        / "app"
        / "services"
        / "ai"
        / "runner"
        / "agent_runner.py"
    ).read_text()
    start = src.index("maybe_compact(")
    # Scan to the call's closing paren, not a fixed window — a comment added
    # to the call site must not be able to push the kwargs out of view.
    call = src[start : src.index("\n        )", start)]
    assert "adapter=self.adapter" in call, call
    assert "tools=composed.tools" in call, call

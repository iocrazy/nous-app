"""Wave H (B): adapter streaming + AgentRunner.stream_turn."""
from __future__ import annotations

from typing import Any

import pytest

from app.schemas.ai_library import ComposedSystemPrompt
from app.services.agent_runner import AgentRunner
from app.services.ai_adapters.base import StreamChunk
from uuid import UUID


def _composed(model="qwen-max"):
    return ComposedSystemPrompt(
        agent_id=UUID(int=0),
        agent_slug="test",
        model=model,
        temperature=0.0,
        max_tokens=512,
        system_message="sys",
        tools=[],
        skill_manifest=[],
        cache_fingerprint="fp",
    )


# ─── stream_turn fallback (no adapter.stream) ─────────────────────────


class _BufferedOnlyAdapter:
    """Adapter that doesn't expose stream() — exercises fallback path."""

    async def call(self, composed, messages):
        return {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "hello world"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2},
        }


@pytest.mark.asyncio
async def test_stream_turn_falls_back_to_buffered():
    runner = AgentRunner(adapter=_BufferedOnlyAdapter(), skill_tool=None)
    chunks = []
    async for chunk in runner.stream_turn(_composed(), [{"role": "user", "content": "hi"}]):
        chunks.append(chunk)
    # One synthetic chunk with full content + finish_reason
    assert len(chunks) == 1
    assert chunks[0].delta_text == "hello world"
    assert chunks[0].finish_reason == "stop"


# ─── streaming adapter (happy path) ──────────────────────────────────


class _StreamingAdapter:
    """Adapter that emits 3 deltas + final finish chunk."""

    async def call(self, composed, messages):
        # Buffered fallback (shouldn't be exercised here)
        return {"choices": [{"message": {"content": "fallback"}}]}

    async def stream(self, composed, messages):
        yield StreamChunk(delta_text="hello ")
        yield StreamChunk(delta_text="world")
        yield StreamChunk(
            delta_text="!",
            finish_reason="stop",
            usage={"prompt_tokens": 10, "completion_tokens": 3},
        )


@pytest.mark.asyncio
async def test_streaming_adapter_yields_all_chunks():
    runner = AgentRunner(adapter=_StreamingAdapter(), skill_tool=None)
    pieces = []
    finish = None
    async for chunk in runner.stream_turn(_composed(), [{"role": "user", "content": "hi"}]):
        if chunk.delta_text:
            pieces.append(chunk.delta_text)
        if chunk.finish_reason:
            finish = chunk.finish_reason
    assert "".join(pieces) == "hello world!"
    assert finish == "stop"


# ─── AbortController mid-stream ──────────────────────────────────────


class _AbortAfterTwoAdapter:
    """3-chunk stream — caller aborts after chunk 2."""

    async def call(self, composed, messages):
        return {"choices": [{"message": {"content": ""}}]}

    async def stream(self, composed, messages):
        yield StreamChunk(delta_text="a")
        yield StreamChunk(delta_text="b")
        yield StreamChunk(delta_text="c", finish_reason="stop")


@pytest.mark.asyncio
async def test_abort_mid_stream_raises_run_aborted():
    from app.agent_framework import AbortController, RunAborted

    runner = AgentRunner(adapter=_AbortAfterTwoAdapter(), skill_tool=None)
    abort = AbortController()
    received = []

    with pytest.raises(RunAborted):
        async for chunk in runner.stream_turn(
            _composed(), [{"role": "user", "content": "hi"}], abort=abort
        ):
            received.append(chunk.delta_text)
            if len(received) == 2:
                abort.fire(reason="user cancelled")
    assert received == ["a", "b"]


# ─── Recorder records usage on final chunk ────────────────────────────


class _FakeRecorder:
    def __init__(self):
        self.usage_calls = []

    def record_usage(self, *, prompt_tokens, completion_tokens):
        self.usage_calls.append((prompt_tokens, completion_tokens))

    async def heartbeat(self):
        pass

    async def check_cancelled(self):
        return False


@pytest.mark.asyncio
async def test_streaming_records_usage_from_final_chunk():
    runner = AgentRunner(adapter=_StreamingAdapter(), skill_tool=None)
    recorder = _FakeRecorder()
    async for _ in runner.stream_turn(
        _composed(), [{"role": "user", "content": "hi"}], recorder=recorder
    ):
        pass
    assert recorder.usage_calls == [(10, 3)]


# ─── StreamChunk shape ───────────────────────────────────────────────


@pytest.mark.unit
def test_stream_chunk_default_fields():
    c = StreamChunk()
    assert c.delta_text is None
    assert c.tool_call_delta is None
    assert c.finish_reason is None
    assert c.usage is None


@pytest.mark.unit
def test_stream_chunk_immutable():
    c = StreamChunk(delta_text="x")
    with pytest.raises((AttributeError, Exception)):
        c.delta_text = "y"  # type: ignore[misc]


# ─── Phase P (P1): tool_calls in stream ──────────────────────────────


from app.services.agent_runner import _merge_tool_call_deltas


@pytest.mark.unit
def test_merge_tool_call_deltas_basic():
    buf: dict = {}
    _merge_tool_call_deltas(buf, [
        {"index": 0, "id": "call_1", "function": {"name": "Skill", "arguments": ""}},
    ])
    _merge_tool_call_deltas(buf, [
        {"index": 0, "function": {"arguments": '{"sk'}},
    ])
    _merge_tool_call_deltas(buf, [
        {"index": 0, "function": {"arguments": 'ill":"x"}'}},
    ])
    assert buf[0]["id"] == "call_1"
    assert buf[0]["function"]["name"] == "Skill"
    assert buf[0]["function"]["arguments"] == '{"skill":"x"}'


@pytest.mark.unit
def test_merge_handles_multiple_calls_by_index():
    buf: dict = {}
    _merge_tool_call_deltas(buf, [
        {"index": 0, "id": "a", "function": {"name": "Skill"}},
        {"index": 1, "id": "b", "function": {"name": "Delegate"}},
    ])
    assert buf[0]["id"] == "a"
    assert buf[1]["id"] == "b"


@pytest.mark.unit
def test_merge_empty_input_no_change():
    buf: dict = {}
    _merge_tool_call_deltas(buf, [])
    assert buf == {}


class _StreamingAdapterWithToolCall:
    """Emits tool_call deltas in iter 1; final text in iter 2."""

    def __init__(self):
        self.iter = 0

    async def call(self, composed, messages):
        return {"choices": [{"message": {"content": "fallback"}}]}

    async def stream(self, composed, messages):
        self.iter += 1
        if self.iter == 1:
            yield StreamChunk(tool_call_delta={"tool_calls": [
                {"index": 0, "id": "c1", "function": {"name": "Skill", "arguments": ""}},
            ]})
            yield StreamChunk(tool_call_delta={"tool_calls": [
                {"index": 0, "function": {"arguments": '{"skill":"foo"}'}},
            ]})
            yield StreamChunk(
                finish_reason="tool_calls",
                usage={"prompt_tokens": 10, "completion_tokens": 3},
            )
        else:
            yield StreamChunk(delta_text="Done with foo result")
            yield StreamChunk(
                finish_reason="stop",
                usage={"prompt_tokens": 30, "completion_tokens": 5},
            )


class _StubSkillTool:
    async def execute(self, args):
        return {"skill": args.get("skill"), "prompt": "tool ran"}


@pytest.mark.asyncio
async def test_stream_turn_executes_tool_calls_and_continues():
    """End-to-end P1: tool_call deltas stitched + executed; second
    iteration emits final text."""
    runner = AgentRunner(
        adapter=_StreamingAdapterWithToolCall(),
        skill_tool=_StubSkillTool(),
    )
    pieces = []
    finish = None
    async for chunk in runner.stream_turn(
        _composed(), [{"role": "user", "content": "do foo"}]
    ):
        if chunk.delta_text:
            pieces.append(chunk.delta_text)
        if chunk.finish_reason:
            finish = chunk.finish_reason
    full = "".join(pieces)
    assert "Running Skill" in full or "→ Running" in full
    assert "Done with foo" in full
    assert finish == "stop"

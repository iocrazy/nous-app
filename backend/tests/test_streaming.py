"""Wave H (B): adapter streaming + AgentRunner.stream_turn."""

from __future__ import annotations

from uuid import UUID

import pytest

from app.schemas.ai_library import ComposedSystemPrompt
from app.services.ai.adapters.base import StreamChunk
from app.services.ai.runner.agent_runner import AgentRunner


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
    async for chunk in runner.stream_turn(
        _composed(), [{"role": "user", "content": "hi"}]
    ):
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
    async for chunk in runner.stream_turn(
        _composed(), [{"role": "user", "content": "hi"}]
    ):
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

    def record_usage(self, *, prompt_tokens, completion_tokens, cached_input_tokens=0):
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


# ─── #2: stream pre-flight (compaction + budget) + heartbeat ──────────


@pytest.mark.asyncio
async def test_stream_turn_rejects_over_budget_before_calling_adapter(monkeypatch):
    """The streaming path must run the same context-budget guard as run_turn:
    an over-budget turn yields ONE clean terminal chunk and never reaches the
    provider (which would otherwise return a cryptic error)."""
    import app.agent_framework as af

    def _raise(**_kw):
        raise af.ContextWindowError("context window exceeded")

    monkeypatch.setattr(af, "check_context_budget", _raise)

    class _NeverAdapter:
        async def call(self, c, m):
            raise AssertionError("adapter.call must not run when over budget")

        async def stream(self, c, m):
            raise AssertionError("adapter.stream must not run when over budget")
            yield  # pragma: no cover — makes this an async generator

    runner = AgentRunner(adapter=_NeverAdapter(), skill_tool=None)
    chunks = []
    async for chunk in runner.stream_turn(
        _composed(), [{"role": "user", "content": "hi"}]
    ):
        chunks.append(chunk)
    assert len(chunks) == 1
    assert chunks[0].finish_reason == "length"
    assert "too long" in (chunks[0].delta_text or "")
    assert chunks[0].usage.get("error_code") == "context_budget_exceeded"


@pytest.mark.asyncio
async def test_stream_turn_heartbeats_each_iteration():
    """Long multi-iteration streams must refresh the heartbeat between
    iterations, else the sweeper can wrongly reap a healthy run as lost."""

    class _CountingRecorder:
        def __init__(self):
            self.heartbeats = 0

        def record_usage(self, **k): ...
        def record_skill(self, *a): ...

        async def heartbeat(self):
            self.heartbeats += 1

        async def check_cancelled(self):
            return False

    rec = _CountingRecorder()
    runner = AgentRunner(
        adapter=_StreamingAdapterWithToolCall(), skill_tool=_StubSkillTool()
    )
    async for _ in runner.stream_turn(
        _composed(),
        [{"role": "user", "content": "do foo"}],
        recorder=rec,
        auto_recorder=False,
    ):
        pass
    # Two iterations (tool-call round + final text) → at least two heartbeats.
    assert rec.heartbeats >= 2


@pytest.mark.asyncio
async def test_stream_turn_cooperative_cancel_stops_before_adapter():
    """A DB-side (cooperative) cancel between iterations stops the stream."""

    class _CancelRecorder:
        def record_usage(self, **k): ...
        def record_skill(self, *a): ...

        async def heartbeat(self): ...

        async def check_cancelled(self):
            return True  # cancelled immediately

    class _NeverStreamAdapter:
        async def call(self, c, m):
            return {"choices": [{"message": {"content": ""}}]}

        async def stream(self, c, m):
            raise AssertionError("must stop before streaming on cancel")
            yield  # pragma: no cover

    runner = AgentRunner(adapter=_NeverStreamAdapter(), skill_tool=None)
    chunks = []
    async for chunk in runner.stream_turn(
        _composed(),
        [{"role": "user", "content": "hi"}],
        recorder=_CancelRecorder(),
        auto_recorder=False,
    ):
        chunks.append(chunk)
    assert chunks == []


# ─── #5: streaming path runs Pre/Post hooks (was bypassed entirely) ───


@pytest.mark.asyncio
async def test_stream_runs_pre_hook_abort_blocks_tool():
    """The streaming path must run PreToolUse hooks. An abort blocks the tool
    (skill never executes) and stops the stream with a [blocked] note."""
    from app.services.infra.hooks import HookContext, HookRegistry, HookResult

    async def gate(ctx: HookContext) -> HookResult:
        return HookResult(decision="abort", abort_reason="blocked by gate")

    reg = HookRegistry()
    reg.register_pre(gate, name="gate")

    ran = {"n": 0}

    class _Skill:
        async def execute(self, args):
            ran["n"] += 1
            return {"prompt": "ran"}

    runner = AgentRunner(
        adapter=_StreamingAdapterWithToolCall(), skill_tool=_Skill(), hooks=reg
    )
    chunks = []
    async for c in runner.stream_turn(
        _composed(), [{"role": "user", "content": "do foo"}]
    ):
        chunks.append(c)
    assert ran["n"] == 0  # tool blocked before execution
    assert any("blocked" in (c.delta_text or "") for c in chunks)


@pytest.mark.asyncio
async def test_stream_runs_pre_hook_modify_replaces_args():
    """PreToolUse modify must rewrite the tool args on the streaming path too."""
    from app.services.infra.hooks import HookContext, HookRegistry, HookResult

    captured: dict = {}

    class _Skill:
        async def execute(self, args):
            captured.update(args)
            return {"prompt": "ran"}

    async def sanitizer(ctx: HookContext) -> HookResult:
        return HookResult(decision="modify", modified_args={"skill": "safe"})

    reg = HookRegistry()
    reg.register_pre(sanitizer, name="sanitizer")
    runner = AgentRunner(
        adapter=_StreamingAdapterWithToolCall(), skill_tool=_Skill(), hooks=reg
    )
    async for _ in runner.stream_turn(
        _composed(), [{"role": "user", "content": "do foo"}]
    ):
        pass
    assert captured == {"skill": "safe"}


@pytest.mark.asyncio
async def test_stream_runs_post_hooks_and_side_effects():
    """PostToolUse must fire on the streaming path (CostAuditor / MemoryHarvester
    side-effects), receiving the tool result."""
    from unittest.mock import MagicMock

    from app.services.infra.hooks import HookContext, HookRegistry, HookResult

    seen: list[dict] = []
    fire = MagicMock()

    async def auditor(ctx: HookContext, result: dict) -> HookResult:
        seen.append(result)
        return HookResult(decision="continue", side_effect=fire)

    reg = HookRegistry()
    reg.register_post(auditor, name="auditor")
    runner = AgentRunner(
        adapter=_StreamingAdapterWithToolCall(),
        skill_tool=_StubSkillTool(),
        hooks=reg,
    )
    async for _ in runner.stream_turn(
        _composed(), [{"role": "user", "content": "do foo"}]
    ):
        pass
    assert len(seen) == 1
    assert seen[0]["skill"] == "foo"
    fire.assert_called_once_with()


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


from app.services.ai.runner.agent_runner import _merge_tool_call_deltas


@pytest.mark.unit
def test_merge_tool_call_deltas_basic():
    buf: dict = {}
    _merge_tool_call_deltas(
        buf,
        [
            {
                "index": 0,
                "id": "call_1",
                "function": {"name": "Skill", "arguments": ""},
            },
        ],
    )
    _merge_tool_call_deltas(
        buf,
        [
            {"index": 0, "function": {"arguments": '{"sk'}},
        ],
    )
    _merge_tool_call_deltas(
        buf,
        [
            {"index": 0, "function": {"arguments": 'ill":"x"}'}},
        ],
    )
    assert buf[0]["id"] == "call_1"
    assert buf[0]["function"]["name"] == "Skill"
    assert buf[0]["function"]["arguments"] == '{"skill":"x"}'


@pytest.mark.unit
def test_merge_handles_multiple_calls_by_index():
    buf: dict = {}
    _merge_tool_call_deltas(
        buf,
        [
            {"index": 0, "id": "a", "function": {"name": "Skill"}},
            {"index": 1, "id": "b", "function": {"name": "Delegate"}},
        ],
    )
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
            yield StreamChunk(
                tool_call_delta={
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "c1",
                            "function": {"name": "Skill", "arguments": ""},
                        },
                    ]
                }
            )
            yield StreamChunk(
                tool_call_delta={
                    "tool_calls": [
                        {"index": 0, "function": {"arguments": '{"skill":"foo"}'}},
                    ]
                }
            )
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


# ─── R4: stream_turn auto_recorder ────────────────────────────────────


@pytest.mark.asyncio
async def test_stream_turn_auto_recorder_skipped_without_user_id():
    """When user_id missing, auto_recorder is silently no-op (no crash)."""
    runner = AgentRunner(adapter=_BufferedOnlyAdapter(), skill_tool=None)
    chunks = []
    async for chunk in runner.stream_turn(
        _composed(),
        [{"role": "user", "content": "hi"}],
        auto_recorder=True,  # default; should still work without user_id
    ):
        chunks.append(chunk)
    assert len(chunks) == 1


@pytest.mark.asyncio
async def test_stream_turn_auto_recorder_disabled_explicitly():
    """auto_recorder=False with user_id provided also bypasses."""
    runner = AgentRunner(adapter=_BufferedOnlyAdapter(), skill_tool=None)
    chunks = []
    async for chunk in runner.stream_turn(
        _composed(),
        [{"role": "user", "content": "hi"}],
        auto_recorder=False,
        user_id=UUID(int=1),
    ):
        chunks.append(chunk)
    assert len(chunks) == 1


@pytest.mark.asyncio
async def test_stream_turn_caller_recorder_overrides_auto(monkeypatch):
    """If caller supplies recorder explicitly, auto path is not entered
    (verified by checking we don't try to construct a new RunRecorder)."""
    sentinel_calls = []

    class _SentinelRecorder:
        async def __aenter__(self):
            sentinel_calls.append("enter")
            return self

        async def __aexit__(self, *a):
            sentinel_calls.append("exit")
            return False

        def record_usage(self, **k): ...
        def record_skill(self, *a): ...

    runner = AgentRunner(adapter=_BufferedOnlyAdapter(), skill_tool=None)
    rec = _SentinelRecorder()
    chunks = []
    async for chunk in runner.stream_turn(
        _composed(),
        [{"role": "user", "content": "hi"}],
        recorder=rec,  # explicit; auto path should not engage
        user_id=UUID(int=1),
    ):
        chunks.append(chunk)
    # Caller's own recorder context-manage is caller's job; we should
    # NOT have entered/exited it for them
    assert sentinel_calls == []


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


# ─── G3: stream_turn routes MCP tool calls ──────────────────────────


class _StreamingAdapterWithMCPCall:
    """Emits a 'notion.create_page' MCP tool_call in iter 1, final text in iter 2."""

    def __init__(self):
        self.iter = 0

    async def call(self, composed, messages):
        return {"choices": [{"message": {"content": "fallback"}}]}

    async def stream(self, composed, messages):
        self.iter += 1
        if self.iter == 1:
            yield StreamChunk(
                tool_call_delta={
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "c1",
                            "function": {"name": "notion.create_page", "arguments": ""},
                        },
                    ]
                }
            )
            yield StreamChunk(
                tool_call_delta={
                    "tool_calls": [
                        {
                            "index": 0,
                            "function": {
                                "arguments": '{"title":"my doc"}',
                            },
                        },
                    ]
                }
            )
            yield StreamChunk(
                finish_reason="tool_calls",
                usage={"prompt_tokens": 10, "completion_tokens": 3},
            )
        else:
            yield StreamChunk(delta_text="Page is created.")
            yield StreamChunk(
                finish_reason="stop",
                usage={"prompt_tokens": 30, "completion_tokens": 5},
            )


@pytest.mark.asyncio
async def test_stream_turn_dispatches_mcp_tool_call():
    """G3: stream_turn discovers MCP tools + routes prefixed tool_calls
    to mcp_registry.call. End-to-end mirrors run_turn behavior."""
    from unittest.mock import AsyncMock, MagicMock

    from app.agent_framework.mcp_outbound_registry import QualifiedTool

    qualified = [
        QualifiedTool(
            qualified_name="notion.create_page",
            server_name="notion",
            raw_name="create_page",
            description="Create page",
            input_schema={},
        )
    ]
    mcp_reg = AsyncMock()
    mcp_reg.all_tools = AsyncMock(return_value=qualified)
    mcp_reg.server_names = MagicMock(return_value=["notion"])
    mcp_reg.call = AsyncMock(
        return_value={
            "content": [{"type": "text", "text": "ok"}],
            "isError": False,
        }
    )

    runner = AgentRunner(
        adapter=_StreamingAdapterWithMCPCall(),
        skill_tool=None,
        mcp_registry=mcp_reg,
    )
    pieces = []
    async for chunk in runner.stream_turn(
        _composed(), [{"role": "user", "content": "make a page"}]
    ):
        if chunk.delta_text:
            pieces.append(chunk.delta_text)

    full = "".join(pieces)
    # Synthetic UI hint surfaced
    assert "Running notion.create_page" in full
    # Final text from iter 2 made it through
    assert "Page is created." in full
    # MCP call actually dispatched with parsed args
    mcp_reg.call.assert_awaited_once_with("notion.create_page", {"title": "my doc"})


@pytest.mark.asyncio
async def test_stream_turn_mcp_transport_error_does_not_crash():
    from unittest.mock import AsyncMock, MagicMock

    from app.agent_framework.mcp_client import MCPClientError
    from app.agent_framework.mcp_outbound_registry import QualifiedTool

    qualified = [
        QualifiedTool(
            qualified_name="srv.broken",
            server_name="srv",
            raw_name="broken",
            description="",
            input_schema={},
        )
    ]
    mcp_reg = AsyncMock()
    mcp_reg.all_tools = AsyncMock(return_value=qualified)
    mcp_reg.server_names = MagicMock(return_value=["srv"])
    mcp_reg.call = AsyncMock(side_effect=MCPClientError("boom"))

    class _Adapter:
        def __init__(self):
            self.iter = 0

        async def call(self, c, m):
            return {"choices": [{"message": {"content": "fb"}}]}

        async def stream(self, c, m):
            self.iter += 1
            if self.iter == 1:
                yield StreamChunk(
                    tool_call_delta={
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "c1",
                                "function": {"name": "srv.broken", "arguments": "{}"},
                            },
                        ]
                    }
                )
                yield StreamChunk(
                    finish_reason="tool_calls",
                    usage={"prompt_tokens": 1, "completion_tokens": 1},
                )
            else:
                yield StreamChunk(delta_text="acknowledged")
                yield StreamChunk(finish_reason="stop")

    runner = AgentRunner(adapter=_Adapter(), skill_tool=None, mcp_registry=mcp_reg)
    pieces = []
    finish = None
    async for chunk in runner.stream_turn(
        _composed(), [{"role": "user", "content": "go"}]
    ):
        if chunk.delta_text:
            pieces.append(chunk.delta_text)
        if chunk.finish_reason:
            finish = chunk.finish_reason
    # Did NOT crash; final text flowed through
    assert "acknowledged" in "".join(pieces)
    assert finish == "stop"

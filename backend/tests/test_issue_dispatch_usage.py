"""A1 (needs_input first-class, Task 5): issue-dispatch turns must record
real token/cost telemetry on their agent_runs row.

Fix 1 (kept, real but NOT the prod cause): AgentRunner._stream_turn_inner
(agent_runner.py) has two buffered fallback branches — the adapter exposes
no ``.stream()`` method at all, or ``.stream()`` raises
``StreamingNotSupported`` mid-loop — that used to hand the provider's usage
dict straight to the caller via ``StreamChunk(usage=...)`` without ever
calling ``recorder.record_usage(...)``. This is a genuine bug (e.g.
``ClaudeAdapter`` has no ``.stream()`` method), but it is NOT what actually
zeroed out the two ``issue_dispatch_auto`` runs in prod: both used
``doubao-seed-2-0-lite-260428``, which routes to ``DoubaoAdapter`` — a full
``OpenAICompatibleAdapter`` subclass with a working ``.stream()`` — so
neither fallback branch was ever taken for them, and ``StreamingNotSupported``
is raised nowhere in the codebase (dead branch today). This fix stays
because it's still a correctness gap for any adapter that genuinely can't
stream (Claude), and its tests keep passing.

Fix 2 (the actual prod root cause): OpenAICompatibleAdapter.stream()
(openai_compat.py) parses the SSE tail assuming usage always arrives
bundled on the SAME chunk as ``finish_reason``. Per the OpenAI streaming
spec, a provider with ``stream_options.include_usage=true`` — confirmed:
Volcengine/Doubao — instead sends the terminal usage as its OWN trailing
chunk with an EMPTY ``choices`` array, arriving AFTER the chunk carrying
``finish_reason``. AgentRunner's stream consumer (agent_runner.py,
``async for chunk in stream_method(...)``) stops iterating the instant it
sees a chunk with ``finish_reason`` set (breaks out to run tool calls /
finish the turn), so whatever usage the adapter attached to THAT chunk is
final — and for this two-chunk-tail shape it was always ``None``, even
though the model produced a real, content-bearing reply. See
``tests/test_ai_adapters/test_openai_compat.py`` for the direct SSE-parser
regression test; the test below reproduces the full path (real adapter →
AgentRunner.stream_turn → RunRecorder → persisted agent_runs row) with the
exact Doubao SSE shape.

Fix: ``stream()`` now defers yielding the finish-bearing ``StreamChunk``
until a subsequent usage-only line has had a chance to fill it in (or
``[DONE]`` confirms none is coming) — no change to AgentRunner's
break-on-finish_reason consumption, which matches the existing StreamChunk
contract ("on the FINAL chunk, usage SHOULD be populated").
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest

from app.schemas.ai_library import ComposedSystemPrompt
from app.services.ai.runner.agent_runner import AgentRunner
from app.services.ai.runner.run_recorder import RunRecorder


def _composed(model: str = "claude-opus-4-5") -> ComposedSystemPrompt:
    return ComposedSystemPrompt(
        agent_id=UUID("00000000-0000-0000-0000-000000000001"),
        agent_slug="issue-agent",
        model=model,
        temperature=0.7,
        max_tokens=1024,
        system_message="SYSTEM",
        tools=[],
        skill_manifest=[],
        cache_fingerprint="fp",
    )


class _NoStreamAdapter:
    """Mimics ClaudeAdapter's shape: only ``.call()``, no ``.stream()`` —
    the provider shape that trips stream_turn's silent-usage-drop branch."""

    def __init__(self, usage: dict) -> None:
        self._usage = usage
        self.call_count = 0

    async def call(self, composed, messages):
        self.call_count += 1
        return {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "the issue is done"},
                    "finish_reason": "stop",
                }
            ],
            "usage": self._usage,
        }


# ─── Minimal fake AsyncSession/RunRecorder DB harness ──────────────────
# Same sniff-the-compiled-SQL technique as test_run_recorder.py, trimmed to
# only the statements this scenario's RunRecorder lifecycle touches.


class _FakeResult:
    def __init__(self, *, first_row=None, mapping=None) -> None:
        self._first_row = first_row
        self._mapping = mapping

    def first(self):
        return self._first_row

    def mappings(self):
        outer = self

        class _M:
            def first(self):
                return outer._mapping

        return _M()


class _FakeSession:
    def __init__(self, table: "_FakeTable") -> None:
        self._table = table

    async def execute(self, stmt, params=None):
        sql = str(stmt).lower()
        bind_params = dict(params) if params else dict(stmt.compile().params)
        is_insert = sql.lstrip().startswith("insert")
        is_update = sql.lstrip().startswith("update")
        is_select = sql.lstrip().startswith("select")

        if is_insert and "agent_runs" in sql:
            self._table.insert_calls.append(dict(bind_params))
            return _FakeResult(first_row=(self._table.insert_id,))
        if is_select and "ai_agents" in sql:
            return _FakeResult(
                mapping={"paused_reason": None, "max_concurrent_runs": None}
            )
        if is_select and "ai_model_prices" in sql:
            return _FakeResult(mapping=self._table.price_row)
        if is_select and "cancel_requested" in sql:
            return _FakeResult(first_row=(False,))
        if is_update and "agent_runs" in sql:
            self._table.update_calls.append(dict(bind_params))
            return _FakeResult()
        return _FakeResult()


class _FakeTable:
    def __init__(self, *, price_row: dict | None = None) -> None:
        self.price_row = price_row
        self.insert_id = 900000000000001
        self.insert_calls: list[dict] = []
        self.update_calls: list[dict] = []

    def scopes(self):
        @asynccontextmanager
        async def _scope():
            yield _FakeSession(self)

        return _scope, _scope


def _patched(table: _FakeTable):
    read_scope, write_scope = table.scopes()
    return (
        patch("app.db.session.read_scope", read_scope),
        patch("app.db.session.write_scope", write_scope),
    )


@pytest.mark.asyncio
async def test_issue_dispatch_turn_records_tokens_and_cost_when_adapter_has_no_stream():
    """The issue-dispatch trigger, run through the real stream_turn + a
    non-streaming-capable adapter, must end up with non-zero/non-null
    tokens and cost_cents on the finished agent_runs row."""
    table = _FakeTable(
        price_row={
            "prompt_cents_per_1k": 1.0,
            "completion_cents_per_1k": 3.0,
            "cached_input_cents_per_1k": None,
            "effective_at": None,
        }
    )
    p_read, p_write = _patched(table)

    adapter = _NoStreamAdapter(
        {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
    )
    composed = _composed()
    runner = AgentRunner(adapter=adapter, skill_tool=None)

    with p_read, p_write:
        async with RunRecorder(
            agent_id=composed.agent_id,
            user_id=uuid4(),
            trigger="issue_dispatch_auto",
            model=composed.model,
            provider="claude",
        ) as recorder:
            async for _ in runner.stream_turn(
                composed,
                [{"role": "user", "content": "Task: do the thing"}],
                recorder=recorder,
                auto_recorder=False,
            ):
                pass

    assert adapter.call_count == 1
    # RunRecorder itself must have accumulated the usage during the turn —
    # not just the DB row after the fact.
    assert recorder.prompt_tokens == 100
    assert recorder.completion_tokens == 50

    assert len(table.update_calls) == 1
    finish = table.update_calls[0]
    assert finish["prompt_tokens"] == 100
    assert finish["completion_tokens"] == 50
    assert finish["prompt_tokens"] + finish["completion_tokens"] == 150
    assert finish.get("cost_cents") is not None
    assert finish["cost_cents"] > 0


@pytest.mark.asyncio
async def test_issue_dispatch_turn_records_usage_when_stream_raises_not_supported():
    """Second fallback branch: .stream() exists but raises
    StreamingNotSupported mid-loop (some adapters probe support lazily).
    Same silent-drop bug, same fix."""
    from app.services.ai.adapters.base import StreamingNotSupported

    class _RaisingStreamAdapter:
        def __init__(self, usage: dict) -> None:
            self._usage = usage
            self.call_count = 0

        async def call(self, composed, messages):
            self.call_count += 1
            return {
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "done"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": self._usage,
            }

        async def stream(self, composed, messages):
            raise StreamingNotSupported("probed lazily, not actually supported")
            yield  # pragma: no cover — makes this an async generator

    table = _FakeTable(
        price_row={
            "prompt_cents_per_1k": 1.0,
            "completion_cents_per_1k": 3.0,
            "cached_input_cents_per_1k": None,
            "effective_at": None,
        }
    )
    p_read, p_write = _patched(table)

    adapter = _RaisingStreamAdapter(
        {"prompt_tokens": 200, "completion_tokens": 75, "total_tokens": 275}
    )
    composed = _composed()
    runner = AgentRunner(adapter=adapter, skill_tool=None)

    with p_read, p_write:
        async with RunRecorder(
            agent_id=composed.agent_id,
            user_id=uuid4(),
            trigger="issue_dispatch",
            model=composed.model,
            provider="claude",
        ) as recorder:
            async for _ in runner.stream_turn(
                composed,
                [{"role": "user", "content": "Task: do the thing"}],
                recorder=recorder,
                auto_recorder=False,
            ):
                pass

    assert recorder.prompt_tokens == 200
    assert recorder.completion_tokens == 75
    finish = table.update_calls[0]
    assert finish["prompt_tokens"] == 200
    assert finish["completion_tokens"] == 75
    assert finish.get("cost_cents") is not None
    assert finish["cost_cents"] > 0


# ─── Fix 2 (the actual prod cause): Doubao SSE tail-usage shape ────────
#
# Reproduces the real prod path end to end: the REAL OpenAICompatibleAdapter
# (DoubaoAdapter's base class) with a mocked httpx SSE stream shaped exactly
# like Volcengine's — finish_reason on one chunk, usage on a separate
# trailing empty-choices chunk — driven through the real AgentRunner.stream_turn
# and a real RunRecorder against the same fake DB harness used above. Must
# fail at the current parser (agent_runner.py:~523's
# ``if recorder is not None and chunk.usage:`` never firing because the
# adapter's terminal chunk carries usage=None), not at the fallback branches
# Fix 1 covers.


class _FakeDoubaoStreamResponse:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def raise_for_status(self) -> None:
        pass

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeDoubaoStreamCM:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    async def __aenter__(self):
        return _FakeDoubaoStreamResponse(self._lines)

    async def __aexit__(self, *exc):
        return False


class _FakeDoubaoHttpxClient:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, method, url, json=None, headers=None):
        return _FakeDoubaoStreamCM(self._lines)


@pytest.mark.asyncio
async def test_issue_dispatch_doubao_sse_trailing_usage_chunk_records_tokens_and_cost():
    """The actual prod shape: DoubaoAdapter (real OpenAICompatibleAdapter.stream()),
    trigger='issue_dispatch_auto', SSE stream where usage arrives ONLY on a
    trailing empty-choices chunk after finish_reason. Must end up with
    non-zero/non-null tokens + cost_cents on the finished agent_runs row."""
    from unittest.mock import patch as _patch

    from app.services.ai.adapters.doubao import DoubaoAdapter

    table = _FakeTable(
        price_row={
            "prompt_cents_per_1k": 1.0,
            "completion_cents_per_1k": 3.0,
            "cached_input_cents_per_1k": None,
            "effective_at": None,
        }
    )
    p_read, p_write = _patched(table)

    sse_lines = [
        'data: {"choices":[{"delta":{"content":"the issue is done"},"finish_reason":null}]}',
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
        'data: {"choices":[],"usage":{"prompt_tokens":100,"completion_tokens":50,"total_tokens":150}}',
        "data: [DONE]",
    ]

    adapter = DoubaoAdapter(api_key="test", default_model="doubao-seed-2-0-lite-260428")
    composed = _composed(model="doubao-seed-2-0-lite-260428")
    runner = AgentRunner(adapter=adapter, skill_tool=None)

    with p_read, p_write, _patch(
        "app.services.ai.adapters.openai_compat.httpx.AsyncClient",
        return_value=_FakeDoubaoHttpxClient(sse_lines),
    ):
        async with RunRecorder(
            agent_id=composed.agent_id,
            user_id=uuid4(),
            trigger="issue_dispatch_auto",
            model=composed.model,
            provider="doubao",
        ) as recorder:
            async for _ in runner.stream_turn(
                composed,
                [{"role": "user", "content": "Task: do the thing"}],
                recorder=recorder,
                auto_recorder=False,
            ):
                pass

    assert recorder.prompt_tokens == 100
    assert recorder.completion_tokens == 50

    assert len(table.update_calls) == 1
    finish = table.update_calls[0]
    assert finish["prompt_tokens"] == 100
    assert finish["completion_tokens"] == 50
    assert finish.get("cost_cents") is not None
    assert finish["cost_cents"] > 0

"""A1 (needs_input first-class, Task 5): issue-dispatch turns must record
real token/cost telemetry on their agent_runs row.

Root cause: AgentRunner._stream_turn_inner (agent_runner.py) has two
fallback branches — the adapter exposes no ``.stream()`` method at all, or
``.stream()`` raises ``StreamingNotSupported`` mid-loop — that call
``self.adapter.call(...)`` directly and hand the provider's usage dict
straight to the caller via ``StreamChunk(usage=...)``, but never call
``recorder.record_usage(...)``. Every turn driven through
``run_session_turn`` passes a ``chunk_callback`` (both interactive chat's
SSE path in ``chat_stream`` and ``issue_agent_executor.run_issue_agent``
do), so both always go through ``stream_turn`` — never the buffered
``run_turn`` (which DOES call ``record_usage`` after every
``adapter.call``). Providers that implement real streaming (the
OpenAI-compatible adapters: Qwen/DeepSeek/Doubao/ModelScope/OpenAI) never
hit these branches, so interactive chat — which commonly binds those
models — records usage fine. ``ClaudeAdapter`` has no ``.stream()`` method
at all, so any issue whose assigned agent is bound to a ``claude-*`` model
always takes the silent-drop branch: the turn completes and produces a real
reply, but the agent_runs row's prompt_tokens/completion_tokens/cost_cents
stay 0/NULL. This matches prod: both ``issue_dispatch_auto`` runs ever
executed show 0 tokens even though one demonstrably produced a model
reply.

Fix: call ``recorder.record_usage(...)`` in both fallback branches too,
reusing the exact accumulate-then-cost-on-finish flow RunRecorder already
provides (no new pricing logic — ``compute_cost_cents``/``_finish`` are
untouched).
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

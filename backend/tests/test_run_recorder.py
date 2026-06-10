"""Unit tests for RunRecorder lifecycle and telemetry behaviour.

Covers the contract callers rely on:
- start persists a running row (or no-ops on telemetry failure)
- heartbeat rate-limits DB writes to 15s
- record_usage accumulates prompt/completion tokens
- cancel_requested observed → status='cancelled'
- exception in with-body → status='failed' + error_code
- AgentPausedError raised pre-insert when agent.paused_reason is set
- cost_cents computed from snapshot rates
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.services.ai.runner.run_recorder import AgentPausedError, RunRecorder, _truncate


class _FakeTable:
    """Minimal stand-in for the Supabase fluent builder — records each call."""

    def __init__(
        self,
        *,
        paused_reason: str | None = None,
        price_row: dict | None = None,
        insert_result_data: list[dict] | None = None,
    ) -> None:
        self._paused_reason = paused_reason
        self._price_row = price_row
        self._insert_result_data = insert_result_data or [{"id": str(uuid4())}]
        self.update_calls: list[dict] = []
        self.insert_calls: list[dict] = []
        self._cancel_requested = False
        self._last_select: str | None = None

    def set_cancel_requested(self, value: bool) -> None:
        self._cancel_requested = value

    def select(self, columns: str):
        self._last_select = columns
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def limit(self, _n: int):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def maybe_single(self):
        return self

    def insert(self, payload: dict):
        self.insert_calls.append(payload)
        return _Executable({"data": self._insert_result_data})

    def update(self, payload: dict):
        self.update_calls.append(payload)
        return _Executable({"data": None})

    async def execute(self):
        # Dispatch based on last select target
        if self._last_select == "paused_reason":
            return _Result({"paused_reason": self._paused_reason})
        if self._last_select == "cancel_requested":
            return _Result({"cancel_requested": self._cancel_requested})
        if self._last_select and "prompt_cents_per_1k" in self._last_select:
            return _Result([self._price_row] if self._price_row else [])
        return _Result(None)


class _Executable:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    async def execute(self):
        return _Result(self._payload.get("data"))


class _Result:
    def __init__(self, data) -> None:
        self.data = data


class _FakeClient:
    def __init__(self, table: _FakeTable) -> None:
        self._table = table

    def table(self, name: str):
        return self._table


@pytest.mark.asyncio
async def test_start_inserts_running_row_and_snapshots_price() -> None:
    table = _FakeTable(
        price_row={"prompt_cents_per_1k": 0.4, "completion_cents_per_1k": 1.2},
    )
    client = _FakeClient(table)

    with patch("app.db.get_async_supabase_admin", AsyncMock(return_value=client)):
        rec = RunRecorder(
            agent_id=uuid4(),
            user_id=uuid4(),
            trigger="chat",
            model="qwen-max",
            provider="qwen",
        )
        async with rec:
            pass

    assert len(table.insert_calls) == 1
    payload = table.insert_calls[0]
    assert payload["status"] == "running"
    assert payload["trigger"] == "chat"
    assert payload["prompt_cents_per_1k_snapshot"] == 0.4
    assert payload["completion_cents_per_1k_snapshot"] == 1.2


@pytest.mark.asyncio
async def test_paused_agent_raises_before_insert() -> None:
    table = _FakeTable(paused_reason="budget")
    client = _FakeClient(table)

    with patch("app.db.get_async_supabase_admin", AsyncMock(return_value=client)):
        rec = RunRecorder(
            agent_id=uuid4(),
            user_id=uuid4(),
            trigger="chat",
            model="qwen-max",
        )
        with pytest.raises(AgentPausedError):
            async with rec:
                pass

    assert table.insert_calls == [], "no row should be inserted when paused"


@pytest.mark.asyncio
async def test_exception_in_body_sets_failed() -> None:
    table = _FakeTable()
    client = _FakeClient(table)

    with patch("app.db.get_async_supabase_admin", AsyncMock(return_value=client)):
        rec = RunRecorder(agent_id=uuid4(), user_id=uuid4(), trigger="chat")
        with pytest.raises(RuntimeError):
            async with rec:
                raise RuntimeError("boom")

    # one insert (start) + one update (finish)
    assert len(table.update_calls) == 1
    finish = table.update_calls[0]
    assert finish["status"] == "failed"
    assert finish["error_code"] == "RuntimeError"


@pytest.mark.asyncio
async def test_completion_computes_cost_from_snapshot() -> None:
    table = _FakeTable(
        price_row={"prompt_cents_per_1k": 0.5, "completion_cents_per_1k": 2.0},
    )
    client = _FakeClient(table)

    with patch("app.db.get_async_supabase_admin", AsyncMock(return_value=client)):
        rec = RunRecorder(
            agent_id=uuid4(),
            user_id=uuid4(),
            trigger="chat",
            model="gpt-4o",
            provider="openai",
        )
        async with rec:
            rec.record_usage(prompt_tokens=1000, completion_tokens=500)

    finish = table.update_calls[-1]
    assert finish["status"] == "completed"
    # 1000 / 1000 * 0.5 + 500 / 1000 * 2.0 = 0.5 + 1.0 = 1.5 cents
    assert finish["cost_cents"] == pytest.approx(1.5)
    assert finish["prompt_tokens"] == 1000
    assert finish["completion_tokens"] == 500


@pytest.mark.asyncio
async def test_cancel_observed_sets_cancelled_status() -> None:
    table = _FakeTable()
    client = _FakeClient(table)
    table.set_cancel_requested(True)

    with patch("app.db.get_async_supabase_admin", AsyncMock(return_value=client)):
        rec = RunRecorder(agent_id=uuid4(), user_id=uuid4(), trigger="chat")
        async with rec:
            observed = await rec.check_cancelled()
            assert observed is True

    finish = table.update_calls[-1]
    assert finish["status"] == "cancelled"


@pytest.mark.asyncio
async def test_heartbeat_rate_limited_to_15s() -> None:
    table = _FakeTable()
    client = _FakeClient(table)

    with patch("app.db.get_async_supabase_admin", AsyncMock(return_value=client)):
        rec = RunRecorder(agent_id=uuid4(), user_id=uuid4(), trigger="chat")
        async with rec:
            # Simulate 10 tight iterations back-to-back
            updates_before = len(table.update_calls)
            for _ in range(10):
                await rec.heartbeat()
            # Start has already set _last_heartbeat_monotonic to now(),
            # so the 10 follow-ups should all be throttled (none write)
            assert len(table.update_calls) == updates_before

            # Force the clock forward past the 15s rate limit
            rec._last_heartbeat_monotonic = time.monotonic() - 30
            await rec.heartbeat()
            assert len(table.update_calls) == updates_before + 1


@pytest.mark.asyncio
async def test_record_usage_accumulates() -> None:
    rec = RunRecorder(agent_id=uuid4(), user_id=uuid4(), trigger="chat")
    rec.record_usage(prompt_tokens=100, completion_tokens=50)
    rec.record_usage(prompt_tokens=200, completion_tokens=100)
    assert rec._prompt_tokens == 300
    assert rec._completion_tokens == 150


@pytest.mark.asyncio
async def test_record_skill_deduplicates() -> None:
    rec = RunRecorder(agent_id=uuid4(), user_id=uuid4(), trigger="chat")
    rec.record_skill("script-outline")
    rec.record_skill("script-outline")
    rec.record_skill("script-expand")
    assert rec._skill_slugs_used == ["script-outline", "script-expand"]


def test_truncate_preserves_short_strings() -> None:
    assert _truncate("hello", 500) == "hello"


def test_truncate_long_strings() -> None:
    long = "a" * 600
    result = _truncate(long, 500)
    assert len(result) == 503  # 500 + "..."
    assert result.endswith("...")


def test_truncate_none_safe() -> None:
    assert _truncate(None, 500) == ""  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_bigint_snowflake_id_finalises_run() -> None:
    """Regression: agent_runs.id became a BIGINT Snowflake (mig 232). The
    insert returns a numeric id; RunRecorder must keep run_id as a string and
    still finalise the run. Previously it did UUID(str(id)) → ValueError, which
    __aenter__ swallowed as 'telemetry disabled' → no run was ever recorded
    after mig 232. The default test fixture used a UUID id and never caught it."""
    table = _FakeTable(
        price_row={"prompt_cents_per_1k": 0.4, "completion_cents_per_1k": 1.2},
        insert_result_data=[{"id": 310819108761487}],  # bigint snowflake, not UUID
    )
    client = _FakeClient(table)

    with patch("app.db.get_async_supabase_admin", AsyncMock(return_value=client)):
        rec = RunRecorder(
            agent_id=uuid4(), user_id=uuid4(), trigger="chat", model="qwen-max"
        )
        async with rec:
            rec.record_usage(prompt_tokens=100, completion_tokens=20)

    # run_id captured as the string form (no UUID crash)
    assert rec.run_id == "310819108761487"
    # the run was actually finalised (update ran) — the bug skipped this
    assert len(table.update_calls) == 1
    assert table.update_calls[0]["status"] == "completed"


class _FakeTableWithTaskRow(_FakeTable):
    """Extends the fake to answer the task_tracking metadata read that
    RunRecorder._link_task performs (mig 282 task ↔ run linkage)."""

    def __init__(self, *, task_metadata: dict | None = None, **kw) -> None:
        super().__init__(**kw)
        self._task_metadata = task_metadata if task_metadata is not None else {}

    async def execute(self):
        if self._last_select == "metadata":
            return _Result({"metadata": self._task_metadata})
        return await super().execute()


@pytest.mark.asyncio
async def test_task_id_links_run_and_task_bidirectionally() -> None:
    """mig 282 paperclip-style linkage: when task_id is passed, the insert
    carries agent_runs.task_id, and the recorder stamps agent_id +
    metadata.run_id back onto the task_tracking row (MERGING metadata,
    never replacing it — the shared-jsonb clobber lesson)."""
    agent = uuid4()
    table = _FakeTableWithTaskRow(
        insert_result_data=[{"id": 310819108761487}],
        task_metadata={"media_id": "42"},
    )
    client = _FakeClient(table)

    with patch("app.db.get_async_supabase_admin", AsyncMock(return_value=client)):
        rec = RunRecorder(
            agent_id=agent,
            user_id=uuid4(),
            trigger="visual_analysis_l1",
            task_id="wf-abc-123",
        )
        async with rec:
            pass

    # run → task: insert payload carries the task id
    assert table.insert_calls[0]["task_id"] == "wf-abc-123"
    # task → run: first update is the linkage stamp (second is finish)
    assert len(table.update_calls) == 2
    link = table.update_calls[0]
    assert link["agent_id"] == str(agent)
    assert link["metadata"] == {"media_id": "42", "run_id": "310819108761487"}
    assert table.update_calls[1]["status"] == "completed"


@pytest.mark.asyncio
async def test_no_task_id_skips_linkage() -> None:
    """Without task_id the recorder behaves exactly as before — one insert,
    one finish update, no task_tracking writes."""
    table = _FakeTable(insert_result_data=[{"id": 310819108761487}])
    client = _FakeClient(table)

    with patch("app.db.get_async_supabase_admin", AsyncMock(return_value=client)):
        rec = RunRecorder(agent_id=uuid4(), user_id=uuid4(), trigger="chat")
        async with rec:
            pass

    assert "task_id" not in table.insert_calls[0]
    assert len(table.update_calls) == 1  # finish only
    assert table.update_calls[0]["status"] == "completed"

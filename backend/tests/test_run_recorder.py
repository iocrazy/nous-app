"""Unit tests for RunRecorder lifecycle and telemetry behaviour.

Covers the contract callers rely on:
- start persists a running row (or no-ops on telemetry failure)
- heartbeat rate-limits DB writes to 15s
- record_usage accumulates prompt/completion tokens
- cancel_requested observed → status='cancelled'
- exception in with-body → status='failed' + error_code
- AgentPausedError raised pre-insert when agent.paused_reason is set
- cost_cents computed from snapshot rates

RunRecorder now talks to Postgres via SQLAlchemy ORM sessions
(``app.db.session.read_scope`` / ``write_scope``) instead of the old
supabase-py fluent client. These fakes stand in for an AsyncSession:
``execute()`` is dispatched by sniffing the compiled SQL text, and the
INSERT / UPDATE calls are recorded so assertions can inspect the bound
params (the ORM-era equivalent of the old ``insert_calls``/``update_calls``
supabase fluent-builder lists).
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.services.ai.runner.run_recorder import AgentPausedError, RunRecorder, _truncate


class _Row(tuple):
    """Tuple subclass so ``row[0]`` access (the RETURNING id / scalar column
    reads RunRecorder does) works exactly like a SQLAlchemy Row."""


class _MappingsResult:
    def __init__(self, mapping: dict | None) -> None:
        self._mapping = mapping

    def first(self):
        return self._mapping

    def all(self):
        return [self._mapping] if self._mapping else []


class _ExecResult:
    def __init__(self, *, scalar=None, first_row=None, mapping=None) -> None:
        self._scalar = scalar
        self._first_row = first_row
        self._mapping = mapping

    def scalar(self):
        return self._scalar

    def first(self):
        return self._first_row

    def mappings(self):
        return _MappingsResult(self._mapping)


class _FakeSession:
    """Stands in for an AsyncSession — dispatches ``execute()`` by sniffing
    the compiled SQL text, and records every call (insert/update/select) so
    tests can assert on the params that were bound."""

    def __init__(self, table: "_FakeTable") -> None:
        self._table = table

    async def execute(self, stmt, params=None):
        sql = str(stmt).lower()
        bind_params = dict(params) if params else dict(stmt.compile().params)
        # ORM-compiled statements carry a "public." schema prefix
        # (Base.__table_args__ = {"schema": "public"}); the raw text()
        # INSERTs in run_recorder.py do not. Dispatch on bare table-name
        # substrings + statement verb so both shapes match.
        is_insert = sql.lstrip().startswith("insert")
        is_update = sql.lstrip().startswith("update")
        is_select = sql.lstrip().startswith("select")

        if is_insert and "agent_run_transcript_events" in sql:
            self._table.event_inserts.append(dict(bind_params))
            return _ExecResult()

        if is_insert and "agent_runs" in sql:
            self._table.insert_calls.append(dict(bind_params))
            row_id = self._table._insert_id
            return _ExecResult(first_row=_Row((row_id,)))

        if is_select and "ai_agents" in sql:
            return _ExecResult(
                mapping={
                    "paused_reason": self._table._paused_reason,
                    "max_concurrent_runs": self._table._max_concurrent_runs,
                }
            )

        if is_select and "count(" in sql and "agent_runs" in sql:
            return _ExecResult(scalar=self._table._running_count)

        if is_select and "ai_model_prices" in sql:
            return _ExecResult(mapping=self._table._price_row)

        if is_select and "pause_requested" in sql:
            return _ExecResult(
                first_row=(
                    _Row((self._table._pause_requested,))
                    if self._table._pause_requested is not None
                    else None
                )
            )

        if is_select and "cancel_requested" in sql:
            return _ExecResult(
                first_row=(
                    _Row((self._table._cancel_requested,))
                    if self._table._cancel_requested is not None
                    else None
                )
            )

        if is_select and "task_tracking" in sql:
            return _ExecResult(mapping={"metadata": self._table._task_metadata})

        if is_update and "task_tracking" in sql:
            self._table.update_calls.append(dict(bind_params))
            return _ExecResult()

        if is_update and "agent_runs" in sql:
            self._table.update_calls.append(dict(bind_params))
            return _ExecResult()

        return _ExecResult()


class _FakeTable:
    """Records every insert/update RunRecorder issues and answers the
    handful of SELECTs it performs (pause/concurrency pre-flight, price
    snapshot, cancel poll, task_tracking metadata read)."""

    def __init__(
        self,
        *,
        paused_reason: str | None = None,
        price_row: dict | None = None,
        insert_result_data: list[dict] | None = None,
        max_concurrent_runs: int | None = None,
        running_count: int = 0,
        cancel_requested: bool | None = False,
        task_metadata: dict | None = None,
        pause_requested: bool | None = False,
    ) -> None:
        self._paused_reason = paused_reason
        self._price_row = price_row
        # agent_runs.id is a BIGINT Snowflake (mig 232); run_recorder does
        # int(self.run_id) for every follow-up query (heartbeat/cancel/finish),
        # so the default fixture id must be numeric, not a UUID string (the
        # pre-mig-232 shape — see test_bigint_snowflake_id_finalises_run for
        # the regression this guards).
        insert_result_data = insert_result_data or [{"id": 900000000000001}]
        self._insert_id = insert_result_data[0]["id"]
        self._max_concurrent_runs = max_concurrent_runs
        self._running_count = running_count
        self._cancel_requested = cancel_requested
        self._pause_requested = pause_requested
        self._task_metadata = task_metadata if task_metadata is not None else {}

        self.insert_calls: list[dict] = []
        self.update_calls: list[dict] = []
        self.event_inserts: list[dict] = []

    def set_cancel_requested(self, value: bool) -> None:
        self._cancel_requested = value

    def scopes(self):
        """Return a (read_scope, write_scope) pair of asynccontextmanagers
        yielding a _FakeSession bound to this table."""

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
async def test_start_inserts_running_row_and_snapshots_price() -> None:
    table = _FakeTable(
        price_row={"prompt_cents_per_1k": 0.4, "completion_cents_per_1k": 1.2},
    )
    p_read, p_write = _patched(table)

    with p_read, p_write:
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
    p_read, p_write = _patched(table)

    with p_read, p_write:
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
    p_read, p_write = _patched(table)

    with p_read, p_write:
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
    p_read, p_write = _patched(table)

    with p_read, p_write:
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
    table = _FakeTable(cancel_requested=True)
    p_read, p_write = _patched(table)

    with p_read, p_write:
        rec = RunRecorder(agent_id=uuid4(), user_id=uuid4(), trigger="chat")
        async with rec:
            observed = await rec.check_cancelled()
            assert observed is True

    finish = table.update_calls[-1]
    assert finish["status"] == "cancelled"


@pytest.mark.asyncio
async def test_pause_observed_does_not_change_the_finish_status() -> None:
    """Phase 2a: ``check_paused`` reads ``pause_requested``; unlike cancel it
    is NOT a finish status — the turn_end event is the record, the row
    completes normally."""
    table = _FakeTable(pause_requested=True)
    p_read, p_write = _patched(table)

    with p_read, p_write:
        rec = RunRecorder(agent_id=uuid4(), user_id=uuid4(), trigger="chat")
        async with rec:
            assert await rec.check_paused() is True
            assert await rec.check_cancelled() is False

    finish = table.update_calls[-1]
    assert finish["status"] == "completed"


@pytest.mark.asyncio
async def test_pause_not_requested_or_unreadable_is_not_paused() -> None:
    table = _FakeTable(pause_requested=False)
    p_read, p_write = _patched(table)
    with p_read, p_write:
        rec = RunRecorder(agent_id=uuid4(), user_id=uuid4(), trigger="chat")
        async with rec:
            assert await rec.check_paused() is False
    table = _FakeTable(pause_requested=None)  # no row → False, never raises
    p_read, p_write = _patched(table)
    with p_read, p_write:
        rec = RunRecorder(agent_id=uuid4(), user_id=uuid4(), trigger="chat")
        async with rec:
            assert await rec.check_paused() is False


@pytest.mark.asyncio
async def test_heartbeat_rate_limited_to_15s() -> None:
    table = _FakeTable()
    p_read, p_write = _patched(table)

    with p_read, p_write:
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
    p_read, p_write = _patched(table)

    with p_read, p_write:
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


@pytest.mark.asyncio
async def test_task_id_links_run_and_task_bidirectionally() -> None:
    """mig 282 paperclip-style linkage: when task_id is passed, the insert
    carries agent_runs.task_id, and the recorder stamps agent_id +
    metadata.run_id back onto the task_tracking row (MERGING metadata,
    never replacing it — the shared-jsonb clobber lesson)."""
    agent = uuid4()
    table = _FakeTable(
        insert_result_data=[{"id": 310819108761487}],
        task_metadata={"media_id": "42"},
    )
    p_read, p_write = _patched(table)

    with p_read, p_write:
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
    p_read, p_write = _patched(table)

    with p_read, p_write:
        rec = RunRecorder(agent_id=uuid4(), user_id=uuid4(), trigger="chat")
        async with rec:
            pass

    assert "task_id" not in table.insert_calls[0]
    assert len(table.update_calls) == 1  # finish only
    assert table.update_calls[0]["status"] == "completed"


@pytest.mark.asyncio
async def test_record_event_appends_sequenced_truncated_rows() -> None:
    """mig 285 transcript: record_event auto-increments seq, truncates long
    payload values, and never writes before the run row exists."""
    table = _FakeTable(insert_result_data=[{"id": 310819108761487}])
    p_read, p_write = _patched(table)

    with p_read, p_write:
        rec = RunRecorder(agent_id=uuid4(), user_id=uuid4(), trigger="chat")
        # Before start: run_id None → no-op, no insert.
        await rec.record_event("user", {"content": "early"})
        assert table.event_inserts == []

        async with rec:
            await rec.record_event("user", {"content": "hello"})
            await rec.record_event(
                "tool_call",
                {"tool": "Skill", "args": {"skill": "x"}, "result": "y" * 10_000},
            )

    assert [e["seq"] for e in table.event_inserts] == [1, 2]
    assert table.event_inserts[0]["event_type"] == "user"
    # ORM insert (mig 453 writer) binds the payload dict onto the JSONB column
    # directly — the old text() path json.dumps'd it.
    payload0 = table.event_inserts[0]["payload"]
    assert payload0["content"] == "hello"
    # Long string value truncated to the cap (+ ellipsis).
    payload1 = table.event_inserts[1]["payload"]
    result_val = payload1["result"]
    assert len(result_val) <= RunRecorder.EVENT_VALUE_MAX_CHARS + 3
    # Non-string values JSON-encoded.
    assert "skill" in payload1["args"]


@pytest.mark.asyncio
async def test_max_concurrent_runs_rejects_with_busy_error() -> None:
    """mig 286: at the cap → AgentBusyError pre-flight, no run row inserted.
    AgentBusyError subclasses AgentPausedError so existing handlers cover it."""
    from app.services.ai.runner.run_recorder import AgentBusyError

    table = _FakeTable(max_concurrent_runs=2, running_count=2)
    p_read, p_write = _patched(table)

    with p_read, p_write:
        rec = RunRecorder(agent_id=uuid4(), user_id=uuid4(), trigger="chat")
        with pytest.raises(AgentBusyError):
            async with rec:
                pass

    assert isinstance(AgentBusyError("x"), AgentPausedError)
    assert table.insert_calls == []


@pytest.mark.asyncio
async def test_below_concurrency_cap_proceeds() -> None:
    table = _FakeTable(
        max_concurrent_runs=2,
        running_count=1,
        insert_result_data=[{"id": 310819108761487}],
    )
    p_read, p_write = _patched(table)

    with p_read, p_write:
        rec = RunRecorder(agent_id=uuid4(), user_id=uuid4(), trigger="chat")
        async with rec:
            pass

    assert len(table.insert_calls) == 1


# ---------------------------------------------------------------------------
# liveness_state terminal value (2026-08-03 needs_input E2E follow-up)
#
# A normally-completed run used to leave liveness_state='running' forever —
# the column had no "finished normally" value at all (mig 207 only modelled
# the running→silent→stuck→dead degradation path plus 'cancelled').
#
# 'dead' is NOT the fix: every writer of dead pairs it atomically with
# status='failed' (liveness_scanner._mark_dead, liveness/reconcile), the
# stuck/dead pair drives the agent fault badge, and
# AgentRunsRepository.mark_empty_output's docstring explicitly records an
# earlier attempt to reuse 'dead' here as the wrong call. Hence a new
# terminal value, 'finished' (mig 406).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_completed_run_gets_finished_liveness_state() -> None:
    table = _FakeTable()
    p_read, p_write = _patched(table)

    with p_read, p_write:
        rec = RunRecorder(agent_id=uuid4(), user_id=uuid4(), trigger="chat")
        async with rec:
            pass

    finish = table.update_calls[-1]
    assert finish["status"] == "completed"
    assert finish["liveness_state"] == "finished"


@pytest.mark.asyncio
async def test_cancelled_run_mirrors_cancelled_liveness_state() -> None:
    """mig 207 defined 'cancelled' as "mirrors agent_runs.status" but nothing
    ever wrote it — the cancel path left liveness_state='running' too."""
    table = _FakeTable(cancel_requested=True)
    p_read, p_write = _patched(table)

    with p_read, p_write:
        rec = RunRecorder(agent_id=uuid4(), user_id=uuid4(), trigger="chat")
        async with rec:
            assert await rec.check_cancelled() is True

    finish = table.update_calls[-1]
    assert finish["status"] == "cancelled"
    assert finish["liveness_state"] == "cancelled"


@pytest.mark.asyncio
async def test_failed_run_also_gets_finished_liveness_state() -> None:
    """`finished` means "wound up in an orderly way", not "succeeded" —
    liveness is orthogonal to status. A body that raised still returned
    control to the recorder and closed its own row, so a failed row left on
    'running' is exactly as wrong as a completed one."""
    table = _FakeTable()
    p_read, p_write = _patched(table)

    with p_read, p_write:
        rec = RunRecorder(agent_id=uuid4(), user_id=uuid4(), trigger="chat")
        with pytest.raises(RuntimeError):
            async with rec:
                raise RuntimeError("boom")

    finish = table.update_calls[-1]
    assert finish["status"] == "failed"
    assert finish["liveness_state"] == "finished"


@pytest.mark.asyncio
async def test_no_finish_path_ever_writes_dead() -> None:
    """The red line. dead ⟺ failed is a whole-DB invariant: both writers of
    dead (liveness_scanner._mark_dead, services/liveness/reconcile) set
    status='failed' in the SAME statement, and dead means "the process
    actually died". The agent fault badge reads liveness_state IN
    ('stuck','dead') — minting dead for ordinary outcomes would light up
    every agent."""
    for cancel_requested, raises in ((False, False), (False, True), (True, False)):
        table = _FakeTable(cancel_requested=cancel_requested)
        p_read, p_write = _patched(table)

        with p_read, p_write:
            rec = RunRecorder(agent_id=uuid4(), user_id=uuid4(), trigger="chat")
            if raises:
                with pytest.raises(RuntimeError):
                    async with rec:
                        raise RuntimeError("boom")
            else:
                async with rec:
                    if cancel_requested:
                        await rec.check_cancelled()

        finish = table.update_calls[-1]
        assert finish.get("liveness_state") != "dead"


def test_truncate_payload_keeps_nested_structure_when_it_fits():
    # The transcript is the replay source: a fold handed a JSON *string* where
    # it expects a dict returns nothing (fold_todo's isinstance(total, int)).
    from app.services.ai.runner.run_recorder import _truncate_payload

    out = _truncate_payload(
        {
            "usage": {"prompt": 3, "completion": 1},
            "todos": [{"id": 1, "content": "a"}],
            "content": "x" * 10,
            "n": 2,
            "flag": None,
        },
        max_chars=500,
    )
    assert out["usage"] == {"prompt": 3, "completion": 1}
    assert out["todos"] == [{"id": 1, "content": "a"}]
    assert out["content"] == "x" * 10 and out["n"] == 2 and out["flag"] is None


def test_truncate_payload_degrades_oversized_nested_value_to_truncated_string():
    from app.services.ai.runner.run_recorder import _truncate_payload

    big = {"result": "y" * 1000}
    out = _truncate_payload({"result": big}, max_chars=50)
    assert isinstance(out["result"], str)
    assert out["result"].endswith("...") and len(out["result"]) == 53


def test_truncate_payload_round_trips_non_json_scalars_inside_structure():
    import datetime as dt

    from app.services.ai.runner.run_recorder import _truncate_payload

    when = dt.datetime(2026, 9, 6, tzinfo=dt.timezone.utc)
    out = _truncate_payload({"meta": {"at": when}}, max_chars=500)
    assert out["meta"] == {"at": str(when)}  # default=str, still a dict

"""Unit tests for CostAuditor PostToolUse hook."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from app.services.hooks import HookContext
from app.services.hooks.cost_auditor import CostAuditorHook, _summarise_args


def _ctx(
    *,
    iteration: int = 1,
    prompt_tokens: int = 100,
    completion_tokens: int = 200,
    cost_cents: float = 5.0,
    run_id: UUID = UUID("00000000-0000-0000-0000-000000000010"),
) -> HookContext:
    return HookContext(
        run_id=run_id,
        agent_id=UUID("00000000-0000-0000-0000-000000000020"),
        agent_slug="script_ai",
        user_id=UUID("00000000-0000-0000-0000-000000000030"),
        session_id=None,
        tool_name="Skill",
        tool_args={"skill": "script-outline"},
        accumulated_prompt_tokens=prompt_tokens,
        accumulated_completion_tokens=completion_tokens,
        accumulated_cost_cents=cost_cents,
        iteration=iteration,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_first_iteration_writes_full_amount_as_delta():
    hook = CostAuditorHook()
    write_mock = AsyncMock()
    with patch.object(CostAuditorHook, "_write_event", write_mock):
        result = await hook(_ctx(prompt_tokens=100, completion_tokens=50, cost_cents=3.0), {"prompt": "ok"})

    assert result.decision == "continue"
    write_mock.assert_awaited_once()
    kwargs = write_mock.await_args.kwargs
    assert kwargs["prompt_tokens_delta"] == 100
    assert kwargs["completion_tokens_delta"] == 50
    assert kwargs["cost_cents_delta"] == 3.0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_second_iteration_emits_delta_only():
    hook = CostAuditorHook()
    write_mock = AsyncMock()
    with patch.object(CostAuditorHook, "_write_event", write_mock):
        await hook(_ctx(iteration=1, prompt_tokens=100, completion_tokens=50, cost_cents=3.0), {})
        await hook(_ctx(iteration=2, prompt_tokens=180, completion_tokens=90, cost_cents=5.0), {})

    assert write_mock.await_count == 2
    second_kwargs = write_mock.await_args_list[1].kwargs
    assert second_kwargs["prompt_tokens_delta"] == 80   # 180 - 100
    assert second_kwargs["completion_tokens_delta"] == 40  # 90 - 50
    assert abs(second_kwargs["cost_cents_delta"] - 2.0) < 1e-6  # 5.0 - 3.0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_negative_delta_clamped_to_zero():
    """Defensive: if cumulative counter ever decreases, delta should be 0, not negative."""
    hook = CostAuditorHook()
    write_mock = AsyncMock()
    with patch.object(CostAuditorHook, "_write_event", write_mock):
        await hook(_ctx(iteration=1, prompt_tokens=200), {})
        await hook(_ctx(iteration=2, prompt_tokens=50), {})  # decreased

    second = write_mock.await_args_list[1].kwargs
    assert second["prompt_tokens_delta"] == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_per_run_isolation_no_cross_user_pollution():
    """Two runs on the same hook instance must NOT share delta state.

    This is the adversarial-review fix: state keyed by run_id.
    """
    hook = CostAuditorHook()
    write_mock = AsyncMock()
    run_a = UUID("00000000-0000-0000-0000-00000000aaaa")
    run_b = UUID("00000000-0000-0000-0000-00000000bbbb")

    with patch.object(CostAuditorHook, "_write_event", write_mock):
        # Run A iter 1 — prompt_tokens=100 (delta=100)
        await hook(_ctx(run_id=run_a, iteration=1, prompt_tokens=100), {})
        # Run B iter 1 — prompt_tokens=50 (delta=50, NOT 50-100=-50→0)
        await hook(_ctx(run_id=run_b, iteration=1, prompt_tokens=50), {})
        # Run A iter 2 — prompt_tokens=180 (delta=80, NOT 180-50=130)
        await hook(_ctx(run_id=run_a, iteration=2, prompt_tokens=180), {})

    deltas = [c.kwargs["prompt_tokens_delta"] for c in write_mock.await_args_list]
    assert deltas == [100, 50, 80]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_iteration_one_always_treats_as_full_amount():
    """Even if hook instance saw the same run_id before (impossible in practice
    with UUID4, but defensive), iteration=1 resets the baseline."""
    hook = CostAuditorHook()
    write_mock = AsyncMock()
    run = UUID("00000000-0000-0000-0000-00000000cccc")

    with patch.object(CostAuditorHook, "_write_event", write_mock):
        await hook(_ctx(run_id=run, iteration=1, prompt_tokens=100), {})
        # Simulate "new run reusing same id" (shouldn't happen but):
        await hook(_ctx(run_id=run, iteration=1, prompt_tokens=50), {})

    deltas = [c.kwargs["prompt_tokens_delta"] for c in write_mock.await_args_list]
    # Both treated as full amount (iteration=1)
    assert deltas == [100, 50]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_db_write_failure_is_swallowed():
    """The whole point of CostAuditor — telemetry failures must not break the run."""
    hook = CostAuditorHook()
    # Patch the import inside _write_event to raise on table().insert().execute()
    fake_admin = MagicMock()
    fake_admin.table.return_value.insert.return_value.execute = AsyncMock(
        side_effect=RuntimeError("DB down")
    )

    async def fake_get_admin():
        return fake_admin

    with patch("app.db.get_async_supabase_admin", fake_get_admin):
        result = await hook(_ctx(), {"prompt": "ok"})

    # Hook still returns continue. Run lives on.
    assert result.decision == "continue"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sentinel_run_id_skipped():
    """run_id of all zeros (test path with no RunRecorder) skips the DB write."""
    hook = CostAuditorHook()
    fake_admin = MagicMock()
    fake_admin.table.return_value.insert.return_value.execute = AsyncMock()

    async def fake_get_admin():
        return fake_admin

    with patch("app.db.get_async_supabase_admin", fake_get_admin):
        result = await hook(_ctx(run_id=UUID(int=0)), {"prompt": "ok"})

    assert result.decision == "continue"
    # Insert was NOT called because of sentinel detection.
    assert not fake_admin.table.called


@pytest.mark.unit
def test_summarise_args_truncates_long_values():
    long_val = "x" * 500
    result = _summarise_args({"k": long_val})
    assert len(result) <= 200
    assert result.endswith("...")


@pytest.mark.unit
def test_summarise_args_empty_returns_empty():
    assert _summarise_args({}) == ""


@pytest.mark.unit
def test_summarise_args_renders_multiple_keys():
    result = _summarise_args({"skill": "script-outline", "lang": "zh"})
    assert "skill=script-outline" in result
    assert "lang=zh" in result

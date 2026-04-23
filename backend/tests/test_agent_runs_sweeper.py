"""Sweeper unit tests — covers lock contention + heartbeat-lost + budget recompute."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_sweep_skips_when_lock_held() -> None:
    """If another worker holds the advisory lock, sweep is a no-op.

    The test forces try_advisory_lock → False and asserts the task
    returns skipped=1 without touching any other tables. (The wrapper
    name is per migration 149 — see agent_runs_sweeper docstring.)
    """
    from app.tasks import agent_runs_sweeper

    client = MagicMock()
    client.rpc.return_value.execute = AsyncMock(
        return_value=MagicMock(data=False)
    )

    with patch(
        "app.tasks.agent_runs_sweeper.get_async_supabase_admin",
        AsyncMock(return_value=client),
    ):
        result = await agent_runs_sweeper._sweep_async()

    assert result["skipped"] == 1
    assert result["heartbeat_lost"] == 0


@pytest.mark.asyncio
async def test_sweep_marks_heartbeat_lost_and_recomputes_budgets() -> None:
    """When lock is acquired: heartbeat sweep runs, budgets recompute, unlock."""
    from app.tasks import agent_runs_sweeper

    client = MagicMock()
    # try_advisory_lock → True, advisory_unlock → None (migration 149 wrappers)
    exec_mock = AsyncMock()
    exec_mock.side_effect = [
        MagicMock(data=True),   # lock acquired
        MagicMock(data=[]),      # agents with budgets (none)
        MagicMock(data=None),    # unlock
    ]
    client.rpc.return_value.execute = exec_mock
    # Table query for agents (monthly_usage_by_agent returns zero rows)
    table_chain = MagicMock()
    table_chain.select.return_value = table_chain
    table_chain.in_.return_value = table_chain
    table_chain.execute = AsyncMock(return_value=MagicMock(data=[]))
    client.table = MagicMock(return_value=table_chain)

    with patch(
        "app.tasks.agent_runs_sweeper.get_async_supabase_admin",
        AsyncMock(return_value=client),
    ), patch(
        "app.tasks.agent_runs_sweeper.AgentRunsRepository"
    ) as runs_cls:
        runs_cls.return_value.mark_heartbeat_lost = AsyncMock(return_value=3)
        runs_cls.return_value.monthly_usage_by_agent = AsyncMock(return_value=[])

        result = await agent_runs_sweeper._sweep_async()

    assert result["skipped"] == 0
    assert result["heartbeat_lost"] == 3


@pytest.mark.asyncio
async def test_sweep_calls_correct_rpc_wrapper_names() -> None:
    """Regression: prod hotfix confirmed PostgREST can't route
    pg_try_advisory_lock / pg_advisory_unlock (pg_catalog built-ins),
    only public-schema wrappers with named params. If someone renames
    the migration-149 wrappers or reverts to the raw pg_* names, this
    test catches it before prod does.
    """
    from app.tasks import agent_runs_sweeper

    client = MagicMock()
    client.rpc.return_value.execute = AsyncMock(return_value=MagicMock(data=False))
    with patch(
        "app.tasks.agent_runs_sweeper.get_async_supabase_admin",
        AsyncMock(return_value=client),
    ):
        await agent_runs_sweeper._sweep_async()

    # Assert the exact RPC name + parameter shape the wrappers expect.
    call_args_list = [call.args for call in client.rpc.call_args_list]
    assert ("try_advisory_lock", {"lock_key": agent_runs_sweeper.SWEEPER_LOCK_KEY}) in call_args_list


@pytest.mark.asyncio
async def test_recompute_flips_agent_to_paused_by_budget() -> None:
    """An agent over its monthly_token_budget gets paused_reason='budget'."""
    from app.tasks import agent_runs_sweeper

    agent_id = str(uuid4())
    client = MagicMock()
    # agents with budgets
    agents_chain = MagicMock()
    agents_chain.select.return_value = agents_chain
    agents_chain.in_.return_value = agents_chain
    agents_chain.execute = AsyncMock(
        return_value=MagicMock(
            data=[
                {
                    "id": agent_id,
                    "monthly_token_budget": 1000,
                    "monthly_cost_cents_budget": None,
                    "paused_reason": None,
                }
            ]
        )
    )
    # update chain
    update_chain = MagicMock()
    update_chain.eq.return_value = update_chain
    update_chain.execute = AsyncMock(return_value=MagicMock(data=None))
    agents_chain.update.return_value = update_chain

    client.table = MagicMock(return_value=agents_chain)

    # Monthly usage shows this agent at 2000 tokens (over budget)
    with patch(
        "app.tasks.agent_runs_sweeper.AgentRunsRepository"
    ) as runs_cls:
        runs_cls.return_value.monthly_usage_by_agent = AsyncMock(
            return_value=[
                {
                    "agent_id": agent_id,
                    "total_tokens": 1500,
                    "cost_cents": None,
                },
                {
                    "agent_id": agent_id,
                    "total_tokens": 500,
                    "cost_cents": None,
                },
            ]
        )
        transitions = await agent_runs_sweeper._recompute_monthly_budgets(client)

    assert transitions == 1
    # Verify the update to paused_reason='budget' was called
    agents_chain.update.assert_called_with({"paused_reason": "budget"})


@pytest.mark.asyncio
async def test_recompute_clears_paused_when_budget_no_longer_exceeded() -> None:
    """Paused_reason='budget' reverts to None when usage drops below budget."""
    from app.tasks import agent_runs_sweeper

    agent_id = str(uuid4())
    client = MagicMock()
    agents_chain = MagicMock()
    agents_chain.select.return_value = agents_chain
    agents_chain.in_.return_value = agents_chain
    agents_chain.execute = AsyncMock(
        return_value=MagicMock(
            data=[
                {
                    "id": agent_id,
                    "monthly_token_budget": 10_000,  # plenty of room
                    "monthly_cost_cents_budget": None,
                    "paused_reason": "budget",
                }
            ]
        )
    )
    update_chain = MagicMock()
    update_chain.eq.return_value = update_chain
    update_chain.execute = AsyncMock(return_value=MagicMock(data=None))
    agents_chain.update.return_value = update_chain
    client.table = MagicMock(return_value=agents_chain)

    with patch(
        "app.tasks.agent_runs_sweeper.AgentRunsRepository"
    ) as runs_cls:
        runs_cls.return_value.monthly_usage_by_agent = AsyncMock(
            return_value=[{"agent_id": agent_id, "total_tokens": 100, "cost_cents": None}]
        )
        transitions = await agent_runs_sweeper._recompute_monthly_budgets(client)

    assert transitions == 1
    agents_chain.update.assert_called_with({"paused_reason": None})


@pytest.mark.asyncio
async def test_recompute_respects_manual_pause() -> None:
    """A manually paused agent stays paused even if over budget."""
    from app.tasks import agent_runs_sweeper

    agent_id = str(uuid4())
    client = MagicMock()
    agents_chain = MagicMock()
    agents_chain.select.return_value = agents_chain
    agents_chain.in_.return_value = agents_chain
    agents_chain.execute = AsyncMock(
        return_value=MagicMock(
            data=[
                {
                    "id": agent_id,
                    "monthly_token_budget": 100,
                    "monthly_cost_cents_budget": None,
                    "paused_reason": "manual",  # Already paused by admin
                }
            ]
        )
    )
    update_chain = MagicMock()
    update_chain.eq.return_value = update_chain
    update_chain.execute = AsyncMock(return_value=MagicMock(data=None))
    agents_chain.update.return_value = update_chain
    client.table = MagicMock(return_value=agents_chain)

    with patch(
        "app.tasks.agent_runs_sweeper.AgentRunsRepository"
    ) as runs_cls:
        runs_cls.return_value.monthly_usage_by_agent = AsyncMock(
            return_value=[{"agent_id": agent_id, "total_tokens": 1000, "cost_cents": None}]
        )
        transitions = await agent_runs_sweeper._recompute_monthly_budgets(client)

    # Over budget but already manually paused → no transition, no clobber
    assert transitions == 0
    agents_chain.update.assert_not_called()

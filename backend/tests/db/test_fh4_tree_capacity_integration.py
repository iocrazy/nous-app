"""fh4 T3 (E3) against real Postgres: the per-tree advisory lock serialises
two spawns that race for the last slot, and a queued background task counts
until its run row exists. Only the database can settle either: the unit tests
stub the session the lock and the count run on."""

from __future__ import annotations

import asyncio
import uuid

import pytest

from tests.db import test_inbox_expire_skips_paused_integration as _base

_skip = _base._skip
orm_dsn = _base.orm_dsn
pg = _base.pg

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
async def world(pg):
    uid = uuid.uuid4()
    await pg.execute("INSERT INTO auth.users (id) VALUES ($1)", uid)
    agent = await pg.fetchval(
        "INSERT INTO ai_agents (name, slug, user_id, created_by, is_system_preset)"
        " VALUES ('Tree Cap Fixture', $1, $2, $2, false) RETURNING id",
        f"tc-{uuid.uuid4().hex[:10]}",
        uid,
    )
    root = await pg.fetchval(
        "INSERT INTO agent_runs (agent_id, user_id, status, trigger)"
        " VALUES ($1, $2, 'running', 'chat') RETURNING id",
        agent,
        uid,
    )
    tasks: list[str] = []
    try:
        yield {"user": uid, "agent": agent, "root": int(root), "tasks": tasks}
    finally:
        await pg.execute(
            "DELETE FROM agent_runs WHERE user_id = $1 AND parent_run_id IS NOT NULL",
            uid,
        )
        await pg.execute("DELETE FROM agent_runs WHERE user_id = $1", uid)
        await pg.execute(
            "DELETE FROM task_tracking WHERE dbos_workflow_id = ANY($1::text[])",
            tasks,
        )
        await pg.execute("DELETE FROM ai_agents WHERE id = $1", agent)
        await pg.execute("DELETE FROM auth.users WHERE id = $1", uid)


async def _children(pg, world, n, *, status="running"):
    for _ in range(n):
        await pg.execute(
            "INSERT INTO agent_runs (agent_id, user_id, status, trigger,"
            " parent_run_id, root_run_id) VALUES ($1, $2, $3, 'subagent_task', $4, $4)",
            world["agent"],
            world["user"],
            status,
            world["root"],
        )


async def _insert_child(world):
    from sqlalchemy import insert

    from app.db.session import write_scope
    from app.models import AgentRuns

    async with write_scope() as session:
        await session.execute(
            insert(AgentRuns).values(
                agent_id=world["agent"],
                user_id=world["user"],
                status="running",
                trigger="subagent_task",
                parent_run_id=world["root"],
                root_run_id=world["root"],
            )
        )
        # Hold the transaction (and so the lock) long enough for the other
        # spawn to arrive and block on it.
        await asyncio.sleep(0.3)
    return "spawned"


@_skip
async def test_two_spawns_racing_for_the_last_slot_admit_exactly_one(
    orm_dsn, pg, world, monkeypatch
):
    from app.services.workforce import tree_capacity as tc

    monkeypatch.setenv("MAX_ACTIVE_SUBAGENTS_PER_TREE", "8")
    await _children(pg, world, 7)
    await _children(pg, world, 3, status="completed")  # finished: not counted

    async def _spawn():
        try:
            return await tc.run_within_tree_capacity(
                world["root"], 1, lambda: _insert_child(world)
            )
        except tc.TreeCapacityExceeded as exc:
            return exc

    a, b = await asyncio.gather(_spawn(), _spawn())

    outcomes = sorted([type(a).__name__, type(b).__name__])
    assert outcomes == ["TreeCapacityExceeded", "str"], (a, b)
    refused = a if isinstance(a, tc.TreeCapacityExceeded) else b
    assert (refused.limit, refused.active) == (8, 8)
    running = await pg.fetchval(
        "SELECT count(*) FROM agent_runs WHERE root_run_id = $1 AND status='running'",
        world["root"],
    )
    assert running == 8


@_skip
async def test_a_queued_background_task_counts_until_its_run_exists(
    orm_dsn, pg, world, monkeypatch
):
    from app.repositories.agent_workforce_repository import (
        get_agent_workforce_repository,
    )
    from app.services.workforce import tree_capacity as tc

    monkeypatch.setenv("MAX_ACTIVE_SUBAGENTS_PER_TREE", "2")
    row = await get_agent_workforce_repository().create_task(
        agent_id=world["agent"],
        user_id=world["user"],
        payload={"kind": "subagent", "root_run_id": str(world["root"])},
        title="Tree Cap Queued",
    )
    world["tasks"].append(str(row["id"]))
    other = await get_agent_workforce_repository().create_task(
        agent_id=world["agent"],
        user_id=world["user"],
        payload={"kind": "subagent", "root_run_id": "1"},  # another tree
        title="Tree Cap Elsewhere",
    )
    world["tasks"].append(str(other["id"]))
    await _children(pg, world, 1)

    with pytest.raises(tc.TreeCapacityExceeded) as err:
        await tc.check_tree_capacity(world["root"], 1)
    assert err.value.active == 2

    # Once the task's run row exists it is counted as that run, not twice.
    await pg.execute(
        "INSERT INTO agent_runs (agent_id, user_id, status, trigger, parent_run_id,"
        " root_run_id, task_id) VALUES ($1, $2, 'completed', 'subagent_task', $3, $3,"
        " $4)",
        world["agent"],
        world["user"],
        world["root"],
        str(row["id"]),
    )
    assert await tc.check_tree_capacity(world["root"], 1) == 1


@_skip
async def test_resolve_tree_root_follows_the_parents_root(orm_dsn, pg, world):
    from app.services.workforce import tree_capacity as tc

    await _children(pg, world, 1)
    child = await pg.fetchval(
        "SELECT id FROM agent_runs WHERE parent_run_id = $1 LIMIT 1", world["root"]
    )
    assert await tc.resolve_tree_root(str(world["root"])) == world["root"]
    assert await tc.resolve_tree_root(str(child)) == world["root"]

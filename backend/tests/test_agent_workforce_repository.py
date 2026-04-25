"""Unit tests for AgentWorkforceRepository (M2).

Mocks Supabase async client/chain. Covers the contract corners that the
state machine and dispatcher depend on:
    - claim_next_unread  → CAS guard on status='unread'
    - claim_next_queued  → CAS guard on lifecycle_status='queued'
    - enqueue_inbox      → dedup collision falls back to lookup
    - create_task        → self-references root_task_id when tree root
    - update_task_status → terminal status sets ended_at
    - requeue_task       → only acts on assigned/in_progress
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.repositories.agent_workforce_repository import AgentWorkforceRepository


# ─── helpers ──────────────────────────────────────────────────────────


def _result(data: Any = None, count: int | None = None) -> Any:
    r = MagicMock()
    r.data = data
    r.count = count
    return r


def _exec_chain(returns: Any) -> MagicMock:
    """A query chain whose terminal .execute() returns `returns`."""
    chain = MagicMock()
    for op in ("select", "eq", "neq", "in_", "lt", "lte", "gt", "gte",
               "order", "limit", "range", "update", "insert", "upsert",
               "maybe_single", "single"):
        getattr(chain, op).return_value = chain
    chain.execute = AsyncMock(return_value=returns)
    return chain


def _make_repo(table_chains: dict[str, MagicMock]) -> AgentWorkforceRepository:
    """Build a repo whose _get_client returns a fake client mapping table()
    name → chain. If a table name isn't in the dict, returns a default chain
    that yields an empty list."""
    repo = AgentWorkforceRepository()

    async def _client():
        client = MagicMock()
        client.table.side_effect = lambda name: table_chains.get(
            name, _exec_chain(_result([]))
        )
        return client

    repo._get_client = _client  # type: ignore[method-assign]
    return repo


# ─── workers ──────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_upsert_worker_payload_idle_default():
    inserted: list[dict] = []

    chain = MagicMock()
    chain.execute = AsyncMock(return_value=_result([{"agent_id": str(uuid4())}]))

    def _upsert(payload, **kwargs):
        inserted.append(payload)
        return chain

    table_chain = MagicMock()
    table_chain.upsert = _upsert

    repo = _make_repo({"agent_workers": table_chain})
    agent_id = uuid4()
    out = await repo.upsert_worker(agent_id=agent_id, worker_pid=1234)

    assert out is not None
    assert inserted[0]["agent_id"] == str(agent_id)
    assert inserted[0]["state"] == "idle"
    assert inserted[0]["worker_pid"] == 1234


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_stale_workers_filters_active_states():
    captured = {"in_args": None, "lt_args": None}

    chain = MagicMock()

    def _select(*args, **kwargs):
        return chain

    def _in_(field, values):
        captured["in_args"] = (field, list(values))
        return chain

    def _lt(field, value):
        captured["lt_args"] = (field, value)
        return chain

    chain.select = _select
    chain.in_ = _in_
    chain.lt = _lt
    chain.execute = AsyncMock(return_value=_result([]))

    repo = _make_repo({"agent_workers": chain})
    stale_before = datetime.now(timezone.utc) - timedelta(minutes=2)
    await repo.list_stale_workers(stale_before=stale_before)

    field, values = captured["in_args"]
    assert field == "state"
    assert set(values) == {"idle", "working", "waiting_for_other"}


# ─── inbox ────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_claim_next_unread_uses_cas_guard():
    """Update must filter on status='unread' to prevent double-claim."""
    select_chain = _exec_chain(_result([{"id": str(uuid4())}]))
    update_chain = MagicMock()
    update_eq_calls: list[tuple[str, Any]] = []

    update_chain.update.return_value = update_chain

    def _eq(field, value):
        update_eq_calls.append((field, value))
        return update_chain

    update_chain.eq = _eq
    update_chain.execute = AsyncMock(
        return_value=_result([{"id": "msg", "status": "reading"}])
    )

    # First .table() call → select_chain; second → update_chain
    repo = AgentWorkforceRepository()
    call_count = {"n": 0}

    async def _client():
        client = MagicMock()

        def _table(name):
            call_count["n"] += 1
            return select_chain if call_count["n"] == 1 else update_chain

        client.table.side_effect = _table
        return client

    repo._get_client = _client  # type: ignore[method-assign]

    out = await repo.claim_next_unread(
        recipient_agent_id=uuid4(), claimed_by="hostA:42"
    )
    assert out is not None
    # The CAS guard: status='unread' must be in the eq filters of the update.
    assert ("status", "unread") in update_eq_calls


@pytest.mark.unit
@pytest.mark.asyncio
async def test_claim_next_unread_returns_none_on_empty_inbox():
    select_chain = _exec_chain(_result([]))
    repo = _make_repo({"agent_inbox": select_chain})
    out = await repo.claim_next_unread(
        recipient_agent_id=uuid4(), claimed_by="hostA:1"
    )
    assert out is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_enqueue_inbox_dedup_collision_falls_back_to_lookup():
    """Unique-violation on dedup_key → return the existing row."""
    insert_calls = {"n": 0}
    existing_row = {"id": str(uuid4()), "status": "unread"}

    insert_chain = MagicMock()
    insert_chain.insert.return_value = insert_chain
    insert_chain.execute = AsyncMock(side_effect=Exception("duplicate key value"))

    select_chain = _exec_chain(_result([existing_row]))

    repo = AgentWorkforceRepository()

    async def _client():
        client = MagicMock()

        def _table(name):
            insert_calls["n"] += 1
            return insert_chain if insert_calls["n"] == 1 else select_chain

        client.table.side_effect = _table
        return client

    repo._get_client = _client  # type: ignore[method-assign]

    out = await repo.enqueue_inbox(
        recipient_agent_id=uuid4(),
        sender_kind="user",
        message_type="task",
        payload={"x": 1},
        dedup_key="user-msg-001",
    )
    assert out == existing_row


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mark_inbox_processed_attaches_task_id():
    captured = {"payload": None}

    chain = MagicMock()
    chain.eq.return_value = chain
    chain.execute = AsyncMock(return_value=_result([{"id": "msg"}]))

    def _update(payload):
        captured["payload"] = payload
        return chain

    chain.update = _update
    repo = _make_repo({"agent_inbox": chain})
    task_id = uuid4()
    await repo.mark_inbox_processed(message_id=uuid4(), task_id=task_id)
    assert captured["payload"]["status"] == "processed"
    assert captured["payload"]["task_id"] == str(task_id)


# ─── tasks ────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_task_self_references_root_task_id():
    """When parent_task_id/root_task_id are not provided, the new task's
    root_task_id must be set to its own id."""
    new_id = str(uuid4())
    insert_chain = MagicMock()
    insert_chain.insert.return_value = insert_chain
    insert_chain.execute = AsyncMock(
        return_value=_result([{"id": new_id, "agent_id": str(uuid4())}])
    )

    update_chain = MagicMock()
    update_chain.update.return_value = update_chain
    update_chain.eq.return_value = update_chain
    update_chain.execute = AsyncMock(return_value=_result([{"id": new_id}]))

    repo = AgentWorkforceRepository()
    n = {"i": 0}

    async def _client():
        client = MagicMock()

        def _table(name):
            n["i"] += 1
            return insert_chain if n["i"] == 1 else update_chain

        client.table.side_effect = _table
        return client

    repo._get_client = _client  # type: ignore[method-assign]

    out = await repo.create_task(
        agent_id=uuid4(), user_id=uuid4(), payload={"prompt": "hi"}
    )
    assert out is not None
    assert out["root_task_id"] == new_id


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_task_preserves_explicit_root():
    new_id = str(uuid4())
    parent = str(uuid4())
    root = str(uuid4())

    chain = MagicMock()
    chain.insert.return_value = chain
    # Repo returns the row as-is; only self-references when root is empty.
    chain.execute = AsyncMock(
        return_value=_result(
            [{"id": new_id, "parent_task_id": parent, "root_task_id": root}]
        )
    )

    repo = _make_repo({"agent_tasks": chain})
    out = await repo.create_task(
        agent_id=uuid4(),
        user_id=uuid4(),
        payload={},
        parent_task_id=uuid4(),
        root_task_id=uuid4(),
    )
    assert out is not None
    assert out["root_task_id"] == root


@pytest.mark.unit
@pytest.mark.asyncio
async def test_claim_next_queued_uses_cas_guard():
    select_chain = _exec_chain(_result([{"id": str(uuid4())}]))
    update_chain = MagicMock()
    update_chain.update.return_value = update_chain
    eq_calls: list[tuple[str, Any]] = []

    def _eq(field, value):
        eq_calls.append((field, value))
        return update_chain

    update_chain.eq = _eq
    update_chain.execute = AsyncMock(return_value=_result([{"id": "task"}]))

    repo = AgentWorkforceRepository()
    n = {"i": 0}

    async def _client():
        client = MagicMock()

        def _table(name):
            n["i"] += 1
            return select_chain if n["i"] == 1 else update_chain

        client.table.side_effect = _table
        return client

    repo._get_client = _client  # type: ignore[method-assign]

    await repo.claim_next_queued(agent_id=uuid4())
    assert ("lifecycle_status", "queued") in eq_calls


@pytest.mark.unit
@pytest.mark.asyncio
async def test_update_task_status_terminal_sets_ended_at():
    captured = {"payload": None}

    chain = MagicMock()
    chain.eq.return_value = chain
    chain.execute = AsyncMock(return_value=_result([{"id": "t"}]))

    def _update(payload):
        captured["payload"] = payload
        return chain

    chain.update = _update
    repo = _make_repo({"agent_tasks": chain})

    for status in ("done", "failed", "cancelled"):
        await repo.update_task_status(task_id=uuid4(), lifecycle_status=status)
        assert captured["payload"]["lifecycle_status"] == status
        assert "ended_at" in captured["payload"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_update_task_status_in_progress_sets_started_at():
    captured = {"payload": None}

    chain = MagicMock()
    chain.eq.return_value = chain
    chain.execute = AsyncMock(return_value=_result([{"id": "t"}]))

    def _update(payload):
        captured["payload"] = payload
        return chain

    chain.update = _update
    repo = _make_repo({"agent_tasks": chain})
    await repo.update_task_status(task_id=uuid4(), lifecycle_status="in_progress")
    assert "started_at" in captured["payload"]
    assert "ended_at" not in captured["payload"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_requeue_task_only_acts_on_in_flight_states():
    in_calls: list[tuple[str, list]] = []

    chain = MagicMock()
    chain.update.return_value = chain
    chain.eq.return_value = chain

    def _in(field, values):
        in_calls.append((field, list(values)))
        return chain

    chain.in_ = _in
    chain.execute = AsyncMock(return_value=_result([{"id": "t"}]))

    repo = _make_repo({"agent_tasks": chain})
    await repo.requeue_task(uuid4())
    field, values = in_calls[0]
    assert field == "lifecycle_status"
    assert set(values) == {"assigned", "in_progress"}


# ─── outbox ───────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_undelivered_outbox_filters_delivered_false():
    eq_calls: list[tuple[str, Any]] = []

    chain = MagicMock()
    chain.select.return_value = chain
    chain.order.return_value = chain
    chain.limit.return_value = chain
    chain.execute = AsyncMock(return_value=_result([]))

    def _eq(field, value):
        eq_calls.append((field, value))
        return chain

    chain.eq = _eq
    repo = _make_repo({"agent_outbox": chain})
    await repo.list_undelivered_outbox(limit=10)
    assert ("delivered", False) in eq_calls

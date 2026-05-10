"""Unit tests for AgentWorkforceRepository (M2).

Mocks Supabase async client/chain. Covers the contract corners that the
state machine and dispatcher depend on:
    - claim_next_unread  → CAS guard on status='unread'
    - claim_next_queued  → CAS guard on phase='queued'
    - enqueue_inbox      → dedup collision falls back to lookup
    - create_task        → self-references root_task_id when tree root
    - update_task_status → terminal status sets completed_at
    - requeue_task       → only acts on assigned/in_progress

A4 (migration 200) note: tasks live in task_tracking[task_kind='agent_task'];
8-state lifecycle precision is preserved in the `phase` column while the
trigger/applayer-shared `status` column carries the 5-state mirror.
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
    for op in (
        "select",
        "eq",
        "neq",
        "in_",
        "lt",
        "lte",
        "gt",
        "gte",
        "order",
        "limit",
        "range",
        "update",
        "insert",
        "upsert",
        "maybe_single",
        "single",
    ):
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
    out = await repo.claim_next_unread(recipient_agent_id=uuid4(), claimed_by="hostA:1")
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
    """A4: when parent_task_id/root_task_id are not provided, root_task_id
    is computed before INSERT (= the new task's dbos_workflow_id). Single
    INSERT — no INSERT-then-UPDATE round-trip anymore."""
    captured: dict[str, Any] = {"insert": None}

    chain = MagicMock()

    def _insert(record):
        captured["insert"] = record
        return chain

    chain.insert = _insert
    # Server echoes back the row (Supabase default behaviour).
    chain.execute = AsyncMock(
        return_value=_result(
            [
                {
                    # dbos_workflow_id is the PK after migration 200; client
                    # generates it before INSERT, so the echo just reflects.
                    "dbos_workflow_id": None,  # filled below
                    "agent_id": str(uuid4()),
                    "phase": "queued",
                    "metadata": {},
                }
            ]
        )
    )

    repo = _make_repo({"task_tracking": chain})
    out = await repo.create_task(
        agent_id=uuid4(), user_id=uuid4(), payload={"prompt": "hi"}
    )
    assert out is not None
    assert captured["insert"] is not None
    inserted = captured["insert"]
    new_id = inserted["dbos_workflow_id"]
    assert new_id, "create_task must self-generate dbos_workflow_id"
    assert inserted["task_kind"] == "agent_task"
    assert inserted["root_task_id"] == new_id, "tree root self-references"
    assert inserted["parent_task_id"] is None
    assert inserted["status"] == "pending" and inserted["phase"] == "queued"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_task_preserves_explicit_root():
    captured: dict[str, Any] = {"insert": None}

    chain = MagicMock()

    def _insert(record):
        captured["insert"] = record
        return chain

    chain.insert = _insert
    chain.execute = AsyncMock(
        return_value=_result([{"dbos_workflow_id": "ignored", "phase": "queued"}])
    )

    repo = _make_repo({"task_tracking": chain})
    explicit_parent = uuid4()
    explicit_root = uuid4()
    out = await repo.create_task(
        agent_id=uuid4(),
        user_id=uuid4(),
        payload={},
        parent_task_id=explicit_parent,
        root_task_id=explicit_root,
    )
    assert out is not None
    inserted = captured["insert"]
    assert inserted["parent_task_id"] == str(explicit_parent)
    assert inserted["root_task_id"] == str(explicit_root)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_claim_next_queued_uses_cas_guard():
    """A4: CAS guard moved from lifecycle_status='queued' (agent_tasks) to
    phase='queued' (task_tracking) — phase column preserves the 8-state
    lifecycle precision after the merge."""
    select_chain = _exec_chain(
        _result([{"dbos_workflow_id": str(uuid4()), "metadata": {}}])
    )
    update_chain = MagicMock()
    update_chain.update.return_value = update_chain
    eq_calls: list[tuple[str, Any]] = []

    def _eq(field, value):
        eq_calls.append((field, value))
        return update_chain

    update_chain.eq = _eq
    update_chain.execute = AsyncMock(
        return_value=_result(
            [{"dbos_workflow_id": "task", "phase": "assigned", "metadata": {}}]
        )
    )

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
    # After A4: CAS guard is on phase column.
    assert ("phase", "queued") in eq_calls


def _update_chain_with_metadata_select(
    captured: dict[str, Any], existing_metadata: dict | None = None
) -> MagicMock:
    """Build a chain that handles update_task_status's SELECT(metadata) +
    UPDATE pattern. captured['payload'] receives the UPDATE record.

    update_task_status (post-A4) does:
      1. SELECT metadata FROM task_tracking WHERE … .maybe_single()
      2. UPDATE task_tracking SET … WHERE …
    Both calls hit the same chain in this fake (one-shot operations all
    return self), so we make .execute() yield the SELECT row first, then
    the UPDATE row on the second invocation.
    """
    chain = MagicMock()
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.maybe_single.return_value = chain
    chain.in_.return_value = chain

    def _update(record):
        captured["payload"] = record
        return chain

    chain.update = _update

    side_effects = iter(
        [
            _result({"metadata": existing_metadata or {}}),  # SELECT
            _result([{"dbos_workflow_id": "t", "phase": "queued"}]),  # UPDATE
        ]
    )

    async def _execute(*a, **kw):
        try:
            return next(side_effects)
        except StopIteration:
            return _result([{"dbos_workflow_id": "t"}])

    chain.execute = AsyncMock(side_effect=_execute)
    return chain


@pytest.mark.unit
@pytest.mark.asyncio
async def test_update_task_status_terminal_sets_completed_at():
    """A4: ended_at column was renamed to completed_at; task_tracking carries
    `status` (5-state mirror) and `phase` (8-state precision)."""
    for status in ("done", "failed", "cancelled"):
        captured: dict[str, Any] = {"payload": None}
        chain = _update_chain_with_metadata_select(captured)
        repo = _make_repo({"task_tracking": chain})
        await repo.update_task_status(task_id=uuid4(), lifecycle_status=status)
        payload = captured["payload"]
        assert payload is not None
        assert payload["phase"] == status
        # 5-state mirror: done→completed, failed→failed, cancelled→cancelled
        expected_status = {
            "done": "completed",
            "failed": "failed",
            "cancelled": "cancelled",
        }[status]
        assert payload["status"] == expected_status
        assert "completed_at" in payload


@pytest.mark.unit
@pytest.mark.asyncio
async def test_update_task_status_in_progress_sets_started_at():
    captured: dict[str, Any] = {"payload": None}
    chain = _update_chain_with_metadata_select(captured)
    repo = _make_repo({"task_tracking": chain})
    await repo.update_task_status(task_id=uuid4(), lifecycle_status="in_progress")
    payload = captured["payload"]
    assert payload["phase"] == "in_progress"
    assert payload["status"] == "processing"
    assert "started_at" in payload
    assert "completed_at" not in payload


@pytest.mark.unit
@pytest.mark.asyncio
async def test_requeue_task_only_acts_on_in_flight_states():
    """A4: the in-flight CAS guard moved from lifecycle_status to phase."""
    captured: dict[str, Any] = {"payload": None}
    chain = _update_chain_with_metadata_select(captured)
    in_calls: list[tuple[str, list]] = []

    def _in(field, values):
        in_calls.append((field, list(values)))
        return chain

    chain.in_ = _in

    repo = _make_repo({"task_tracking": chain})
    await repo.requeue_task(uuid4())
    field, values = in_calls[0]
    # After A4: filter on phase column (preserves 8-state precision).
    assert field == "phase"
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

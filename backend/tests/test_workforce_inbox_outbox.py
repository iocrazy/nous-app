"""Unit tests for InboxProcessor and OutboxDispatcher.

These cover the contract corners that the M2 dispatch loop depends on:
    - Inbox tick: spawn_task path creates task + transitions worker
    - Inbox tick: cancel/status_query bypass task creation
    - Outbox tick: user vs agent vs broadcast routing
    - Outbox tick: dedup_key set to outbox row id
"""

from __future__ import annotations

from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from app.services.workforce.inbox_processor import InboxProcessor
from app.services.workforce.outbox_dispatcher import OutboxDispatcher
from app.services.workforce.state_machine import (
    InvalidTransitionError,
)


# ─── shared fakes ────────────────────────────────────────────────────


def _stub_repo() -> MagicMock:
    repo = MagicMock()
    repo.claim_next_unread = AsyncMock(return_value=None)
    repo.create_task = AsyncMock(return_value=None)
    repo.mark_inbox_processed = AsyncMock(return_value=True)
    repo.update_task_status = AsyncMock(return_value=True)
    repo.enqueue_outbox = AsyncMock(return_value={"id": str(uuid4())})
    repo.enqueue_inbox = AsyncMock(return_value={"id": str(uuid4())})
    repo.mark_outbox_delivered = AsyncMock(return_value=True)
    repo.list_undelivered_outbox = AsyncMock(return_value=[])
    repo.get_worker = AsyncMock(return_value={"state": "idle"})
    repo.requeue_task = AsyncMock(return_value=True)
    return repo


def _stub_state_machine() -> MagicMock:
    sm = MagicMock()
    sm.transition = AsyncMock(return_value=MagicMock(to_state="working"))
    return sm


# ───────────────────────────────────────────────────────────────────
# InboxProcessor
# ───────────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_inbox_tick_spawns_task_and_transitions_worker():
    repo = _stub_repo()
    sm = _stub_state_machine()

    agent_id = uuid4()
    user_id = uuid4()
    msg_id = uuid4()
    task_id = uuid4()

    repo.claim_next_unread = AsyncMock(
        return_value={
            "id": str(msg_id),
            "message_type": "task",
            "sender_user_id": str(user_id),
            "payload": {"title": "do the thing", "prompt": "..."},
        }
    )
    repo.create_task = AsyncMock(
        return_value={"id": str(task_id), "agent_id": str(agent_id)}
    )

    processor = InboxProcessor(repo=repo, state_machine=sm)

    with patch.object(
        processor, "_agents_with_unread_messages",
        AsyncMock(return_value=[agent_id]),
    ):
        stats = await processor.tick()

    assert stats["tasks_created"] == 1
    repo.create_task.assert_awaited_once()
    repo.mark_inbox_processed.assert_awaited_once()
    sm.transition.assert_awaited_once()
    # The transition trigger must be task_assigned (not error / pause).
    kwargs = sm.transition.await_args.kwargs
    assert kwargs["trigger"] == "task_assigned"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_inbox_cancel_message_updates_task_no_state_transition():
    repo = _stub_repo()
    sm = _stub_state_machine()
    target_task = uuid4()
    repo.claim_next_unread = AsyncMock(
        return_value={
            "id": str(uuid4()),
            "message_type": "cancel",
            "sender_user_id": str(uuid4()),
            "payload": {"task_id": str(target_task)},
        }
    )
    processor = InboxProcessor(repo=repo, state_machine=sm)
    with patch.object(
        processor, "_agents_with_unread_messages",
        AsyncMock(return_value=[uuid4()]),
    ):
        stats = await processor.tick()

    repo.update_task_status.assert_awaited_once()
    update_kwargs = repo.update_task_status.await_args.kwargs
    assert update_kwargs["lifecycle_status"] == "cancelled"
    sm.transition.assert_not_called()
    assert stats["tasks_created"] == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_inbox_status_query_writes_outbox_response():
    repo = _stub_repo()
    sm = _stub_state_machine()
    user_id = uuid4()
    repo.claim_next_unread = AsyncMock(
        return_value={
            "id": str(uuid4()),
            "message_type": "status_query",
            "sender_user_id": str(user_id),
            "payload": {},
        }
    )
    processor = InboxProcessor(repo=repo, state_machine=sm)
    with patch.object(
        processor, "_agents_with_unread_messages",
        AsyncMock(return_value=[uuid4()]),
    ):
        await processor.tick()
    repo.enqueue_outbox.assert_awaited_once()
    out_kwargs = repo.enqueue_outbox.await_args.kwargs
    assert out_kwargs["recipient_kind"] == "user"
    assert out_kwargs["message_type"] == "status_response"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_inbox_invalid_transition_marks_blocked():
    """When SM rejects task_assigned (e.g. agent paused), worker → blocked."""
    repo = _stub_repo()
    sm = _stub_state_machine()
    sm.transition = AsyncMock(
        side_effect=[
            InvalidTransitionError(
                from_state="paused", to_state="working", trigger="task_assigned"
            ),
            MagicMock(to_state="blocked"),
        ]
    )
    repo.claim_next_unread = AsyncMock(
        return_value={
            "id": str(uuid4()),
            "message_type": "task",
            "sender_user_id": str(uuid4()),
            "payload": {},
        }
    )
    repo.create_task = AsyncMock(return_value={"id": str(uuid4())})

    processor = InboxProcessor(repo=repo, state_machine=sm)
    with patch.object(
        processor, "_agents_with_unread_messages",
        AsyncMock(return_value=[uuid4()]),
    ):
        await processor.tick()

    # Two attempts: first task_assigned, then error → blocked
    triggers = [c.kwargs["trigger"] for c in sm.transition.await_args_list]
    assert triggers == ["task_assigned", "error"]


# ───────────────────────────────────────────────────────────────────
# OutboxDispatcher
# ───────────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_outbox_user_recipient_only_marks_delivered():
    """User-channel delivery is a Realtime-side effect of the original
    INSERT; dispatch is just bookkeeping."""
    repo = _stub_repo()
    user_row = {
        "id": str(uuid4()),
        "recipient_kind": "user",
        "recipient_user_id": str(uuid4()),
        "sender_agent_id": str(uuid4()),
        "message_type": "notification",
        "payload": {},
    }
    repo.list_undelivered_outbox = AsyncMock(return_value=[user_row])

    disp = OutboxDispatcher(repo=repo)
    stats = await disp.tick()
    assert stats["delivered_user"] == 1
    repo.mark_outbox_delivered.assert_awaited_once()
    repo.enqueue_inbox.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_outbox_agent_recipient_writes_inbox_with_dedup_key():
    repo = _stub_repo()
    sender = uuid4()
    recipient = uuid4()
    out_id = uuid4()
    row = {
        "id": str(out_id),
        "recipient_kind": "agent",
        "recipient_agent_id": str(recipient),
        "sender_agent_id": str(sender),
        "message_type": "task",
        "payload": {"prompt": "summarise"},
    }
    repo.list_undelivered_outbox = AsyncMock(return_value=[row])

    disp = OutboxDispatcher(repo=repo)
    stats = await disp.tick()

    assert stats["delivered_agent"] == 1
    repo.enqueue_inbox.assert_awaited_once()
    in_kwargs = repo.enqueue_inbox.await_args.kwargs
    assert in_kwargs["recipient_agent_id"] == recipient
    assert in_kwargs["sender_kind"] == "agent"
    assert in_kwargs["sender_agent_id"] == sender
    assert in_kwargs["dedup_key"] == f"outbox:{out_id}"
    repo.mark_outbox_delivered.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_outbox_broadcast_fans_out_to_both_channels():
    repo = _stub_repo()
    row = {
        "id": str(uuid4()),
        "recipient_kind": "broadcast",
        "recipient_user_id": str(uuid4()),
        "recipient_agent_id": str(uuid4()),
        "sender_agent_id": str(uuid4()),
        "message_type": "notification",
        "payload": {},
    }
    repo.list_undelivered_outbox = AsyncMock(return_value=[row])

    disp = OutboxDispatcher(repo=repo)
    stats = await disp.tick()
    assert stats["delivered_user"] == 1
    assert stats["delivered_agent"] == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_outbox_unknown_message_type_falls_back_to_notification():
    repo = _stub_repo()
    row = {
        "id": str(uuid4()),
        "recipient_kind": "agent",
        "recipient_agent_id": str(uuid4()),
        "sender_agent_id": str(uuid4()),
        "message_type": "weird_custom_event",  # not in inbox CHECK set
        "payload": {},
    }
    repo.list_undelivered_outbox = AsyncMock(return_value=[row])
    disp = OutboxDispatcher(repo=repo)
    await disp.tick()
    in_kwargs = repo.enqueue_inbox.await_args.kwargs
    assert in_kwargs["message_type"] == "notification"

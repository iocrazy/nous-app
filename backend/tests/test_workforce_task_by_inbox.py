"""Tests for GET /api/v1/workforce/tasks/by-inbox/{inbox_message_id}.

This endpoint backs the chat sub-task cards' "live status" feature:
the Delegate tool returns ``inbox_message_id``, the frontend uses
this route + a Realtime subscription on agent_tasks to follow
queued → in_progress → done.

Auth contract: only the inbox sender (the user whose chat turn fired
Delegate) can read it. Agent-to-agent delegates are 403 here — they're
admin-only via the workforce drawer.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException


def _chain(data):
    """Build a chainable supabase mock that resolves to ``data``."""
    chain = MagicMock()
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.order.return_value = chain
    chain.limit.return_value = chain
    chain.maybe_single.return_value = chain
    chain.execute = AsyncMock(return_value=MagicMock(data=data))
    return chain


def _client_for(tables: dict[str, list]):
    """Build a fake supabase client whose ``table(name)`` returns a chain
    that yields the next entry from ``tables[name]`` per call.

    Each entry can be either a single row dict (maybe_single) or a list
    (multi-row queries). Using a list lets one test stage multiple
    queries on the same table in order.
    """
    client = MagicMock()
    iters = {name: iter(rows) for name, rows in tables.items()}

    def _table(name: str):
        try:
            data = next(iters[name])
        except (KeyError, StopIteration):
            data = None
        return _chain(data)

    client.table.side_effect = _table
    return client


@pytest.mark.asyncio
async def test_returns_task_and_outbox_when_done() -> None:
    """Happy path: task is done → response includes both task row and
    the sub-agent's outbox reply."""
    from app.api.workforce_router import get_task_by_inbox

    user_id = uuid4()
    inbox_id = uuid4()
    task_row = {
        "id": str(uuid4()),
        "agent_id": str(uuid4()),
        "lifecycle_status": "done",
        "started_at": "2026-04-26T10:00:00Z",
        "ended_at": "2026-04-26T10:00:24Z",
        "error_code": None,
        "error_message": None,
        "created_at": "2026-04-26T10:00:00Z",
        "inbox_message_id": str(inbox_id),
        "result": {"summary": "All done."},
    }
    outbox_row = {
        "id": str(uuid4()),
        "sender_agent_id": task_row["agent_id"],
        "message_type": "result",
        "payload": {"summary": "All done."},
        "created_at": "2026-04-26T10:00:24Z",
        "delivered": True,
        "delivered_at": "2026-04-26T10:00:24Z",
    }
    client = _client_for(
        {
            "agent_inbox": [
                # First call: lookup inbox row
                {
                    "id": str(inbox_id),
                    "recipient_agent_id": str(uuid4()),
                    "sender_kind": "user",
                    "sender_user_id": str(user_id),
                    "sender_agent_id": None,
                    "reply_to_message_id": None,
                },
            ],
            "agent_tasks": [task_row],
            "agent_outbox": [[outbox_row]],
        }
    )

    fake_user = SimpleNamespace(id=user_id)
    with patch(
        "app.api.workforce_router.get_async_supabase_admin",
        AsyncMock(return_value=client),
    ):
        out = await get_task_by_inbox(inbox_message_id=inbox_id, user=fake_user)

    assert out["inbox_message_id"] == str(inbox_id)
    assert out["task"]["lifecycle_status"] == "done"
    assert out["task"]["result"] == {"summary": "All done."}
    assert out["outbox_response"]["payload"] == {"summary": "All done."}


@pytest.mark.asyncio
async def test_no_task_yet_when_recipient_hasnt_ticked() -> None:
    """The Delegate just enqueued — no agent_tasks row exists yet.
    Endpoint returns task=None so the frontend keeps showing 'queued'."""
    from app.api.workforce_router import get_task_by_inbox

    user_id = uuid4()
    inbox_id = uuid4()
    client = _client_for(
        {
            "agent_inbox": [
                {
                    "id": str(inbox_id),
                    "recipient_agent_id": str(uuid4()),
                    "sender_kind": "user",
                    "sender_user_id": str(user_id),
                    "sender_agent_id": None,
                    "reply_to_message_id": None,
                },
            ],
            # agent_tasks lookup returns None
            "agent_tasks": [None],
        }
    )

    fake_user = SimpleNamespace(id=user_id)
    with patch(
        "app.api.workforce_router.get_async_supabase_admin",
        AsyncMock(return_value=client),
    ):
        out = await get_task_by_inbox(inbox_message_id=inbox_id, user=fake_user)

    assert out["task"] is None
    assert out["outbox_response"] is None


@pytest.mark.asyncio
async def test_outbox_skipped_when_task_in_progress() -> None:
    """Don't waste a query on outbox until the sub-agent finishes."""
    from app.api.workforce_router import get_task_by_inbox

    user_id = uuid4()
    inbox_id = uuid4()
    task_row = {
        "id": str(uuid4()),
        "agent_id": str(uuid4()),
        "lifecycle_status": "in_progress",
        "started_at": "2026-04-26T10:00:00Z",
        "ended_at": None,
        "error_code": None,
        "error_message": None,
        "created_at": "2026-04-26T10:00:00Z",
        "inbox_message_id": str(inbox_id),
        "result": None,
    }
    client = _client_for(
        {
            "agent_inbox": [
                {
                    "id": str(inbox_id),
                    "recipient_agent_id": str(uuid4()),
                    "sender_kind": "user",
                    "sender_user_id": str(user_id),
                    "sender_agent_id": None,
                    "reply_to_message_id": None,
                },
            ],
            "agent_tasks": [task_row],
        }
    )

    fake_user = SimpleNamespace(id=user_id)
    with patch(
        "app.api.workforce_router.get_async_supabase_admin",
        AsyncMock(return_value=client),
    ):
        out = await get_task_by_inbox(inbox_message_id=inbox_id, user=fake_user)

    assert out["task"]["lifecycle_status"] == "in_progress"
    assert out["outbox_response"] is None
    # agent_outbox table was never touched — table iterator only
    # consumed agent_inbox + agent_tasks.
    assert client.table.call_count == 2


@pytest.mark.asyncio
async def test_403_when_caller_not_sender() -> None:
    """Foreign user → 403 (NOT 404, because the inbox row exists; we just
    refuse to leak its lifecycle to a stranger)."""
    from app.api.workforce_router import get_task_by_inbox

    user_id = uuid4()
    other_user = uuid4()
    inbox_id = uuid4()
    client = _client_for(
        {
            "agent_inbox": [
                {
                    "id": str(inbox_id),
                    "recipient_agent_id": str(uuid4()),
                    "sender_kind": "user",
                    "sender_user_id": str(other_user),
                    "sender_agent_id": None,
                    "reply_to_message_id": None,
                },
            ],
        }
    )

    fake_user = SimpleNamespace(id=user_id)
    with patch(
        "app.api.workforce_router.get_async_supabase_admin",
        AsyncMock(return_value=client),
    ):
        with pytest.raises(HTTPException) as exc:
            await get_task_by_inbox(inbox_message_id=inbox_id, user=fake_user)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_403_when_agent_to_agent_delegate() -> None:
    """Agent-to-agent delegates have sender_user_id=None — those are
    only inspectable from the workforce admin drawer, not chat."""
    from app.api.workforce_router import get_task_by_inbox

    user_id = uuid4()
    inbox_id = uuid4()
    client = _client_for(
        {
            "agent_inbox": [
                {
                    "id": str(inbox_id),
                    "recipient_agent_id": str(uuid4()),
                    "sender_kind": "agent",
                    "sender_user_id": None,
                    "sender_agent_id": str(uuid4()),
                    "reply_to_message_id": None,
                },
            ],
        }
    )

    fake_user = SimpleNamespace(id=user_id)
    with patch(
        "app.api.workforce_router.get_async_supabase_admin",
        AsyncMock(return_value=client),
    ):
        with pytest.raises(HTTPException) as exc:
            await get_task_by_inbox(inbox_message_id=inbox_id, user=fake_user)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_404_when_inbox_message_does_not_exist() -> None:
    from app.api.workforce_router import get_task_by_inbox

    inbox_id = uuid4()
    client = _client_for({"agent_inbox": [None]})

    fake_user = SimpleNamespace(id=uuid4())
    with patch(
        "app.api.workforce_router.get_async_supabase_admin",
        AsyncMock(return_value=client),
    ):
        with pytest.raises(HTTPException) as exc:
            await get_task_by_inbox(inbox_message_id=inbox_id, user=fake_user)
    assert exc.value.status_code == 404

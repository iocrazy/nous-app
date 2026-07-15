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

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi import HTTPException


class _Mappings:
    """Result-mappings stand-in serving both .first() (single-row lookups)
    and .all() (multi-row queries) from one staged entry."""

    def __init__(self, data):
        self._data = data

    def first(self):
        if isinstance(self._data, list):
            return self._data[0] if self._data else None
        return self._data

    def all(self):
        if isinstance(self._data, list):
            return self._data
        return [self._data] if self._data is not None else []


class _Result:
    def __init__(self, data):
        self._data = data

    def mappings(self):
        return _Mappings(self._data)


def _read_scope_for(tables: dict[str, list], executed: list):
    """A ``read_scope()`` stand-in for get_task_by_inbox's ORM reads.

    Dispatches each executed statement to the staged rows for its table
    (matched by the table name rendered into the compiled SQL) and records
    the table name in ``executed`` so a test can assert how many queries ran
    (replacing the old ``client.table.call_count`` assertion)."""
    iters = {name: iter(rows) for name, rows in tables.items()}

    class _Session:
        async def execute(self, stmt):
            sql = str(stmt).lower()
            if "agent_inbox" in sql:
                name = "agent_inbox"
            elif "task_tracking" in sql:
                name = "task_tracking"
            elif "agent_outbox" in sql:
                name = "agent_outbox"
            else:  # pragma: no cover - defensive
                name = None
            executed.append(name)
            try:
                data = next(iters[name])
            except (KeyError, StopIteration):
                data = None
            return _Result(data)

    @asynccontextmanager
    async def _scope():
        yield _Session()

    return _scope


@pytest.mark.asyncio
async def test_returns_task_and_outbox_when_done() -> None:
    """Happy path: task is done → response includes both task row and
    the sub-agent's outbox reply."""
    from app.api.workforce_router import get_task_by_inbox

    user_id = uuid4()
    inbox_id = uuid4()
    # A4 (migration 200): task_tracking row shape (dbos_workflow_id PK,
    # phase column for 8-state, completed_at / error_msg renames, result
    # nested under metadata.agent_result).
    task_row = {
        "dbos_workflow_id": str(uuid4()),
        "agent_id": str(uuid4()),
        "phase": "done",
        "started_at": "2026-04-26T10:00:00Z",
        "completed_at": "2026-04-26T10:00:24Z",
        "error_code": None,
        "error_msg": None,
        "created_at": "2026-04-26T10:00:00Z",
        "inbox_message_id": str(inbox_id),
        "metadata": {"agent_result": {"summary": "All done."}},
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
    executed: list = []
    scope = _read_scope_for(
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
            "task_tracking": [task_row],
            "agent_outbox": [[outbox_row]],
        },
        executed,
    )

    fake_user = SimpleNamespace(id=user_id)
    import importlib

    wf_mod = importlib.import_module("app.api.workforce_router")

    with patch.object(wf_mod, "read_scope", scope):
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
    executed: list = []
    scope = _read_scope_for(
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
            # A4: task_tracking lookup returns None
            "task_tracking": [None],
        },
        executed,
    )

    fake_user = SimpleNamespace(id=user_id)
    import importlib

    wf_mod = importlib.import_module("app.api.workforce_router")

    with patch.object(wf_mod, "read_scope", scope):
        out = await get_task_by_inbox(inbox_message_id=inbox_id, user=fake_user)

    assert out["task"] is None
    assert out["outbox_response"] is None


@pytest.mark.asyncio
async def test_outbox_skipped_when_task_in_progress() -> None:
    """Don't waste a query on outbox until the sub-agent finishes."""
    from app.api.workforce_router import get_task_by_inbox

    user_id = uuid4()
    inbox_id = uuid4()
    # A4: task_tracking row shape.
    task_row = {
        "dbos_workflow_id": str(uuid4()),
        "agent_id": str(uuid4()),
        "phase": "in_progress",
        "started_at": "2026-04-26T10:00:00Z",
        "completed_at": None,
        "error_code": None,
        "error_msg": None,
        "created_at": "2026-04-26T10:00:00Z",
        "inbox_message_id": str(inbox_id),
        "metadata": {},
    }
    executed: list = []
    scope = _read_scope_for(
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
            "task_tracking": [task_row],
        },
        executed,
    )

    fake_user = SimpleNamespace(id=user_id)
    import importlib

    wf_mod = importlib.import_module("app.api.workforce_router")

    with patch.object(wf_mod, "read_scope", scope):
        out = await get_task_by_inbox(inbox_message_id=inbox_id, user=fake_user)

    assert out["task"]["lifecycle_status"] == "in_progress"
    assert out["outbox_response"] is None
    # agent_outbox was never queried — only agent_inbox + task_tracking ran.
    assert executed == ["agent_inbox", "task_tracking"]


@pytest.mark.asyncio
async def test_403_when_caller_not_sender() -> None:
    """Foreign user → 403 (NOT 404, because the inbox row exists; we just
    refuse to leak its lifecycle to a stranger)."""
    from app.api.workforce_router import get_task_by_inbox

    user_id = uuid4()
    other_user = uuid4()
    inbox_id = uuid4()
    executed: list = []
    scope = _read_scope_for(
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
        },
        executed,
    )

    fake_user = SimpleNamespace(id=user_id)
    import importlib

    wf_mod = importlib.import_module("app.api.workforce_router")

    with patch.object(wf_mod, "read_scope", scope):
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
    executed: list = []
    scope = _read_scope_for(
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
        },
        executed,
    )

    fake_user = SimpleNamespace(id=user_id)
    import importlib

    wf_mod = importlib.import_module("app.api.workforce_router")

    with patch.object(wf_mod, "read_scope", scope):
        with pytest.raises(HTTPException) as exc:
            await get_task_by_inbox(inbox_message_id=inbox_id, user=fake_user)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_404_when_inbox_message_does_not_exist() -> None:
    from app.api.workforce_router import get_task_by_inbox

    inbox_id = uuid4()
    executed: list = []
    scope = _read_scope_for({"agent_inbox": [None]}, executed)

    fake_user = SimpleNamespace(id=uuid4())
    import importlib

    wf_mod = importlib.import_module("app.api.workforce_router")

    with patch.object(wf_mod, "read_scope", scope):
        with pytest.raises(HTTPException) as exc:
            await get_task_by_inbox(inbox_message_id=inbox_id, user=fake_user)
    assert exc.value.status_code == 404

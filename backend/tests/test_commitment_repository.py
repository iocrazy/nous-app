"""Sprint 4 — CommitmentRepository unit tests (mock Supabase)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import pytest

from app.agent_framework.commitments import (
    Commitment,
    CommitmentStatus,
    TriggerType,
)
from app.repositories.commitment_repository import CommitmentRepository

# ─── Fake Supabase plumbing (mirrors test_agent_repository.py style) ──


class _FakeQuery:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self._data: Any = []
        # Chain access for `.not_.is_("col", "null")` — see Supabase client
        self.not_ = self  # noqa: A003

    def __getattr__(self, name: str):
        def _capture(*args: Any, **kwargs: Any) -> "_FakeQuery":
            self.calls.append((name, args, kwargs))
            return self

        return _capture

    async def execute(self) -> Any:
        class _R:
            data = self._data

        return _R()


class _FakeClient:
    def __init__(self, query: _FakeQuery) -> None:
        self._query = query

    def table(self, name: str) -> _FakeQuery:
        self._query.calls.append(("table", (name,), {}))
        return self._query


@pytest.fixture
def fake_query() -> _FakeQuery:
    return _FakeQuery()


@pytest.fixture
def repo(fake_query: _FakeQuery) -> CommitmentRepository:
    r = CommitmentRepository()

    async def _get_client():
        return _FakeClient(fake_query)

    r._get_client = _get_client  # type: ignore[method-assign]
    return r


_AGENT = str(uuid4())
_USER = str(uuid4())
_NOW = datetime(2026, 5, 2, 12, 0, 0, tzinfo=timezone.utc)


# ─── Translators ──────────────────────────────────────────────────────


def test_row_to_commitment_parses_iso_timestamps():
    row = {
        "id": 100,
        "agent_id": _AGENT,
        "user_id": _USER,
        "session_id": None,
        "description": "follow up",
        "payload_json": {"topic": "cooldown"},
        "trigger_type": "time",
        "trigger_at": "2026-05-09T12:00:00+00:00",
        "trigger_event": None,
        "status": "pending",
        "expires_at": None,
        "created_at": "2026-05-02T12:00:00Z",
        "fulfilled_at": None,
        "fulfillment_run_id": None,
        "fulfillment_notes": None,
    }
    c = CommitmentRepository._row_to_commitment(row)
    assert c.id == 100
    assert c.trigger_type == TriggerType.TIME
    assert c.status == CommitmentStatus.PENDING
    assert c.trigger_at == datetime(2026, 5, 9, 12, 0, 0, tzinfo=timezone.utc)
    assert c.created_at == datetime(2026, 5, 2, 12, 0, 0, tzinfo=timezone.utc)
    assert c.payload_json == {"topic": "cooldown"}


def test_commitment_to_insert_omits_server_managed_columns():
    c = Commitment(
        agent_id=_AGENT,
        user_id=_USER,
        description="x",
        trigger_type=TriggerType.TIME,
        trigger_at=_NOW,
    )
    payload = CommitmentRepository._commitment_to_insert(c)
    assert "id" not in payload
    assert "created_at" not in payload
    assert "fulfilled_at" not in payload
    assert payload["agent_id"] == _AGENT
    assert payload["user_id"] == _USER
    assert payload["trigger_type"] == "time"
    assert payload["status"] == "pending"
    assert payload["trigger_at"] == _NOW.isoformat()


# ─── Writes ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_inserts_and_returns_value_object(
    repo: CommitmentRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [
        {
            "id": 42,
            "agent_id": _AGENT,
            "user_id": _USER,
            "description": "x",
            "trigger_type": "next_session",
            "status": "pending",
            "payload_json": {},
            "created_at": "2026-05-02T12:00:00Z",
        }
    ]
    c = Commitment(
        agent_id=_AGENT,
        user_id=_USER,
        description="x",
        trigger_type=TriggerType.NEXT_SESSION,
    )
    result = await repo.create(c)
    assert result.id == 42
    assert result.status == CommitmentStatus.PENDING

    insert = next(c for c in fake_query.calls if c[0] == "insert")
    assert insert[1][0]["agent_id"] == _AGENT


@pytest.mark.asyncio
async def test_mark_fulfilled_sets_status_and_run_id(
    repo: CommitmentRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [
        {
            "id": 42,
            "agent_id": _AGENT,
            "description": "x",
            "trigger_type": "next_session",
            "status": "fulfilled",
            "payload_json": {},
            "fulfillment_run_id": "run-1",
        }
    ]
    result = await repo.mark_fulfilled(42, fulfillment_run_id="run-1")
    assert result is not None
    assert result.status == CommitmentStatus.FULFILLED

    update = next(c for c in fake_query.calls if c[0] == "update")
    payload = update[1][0]
    assert payload["status"] == "fulfilled"
    assert payload["fulfillment_run_id"] == "run-1"
    assert "fulfilled_at" in payload  # auto-stamped

    # Compare-and-swap: must filter by status='pending' to be idempotent
    eq_pairs = [c[1] for c in fake_query.calls if c[0] == "eq"]
    assert ("status", "pending") in eq_pairs
    assert ("id", 42) in eq_pairs


@pytest.mark.asyncio
async def test_mark_fulfilled_returns_none_when_not_pending(
    repo: CommitmentRepository, fake_query: _FakeQuery
) -> None:
    """Already-terminal row → no update happens → None returned."""
    fake_query._data = []
    result = await repo.mark_fulfilled(42)
    assert result is None


@pytest.mark.asyncio
async def test_mark_cancelled_does_not_set_fulfilled_at(
    repo: CommitmentRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [
        {
            "id": 42,
            "agent_id": _AGENT,
            "description": "x",
            "trigger_type": "next_session",
            "status": "cancelled",
            "payload_json": {},
        }
    ]
    await repo.mark_cancelled(42, notes="user dismissed")
    update = next(c for c in fake_query.calls if c[0] == "update")
    payload = update[1][0]
    assert payload["status"] == "cancelled"
    assert "fulfilled_at" not in payload
    assert payload["fulfillment_notes"] == "user dismissed"


@pytest.mark.asyncio
async def test_set_terminal_status_rejects_pending(
    repo: CommitmentRepository,
) -> None:
    """Internal helper guards against accidental misuse."""
    with pytest.raises(ValueError, match="non-terminal"):
        await repo._set_terminal_status(1, CommitmentStatus.PENDING)


# ─── Sweeper queries ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_due_time_filters_by_trigger_at(
    repo: CommitmentRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    await repo.list_due_time(now=_NOW, limit=50)
    eq_pairs = [c[1] for c in fake_query.calls if c[0] == "eq"]
    lte_pairs = [c[1] for c in fake_query.calls if c[0] == "lte"]
    assert ("status", "pending") in eq_pairs
    assert ("trigger_type", "time") in eq_pairs
    assert ("trigger_at", _NOW.isoformat()) in lte_pairs


@pytest.mark.asyncio
async def test_list_pending_event_filters_by_event_name(
    repo: CommitmentRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    await repo.list_pending_event("pr.merged:142")
    eq_pairs = [c[1] for c in fake_query.calls if c[0] == "eq"]
    assert ("trigger_type", "event") in eq_pairs
    assert ("trigger_event", "pr.merged:142") in eq_pairs


@pytest.mark.asyncio
async def test_list_next_session_filters_by_agent_and_user(
    repo: CommitmentRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    await repo.list_next_session(agent_id=_AGENT, user_id=_USER)
    eq_pairs = [c[1] for c in fake_query.calls if c[0] == "eq"]
    assert ("trigger_type", "next_session") in eq_pairs
    assert ("agent_id", _AGENT) in eq_pairs
    assert ("user_id", _USER) in eq_pairs


@pytest.mark.asyncio
async def test_list_for_user_optional_status_filter(
    repo: CommitmentRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    await repo.list_for_user(_USER, status=CommitmentStatus.FULFILLED)
    eq_pairs = [c[1] for c in fake_query.calls if c[0] == "eq"]
    assert ("user_id", _USER) in eq_pairs
    assert ("status", "fulfilled") in eq_pairs


@pytest.mark.asyncio
async def test_list_for_user_no_status_filter(
    repo: CommitmentRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    await repo.list_for_user(_USER)
    eq_pairs = [c[1] for c in fake_query.calls if c[0] == "eq"]
    statuses = [v for (k, v) in eq_pairs if k == "status"]
    assert statuses == []  # no status filter


@pytest.mark.asyncio
async def test_list_expired_pending_filters_by_expires_at(
    repo: CommitmentRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    later = _NOW + timedelta(hours=1)
    await repo.list_expired_pending(now=later)
    lte_pairs = [c[1] for c in fake_query.calls if c[0] == "lte"]
    assert ("expires_at", later.isoformat()) in lte_pairs

"""Sprint 4 — CommitmentRepository unit tests (ORM 2.0, mocked session).

Post-rollout the repo is the SQLAlchemy 2.0 implementation — reads go through
``read_scope()`` (``select``) and writes through ``write_scope()``
(``insert ... returning`` / ``update ... returning``). These tests mock those
scopes with a fake session that returns scripted ORM row objects (so
``_orm_obj_to_dict`` → ``_rest_row`` → the inherited ``_row_to_commitment``
builder yields a real ``Commitment``), covering create / terminal-status /
list behaviour and asserting the compiled SQL + binds — WITHOUT a live DB. The
DSN-gated integration suite in
``tests/integration/test_commitment_repository_orm.py`` exercises the real
round-trip. The pure translator tests (``_row_to_commitment`` /
``_commitment_to_insert``) stay client-free.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional
from uuid import uuid4

import pytest

import app.repositories.commitment_repository as mod
from app.agent_framework.commitments import (
    Commitment,
    CommitmentStatus,
    TriggerType,
)
from app.repositories.commitment_repository import CommitmentRepository

# ─── Fake ORM session plumbing (mirrors test_session_memory_repository.py) ──


class _FakeRow:
    """Stand-in agent_commitments ORM row. ``_orm_obj_to_dict`` reads mapped
    attributes off it by name, so exposing every DB column as an attribute is
    enough for parity."""

    def __init__(self, **cols: Any) -> None:
        defaults = {
            "id": 1,
            "agent_id": str(uuid4()),
            "user_id": None,
            "session_id": None,
            "description": "x",
            "payload_json": {},
            "trigger_type": "next_session",
            "trigger_at": None,
            "trigger_event": None,
            "expires_at": None,
            "status": "pending",
            "created_at": datetime(2026, 5, 2, 12, 0, tzinfo=timezone.utc),
            "fulfilled_at": None,
            "fulfillment_run_id": None,
            "fulfillment_notes": None,
        }
        for key, val in {**defaults, **cols}.items():
            setattr(self, key, val)


class _FakeScalars:
    def __init__(self, rows: List[_FakeRow]) -> None:
        self._rows = rows

    def first(self) -> Optional[_FakeRow]:
        return self._rows[0] if self._rows else None

    def all(self) -> List[_FakeRow]:
        return list(self._rows)


class _FakeResult:
    def __init__(self, rows: List[_FakeRow]) -> None:
        self._rows = rows

    def scalars(self) -> _FakeScalars:
        return _FakeScalars(self._rows)


class _FakeSession:
    """Returns a scripted row-list per execute() call (sequenced) and records
    the compiled SQL + bind params of every statement it runs."""

    def __init__(self, results: List[List[_FakeRow]]) -> None:
        self._results = list(results)
        self.statements: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, stmt) -> _FakeResult:
        compiled = stmt.compile()
        self.statements.append((str(compiled), dict(compiled.params)))
        rows = self._results.pop(0) if self._results else []
        return _FakeResult(rows)

    # convenience accessors on the last-executed statement
    @property
    def last_sql(self) -> str:
        return self.statements[-1][0]

    @property
    def last_params(self) -> dict[str, Any]:
        return self.statements[-1][1]


class _ScopeCM:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, *exc) -> bool:
        return False


def _patch_scopes(monkeypatch, session: _FakeSession) -> None:
    """Route both read_scope and write_scope to the same fake session."""
    monkeypatch.setattr(mod, "read_scope", lambda: _ScopeCM(session))
    monkeypatch.setattr(mod, "write_scope", lambda: _ScopeCM(session))


@pytest.fixture
def repo() -> CommitmentRepository:
    return CommitmentRepository()


_AGENT = str(uuid4())
_USER = str(uuid4())
_NOW = datetime(2026, 5, 2, 12, 0, 0, tzinfo=timezone.utc)


# ─── Translators (client-free) ────────────────────────────────────────


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
    repo: CommitmentRepository, monkeypatch
) -> None:
    session = _FakeSession(
        [[_FakeRow(id=42, agent_id=_AGENT, user_id=_USER, status="pending")]]
    )
    _patch_scopes(monkeypatch, session)

    c = Commitment(
        agent_id=_AGENT,
        user_id=_USER,
        description="x",
        trigger_type=TriggerType.NEXT_SESSION,
    )
    result = await repo.create(c)
    assert result.id == 42
    assert result.status == CommitmentStatus.PENDING

    # Compiled INSERT carries the agent_id in its bind params.
    sql, params = session.statements[-1]
    assert sql.startswith("INSERT INTO public.agent_commitments")
    assert params["agent_id"] == _AGENT


@pytest.mark.asyncio
async def test_create_raises_when_no_row(
    repo: CommitmentRepository, monkeypatch
) -> None:
    session = _FakeSession([[]])  # RETURNING yields no row
    _patch_scopes(monkeypatch, session)
    with pytest.raises(RuntimeError, match="no row"):
        await repo.create(
            Commitment(
                agent_id=_AGENT,
                description="x",
                trigger_type=TriggerType.NEXT_SESSION,
            )
        )


@pytest.mark.asyncio
async def test_mark_fulfilled_sets_status_and_run_id(
    repo: CommitmentRepository, monkeypatch
) -> None:
    session = _FakeSession([[_FakeRow(id=42, agent_id=_AGENT, status="fulfilled")]])
    _patch_scopes(monkeypatch, session)

    # fulfillment_run_id arrives as a numeric STR (REST cast str→bigint);
    # the ORM int()-coerces it for asyncpg's strict int8 codec.
    result = await repo.mark_fulfilled(42, fulfillment_run_id="777")
    assert result is not None
    assert result.status == CommitmentStatus.FULFILLED

    sql, params = session.statements[-1]
    assert sql.startswith("UPDATE public.agent_commitments")
    # SET status='fulfilled' + fulfilled_at auto-stamped + run id int-coerced.
    assert params["status"] == "fulfilled"
    assert params["fulfillment_run_id"] == 777
    assert isinstance(params["fulfillment_run_id"], int)
    assert "fulfilled_at" in params
    # Compare-and-swap: WHERE filters by id AND status='pending' (idempotent).
    assert 42 in params.values()
    assert "pending" in params.values()


@pytest.mark.asyncio
async def test_mark_fulfilled_returns_none_when_not_pending(
    repo: CommitmentRepository, monkeypatch
) -> None:
    """Already-terminal row → no matching pending row → None."""
    session = _FakeSession([[]])
    _patch_scopes(monkeypatch, session)
    result = await repo.mark_fulfilled(42)
    assert result is None


@pytest.mark.asyncio
async def test_mark_cancelled_does_not_set_fulfilled_at(
    repo: CommitmentRepository, monkeypatch
) -> None:
    session = _FakeSession([[_FakeRow(id=42, agent_id=_AGENT, status="cancelled")]])
    _patch_scopes(monkeypatch, session)

    await repo.mark_cancelled(42, notes="user dismissed")
    sql, params = session.statements[-1]
    assert sql.startswith("UPDATE public.agent_commitments")
    assert params["status"] == "cancelled"
    assert params["fulfillment_notes"] == "user dismissed"
    # No fulfilled_at stamp on the cancel path.
    assert "fulfilled_at" not in params
    # Still a compare-and-swap on pending.
    assert "pending" in params.values()


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
    repo: CommitmentRepository, monkeypatch
) -> None:
    session = _FakeSession([[]])
    _patch_scopes(monkeypatch, session)
    await repo.list_due_time(now=_NOW, limit=50)
    sql, params = session.statements[-1]
    assert sql.startswith("SELECT")
    assert "pending" in params.values()
    assert "time" in params.values()
    # v3: the cutoff is bound as a NATIVE aware datetime, never an ISO string.
    assert _NOW in params.values()


@pytest.mark.asyncio
async def test_list_pending_event_filters_by_event_name(
    repo: CommitmentRepository, monkeypatch
) -> None:
    session = _FakeSession([[]])
    _patch_scopes(monkeypatch, session)
    await repo.list_pending_event("pr.merged:142")
    _, params = session.statements[-1]
    assert "event" in params.values()
    assert "pr.merged:142" in params.values()


@pytest.mark.asyncio
async def test_list_next_session_filters_by_agent_and_user(
    repo: CommitmentRepository, monkeypatch
) -> None:
    session = _FakeSession([[]])
    _patch_scopes(monkeypatch, session)
    await repo.list_next_session(agent_id=_AGENT, user_id=_USER)
    _, params = session.statements[-1]
    assert "next_session" in params.values()
    assert _AGENT in params.values()
    assert _USER in params.values()


@pytest.mark.asyncio
async def test_list_for_user_optional_status_filter(
    repo: CommitmentRepository, monkeypatch
) -> None:
    session = _FakeSession([[]])
    _patch_scopes(monkeypatch, session)
    await repo.list_for_user(_USER, status=CommitmentStatus.FULFILLED)
    _, params = session.statements[-1]
    assert _USER in params.values()
    assert "fulfilled" in params.values()


@pytest.mark.asyncio
async def test_list_for_user_no_status_filter(
    repo: CommitmentRepository, monkeypatch
) -> None:
    session = _FakeSession([[]])
    _patch_scopes(monkeypatch, session)
    await repo.list_for_user(_USER)
    _, params = session.statements[-1]
    assert _USER in params.values()
    # No status filter → no CommitmentStatus value bound.
    assert not any(v in {s.value for s in CommitmentStatus} for v in params.values())


@pytest.mark.asyncio
async def test_list_expired_pending_filters_by_expires_at(
    repo: CommitmentRepository, monkeypatch
) -> None:
    session = _FakeSession([[]])
    _patch_scopes(monkeypatch, session)
    later = _NOW + timedelta(hours=1)
    await repo.list_expired_pending(now=later)
    sql, params = session.statements[-1]
    # v3: the cutoff is bound as a NATIVE aware datetime, never an ISO string.
    assert later in params.values()
    # expires_at IS NOT NULL guard present.
    assert "IS NOT NULL" in sql

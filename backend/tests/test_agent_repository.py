"""Unit tests for AgentRepository (SQLAlchemy 2.0 ORM, ai_agents + agent_skills).

Post-rollout the repository IS the SQLAlchemy 2.0 implementation — the legacy
supabase-py REST path was retired with USE_ORM_AGENTS. These tests mock
``read_scope``/``write_scope`` with a fake session that captures every emitted
``(compiled sql, binds)`` pair and returns configured ORM row objects, so the
compiled SQL shape + bind params AND the strategy-C value-type parity (ai_agents
uuid id → str, agent_skills.skill_id BIGINT → native int) are asserted WITHOUT a
live database (the DSN-gated integration suite in
``tests/integration/test_agent_repository_orm.py`` exercises the real
round-trip). Same fake-session shape as ``tests/test_skill_repository.py`` /
``tests/test_version_capture.py``.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

import app.repositories.agent_repository as mod
from app.models import AiAgents
from app.repositories.agent_repository import AgentRepository

# ─── ORM fake session ──────────────────────────────────────────────────


def _compile(stmt: Any) -> tuple[str, dict[str, Any]]:
    compiled = stmt.compile(dialect=postgresql.dialect())
    try:
        params = dict(compiled.params)
    except Exception:  # pragma: no cover - text() with unbound params
        params = {}
    return str(compiled), params


class _FakeScalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return list(self._rows)

    def first(self) -> Any:
        return self._rows[0] if self._rows else None


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> _FakeScalars:
        return _FakeScalars(self._rows)


class _FakeSession:
    """Captures execute (compiled sql, binds); returns configured ORM rows /
    scalar values. Successive statements pop the result queue if populated,
    otherwise fall back to the ``rows`` default."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.rows: list[Any] = []
        self._queue: list[list[Any]] = []

    def queue(self, rows: list[Any] | None = None) -> "_FakeSession":
        self._queue.append(rows or [])
        return self

    async def execute(self, stmt: Any, params: Any = None) -> _FakeResult:
        sql, binds = _compile(stmt)
        if params:
            binds = {**binds, **params}
        self.calls.append((sql, binds))
        if self._queue:
            return _FakeResult(self._queue.pop(0))
        return _FakeResult(self.rows)


class _ScopeCM:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, *exc: Any) -> bool:
        return False


@pytest.fixture
def fake_session(monkeypatch: pytest.MonkeyPatch) -> _FakeSession:
    session = _FakeSession()
    monkeypatch.setattr(mod, "read_scope", lambda: _ScopeCM(session))
    monkeypatch.setattr(mod, "write_scope", lambda: _ScopeCM(session))
    return session


@pytest.fixture
def repo() -> AgentRepository:
    return AgentRepository()


# ─── get_by_slug ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_by_slug_returns_none_for_missing(
    repo: AgentRepository, fake_session: _FakeSession
) -> None:
    """No matching row => None; the query filters on slug."""
    fake_session.rows = []
    result = await repo.get_by_slug("nonexistent_slug")
    assert result is None

    sql, binds = fake_session.calls[-1]
    assert sql.startswith("SELECT")
    assert "ai_agents.slug" in sql
    assert "nonexistent_slug" in binds.values()


@pytest.mark.asyncio
async def test_get_by_slug_returns_row_when_found(
    repo: AgentRepository, fake_session: _FakeSession
) -> None:
    agent_id = uuid4()
    fake_session.rows = [AiAgents(id=agent_id, slug="script_ai", name="Script AI")]
    result = await repo.get_by_slug("script_ai")
    assert result is not None
    assert result["slug"] == "script_ai"
    assert result["name"] == "Script AI"
    assert type(result["id"]) is str  # uuid → str (strategy-C parity)
    assert result["id"] == str(agent_id)


# ─── get_skill_ids ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_skill_ids_returns_empty_for_agent_with_no_skills(
    repo: AgentRepository, fake_session: _FakeSession
) -> None:
    """No binding rows => empty list; query filters on enabled + agent_id."""
    fake_session.rows = []
    agent_id = uuid4()
    skill_ids = await repo.get_skill_ids(agent_id)

    assert skill_ids == []
    sql, binds = fake_session.calls[-1]
    assert "agent_skills" in sql
    assert "enabled" in sql  # filters enabled IS true
    assert agent_id in binds.values()


@pytest.mark.asyncio
async def test_get_skill_ids_casts_bigint_rows_to_int(
    repo: AgentRepository, fake_session: _FakeSession
) -> None:
    """skill_id is BIGINT — make sure we coerce to int."""
    fake_session.rows = [100, 200, 300]
    skill_ids = await repo.get_skill_ids(uuid4())
    assert skill_ids == [100, 200, 300]
    assert all(isinstance(sid, int) for sid in skill_ids)


# ─── update_skill_bindings ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_skill_bindings_replaces_existing(
    repo: AgentRepository, fake_session: _FakeSession
) -> None:
    """Set-to-exactly semantics: upsert the desired rows (ON CONFLICT DO
    UPDATE), then delete any binding no longer desired."""
    agent_id = uuid4()

    # First call: bind skill=10.
    await repo.update_skill_bindings(agent_id, [10])
    # Second call: replace with [20].
    await repo.update_skill_bindings(agent_id, [20])

    # Each non-empty call issues an upsert INSERT + a prune DELETE.
    upserts = [c for c in fake_session.calls if "INSERT INTO" in c[0]]
    deletes = [c for c in fake_session.calls if c[0].startswith("DELETE")]
    assert len(upserts) == 2
    assert len(deletes) == 2

    # The upsert is concurrency-safe (ON CONFLICT DO UPDATE), not a plain insert.
    last_upsert_sql, last_upsert_binds = upserts[-1]
    assert "ON CONFLICT" in last_upsert_sql and "DO UPDATE" in last_upsert_sql
    # Last upsert carries skill_id=20 (replaced 10), sort_order=0, enabled, agent.
    # (Multi-row VALUES binds are suffixed _m0, so assert on values.)
    assert 20 in last_upsert_binds.values()
    assert agent_id in last_upsert_binds.values()
    assert 0 in last_upsert_binds.values()  # sort_order
    assert True in last_upsert_binds.values()  # enabled

    # The prune DELETE excludes the still-desired skill (skill_id NOT IN (...)).
    last_delete_sql, _ = deletes[-1]
    assert "NOT IN" in last_delete_sql


@pytest.mark.asyncio
async def test_update_skill_bindings_skips_insert_when_empty(
    repo: AgentRepository, fake_session: _FakeSession
) -> None:
    """Clearing bindings: delete-all only, no INSERT."""
    await repo.update_skill_bindings(uuid4(), [])

    sqls = [c[0] for c in fake_session.calls]
    assert any(s.startswith("DELETE") for s in sqls)
    assert not any("INSERT INTO" in s for s in sqls)


# ─── update_fields ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_fields_returns_first_row(
    repo: AgentRepository, fake_session: _FakeSession
) -> None:
    agent_id = uuid4()
    fake_session.rows = [AiAgents(id=agent_id, name="Renamed")]

    result = await repo.update_fields(agent_id, {"name": "Renamed"})
    assert result["name"] == "Renamed"
    assert result["id"] == str(agent_id)

    sql, binds = fake_session.calls[-1]
    assert "UPDATE" in sql and "ai_agents" in sql
    assert "RETURNING" in sql
    assert "Renamed" in binds.values()
    assert agent_id in binds.values()


@pytest.mark.asyncio
async def test_update_fields_returns_empty_dict_when_no_row(
    repo: AgentRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = []
    result = await repo.update_fields(uuid4(), {"name": "x"})
    assert result == {}

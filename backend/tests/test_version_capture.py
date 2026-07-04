"""Tests for the AgentRepository versioned repo methods.

These tests exercise the version-capture workflow: fetch the current row, diff
against incoming updates, insert old behavioral content into ai_agent_versions,
bump current_version on the live row.

Post-rollout the repository IS the SQLAlchemy 2.0 ORM implementation — the
legacy supabase-py REST path was retired with USE_ORM_AGENTS. These tests mock
``read_scope``/``write_scope`` with a fake session that captures every emitted
``(compiled sql, binds)`` pair and returns configured ORM row objects, so the
snapshot-then-update statement sequence is asserted WITHOUT a live database (the
DSN-gated integration suite in ``tests/integration/test_agent_repository_orm.py``
exercises the real round-trip). Same fake-session shape as
``tests/test_skill_repository.py``.

The SkillRepository versioned-write coverage lives in
``tests/test_skill_repository.py``.
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
    """Captures execute (compiled sql, binds); returns configured ORM rows.
    Successive statements pop the result queue if populated, otherwise fall
    back to the ``rows`` default."""

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


# ─── tests ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_agent_update_fields_versioned_snapshots_old_content(
    repo: AgentRepository, fake_session: _FakeSession
) -> None:
    """Update bumps version: inserts old content into ai_agent_versions
    with version_number=3 (prior current_version), patches live row with
    current_version=4 + new fields."""
    agent_id = uuid4()
    caller_id = uuid4()
    # 1st execute: SELECT current → the live row; snapshot INSERT + UPDATE follow.
    fake_session.queue(
        rows=[
            AiAgents(
                id=agent_id,
                identity_md="OLD identity",
                soul_md="OLD soul",
                agent_md="OLD agent",
                model="qwen-max",
                temperature=0.7,
                max_tokens=4000,
                current_version=3,
            )
        ]
    )

    await repo.update_fields_versioned(
        agent_id,
        {"identity_md": "NEW identity", "model": "qwen-plus"},
        created_by=caller_id,
    )

    # SELECT current, INSERT ai_agent_versions, UPDATE ai_agents.
    assert len(fake_session.calls) == 3
    assert fake_session.calls[0][0].startswith("SELECT")

    ins_sql, ins_binds = fake_session.calls[1]
    assert "INSERT INTO" in ins_sql and "ai_agent_versions" in ins_sql
    # Version snapshot captured the OLD content at the pre-bump version.
    assert ins_binds["agent_id"] == agent_id
    assert ins_binds["version_number"] == 3  # pre-bump value
    assert ins_binds["identity_md"] == "OLD identity"
    assert ins_binds["soul_md"] == "OLD soul"
    assert ins_binds["agent_md"] == "OLD agent"
    assert ins_binds["model"] == "qwen-max"
    assert ins_binds["temperature"] == 0.7
    assert ins_binds["max_tokens"] == 4000
    assert ins_binds["created_by"] == caller_id

    upd_sql, upd_binds = fake_session.calls[2]
    assert "UPDATE" in upd_sql and "ai_agents" in upd_sql
    # Live row patched with new content + bumped version.
    assert "NEW identity" in upd_binds.values()
    assert "qwen-plus" in upd_binds.values()
    assert 4 in upd_binds.values()  # current_version bumped to 4


@pytest.mark.asyncio
async def test_agent_update_fields_versioned_noop_when_no_tracked_change(
    repo: AgentRepository, fake_session: _FakeSession
) -> None:
    """If incoming updates don't actually change any field, no snapshot, no
    update (silences seed-loader on-startup reruns)."""
    agent_id = uuid4()
    fake_session.queue(
        rows=[
            AiAgents(
                id=agent_id,
                identity_md="same",
                soul_md="same",
                agent_md="same",
                model="qwen-max",
                temperature=0.7,
                max_tokens=4000,
                current_version=1,
            )
        ]
    )

    # Incoming equals live: the whole payload is a no-op.
    await repo.update_fields_versioned(
        agent_id,
        {"identity_md": "same", "model": "qwen-max"},
        created_by=None,
    )

    # Only the current-fetch SELECT — no snapshot INSERT, no UPDATE.
    assert len(fake_session.calls) == 1
    assert fake_session.calls[0][0].startswith("SELECT")


@pytest.mark.asyncio
async def test_agent_update_fields_versioned_writes_untracked_field_without_snapshot(
    repo: AgentRepository, fake_session: _FakeSession
) -> None:
    """Budget / paused_reason etc. are not in _VERSIONED_AGENT_FIELDS — they
    still need to land in the DB, they just shouldn't bump version history."""
    agent_id = uuid4()
    fake_session.queue(
        rows=[
            AiAgents(
                id=agent_id,
                identity_md="same",
                soul_md="same",
                agent_md="same",
                model="qwen-max",
                temperature=0.7,
                max_tokens=4000,
                monthly_token_budget=None,
                monthly_cost_cents_budget=None,
                paused_reason=None,
                current_version=3,
            )
        ]
    )

    # Budget-only change: no behavioral field moved, so no snapshot, but the
    # update MUST land so the cap takes effect.
    await repo.update_fields_versioned(
        agent_id,
        {"monthly_token_budget": 100_000},
        created_by=None,
    )

    # SELECT current + UPDATE — no version snapshot INSERT.
    assert len(fake_session.calls) == 2
    assert fake_session.calls[0][0].startswith("SELECT")

    upd_sql, upd_binds = fake_session.calls[1]
    assert "UPDATE" in upd_sql and "ai_agents" in upd_sql
    assert upd_binds["monthly_token_budget"] == 100_000
    # current_version should NOT be bumped on a non-tracked-field-only change.
    assert "current_version" not in upd_binds


@pytest.mark.asyncio
async def test_agent_update_fields_versioned_missing_row_raises(
    repo: AgentRepository, fake_session: _FakeSession
) -> None:
    """Row doesn't exist → ValueError (caller should 404 in the router)."""
    fake_session.rows = []  # SELECT current → None

    with pytest.raises(ValueError, match="not found"):
        await repo.update_fields_versioned(uuid4(), {"identity_md": "x"})

"""Compile-level coverage for the 3 Phase B1 rewrites that shipped without it
(review round 1, Important #2): ``issues_router.dispatch_issue``,
``pipeline_relay.RelayGateway.dispatch_issue`` (same ``dbos_workflow_id``
write shape as ``input_gate._clear_issue_lock``, already covered — lower
risk), and ``issue_messages_router._load_awaiting_marker`` (the one
genuinely novel operator choice in this batch: ``->`` via ``.op("->",
return_type=JSONB)`` rather than ``->>`` or the PG14+ bracket-subscript
default SQLAlchemy 2.0 would otherwise emit — the highest-risk equivalence
claim in the whole migration, per the report's own "等价性关键点 #3").

These don't hit a real database — they capture the compiled statement each
function hands to the session and assert the operator/WHERE clause survived
the raw-SQL → ORM rewrite, the same technique as
``tests/test_input_gate_orm.py`` / ``tests/test_pipeline_repository_cas_orm.py``.
"""

from __future__ import annotations

import importlib
from typing import Any, Optional
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from app.db import session as db_session
from app.services.infra import dbos_orchestrator
from app.services.issues import pipeline_relay


def _compile(stmt: Any) -> tuple[str, dict[str, Any]]:
    compiled = stmt.compile(dialect=postgresql.dialect())
    return str(compiled), dict(compiled.params)


class _FakeResult:
    def __init__(self, scalar: Any = None) -> None:
        self._scalar = scalar

    def scalar_one_or_none(self) -> Any:
        return self._scalar


class _FakeSession:
    """Captures every (compiled_sql, binds) pair handed to execute()."""

    def __init__(self, scalar: Any = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._scalar = scalar

    async def execute(self, stmt: Any) -> _FakeResult:
        self.calls.append(_compile(stmt))
        return _FakeResult(self._scalar)


class _ScopeCM:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, *exc: Any) -> bool:
        return False


def _all_sql(session: _FakeSession) -> str:
    return "\n".join(sql for sql, _ in session.calls)


def _all_binds(session: _FakeSession) -> list[Any]:
    values: list[Any] = []
    for _sql, params in session.calls:
        values.extend(params.values())
    return values


# ─── issues_router.dispatch_issue ──────────────────────────────────────────

# `app/api/__init__.py` rebinds the name `issues_router` to the APIRouter
# instance (shadowing the submodule) — load the actual module (same idiom
# as test_issues_dispatch_client.py).
issues_router = importlib.import_module("app.api.issues_router")


class _Auth:
    def __init__(self, user_id: str) -> None:
        self.user_id = user_id


async def test_dispatch_issue_writes_dbos_workflow_id_with_service_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = "11111111-1111-4111-8111-111111111111"
    issue_row = {
        "id": 4242,
        "created_by_user_id": user_id,
        "assignee_user_id": None,
        "team_id": None,
    }
    monkeypatch.setattr(
        issues_router.issue_repository, "get_by_id", AsyncMock(return_value=issue_row)
    )
    monkeypatch.setattr(dbos_orchestrator, "is_enabled", lambda: True)
    monkeypatch.setattr(
        issues_router, "_dispatch_execute_issue", AsyncMock(return_value=None)
    )

    session = _FakeSession()
    monkeypatch.setattr(db_session, "write_scope", lambda: _ScopeCM(session))

    class _FakeIssueModel:
        def model_validate(self, row: Any) -> Any:
            return row

    monkeypatch.setattr(issues_router, "Issue", _FakeIssueModel())

    await issues_router.dispatch_issue(4242, _Auth(user_id))

    sql = _all_sql(session)
    assert "SET LOCAL ROLE service_role" in sql
    assert "UPDATE public.issues SET dbos_workflow_id=" in sql
    assert "WHERE public.issues.id = " in sql
    assert 4242 in _all_binds(session)


# ─── pipeline_relay.RelayGateway.dispatch_issue ────────────────────────────


async def test_relay_gateway_dispatch_issue_writes_dbos_workflow_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dbos_orchestrator, "_client", None)
    monkeypatch.setattr(
        issues_router, "_dispatch_execute_issue", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(dbos_orchestrator, "is_enabled", lambda: True)

    session = _FakeSession()
    monkeypatch.setattr(db_session, "write_scope", lambda: _ScopeCM(session))

    wf_id = await pipeline_relay.RelayGateway().dispatch_issue(777)

    assert wf_id is not None and wf_id.startswith("issue-777-")
    sql = _all_sql(session)
    assert "SET LOCAL ROLE service_role" in sql
    assert "UPDATE public.issues SET dbos_workflow_id=" in sql
    assert "WHERE public.issues.id = " in sql
    binds = _all_binds(session)
    assert 777 in binds
    assert wf_id in binds


async def test_relay_gateway_dispatch_issue_noop_when_dbos_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DBOS disabled -> no write at all (best-effort short-circuit unchanged
    by the ORM rewrite)."""
    monkeypatch.setattr(dbos_orchestrator, "is_enabled", lambda: False)
    session = _FakeSession()
    monkeypatch.setattr(db_session, "write_scope", lambda: _ScopeCM(session))

    wf_id = await pipeline_relay.RelayGateway().dispatch_issue(778)

    assert wf_id is None
    assert session.calls == []


# ─── issue_messages_router._load_awaiting_marker ───────────────────────────

# `app.api.__init__` shadows this submodule name too (same trap as above).
issue_messages_router = importlib.import_module("app.api.issue_messages_router")


async def test_load_awaiting_marker_uses_arrow_not_double_arrow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The highest-risk operator choice in this batch: ``execution_state ->
    'awaiting_input'`` (JSONB-typed, auto-deserializing) must survive as a
    literal ``->``, NOT ``->>`` (text-extracting — would silently degrade
    every downstream ``isinstance(marker, dict)`` check to False) and NOT the
    PG14+ bracket-subscript form SQLAlchemy 2.0 emits by default for
    ``col['key']`` (functionally equivalent on PG14+, but a needless version
    dependency the source deliberately opted out of — see input_gate.py's
    module docstring "等价性关键点 #3")."""
    session = _FakeSession(scalar={"prompt": "which color?", "issue_id": 42})
    monkeypatch.setattr(db_session, "read_scope", lambda: _ScopeCM(session))

    marker = await issue_messages_router._load_awaiting_marker("wf-99")

    assert marker == {"prompt": "which color?", "issue_id": 42}
    sql = _all_sql(session)
    assert "execution_state -> " in sql
    assert "->>" not in sql
    assert "[" not in sql  # not the bracket-subscript form either
    assert "WHERE public.issues.dbos_workflow_id = " in sql
    binds = _all_binds(session)
    assert "awaiting_input" in binds
    assert "wf-99" in binds


async def test_load_awaiting_marker_returns_none_when_no_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession(scalar=None)
    monkeypatch.setattr(db_session, "read_scope", lambda: _ScopeCM(session))

    marker = await issue_messages_router._load_awaiting_marker("wf-100")
    assert marker is None


async def test_load_awaiting_marker_json_string_fallback_still_handled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defensive fallback preserved: if a caller (or a future driver change)
    ever hands back a JSON-encoded string instead of an already-deserialized
    dict, ``_load_awaiting_marker`` still parses it rather than returning the
    raw string."""
    session = _FakeSession(scalar='{"prompt": "x"}')
    monkeypatch.setattr(db_session, "read_scope", lambda: _ScopeCM(session))

    marker = await issue_messages_router._load_awaiting_marker("wf-101")
    assert marker == {"prompt": "x"}

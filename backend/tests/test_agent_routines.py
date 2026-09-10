"""Tests for agent routines (paperclip R1).

Covers:
- schedules_router: agent_routine payload validation at create
- scheduled_master._fire_agent_routine: skip_if_active gate, issue creation
  shape (origin_kind='routine', assignee), dispatch + last_issue_id stash

ORM (Phase B4): the budget-team lookup, previous-issue-status check, agent
lookup, dbos_workflow_id write (SET LOCAL ROLE service_role + ORM update),
and payload stash all moved from raw db_engine.fetch_val/fetch_one/execute/
execute_as_service_role calls to SQLAlchemy Core through
app.db.session.read_scope()/write_scope(). The harness patches both scopes
with one shared recording session (mirrors
tests/test_orm_b3_task1_compile_coverage.py) instead of the raw engine
helpers.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

import app.db.session as db_session

USER_ID = "11111111-1111-1111-1111-111111111111"


def _app() -> FastAPI:
    from app.api.schedules_router import router

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    from app.core.deps import get_auth

    class _FakeAuth:
        user_id = USER_ID
        email = "user@example.com"

    async def _grant():
        return _FakeAuth()

    app.dependency_overrides[get_auth] = _grant
    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_app())


# ─── router payload validation ──────────────────────────────────────────────


def _create_body(**payload_over: Any) -> Dict[str, Any]:
    return {
        "name": "Daily digest",
        "cron_expr": "0 9 * * *",
        "task_type": "agent_routine",
        "payload": {
            "agent_slug": "ceo",
            "prompt_md": "Summarize yesterday's downloads.",
            **payload_over,
        },
        "enabled": True,
    }


def test_create_routine_requires_agent_slug(client: TestClient) -> None:
    resp = client.post("/api/v1/schedules", json=_create_body(agent_slug="  "))
    assert resp.status_code == 400
    assert "agent_slug" in resp.json()["detail"]


def test_create_routine_requires_prompt(client: TestClient) -> None:
    resp = client.post("/api/v1/schedules", json=_create_body(prompt_md=""))
    assert resp.status_code == 400
    assert "prompt_md" in resp.json()["detail"]


def test_create_routine_rejects_unknown_policy(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/schedules", json=_create_body(delivery_policy="pile_up")
    )
    assert resp.status_code == 400
    assert "delivery_policy" in resp.json()["detail"]


# ─── _fire_agent_routine ────────────────────────────────────────────────────


def _routine_row(**payload_over: Any) -> Dict[str, Any]:
    return {
        "id": "sched-1",
        "user_id": USER_ID,
        "name": "Daily digest",
        "payload": {
            "agent_slug": "ceo",
            "prompt_md": "Summarize yesterday's downloads.",
            **payload_over,
        },
    }


class _FakeResult:
    def __init__(self, rows: list[Any] | None = None, scalar: Any = None) -> None:
        self._rows = rows if rows is not None else []
        self._scalar = scalar

    def mappings(self) -> "_FakeResult":
        return self

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def scalar(self) -> Any:
        return self._scalar


class _RecordingSession:
    def __init__(self, results: list[_FakeResult] | None = None) -> None:
        self.calls: list[Any] = []
        self._results = list(results or [])
        self._default = _FakeResult()

    async def execute(self, stmt: Any) -> _FakeResult:
        self.calls.append(stmt)
        return self._results.pop(0) if self._results else self._default


def _patch_scopes(
    monkeypatch: pytest.MonkeyPatch, results: list[_FakeResult] | None = None
) -> _RecordingSession:
    session = _RecordingSession(results)

    @asynccontextmanager
    async def fake_scope():
        yield session

    monkeypatch.setattr(db_session, "read_scope", fake_scope)
    monkeypatch.setattr(db_session, "write_scope", fake_scope)
    return session


def _compile(stmt: Any) -> tuple[str, dict[str, Any]]:
    compiled = stmt.compile(dialect=postgresql.dialect())
    return str(compiled), dict(compiled.params)


@pytest.mark.asyncio
async def test_fire_creates_issue_returns_order_and_stashes_last_issue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.workflows import scheduled_master as sm

    agent_id = str(uuid4())
    # [0] budget_team_id lookup (scalar None → is_team_over_budget(None)==False)
    # [1] agent lookup
    session = _patch_scopes(
        monkeypatch,
        [
            _FakeResult(scalar=None),
            _FakeResult(rows=[{"id": agent_id, "name": "CEO"}]),
        ],
    )
    created: Dict[str, Any] = {}

    async def _atomic_create(body: Dict[str, Any]) -> Dict[str, Any]:
        created.update(body)
        return {"id": 42, **body}

    repo = MagicMock()
    repo.atomic_create = AsyncMock(side_effect=_atomic_create)
    tracker = MagicMock()
    tracker.create = AsyncMock(return_value="task-1")

    with (
        patch("app.repositories.issue_repository.issue_repository", repo),
        patch(
            "app.services.infra.unified_task_manager.get_task_manager",
            lambda: tracker,
        ),
    ):
        order = await sm._fire_agent_routine(_routine_row())

    # Issue shape
    assert created["origin_kind"] == "routine"
    assert created["assignee_agent_id"] == agent_id
    assert created["created_by_user_id"] == USER_ID
    assert created["description"] == "Summarize yesterday's downloads."
    assert created["title"].startswith("Daily digest — ")
    assert "origin_fingerprint" not in created  # skip_if_active keeps 'default'
    # Returns a dispatch order with a pinned issue-42-* workflow id —
    # the WORKFLOW body performs the actual dispatch (never the step).
    assert order is not None
    assert order["issue_id"] == 42
    assert order["workflow_id"].startswith("issue-42-")
    assert order["sched_id"] == "sched-1"
    # workflow_id persisted onto the issue before dispatch — via
    # write_scope() + SET LOCAL ROLE service_role: the mig-170 allowlist
    # trigger blocks dbos_workflow_id writes from the app-role engine (raw
    # AND ORM repo).
    compiled_calls = [_compile(c) for c in session.calls]
    assert any("SET LOCAL ROLE service_role" in sql for sql, _ in compiled_calls)
    issue_updates = [
        (sql, binds) for sql, binds in compiled_calls if "public.issues" in sql
    ]
    assert len(issue_updates) == 1
    _sql, binds = issue_updates[0]
    assert binds == {"dbos_workflow_id": order["workflow_id"], "id_1": 42}
    # last_issue_id stashed back into the schedule payload (merge, never
    # replace — the payload also carries the routine's config).
    stash_calls = [
        (sql, binds) for sql, binds in compiled_calls if "public.user_schedules" in sql
    ]
    assert len(stash_calls) == 1
    _sql, binds = stash_calls[0]
    assert binds["payload"] == {
        "agent_slug": "ceo",
        "prompt_md": "Summarize yesterday's downloads.",
        "last_issue_id": 42,
    }
    # Task Center row pinned to the workflow id (mirror trigger does the rest)
    tracker.create.assert_awaited_once()
    tk = tracker.create.await_args.kwargs
    assert tk["task_type"] == "agent_routine"
    assert tk["dbos_workflow_id"] == order["workflow_id"]
    assert tk["metadata"]["issue_id"] == 42


@pytest.mark.asyncio
async def test_unique_violation_is_quiet_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    """The DB partial unique index is the second delivery gate: an open
    issue from a previous fire (whose payload stash was lost) must skip
    quietly, not error."""
    from app.workflows import scheduled_master as sm

    agent_id = str(uuid4())
    _patch_scopes(
        monkeypatch,
        [
            _FakeResult(scalar=None),  # budget lookup
            _FakeResult(rows=[{"id": agent_id, "name": "CEO"}]),  # agent lookup
        ],
    )
    repo = MagicMock()
    repo.atomic_create = AsyncMock(
        side_effect=Exception(
            "duplicate key value violates unique constraint "
            '"issues_open_routine_execution_uq"'
        )
    )

    with patch("app.repositories.issue_repository.issue_repository", repo):
        order = await sm._fire_agent_routine(_routine_row())

    assert order is None


@pytest.mark.asyncio
async def test_always_policy_uses_unique_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """policy=always legitimately allows several open issues per routine —
    a unique origin_fingerprint dodges issues_open_routine_execution_uq."""
    from app.workflows import scheduled_master as sm

    agent_id = str(uuid4())
    _patch_scopes(
        monkeypatch,
        [
            _FakeResult(scalar=None),  # budget lookup
            _FakeResult(rows=[{"id": agent_id, "name": "CEO"}]),  # agent lookup
        ],
    )
    created: Dict[str, Any] = {}

    async def _atomic_create(body: Dict[str, Any]) -> Dict[str, Any]:
        created.update(body)
        return {"id": 43, **body}

    repo = MagicMock()
    repo.atomic_create = AsyncMock(side_effect=_atomic_create)
    tracker = MagicMock()
    tracker.create = AsyncMock(return_value="task-1")

    with (
        patch("app.repositories.issue_repository.issue_repository", repo),
        patch(
            "app.services.infra.unified_task_manager.get_task_manager",
            lambda: tracker,
        ),
    ):
        order = await sm._fire_agent_routine(_routine_row(delivery_policy="always"))

    assert order is not None
    assert created.get("origin_fingerprint") not in (None, "", "default")


@pytest.mark.asyncio
async def test_dispatch_routine_orders_dispatches_and_records_failures() -> None:
    from app.workflows import scheduled_master as sm

    # _dispatch_execute_issue is async since phase 2b-2 (it writes the
    # dispatching marker before enqueueing), so the stub must be awaitable.
    dispatch = AsyncMock(
        side_effect=[None, RuntimeError("boom"), Exception("already exists")]
    )
    record = AsyncMock()
    counters: Dict[str, Any] = {"errors": 0}
    orders = [
        {"sched_id": "s1", "issue_id": 1, "workflow_id": "issue-1-a"},
        {"sched_id": "s2", "issue_id": 2, "workflow_id": "issue-2-b"},
        {"sched_id": "s3", "issue_id": 3, "workflow_id": "issue-3-c"},
    ]

    with (
        patch("app.api.issues_router._dispatch_execute_issue", dispatch),
        patch.object(sm, "record_routine_dispatch_error_step", record),
    ):
        await sm._dispatch_routine_orders(orders, counters)

    assert dispatch.await_count == 3
    assert dispatch.await_args_list[0].args == (1, "issue-1-a")
    # boom → recorded as schedule last_error; duplicate → soft success
    assert counters["errors"] == 1
    record.assert_awaited_once()
    assert record.await_args.args[0] == "s2"


@pytest.mark.asyncio
async def test_skip_if_active_skips_when_previous_issue_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.workflows import scheduled_master as sm

    _patch_scopes(
        monkeypatch,
        [
            _FakeResult(scalar=None),  # budget lookup
            _FakeResult(rows=[{"status": "in_progress"}]),  # previous issue status
        ],
    )
    repo = MagicMock()
    repo.atomic_create = AsyncMock()

    with patch("app.repositories.issue_repository.issue_repository", repo):
        await sm._fire_agent_routine(_routine_row(last_issue_id=41))

    repo.atomic_create.assert_not_awaited()


@pytest.mark.asyncio
async def test_always_policy_fires_even_with_open_previous_issue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.workflows import scheduled_master as sm

    agent_id = str(uuid4())
    # With policy=always the previous-issue status is never queried; the
    # first read_scope call after budget is the agent lookup.
    _patch_scopes(
        monkeypatch,
        [
            _FakeResult(scalar=None),  # budget lookup
            _FakeResult(rows=[{"id": agent_id, "name": "CEO"}]),  # agent lookup
        ],
    )
    repo = MagicMock()
    repo.atomic_create = AsyncMock(return_value={"id": 43})
    tracker = MagicMock()
    tracker.create = AsyncMock(return_value="task-1")

    with (
        patch("app.repositories.issue_repository.issue_repository", repo),
        patch(
            "app.services.infra.unified_task_manager.get_task_manager",
            lambda: tracker,
        ),
    ):
        await sm._fire_agent_routine(
            _routine_row(last_issue_id=41, delivery_policy="always")
        )

    repo.atomic_create.assert_awaited_once()

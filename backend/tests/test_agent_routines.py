"""Tests for agent routines (paperclip R1).

Covers:
- schedules_router: agent_routine payload validation at create
- scheduled_master._fire_agent_routine: skip_if_active gate, issue creation
  shape (origin_kind='routine', assignee), dispatch + last_issue_id stash
"""

from __future__ import annotations

from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.schedules_router import router

USER_ID = "11111111-1111-1111-1111-111111111111"


def _app() -> FastAPI:
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


@pytest.mark.asyncio
async def test_fire_creates_issue_dispatches_and_stashes_last_issue() -> None:
    from app.workflows import scheduled_master as sm

    agent_id = str(uuid4())
    fetch_one = AsyncMock(
        side_effect=[
            {"id": agent_id, "name": "CEO"},  # agent lookup
        ]
    )
    execute = AsyncMock()
    created: Dict[str, Any] = {}

    async def _atomic_create(body: Dict[str, Any]) -> Dict[str, Any]:
        created.update(body)
        return {"id": 42, **body}

    repo = MagicMock()
    repo.atomic_create = AsyncMock(side_effect=_atomic_create)
    dispatch = MagicMock()

    with (
        patch("app.db.engine.fetch_one", fetch_one),
        patch("app.db.engine.execute", execute),
        patch("app.repositories.issue_repository.issue_repository", repo),
        patch("app.api.issues_router._dispatch_execute_issue", dispatch),
    ):
        await sm._fire_agent_routine(_routine_row())

    # Issue shape
    assert created["origin_kind"] == "routine"
    assert created["assignee_agent_id"] == agent_id
    assert created["created_by_user_id"] == USER_ID
    assert created["description"] == "Summarize yesterday's downloads."
    assert created["title"].startswith("Daily digest — ")
    # Dispatched with a pinned issue-42-* workflow id
    dispatch.assert_called_once()
    args = dispatch.call_args.args
    assert args[0] == 42
    assert str(args[1]).startswith("issue-42-")
    # last_issue_id stashed back into the schedule payload (merge)
    stash_call = [c for c in execute.await_args_list if "user_schedules" in c.args[0]]
    assert stash_call, "expected payload stash UPDATE"
    import json

    merged = json.loads(stash_call[0].args[1]["p"])
    assert merged["last_issue_id"] == 42
    assert merged["agent_slug"] == "ceo"  # config preserved, not replaced


@pytest.mark.asyncio
async def test_skip_if_active_skips_when_previous_issue_open() -> None:
    from app.workflows import scheduled_master as sm

    fetch_one = AsyncMock(return_value={"status": "in_progress"})
    repo = MagicMock()
    repo.atomic_create = AsyncMock()

    with (
        patch("app.db.engine.fetch_one", fetch_one),
        patch("app.db.engine.execute", AsyncMock()),
        patch("app.repositories.issue_repository.issue_repository", repo),
    ):
        await sm._fire_agent_routine(_routine_row(last_issue_id=41))

    repo.atomic_create.assert_not_awaited()


@pytest.mark.asyncio
async def test_always_policy_fires_even_with_open_previous_issue() -> None:
    from app.workflows import scheduled_master as sm

    agent_id = str(uuid4())
    # With policy=always the previous-issue status is never queried; the
    # first fetch_one is the agent lookup.
    fetch_one = AsyncMock(return_value={"id": agent_id, "name": "CEO"})
    repo = MagicMock()
    repo.atomic_create = AsyncMock(return_value={"id": 43})

    with (
        patch("app.db.engine.fetch_one", fetch_one),
        patch("app.db.engine.execute", AsyncMock()),
        patch("app.repositories.issue_repository.issue_repository", repo),
        patch("app.api.issues_router._dispatch_execute_issue", MagicMock()),
    ):
        await sm._fire_agent_routine(
            _routine_row(last_issue_id=41, delivery_policy="always")
        )

    repo.atomic_create.assert_awaited_once()

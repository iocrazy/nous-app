"""Tests for the per-agent Dashboard endpoint.

GET /api/v1/ai-library/agents/{slug}/dashboard powers the AgentEditor
Dashboard tab — a Paperclip-style aggregate over the last 14 days
(scoped to the authenticated user, same as the Runs tab).

The endpoint does 5 table queries and buckets the results in Python;
these tests pin:
    - Slug not found → 404
    - 14-day series has exactly 14 entries (gaps filled with zeros)
    - Run activity / success rate / cost summary correctly bucket runs
    - tasks_by_status_14d counts lifecycle states
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app

BASE = "/api/v1/ai-library"
FAKE_USER_ID = str(uuid4())


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=FAKE_USER_ID, auth_type="jwt")


@pytest.fixture(autouse=True)
def _override_auth():
    app.dependency_overrides[get_auth] = _fake_auth
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _chain(data):
    """Build a chainable supabase mock returning ``data`` from execute()."""
    chain = MagicMock()
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.gte.return_value = chain
    chain.lte.return_value = chain
    chain.order.return_value = chain
    chain.range.return_value = chain
    chain.limit.return_value = chain
    chain.maybe_single.return_value = chain
    chain.execute = AsyncMock(return_value=MagicMock(data=data))
    return chain


def _client_for(tables: dict[str, list]):
    """Build a fake supabase client whose ``table(name)`` returns chains
    yielding successive entries from ``tables[name]``."""
    client = MagicMock()
    iters = {name: iter(rows) for name, rows in tables.items()}

    def _table(name: str):
        try:
            data = next(iters[name])
        except (KeyError, StopIteration):
            data = []
        return _chain(data)

    client.table.side_effect = _table
    return client


@pytest.mark.asyncio
async def test_dashboard_404_when_slug_missing(client: AsyncClient) -> None:
    with patch(
        "app.api.ai_library_router.AgentRepository",
    ) as mock_cls:
        mock_cls.return_value.get_by_slug = AsyncMock(return_value=None)
        resp = await client.get(f"{BASE}/agents/missing/dashboard")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_dashboard_happy_path_buckets_runs_and_tasks(
    client: AsyncClient,
) -> None:
    """One completed run + one failed run today + one queued task →
    response shape pins activity buckets, success rate, cost summary."""
    agent_id = str(uuid4())
    agent_row = {
        "id": agent_id,
        "slug": "ceo",
        "name": "CEO",
        "icon": None,
        "model": "qwen-max",
        "persistent": True,
        "paused_reason": None,
    }
    today_iso = datetime.now(timezone.utc).isoformat()
    today_date = today_iso[:10]

    runs_14d = [
        {
            "id": str(uuid4()),
            "status": "completed",
            "trigger": "automation",
            "model": "qwen-max",
            "started_at": today_iso,
            "ended_at": today_iso,
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "cost_cents": "1.5",  # mimic PostgREST string-numeric
        },
        {
            "id": str(uuid4()),
            "status": "failed",
            "trigger": "automation",
            "model": "qwen-max",
            "started_at": today_iso,
            "ended_at": today_iso,
            "prompt_tokens": 10,
            "completion_tokens": 0,
            "cost_cents": "0.5",
        },
    ]
    latest_run = runs_14d[0]
    # A4 (mig 200): agent_tasks merged into task_tracking with task_kind='agent_task'.
    # Mock data uses the new column shape (`phase` instead of `lifecycle_status`,
    # `dbos_workflow_id` as PK) so it travels through tt_row_to_task_shape() the
    # same way real rows do.
    tasks_14d = [
        {
            "dbos_workflow_id": str(uuid4()),
            "phase": "queued",
            "created_at": today_iso,
            "title": "Hire eng",
        },
        {
            "dbos_workflow_id": str(uuid4()),
            "phase": "done",
            "created_at": today_iso,
            "title": "Pick stack",
        },
        {
            "dbos_workflow_id": str(uuid4()),
            "phase": "done",
            "created_at": today_iso,
            "title": "Approve",
        },
    ]
    recent_tasks = tasks_14d[:5]
    recent_runs = runs_14d[:10]

    fake_client = _client_for(
        {
            "agent_runs": [
                runs_14d,  # 14d list
                [latest_run],  # latest 1
                recent_runs,  # recent 10
            ],
            "task_tracking": [
                tasks_14d,  # 14d list for status counts
                recent_tasks,  # recent 5
            ],
        }
    )

    with (
        patch("app.api.ai_library_router.AgentRepository") as mock_repo,
        patch(
            "app.api.ai_library_router.get_async_supabase_admin",
            AsyncMock(return_value=fake_client),
        ),
    ):
        mock_repo.return_value.get_by_slug = AsyncMock(
            return_value={**agent_row, "id": agent_id}
        )
        resp = await client.get(f"{BASE}/agents/ceo/dashboard")

    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Header
    assert body["agent"]["slug"] == "ceo"
    assert body["agent"]["model"] == "qwen-max"
    assert body["agent"]["persistent"] is True

    # Latest run banner
    assert body["latest_run"]["status"] == "completed"

    # 14-day daily series — fixed length, gaps filled with zeros
    assert len(body["run_activity_14d"]) == 14
    assert len(body["success_rate_14d"]) == 14

    # Today's bucket has both runs + 1 success
    today_activity = next(
        d for d in body["run_activity_14d"] if d["date"] == today_date
    )
    assert today_activity["count"] == 2
    today_success = next(d for d in body["success_rate_14d"] if d["date"] == today_date)
    assert today_success == {"date": today_date, "success": 1, "total": 2}

    # Costs summary
    assert body["costs_14d"]["prompt_tokens"] == 110
    assert body["costs_14d"]["completion_tokens"] == 50
    assert body["costs_14d"]["total_tokens"] == 160
    assert body["costs_14d"]["total_cost_cents"] == 2.0
    assert body["costs_14d"]["run_count"] == 2

    # Tasks by status: 1 queued, 2 done
    assert body["tasks_by_status_14d"] == {"queued": 1, "done": 2}

    # Recent collections
    assert len(body["recent_tasks"]) == 3
    assert len(body["recent_runs"]) == 2


@pytest.mark.asyncio
async def test_dashboard_empty_agent_returns_zero_buckets(
    client: AsyncClient,
) -> None:
    """Brand-new agent — no runs, no tasks. Endpoint must return
    valid shape (14 zero entries, empty maps) so the chart renders
    flat instead of crashing on undefined."""
    agent_id = str(uuid4())
    fake_client = _client_for(
        {
            "agent_runs": [[], [], []],  # 14d, latest, recent
            "task_tracking": [[], []],  # A4: was `agent_tasks` before mig 200
        }
    )

    with (
        patch("app.api.ai_library_router.AgentRepository") as mock_repo,
        patch(
            "app.api.ai_library_router.get_async_supabase_admin",
            AsyncMock(return_value=fake_client),
        ),
    ):
        mock_repo.return_value.get_by_slug = AsyncMock(
            return_value={
                "id": agent_id,
                "slug": "fresh",
                "name": "Fresh",
                "icon": None,
                "model": None,
                "persistent": False,
                "paused_reason": None,
            }
        )
        resp = await client.get(f"{BASE}/agents/fresh/dashboard")

    assert resp.status_code == 200
    body = resp.json()
    assert body["latest_run"] is None
    assert all(d["count"] == 0 for d in body["run_activity_14d"])
    assert body["tasks_by_status_14d"] == {}
    assert body["costs_14d"]["run_count"] == 0
    assert body["costs_14d"]["total_cost_cents"] == 0.0
    assert body["recent_tasks"] == []
    assert body["recent_runs"] == []

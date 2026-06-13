"""Tests for the agent header action endpoints (paperclip UI port P2):

- POST /api/v1/ai-library/agents/{slug}/pause — sets paused_reason='manual';
  400 when already paused; 403 for system presets; 404 when missing.
- GET /api/v1/ai-library/agents/{slug}/status — derived chip status:
  paused_reason → 'paused'; running agent_runs (caller-scoped) → 'running';
  else 'idle'.
"""

from __future__ import annotations

from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.ai_library_router import router

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


def _agent_row(**over: Any) -> Dict[str, Any]:
    return {
        "id": str(uuid4()),
        "slug": "my-agent",
        "name": "My Agent",
        "model": "qwen-max",
        "is_system_preset": False,
        "paused_reason": None,
        "skill_ids": [],
        "created_at": "2026-06-01T00:00:00Z",
        "updated_at": "2026-06-01T00:00:00Z",
        **over,
    }


def _patch_repos(agent: Dict[str, Any] | None):
    repo = AsyncMock()
    repo.get_by_slug = AsyncMock(return_value=agent)
    repo.get_skill_ids = AsyncMock(return_value=[])
    repo.update_fields = AsyncMock()
    return patch("app.api.ai_library_router._repos", return_value=(repo, None)), repo


def _running_count_client(count: int):
    """Fake supabase admin whose agent_runs count query returns `count`."""
    chain = MagicMock()
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.execute = AsyncMock(return_value=MagicMock(count=count))
    sb = MagicMock()
    sb.table.return_value = chain
    return sb


# ─── pause ──────────────────────────────────────────────────────────────────


def test_pause_sets_manual_reason(client: TestClient) -> None:
    agent = _agent_row()
    repos_patch, repo = _patch_repos(agent)
    # After update, get_by_slug returns the paused row.
    repo.get_by_slug = AsyncMock(
        side_effect=[agent, {**agent, "paused_reason": "manual"}]
    )
    with (
        repos_patch,
        patch(
            "app.api.ai_library_router._enrich_agents_with_scope_names",
            AsyncMock(side_effect=lambda rows: rows),
        ),
    ):
        resp = client.post("/api/v1/ai-library/agents/my-agent/pause")
    assert resp.status_code == 200, resp.text
    assert resp.json()["paused_reason"] == "manual"
    repo.update_fields.assert_awaited_once()
    assert repo.update_fields.await_args.args[1] == {"paused_reason": "manual"}


def test_pause_400_when_already_paused(client: TestClient) -> None:
    repos_patch, _ = _patch_repos(_agent_row(paused_reason="budget"))
    with repos_patch:
        resp = client.post("/api/v1/ai-library/agents/my-agent/pause")
    assert resp.status_code == 400


def test_pause_403_for_presets(client: TestClient) -> None:
    repos_patch, _ = _patch_repos(_agent_row(is_system_preset=True))
    with repos_patch:
        resp = client.post("/api/v1/ai-library/agents/my-agent/pause")
    assert resp.status_code == 403


def test_pause_404_when_missing(client: TestClient) -> None:
    repos_patch, _ = _patch_repos(None)
    with repos_patch:
        resp = client.post("/api/v1/ai-library/agents/nope/pause")
    assert resp.status_code == 404


# ─── status ─────────────────────────────────────────────────────────────────


def test_status_paused_short_circuits(client: TestClient) -> None:
    repos_patch, _ = _patch_repos(_agent_row(paused_reason="manual"))
    with repos_patch:
        resp = client.get("/api/v1/ai-library/agents/my-agent/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "paused"
    assert body["paused_reason"] == "manual"


def test_status_running_when_live_runs_exist(client: TestClient) -> None:
    repos_patch, _ = _patch_repos(_agent_row())
    with (
        repos_patch,
        patch(
            "app.api.ai_library_router.get_async_supabase_admin",
            AsyncMock(return_value=_running_count_client(2)),
        ),
    ):
        resp = client.get("/api/v1/ai-library/agents/my-agent/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert body["running_count"] == 2


def test_status_idle_when_no_live_runs(client: TestClient) -> None:
    repos_patch, _ = _patch_repos(_agent_row())
    with (
        repos_patch,
        patch(
            "app.api.ai_library_router.get_async_supabase_admin",
            AsyncMock(return_value=_running_count_client(0)),
        ),
    ):
        resp = client.get("/api/v1/ai-library/agents/my-agent/status")
    assert resp.status_code == 200
    assert resp.json()["status"] == "idle"


# ─── live runs strip ────────────────────────────────────────────────────────


def _live_runs_client(runs: list, agents: list):
    """table('agent_runs') → runs; table('ai_agents') → agents."""

    def _chain(data):
        c = MagicMock()
        c.select.return_value = c
        c.eq.return_value = c
        c.in_.return_value = c
        c.order.return_value = c
        c.limit.return_value = c
        c.execute = AsyncMock(return_value=MagicMock(data=data))
        return c

    sb = MagicMock()
    sb.table.side_effect = lambda name: _chain(runs if name == "agent_runs" else agents)
    return sb


def test_live_runs_enriched_with_agent_identity(client: TestClient) -> None:
    """R4: /runs/live returns caller's running runs with agent slug/name/icon
    stitched in and bigint ids str-coerced."""
    agent_id = str(uuid4())
    runs = [
        {
            "id": 315543034218506,  # bigint snowflake
            "agent_id": agent_id,
            "status": "running",
            "trigger": "visual_analysis_l1",
            "model": "doubao-seed-2-0-pro-260215",
            "started_at": "2026-06-11T00:00:00Z",
            "prompt_tokens": 100,
            "completion_tokens": 5,
            "cost_cents": "0.1",
            "input_summary": "L1 analysis of ...",
            "task_id": "wf-1",
        }
    ]
    agents = [
        {"id": agent_id, "slug": "test-analyze", "name": "Test Analyze", "icon": "bot"}
    ]
    with patch(
        "app.api.ai_library_router.get_async_supabase_admin",
        AsyncMock(return_value=_live_runs_client(runs, agents)),
    ):
        resp = client.get("/api/v1/ai-library/runs/live")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] == 1
    item = body["items"][0]
    assert item["id"] == "315543034218506"  # str, not precision-lost number
    assert item["agent_slug"] == "test-analyze"
    assert item["agent_name"] == "Test Analyze"
    assert item["cost_cents"] == 0.1


def test_live_runs_empty(client: TestClient) -> None:
    with patch(
        "app.api.ai_library_router.get_async_supabase_admin",
        AsyncMock(return_value=_live_runs_client([], [])),
    ):
        resp = client.get("/api/v1/ai-library/runs/live")
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "count": 0}

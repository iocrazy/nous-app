"""Content relay pipelines REST — team-boundary + run wiring (W2b).

Builds a FastAPI app around just the pipelines router, overrides auth, and
patches the pipeline_repository / issue_repository / start_pipeline_run seams
with AsyncMocks (matches test_tags_router.py's convention). Focus: the team is a
HARD boundary — a non-member gets 404 (never 403, never a leak), and the run
endpoint maps the relay errors to their status codes.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.pipelines_router import router
from app.core.deps import get_auth

USER_ID = "11111111-1111-1111-1111-111111111111"
AGENT_ID = "22222222-2222-2222-2222-222222222222"


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    return app


@pytest.fixture
def client() -> TestClient:
    app = _app()

    async def _auth():
        class _FakeAuth:
            user_id = USER_ID
            email = "u@example.com"

        return _FakeAuth()

    app.dependency_overrides[get_auth] = _auth
    c = TestClient(app)
    yield c
    app.dependency_overrides.clear()


def _pipeline_row() -> dict:
    return {
        "id": "500",
        "team_id": "7",
        "name": "Content Relay",
        "description": None,
        "enabled": True,
        "created_by_user_id": USER_ID,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "steps": [
            {
                "id": "1",
                "pipeline_id": "500",
                "step_order": 1,
                "agent_id": AGENT_ID,
                "title_template": "Step 1",
                "prompt_template": "Do it",
            }
        ],
    }


def test_list_requires_membership_404(client):
    with patch(
        "app.api.pipelines_router.issue_repository.is_team_member",
        new=AsyncMock(return_value=False),
    ):
        res = client.get("/api/v1/pipelines/?team_id=7")
    assert res.status_code == 404


def test_list_member_ok(client):
    with (
        patch(
            "app.api.pipelines_router.issue_repository.is_team_member",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.api.pipelines_router.pipeline_repository.list_pipelines",
            new=AsyncMock(return_value=[_pipeline_row()]),
        ),
    ):
        res = client.get("/api/v1/pipelines/?team_id=7")
    assert res.status_code == 200
    body = res.json()
    assert body[0]["id"] == "500"  # snowflake surfaced as string
    assert body[0]["steps"][0]["agent_id"] == AGENT_ID


def test_get_cross_team_is_404(client):
    with (
        patch(
            "app.api.pipelines_router.pipeline_repository.get_pipeline",
            new=AsyncMock(return_value=_pipeline_row()),
        ),
        patch(
            "app.api.pipelines_router.issue_repository.is_team_member",
            new=AsyncMock(return_value=False),
        ),
    ):
        res = client.get("/api/v1/pipelines/500")
    assert res.status_code == 404


def test_create_validates_membership(client):
    payload = {
        "team_id": 7,
        "name": "Relay",
        "steps": [
            {
                "step_order": 1,
                "agent_id": AGENT_ID,
                "title_template": "T",
                "prompt_template": "P",
            }
        ],
    }
    with patch(
        "app.api.pipelines_router.issue_repository.is_team_member",
        new=AsyncMock(return_value=False),
    ):
        res = client.post("/api/v1/pipelines/", json=payload)
    assert res.status_code == 404


def test_run_maps_conflict_to_409(client):
    from app.services.issues.pipeline_relay import PipelineConflict

    with (
        patch(
            "app.api.pipelines_router.pipeline_repository.get_pipeline",
            new=AsyncMock(return_value=_pipeline_row()),
        ),
        patch(
            "app.api.pipelines_router.issue_repository.is_team_member",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.api.pipelines_router.start_pipeline_run",
            new=AsyncMock(side_effect=PipelineConflict("already running")),
        ),
    ):
        res = client.post("/api/v1/pipelines/500/run", json={"parent_issue_id": 1000})
    assert res.status_code == 409


def test_run_success_returns_enriched_run(client):
    run = {
        "id": "900",
        "pipeline_id": "500",
        "parent_issue_id": "1000",
        "current_step": 1,
        "status": "running",
        "halted_reason": None,
        "started_by_user_id": USER_ID,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "completed_at": None,
    }
    with (
        patch(
            "app.api.pipelines_router.pipeline_repository.get_pipeline",
            new=AsyncMock(return_value=_pipeline_row()),
        ),
        patch(
            "app.api.pipelines_router.issue_repository.is_team_member",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.api.pipelines_router.start_pipeline_run",
            new=AsyncMock(return_value=run),
        ),
    ):
        res = client.post("/api/v1/pipelines/500/run", json={"parent_issue_id": 1000})
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == "900"
    assert body["pipeline_name"] == "Content Relay"
    assert body["total_steps"] == 1
    assert body["current_agent_id"] == AGENT_ID


# ── cancel run ────────────────────────────────────────────────────────────


def _running_run() -> dict:
    return {
        "id": "900",
        "pipeline_id": "500",
        "parent_issue_id": "1000",
        "current_step": 1,
        "status": "running",
        "halted_reason": None,
        "started_by_user_id": USER_ID,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "completed_at": None,
    }


def test_cancel_missing_run_is_404(client):
    with patch(
        "app.api.pipelines_router.pipeline_repository.get_run",
        new=AsyncMock(return_value=None),
    ):
        res = client.post("/api/v1/pipelines/runs/900/cancel")
    assert res.status_code == 404


def test_cancel_cross_team_is_404(client):
    # The run exists but its pipeline's team excludes the caller → 404, never a
    # 403 and never a leak that the run exists.
    with (
        patch(
            "app.api.pipelines_router.pipeline_repository.get_run",
            new=AsyncMock(return_value=_running_run()),
        ),
        patch(
            "app.api.pipelines_router.pipeline_repository.get_pipeline",
            new=AsyncMock(return_value=_pipeline_row()),
        ),
        patch(
            "app.api.pipelines_router.issue_repository.is_team_member",
            new=AsyncMock(return_value=False),
        ),
    ):
        res = client.post("/api/v1/pipelines/runs/900/cancel")
    assert res.status_code == 404


def test_cancel_non_running_returns_409(client):
    done = {**_running_run(), "status": "completed"}
    with (
        patch(
            "app.api.pipelines_router.pipeline_repository.get_run",
            new=AsyncMock(return_value=done),
        ),
        patch(
            "app.api.pipelines_router.pipeline_repository.get_pipeline",
            new=AsyncMock(return_value=_pipeline_row()),
        ),
        patch(
            "app.api.pipelines_router.issue_repository.is_team_member",
            new=AsyncMock(return_value=True),
        ),
    ):
        res = client.post("/api/v1/pipelines/runs/900/cancel")
    assert res.status_code == 409


def test_cancel_lost_race_returns_409(client):
    # The pre-check saw 'running' but the CAS returns None (another observer
    # terminalized the run first) → 409, not a false success.
    with (
        patch(
            "app.api.pipelines_router.pipeline_repository.get_run",
            new=AsyncMock(return_value=_running_run()),
        ),
        patch(
            "app.api.pipelines_router.pipeline_repository.get_pipeline",
            new=AsyncMock(return_value=_pipeline_row()),
        ),
        patch(
            "app.api.pipelines_router.issue_repository.is_team_member",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.api.pipelines_router.pipeline_repository.cancel_run",
            new=AsyncMock(return_value=None),
        ),
    ):
        res = client.post("/api/v1/pipelines/runs/900/cancel")
    assert res.status_code == 409


def test_cancel_success_posts_timeline_and_returns_cancelled(client):
    cancelled = {**_running_run(), "status": "cancelled"}
    post_msg = AsyncMock()
    with (
        patch(
            "app.api.pipelines_router.pipeline_repository.get_run",
            # 1st read: the running run; 2nd read: the cancelled row.
            new=AsyncMock(side_effect=[_running_run(), cancelled]),
        ),
        patch(
            "app.api.pipelines_router.pipeline_repository.get_pipeline",
            new=AsyncMock(return_value=_pipeline_row()),
        ),
        patch(
            "app.api.pipelines_router.issue_repository.is_team_member",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.api.pipelines_router.pipeline_repository.cancel_run",
            new=AsyncMock(return_value=cancelled),
        ),
        patch(
            "app.api.pipelines_router.issue_repository.get_by_id",
            new=AsyncMock(return_value={"id": "1000", "ai_session_id": None}),
        ),
        patch(
            "app.api.pipelines_router.RelayGateway.post_parent_message",
            new=post_msg,
        ),
    ):
        res = client.post("/api/v1/pipelines/runs/900/cancel")
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == "900"
    assert body["status"] == "cancelled"
    # a system line was written onto the parent issue's timeline
    assert post_msg.await_count == 1
    posted_body = post_msg.await_args.args[1]
    assert "cancelled" in posted_body.lower()

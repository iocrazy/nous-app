"""POST /ai-library/runs/{id}/fork + GET /ai-library/runs/{id}/forks (phase
2b-1). The service is stubbed: this pins the HTTP contract — 201 passthrough,
typed refusals as ``detail={code, message}``, validation, ownership."""

from __future__ import annotations

import importlib
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

router_mod = importlib.import_module("app.api.ai_library_router")
fork_mod = importlib.import_module("app.services.issues.issue_fork")
pytestmark = pytest.mark.unit
USER_ID = "11111111-1111-1111-1111-111111111111"
RUN_ID = "310819108761481"


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(router_mod.router, prefix="/api/v1")
    from app.core.deps import get_auth

    class _Auth:
        user_id = USER_ID
        email = "u@example.com"

    async def _grant():
        return _Auth()

    app.dependency_overrides[get_auth] = _grant
    return app


def test_fork_passes_the_body_through_and_returns_201():
    fork = AsyncMock(
        return_value={
            "run_id": None,
            "session_id": "200",
            "workflow_id": "wf-1",
            "issue_id": 9,
            "forked_from": {"run_id": int(RUN_ID), "at_seq": 4},
        }
    )
    with (
        patch.object(fork_mod, "fork_run", fork),
        patch.object(fork_mod, "default_deps", lambda: "DEPS"),
    ):
        r = TestClient(_app()).post(
            f"/api/v1/ai-library/runs/{RUN_ID}/fork",
            json={"at_seq": 4, "steer": "be darker"},
        )
    assert r.status_code == 201, r.text
    assert r.json()["session_id"] == "200" and r.json()["run_id"] is None
    fork.assert_awaited_once_with(
        int(RUN_ID), at_seq=4, steer="be darker", user_id=USER_ID, deps="DEPS"
    )


@pytest.mark.parametrize(
    "code, status",
    [
        ("run_live", 409),
        ("not_a_step_boundary", 400),
        ("run_state_unavailable", 503),
        ("not_found", 404),
    ],
)
def test_typed_refusals_keep_their_status_and_code(code, status):
    fork = AsyncMock(side_effect=fork_mod.ForkRejected(code, status, "why"))
    with (
        patch.object(fork_mod, "fork_run", fork),
        patch.object(fork_mod, "default_deps", lambda: None),
    ):
        r = TestClient(_app()).post(
            f"/api/v1/ai-library/runs/{RUN_ID}/fork", json={"at_seq": 4}
        )
    assert r.status_code == status
    assert r.json()["detail"] == {"code": code, "message": "why"}


def test_validation_at_seq_required_positive_and_steer_bounded():
    c = TestClient(_app())
    assert c.post(f"/api/v1/ai-library/runs/{RUN_ID}/fork", json={}).status_code == 422
    assert (
        c.post(f"/api/v1/ai-library/runs/{RUN_ID}/fork", json={"at_seq": 0}).status_code
        == 422
    )
    assert (
        c.post(
            f"/api/v1/ai-library/runs/{RUN_ID}/fork",
            json={"at_seq": 1, "steer": "x" * 4001},
        ).status_code
        == 422
    )


def test_forks_lists_the_repository_rows_with_string_run_ids():
    repo = AsyncMock()
    repo.get_by_id = AsyncMock(return_value={"id": int(RUN_ID)})
    repo.list_forks = AsyncMock(
        return_value=[
            {
                "id": 310819108761999,
                "fork_at_seq": 4,
                "created_at": "2026-09-09T10:00:00+00:00",
                "status": "completed",
            }
        ]
    )
    with patch.object(router_mod, "get_agent_runs_repository", return_value=repo):
        r = TestClient(_app()).get(f"/api/v1/ai-library/runs/{RUN_ID}/forks")
    assert r.status_code == 200, r.text
    assert r.json() == {
        "items": [
            {
                "run_id": "310819108761999",
                "at_seq": 4,
                "created_at": "2026-09-09T10:00:00+00:00",
                "status": "completed",
            }
        ]
    }
    repo.list_forks.assert_awaited_once_with(int(RUN_ID))


def test_fork_of_a_non_numeric_run_id_is_404_not_500():
    fork = AsyncMock()
    with patch.object(fork_mod, "fork_run", fork):
        r = TestClient(_app()).post(
            "/api/v1/ai-library/runs/not-a-run/fork", json={"at_seq": 4}
        )
    assert r.status_code == 404
    fork.assert_not_awaited()


def test_forks_of_a_foreign_run_is_404():
    repo = AsyncMock()
    repo.get_by_id = AsyncMock(return_value=None)
    with patch.object(router_mod, "get_agent_runs_repository", return_value=repo):
        r = TestClient(_app()).get(f"/api/v1/ai-library/runs/{RUN_ID}/forks")
    assert r.status_code == 404
    repo.list_forks.assert_not_awaited()

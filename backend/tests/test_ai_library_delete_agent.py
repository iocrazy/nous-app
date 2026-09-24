"""Tests for DELETE /api/v1/ai-library/agents/{slug} (2026-07-07).

User-owned agents can be deleted by their creator; system presets and other
users' agents are refused. Before this endpoint existed there was NO way to
delete an agent at all (create/edit/reset only) — test agents were immortal.

Since mig 501 the delete is SOFT (``ai_agents.deleted_at``) and is refused
with 409 ``agent_in_use`` while live routing still points at the agent —
the hard delete cascaded away the agent's runs AND other agents' Delegate
child runs, and silently reshaped pipelines.
"""

from __future__ import annotations

from typing import Any, Dict
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.ai_library_router import router

_OWNER = "11111111-1111-1111-1111-111111111111"


def _app_with_router() -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_app_with_router())


@pytest.fixture
def fake_auth():
    class _FakeAuth:
        user_id = _OWNER
        email = "user@example.com"

    return _FakeAuth()


def _install_auth_override(app: FastAPI, fake_auth) -> None:
    from app.core.deps import get_auth

    async def _grant():
        return fake_auth

    app.dependency_overrides[get_auth] = _grant


def _agent(**over: Any) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "id": str(uuid4()),
        "slug": "test-analyze",
        "name": "test-analyze",
        "is_system_preset": False,
        "user_id": _OWNER,
    }
    base.update(over)
    return base


def _repo(agent: Dict[str, Any] | None, deleted: bool = True) -> AsyncMock:
    repo = AsyncMock()
    repo.get_by_slug = AsyncMock(return_value=agent)
    repo.delete_agent = AsyncMock(return_value=deleted)
    return repo


def _refs(**counts: int):
    from app.repositories.agent_references import AgentLiveReferences

    return AgentLiveReferences(**counts)


def _do_delete(
    client: TestClient,
    fake_auth,
    repo: AsyncMock,
    slug: str = "test-analyze",
    refs=None,
):
    _install_auth_override(client.app, fake_auth)
    counter = AsyncMock(return_value=refs if refs is not None else _refs())
    with (
        patch("app.api.ai_library_router._repos", return_value=(repo, AsyncMock())),
        patch("app.api.ai_library_router.count_live_agent_references", counter),
    ):
        return client.delete(f"/api/v1/ai-library/agents/{slug}")


def test_owner_deletes_own_agent(client: TestClient, fake_auth) -> None:
    repo = _repo(_agent())
    resp = _do_delete(client, fake_auth, repo)
    assert resp.status_code == 204
    repo.delete_agent.assert_awaited_once()


def test_missing_agent_404(client: TestClient, fake_auth) -> None:
    resp = _do_delete(client, fake_auth, _repo(None))
    assert resp.status_code == 404


def test_system_preset_403(client: TestClient, fake_auth) -> None:
    repo = _repo(_agent(is_system_preset=True))
    resp = _do_delete(client, fake_auth, repo)
    assert resp.status_code == 403
    repo.delete_agent.assert_not_awaited()


def test_non_creator_403(client: TestClient, fake_auth) -> None:
    repo = _repo(_agent(user_id=str(uuid4())))
    resp = _do_delete(client, fake_auth, repo)
    assert resp.status_code == 403
    repo.delete_agent.assert_not_awaited()


def test_repo_failure_500(client: TestClient, fake_auth) -> None:
    repo = _repo(_agent(), deleted=False)
    resp = _do_delete(client, fake_auth, repo)
    assert resp.status_code == 500


# ── mig 501: refuse while live routing points at the agent ─────────────


def test_live_references_refuse_with_typed_409_and_counts(
    client: TestClient, fake_auth
) -> None:
    repo = _repo(_agent())
    resp = _do_delete(client, fake_auth, repo, refs=_refs(issues=2, running_runs=1))
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "agent_in_use"
    assert detail["counts"] == {
        "issues": 2,
        "pipeline_steps": 0,
        "stage_nodes": 0,
        "template_nodes": 0,
        "running_runs": 1,
    }
    assert detail["message"]
    repo.delete_agent.assert_not_awaited()


def test_every_reference_kind_alone_refuses(client: TestClient, fake_auth) -> None:
    for kind in (
        "issues",
        "pipeline_steps",
        "stage_nodes",
        "template_nodes",
        "running_runs",
    ):
        repo = _repo(_agent())
        resp = _do_delete(client, fake_auth, repo, refs=_refs(**{kind: 1}))
        assert resp.status_code == 409, kind
        repo.delete_agent.assert_not_awaited()


def test_production_envelope_carries_the_code_under_details(fake_auth) -> None:
    """Production wraps HTTPException in ErrorResponse; the frontend reads
    ``details.code``. Assert on that shape, not the bare FastAPI one."""
    from app.core.exceptions import register_exception_handlers

    app = _app_with_router()
    register_exception_handlers(app)
    resp = _do_delete(
        TestClient(app), fake_auth, _repo(_agent()), refs=_refs(pipeline_steps=3)
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["success"] is False
    assert body["details"]["code"] == "agent_in_use"
    assert body["details"]["counts"]["pipeline_steps"] == 3


def test_preset_refused_before_reference_count(client: TestClient, fake_auth) -> None:
    repo = _repo(_agent(is_system_preset=True))
    resp = _do_delete(client, fake_auth, repo, refs=_refs(issues=5))
    assert resp.status_code == 403


def test_reference_count_failure_is_500_and_deletes_nothing(fake_auth) -> None:
    """A guard that cannot count must not read as "no references". Pinned so a
    future ``except`` around the counter cannot quietly let deletes through."""
    from app.core.exceptions import register_exception_handlers

    app = _app_with_router()
    register_exception_handlers(app)
    _install_auth_override(app, fake_auth)
    repo = _repo(_agent())
    counter = AsyncMock(side_effect=RuntimeError("db down"))
    with (
        patch("app.api.ai_library_router._repos", return_value=(repo, AsyncMock())),
        patch("app.api.ai_library_router.count_live_agent_references", counter),
    ):
        resp = TestClient(app, raise_server_exceptions=False).delete(
            "/api/v1/ai-library/agents/test-analyze"
        )
    assert resp.status_code == 500
    repo.delete_agent.assert_not_awaited()

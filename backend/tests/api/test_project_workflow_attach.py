# backend/tests/api/test_project_workflow_attach.py
"""POST /projects/{id}/workflow — attach a workflow template to an EXISTING
project that has none yet (M1.x opt-in migration path: an alternative to a
lossy bulk legacy-SOP-to-template migration script). Repos/instantiation are
faked so these run without a DB; the gating (write access, already-has-a-
workflow 409, template-scope match) is the load-bearing behavior under test.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from app.core.deps import get_auth

# `verify_project_write_access` must be imported directly (not read off `pr`)
# because `Depends(verify_project_write_access)` in the route signature
# captured this exact function object at import time — monkeypatching the
# attribute on the router module afterwards would not reach FastAPI's
# already-built dependency graph. `app.dependency_overrides[<this object>]`
# is the correct override seam (same idiom as the `get_auth` override below).
from app.core.scope_guards import verify_project_write_access

# See test_workflow_templates_router.py for why this can't be a plain
# `from app.api import projects_router as pr` — app/api/__init__.py rebinds
# the `projects_router` attribute on the `app.api` package to the APIRouter
# instance itself, shadowing the submodule.
pr = importlib.import_module("app.api.projects_router")

USER_ID = "11111111-1111-1111-1111-111111111111"
OWNER_ID = "22222222-2222-2222-2222-222222222222"


class _AuthStub:
    user_id = USER_ID


@pytest.fixture
def app():
    application = FastAPI()
    # pr.router already declares prefix="/projects" — mounting it again under
    # "/api/v1/projects" would double it to /api/v1/projects/projects/...
    application.include_router(pr.router, prefix="/api/v1")

    async def _fake_auth():
        return _AuthStub()

    application.dependency_overrides[get_auth] = _fake_auth
    return application


def _allow_write(app):
    async def fake_guard(project_id, auth=None):
        return None

    app.dependency_overrides[verify_project_write_access] = fake_guard


@pytest.mark.asyncio
async def test_attach_workflow_denies_without_write_access(app):
    async def deny(project_id, auth=None):
        raise HTTPException(
            status_code=403, detail="You do not have access to this project"
        )

    app.dependency_overrides[verify_project_write_access] = deny

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.post(
            "/api/v1/projects/500/workflow", json={"template_id": "tpl-1"}
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_attach_workflow_409s_when_project_already_has_nodes(app, monkeypatch):
    _allow_write(app)

    async def fake_get_project_by_id(self, project_id):
        return {"id": "500", "owner_id": OWNER_ID, "team_id": "42"}

    async def fake_list_nodes(self, project_id):
        return [{"id": "n1", "name": "Existing node"}]

    async def fail_instantiate(*args, **kwargs):
        raise AssertionError("must not instantiate when the project already has nodes")

    monkeypatch.setattr(
        "app.repositories.projects_repository.ProjectsRepository.get_project_by_id",
        fake_get_project_by_id,
    )
    monkeypatch.setattr(
        "app.repositories.project_stage_nodes_repository.ProjectStageNodesRepository.list_nodes",
        fake_list_nodes,
    )
    monkeypatch.setattr(
        "app.services.workflow.instantiation.instantiate_project_workflow",
        fail_instantiate,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.post(
            "/api/v1/projects/500/workflow", json={"template_id": "tpl-1"}
        )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_attach_workflow_404s_when_template_belongs_to_another_team(
    app, monkeypatch
):
    _allow_write(app)

    async def fake_get_project_by_id(self, project_id):
        return {"id": "500", "owner_id": OWNER_ID, "team_id": "42"}

    async def fake_list_nodes(self, project_id):
        return []

    async def fake_get_template_team_id(self, template_id):
        return "99"  # a different team than the project's "42"

    async def fail_instantiate(*args, **kwargs):
        raise AssertionError("must not instantiate across a team boundary")

    monkeypatch.setattr(
        "app.repositories.projects_repository.ProjectsRepository.get_project_by_id",
        fake_get_project_by_id,
    )
    monkeypatch.setattr(
        "app.repositories.project_stage_nodes_repository.ProjectStageNodesRepository.list_nodes",
        fake_list_nodes,
    )
    monkeypatch.setattr(
        "app.repositories.workflow_templates_repository.WorkflowTemplatesRepository.get_template_team_id",
        fake_get_template_team_id,
    )
    monkeypatch.setattr(
        "app.services.workflow.instantiation.instantiate_project_workflow",
        fail_instantiate,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.post(
            "/api/v1/projects/500/workflow", json={"template_id": "tpl-1"}
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_attach_workflow_happy_path_team_project(app, monkeypatch):
    _allow_write(app)

    async def fake_get_project_by_id(self, project_id):
        return {"id": "500", "owner_id": OWNER_ID, "team_id": "42"}

    async def fake_list_nodes(self, project_id):
        return []

    async def fake_get_template_team_id(self, template_id):
        assert template_id == "tpl-1"
        return "42"

    async def fake_instantiate(project_id, template_id, *, method=None, user_id=None):
        assert project_id == "500"
        assert template_id == "tpl-1"
        assert method == "hybrid"
        assert user_id == USER_ID
        return [{"id": "n1", "name": "Script"}]

    monkeypatch.setattr(
        "app.repositories.projects_repository.ProjectsRepository.get_project_by_id",
        fake_get_project_by_id,
    )
    monkeypatch.setattr(
        "app.repositories.project_stage_nodes_repository.ProjectStageNodesRepository.list_nodes",
        fake_list_nodes,
    )
    monkeypatch.setattr(
        "app.repositories.workflow_templates_repository.WorkflowTemplatesRepository.get_template_team_id",
        fake_get_template_team_id,
    )
    monkeypatch.setattr(
        "app.services.workflow.instantiation.instantiate_project_workflow",
        fake_instantiate,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.post(
            "/api/v1/projects/500/workflow",
            json={"template_id": "tpl-1", "method": "hybrid"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"] == [{"id": "n1", "name": "Script"}]


@pytest.mark.asyncio
async def test_attach_workflow_personal_project_resolves_owner_personal_team(
    app, monkeypatch
):
    _allow_write(app)

    async def fake_get_project_by_id(self, project_id):
        return {"id": "501", "owner_id": OWNER_ID, "team_id": None}

    async def fake_list_nodes(self, project_id):
        return []

    async def fake_get_personal_team_id(self, owner_id):
        assert owner_id == OWNER_ID
        return "7777"

    async def fake_get_template_team_id(self, template_id):
        return "7777"

    async def fake_instantiate(project_id, template_id, *, method=None, user_id=None):
        return [{"id": "n1", "name": "Script"}]

    monkeypatch.setattr(
        "app.repositories.projects_repository.ProjectsRepository.get_project_by_id",
        fake_get_project_by_id,
    )
    monkeypatch.setattr(
        "app.repositories.project_stage_nodes_repository.ProjectStageNodesRepository.list_nodes",
        fake_list_nodes,
    )
    monkeypatch.setattr(
        "app.repositories.team_repository.TeamRepository.get_personal_team_id",
        fake_get_personal_team_id,
    )
    monkeypatch.setattr(
        "app.repositories.workflow_templates_repository.WorkflowTemplatesRepository.get_template_team_id",
        fake_get_template_team_id,
    )
    monkeypatch.setattr(
        "app.services.workflow.instantiation.instantiate_project_workflow",
        fake_instantiate,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.post(
            "/api/v1/projects/501/workflow", json={"template_id": "tpl-1"}
        )
    assert resp.status_code == 200
    assert resp.json()["data"] == [{"id": "n1", "name": "Script"}]

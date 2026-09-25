# backend/tests/api/test_workflow_templates_router.py
"""GET /workflows team_id-optional personal-team resolution (M1.x — the
personal-project workflow gap: CreateProjectModal never had a real team_id
to pass for a personal project, so the endpoint required one and the picker
never rendered). The repo/seeder calls are faked so these run without a DB;
the resolution logic (team_id supplied vs. omitted → personal team) is the
load-bearing behavior under test.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.deps import get_auth

# NOT `from app.api import workflow_templates_router as wtr` — app/api/__init__.py
# does `from app.api.workflow_templates_router import router as workflow_templates_router`,
# which rebinds that very name on the `app.api` package to the APIRouter instance
# and shadows the submodule (unlike sibling routers imported there under a
# `_foo_router` alias). `importlib` reaches the submodule directly via
# sys.modules, bypassing the package namespace's rebinding.
wtr = importlib.import_module("app.api.workflow_templates_router")

USER_ID = "11111111-1111-1111-1111-111111111111"


def _template_row(tpl_id: str, name: str, node_count: int) -> dict:
    """A list row in the repository's real shape (``_template_row``)."""
    return {
        "id": tpl_id,
        "team_id": "9000",
        "name": name,
        "is_default": False,
        "created_by": USER_ID,
        "created_at": "2026-09-24T01:02:03.456789+00:00",
        "updated_at": "2026-09-24T01:02:03.456789+00:00",
        "node_count": node_count,
    }


class _AuthStub:
    user_id = USER_ID


@pytest.fixture
def app():
    application = FastAPI()
    application.include_router(wtr.router, prefix="/api/v1")

    async def _fake_auth():
        return _AuthStub()

    application.dependency_overrides[get_auth] = _fake_auth
    return application


@pytest.mark.asyncio
async def test_list_templates_without_team_id_resolves_personal_team(app, monkeypatch):
    async def fake_get_personal_team_id(self, owner_id):
        assert owner_id == USER_ID
        return "9000"

    async def fake_ensure_seed_templates(team_id, *, repo=None, created_by=None):
        assert team_id == "9000"
        assert created_by == USER_ID
        return [_template_row("101", "Short-form", 5)]

    monkeypatch.setattr(
        wtr.get_team_repository().__class__,
        "get_personal_team_id",
        fake_get_personal_team_id,
    )
    monkeypatch.setattr(wtr, "ensure_seed_templates", fake_ensure_seed_templates)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.get("/api/v1/workflows")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"] == [_template_row("101", "Short-form", 5)]


@pytest.mark.asyncio
async def test_list_templates_without_team_id_no_personal_team_returns_empty(
    app, monkeypatch
):
    async def fake_get_personal_team_id(self, owner_id):
        return None

    async def fail_ensure_seed_templates(*args, **kwargs):
        raise AssertionError("must not seed when there is no personal team to scope to")

    monkeypatch.setattr(
        wtr.get_team_repository().__class__,
        "get_personal_team_id",
        fake_get_personal_team_id,
    )
    monkeypatch.setattr(wtr, "ensure_seed_templates", fail_ensure_seed_templates)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.get("/api/v1/workflows")
    assert resp.status_code == 200
    assert resp.json() == {"success": True, "data": []}


@pytest.mark.asyncio
async def test_list_templates_with_team_id_still_403s_non_member(app, monkeypatch):
    async def fake_resolve_effective_role(user_id, *, team_id=None, project_id=None):
        return None

    async def fail_ensure_seed_templates(*args, **kwargs):
        raise AssertionError("must not reach the seeder when the role check 403s")

    monkeypatch.setattr(wtr, "resolve_effective_role", fake_resolve_effective_role)
    monkeypatch.setattr(wtr, "ensure_seed_templates", fail_ensure_seed_templates)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.get("/api/v1/workflows", params={"team_id": "42"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_list_templates_with_team_id_unchanged_for_a_member(app, monkeypatch):
    async def fake_resolve_effective_role(user_id, *, team_id=None, project_id=None):
        assert team_id == "42"
        return "editor"

    async def fake_ensure_seed_templates(team_id, *, repo=None, created_by=None):
        assert team_id == "42"
        return [_template_row("102", "Long-form", 11)]

    monkeypatch.setattr(wtr, "resolve_effective_role", fake_resolve_effective_role)
    monkeypatch.setattr(wtr, "ensure_seed_templates", fake_ensure_seed_templates)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.get("/api/v1/workflows", params={"team_id": "42"})
    assert resp.status_code == 200
    assert resp.json()["data"] == [_template_row("102", "Long-form", 11)]


# ── non-numeric ids (used to 500 in int()) ──────────────────────────────────


def _fail_repo(monkeypatch):
    class _Repo:
        def __getattr__(self, name):
            raise AssertionError(f"a non-numeric id must not reach the repo ({name})")

    monkeypatch.setattr(wtr, "get_workflow_templates_repository", lambda: _Repo())

    async def fail_role(*args, **kwargs):
        raise AssertionError("a non-numeric id must not reach the role lookup")

    monkeypatch.setattr(wtr, "resolve_effective_role", fail_role)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/v1/workflows/runs"),
        ("get", "/api/v1/workflows/not-an-id"),
        ("patch", "/api/v1/workflows/12ab"),
        ("delete", "/api/v1/workflows/%C2%B2"),
    ],
)
async def test_non_numeric_template_id_is_a_typed_404(app, monkeypatch, method, path):
    _fail_repo(monkeypatch)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        kwargs = {"json": {"name": "X"}} if method == "patch" else {}
        resp = await getattr(c, method)(path, **kwargs)
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["code"] == "not_found_or_out_of_scope"


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["get", "post"])
async def test_non_numeric_team_id_is_a_403(app, monkeypatch, method):
    _fail_repo(monkeypatch)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        kwargs = {"json": {"name": "X"}} if method == "post" else {}
        resp = await getattr(c, method)(
            "/api/v1/workflows", params={"team_id": "abc"}, **kwargs
        )
    assert resp.status_code == 403, resp.text

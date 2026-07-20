"""Workflow template CRUD router (M1 PR-A).

Handlers are called directly (as in test_issues_dispatch_preview) with the repo,
``resolve_effective_role`` and the seeder monkeypatched — the HTTP path needs
heavy auth/DB setup that these predicate-focused tests don't. Covers each
endpoint plus the 403/404 authz branches and the 422 guardrails.
"""

from __future__ import annotations

import importlib
from typing import Any, Dict, List, Optional

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.schemas.workflow import (
    MAX_NODES_PER_TEMPLATE,
    TemplateCreate,
    TemplateNodeIn,
    TemplateUpdate,
)

router_mod = importlib.import_module("app.api.workflow_templates_router")


class _Auth:
    user_id = "00000000-0000-0000-0000-000000000001"


_AUTH = _Auth()


class _FakeRepo:
    def __init__(
        self,
        *,
        team_id_for_template: Optional[str] = "777",
        template: Optional[Dict[str, Any]] = None,
        template_count: int = 0,
        delete_ok: bool = True,
    ):
        self._team_id_for_template = team_id_for_template
        self._template = template
        self._template_count = template_count
        self._delete_ok = delete_ok
        self.created: List[Dict[str, Any]] = []
        self.updated: List[Dict[str, Any]] = []
        self.deleted: List[str] = []

    async def list_stage_library(self) -> List[Dict[str, Any]]:
        return [{"id": "1", "slug": "script", "name": "Script"}]

    async def count_templates(self, team_id: str) -> int:
        return self._template_count

    async def create_template(self, team_id, name, created_by) -> Dict[str, Any]:
        row = {"id": "500", "team_id": str(team_id), "name": name, "node_count": 0}
        self.created.append(row)
        return row

    async def get_template_team_id(self, template_id) -> Optional[str]:
        return self._team_id_for_template

    async def get_template(self, template_id, team_id) -> Optional[Dict[str, Any]]:
        return self._template

    async def update_template(self, template_id, team_id, **kwargs):
        self.updated.append({"id": template_id, **kwargs})
        return self._template

    async def delete_template(self, template_id, team_id) -> bool:
        self.deleted.append(str(template_id))
        return self._delete_ok


def _install(monkeypatch, repo, *, role="manager", seeded=None):
    monkeypatch.setattr(router_mod, "get_workflow_templates_repository", lambda: repo)

    async def _role(user_id, *, project_id=None, team_id=None):
        return role

    monkeypatch.setattr(router_mod, "resolve_effective_role", _role)

    async def _seed(team_id, *, repo=None, created_by=None):
        return seeded if seeded is not None else []

    monkeypatch.setattr(router_mod, "ensure_seed_templates", _seed)


# ── stage-library ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stage_library_returns_nodes(monkeypatch):
    repo = _FakeRepo()
    _install(monkeypatch, repo)
    r = await router_mod.list_stage_library(_AUTH)
    assert r["success"] is True
    assert r["data"][0]["slug"] == "script"


# ── list ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_seeds_and_returns_for_member(monkeypatch):
    repo = _FakeRepo()
    _install(
        monkeypatch, repo, role="editor", seeded=[{"id": "1", "name": "Short-form"}]
    )
    r = await router_mod.list_templates(_AUTH, team_id="777")
    assert r["data"][0]["name"] == "Short-form"


@pytest.mark.asyncio
async def test_list_non_member_403(monkeypatch):
    repo = _FakeRepo()
    _install(monkeypatch, repo, role=None)
    with pytest.raises(HTTPException) as exc:
        await router_mod.list_templates(_AUTH, team_id="777")
    assert exc.value.status_code == 403


# ── create ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_member_ok(monkeypatch):
    repo = _FakeRepo()
    _install(monkeypatch, repo, role="manager")
    r = await router_mod.create_template(
        TemplateCreate(name="AI Short"), _AUTH, team_id="777"
    )
    assert r["data"]["name"] == "AI Short"
    assert repo.created


@pytest.mark.asyncio
async def test_create_non_member_403(monkeypatch):
    repo = _FakeRepo()
    _install(monkeypatch, repo, role=None)
    with pytest.raises(HTTPException) as exc:
        await router_mod.create_template(TemplateCreate(name="X"), _AUTH, team_id="777")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_create_over_team_cap_422(monkeypatch):
    repo = _FakeRepo(template_count=20)
    _install(monkeypatch, repo, role="manager")
    with pytest.raises(HTTPException) as exc:
        await router_mod.create_template(TemplateCreate(name="X"), _AUTH, team_id="777")
    assert exc.value.status_code == 422


# ── get ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_member_ok(monkeypatch):
    tpl = {"id": "500", "name": "Short-form", "nodes": []}
    repo = _FakeRepo(template=tpl)
    _install(monkeypatch, repo, role="viewer")
    r = await router_mod.get_template("500", _AUTH)
    assert r["data"]["id"] == "500"


@pytest.mark.asyncio
async def test_get_missing_template_404(monkeypatch):
    repo = _FakeRepo(team_id_for_template=None)
    _install(monkeypatch, repo, role="manager")
    with pytest.raises(HTTPException) as exc:
        await router_mod.get_template("500", _AUTH)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_non_member_404_no_leak(monkeypatch):
    repo = _FakeRepo(template={"id": "500"})
    _install(monkeypatch, repo, role=None)
    with pytest.raises(HTTPException) as exc:
        await router_mod.get_template("500", _AUTH)
    # 404 (not 403) so existence never leaks across teams.
    assert exc.value.status_code == 404


# ── update ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_member_write_ok(monkeypatch):
    tpl = {"id": "500", "name": "Renamed", "nodes": []}
    repo = _FakeRepo(template=tpl)
    _install(monkeypatch, repo, role="editor")
    r = await router_mod.update_template("500", TemplateUpdate(name="Renamed"), _AUTH)
    assert r["data"]["name"] == "Renamed"
    assert repo.updated


@pytest.mark.asyncio
async def test_update_non_member_404(monkeypatch):
    repo = _FakeRepo(template={"id": "500"})
    _install(monkeypatch, repo, role=None)
    with pytest.raises(HTTPException) as exc:
        await router_mod.update_template("500", TemplateUpdate(name="X"), _AUTH)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_update_nodes_forwarded_as_dicts(monkeypatch):
    tpl = {"id": "500", "nodes": []}
    repo = _FakeRepo(template=tpl)
    _install(monkeypatch, repo, role="manager")
    payload = TemplateUpdate(
        nodes=[TemplateNodeIn(name="Script", sort_order=10, source_stage_id="1")]
    )
    await router_mod.update_template("500", payload, _AUTH)
    forwarded = repo.updated[0]["nodes"]
    assert isinstance(forwarded, list)
    assert forwarded[0]["name"] == "Script"
    assert forwarded[0]["source_stage_id"] == "1"


# ── delete ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_member_write_ok(monkeypatch):
    repo = _FakeRepo(delete_ok=True)
    _install(monkeypatch, repo, role="manager")
    r = await router_mod.delete_template("500", _AUTH)
    assert r["data"]["deleted"] is True
    assert repo.deleted == ["500"]


@pytest.mark.asyncio
async def test_delete_missing_404(monkeypatch):
    repo = _FakeRepo(team_id_for_template=None)
    _install(monkeypatch, repo, role="manager")
    with pytest.raises(HTTPException) as exc:
        await router_mod.delete_template("500", _AUTH)
    assert exc.value.status_code == 404


# ── schema guardrail: nodes <= 30 → 422 at parse ────────────────────────────


def test_template_update_rejects_over_30_nodes():
    nodes = [
        TemplateNodeIn(name=f"n{i}", sort_order=i)
        for i in range(MAX_NODES_PER_TEMPLATE + 1)
    ]
    with pytest.raises(ValidationError):
        TemplateUpdate(nodes=nodes)


def test_template_update_accepts_exactly_30_nodes():
    nodes = [
        TemplateNodeIn(name=f"n{i}", sort_order=i)
        for i in range(MAX_NODES_PER_TEMPLATE)
    ]
    assert len(TemplateUpdate(nodes=nodes).nodes) == MAX_NODES_PER_TEMPLATE


def test_template_node_owner_xor_rejected():
    import uuid

    with pytest.raises(ValidationError):
        TemplateNodeIn(
            name="n",
            sort_order=1,
            default_owner_user_id=uuid.uuid4(),
            default_owner_agent_id=uuid.uuid4(),
        )

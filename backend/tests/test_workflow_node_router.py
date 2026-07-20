"""Instance node add/delete router mapping (M2-W3-1).

Handlers are called directly (as in ``test_workflows_router``) with
``resolve_effective_role`` and the ``node_mutations`` service monkeypatched —
the focus is the HTTP status mapping: role gate → 403, blocked guard → 409 with
the machine reason, missing node → 404, bad source combo → 422.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi import HTTPException

import app.core.workflow_roles as roles_mod
import app.services.workflow.node_mutations as nm
from app.schemas.workflow import (
    DELETE_BLOCK_NOT_PENDING,
    NodeCreate,
    NodeDeleteBlocked,
)

router_mod = importlib.import_module("app.api.projects_router")


class _Auth:
    user_id = "00000000-0000-0000-0000-000000000001"


_AUTH = _Auth()


async def _role_manager(*a, **k):
    return "manager"


async def _role_viewer(*a, **k):
    return "viewer"


# ── add ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_node_manager_ok(monkeypatch):
    monkeypatch.setattr(roles_mod, "resolve_effective_role", _role_manager)

    async def _add(project_id, **kwargs):
        return {"id": "new", "name": "Voiceover", **kwargs}

    monkeypatch.setattr(nm, "add_project_node", _add)
    res = await router_mod.add_workflow_node(
        "100", NodeCreate(source_stage_id="55", sort_order=2), _AUTH, None
    )
    assert res["success"] is True
    assert res["data"]["source_stage_id"] == "55"


@pytest.mark.asyncio
async def test_add_node_viewer_forbidden(monkeypatch):
    monkeypatch.setattr(roles_mod, "resolve_effective_role", _role_viewer)
    with pytest.raises(HTTPException) as exc:
        await router_mod.add_workflow_node(
            "100", NodeCreate(name="Blank", sort_order=1), _AUTH, None
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_add_node_bad_source_maps_422(monkeypatch):
    monkeypatch.setattr(roles_mod, "resolve_effective_role", _role_manager)

    async def _add(project_id, **kwargs):
        raise ValueError("unknown node-bank stage")

    monkeypatch.setattr(nm, "add_project_node", _add)
    with pytest.raises(HTTPException) as exc:
        await router_mod.add_workflow_node(
            "100", NodeCreate(source_stage_id="999", sort_order=1), _AUTH, None
        )
    assert exc.value.status_code == 422


def test_node_create_rejects_both_sources():
    with pytest.raises(Exception):
        NodeCreate(source_stage_id="1", name="x", sort_order=0)


def test_node_create_rejects_neither_source():
    with pytest.raises(Exception):
        NodeCreate(sort_order=0)


# ── delete ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_node_ok(monkeypatch):
    monkeypatch.setattr(roles_mod, "resolve_effective_role", _role_manager)

    async def _del(project_id, node_id):
        return True

    monkeypatch.setattr(nm, "delete_project_node", _del)
    res = await router_mod.delete_workflow_node("100", "900", _AUTH, None)
    assert res == {"success": True, "data": {"deleted": True}}


@pytest.mark.asyncio
async def test_delete_node_viewer_forbidden(monkeypatch):
    monkeypatch.setattr(roles_mod, "resolve_effective_role", _role_viewer)
    with pytest.raises(HTTPException) as exc:
        await router_mod.delete_workflow_node("100", "900", _AUTH, None)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_delete_node_blocked_maps_409_with_reason(monkeypatch):
    monkeypatch.setattr(roles_mod, "resolve_effective_role", _role_manager)

    async def _del(project_id, node_id):
        raise NodeDeleteBlocked(DELETE_BLOCK_NOT_PENDING)

    monkeypatch.setattr(nm, "delete_project_node", _del)
    with pytest.raises(HTTPException) as exc:
        await router_mod.delete_workflow_node("100", "900", _AUTH, None)
    assert exc.value.status_code == 409
    assert exc.value.detail == DELETE_BLOCK_NOT_PENDING


@pytest.mark.asyncio
async def test_delete_node_missing_maps_404(monkeypatch):
    monkeypatch.setattr(roles_mod, "resolve_effective_role", _role_manager)

    async def _del(project_id, node_id):
        raise LookupError()

    monkeypatch.setattr(nm, "delete_project_node", _del)
    with pytest.raises(HTTPException) as exc:
        await router_mod.delete_workflow_node("100", "900", _AUTH, None)
    assert exc.value.status_code == 404

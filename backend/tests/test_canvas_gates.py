"""Unit tests for canvases_router's canvas→project gate helpers.

Follow-up to Projects Phase A: ``_gate_canvas_read`` used to delegate to
``_gate_canvas_write`` (viewer-role project members could not even read a
canvas). It now calls ``verify_project_read_access`` so read semantics
match the rest of the projects surface (owner ∨ team member ∨ any
project_members role), while write still requires manager/editor.

Runs against a fake Supabase admin client (same style as
``test_scope_guards.py``) plus a stubbed ``CanvasService.get_project_id`` —
no DB, no HTTP layer.
"""

from __future__ import annotations

import sys

import pytest
from fastapi import HTTPException

from app.core.deps import AuthContext
from app.services.canvas.canvas_service import CanvasService

# ``app.api.__init__`` rebinds ``canvases_router`` to the APIRouter
# instance, so grab the real module from sys.modules (same trick as
# test_canvas_classic_node_route.py).
canvases_router = sys.modules.get("app.api.canvases_router")
if canvases_router is None:  # pragma: no cover - import guard for direct runs
    import app.api.canvases_router as canvases_router  # noqa: F401

    canvases_router = sys.modules["app.api.canvases_router"]

pytestmark = pytest.mark.unit

FAKE_PROJECT_ID = "p1"


class _FakeQuery:
    def __init__(self, data: list) -> None:
        self._data = data

    def __getattr__(self, name: str):
        def _capture(*args, **kwargs) -> "_FakeQuery":
            return self

        return _capture

    async def execute(self):
        class _R:
            data = self._data

        return _R()


class _FakeClient:
    def __init__(self, tables: dict[str, list]) -> None:
        self._tables = tables

    def table(self, name: str) -> _FakeQuery:
        return _FakeQuery(self._tables.get(name, []))


@pytest.fixture
def patch_admin(monkeypatch):
    def _install(tables: dict[str, list]) -> None:
        async def _fake_admin():
            return _FakeClient(tables)

        monkeypatch.setattr(
            "app.core.scope_guards.get_async_supabase_admin", _fake_admin
        )

    return _install


@pytest.fixture(autouse=True)
def _stub_canvas_lookup(monkeypatch):
    """Canvas → project resolution is not under test here; stub it."""

    async def _fake_get_project_id(self, canvas_id: str):
        return FAKE_PROJECT_ID

    monkeypatch.setattr(CanvasService, "get_project_id", _fake_get_project_id)


def _auth(user_id: str = "u1") -> AuthContext:
    return AuthContext(user_id=user_id, auth_type="jwt")


async def test_viewer_passes_canvas_read_gate(patch_admin):
    patch_admin(
        {
            "projects": [{"owner_id": "owner", "team_id": None}],
            "project_members": [{"role": "viewer"}],
        }
    )
    project_id = await canvases_router._gate_canvas_read("c1", _auth("u1"))
    assert project_id == FAKE_PROJECT_ID


async def test_viewer_fails_canvas_write_gate(patch_admin):
    patch_admin(
        {
            "projects": [{"owner_id": "owner", "team_id": None}],
            "project_members": [{"role": "viewer"}],
        }
    )
    with pytest.raises(HTTPException) as ei:
        await canvases_router._gate_canvas_write("c1", _auth("u1"))
    assert ei.value.status_code == 403


async def test_editor_passes_both_gates(patch_admin):
    patch_admin(
        {
            "projects": [{"owner_id": "owner", "team_id": None}],
            "project_members": [{"role": "editor"}],
        }
    )
    await canvases_router._gate_canvas_read("c1", _auth("u1"))
    await canvases_router._gate_canvas_write("c1", _auth("u1"))


async def test_non_member_fails_canvas_read_gate(patch_admin):
    patch_admin(
        {
            "projects": [{"owner_id": "owner", "team_id": None}],
            "project_members": [],
        }
    )
    with pytest.raises(HTTPException) as ei:
        await canvases_router._gate_canvas_read("c1", _auth("u1"))
    assert ei.value.status_code == 403

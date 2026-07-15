"""Unit tests for canvases_router's canvas→project gate helpers.

Follow-up to Projects Phase A: ``_gate_canvas_read`` used to delegate to
``_gate_canvas_write`` (viewer-role project members could not even read a
canvas). It now calls ``verify_project_read_access`` so read semantics
match the rest of the projects surface (owner ∨ team member ∨ any
project_members role), while write still requires manager/editor.

Runs against a fake ORM read session (same style as
``test_scope_guards.py``) plus a stubbed ``CanvasService.get_project_id`` —
no DB, no HTTP layer.

Note: the project id is numeric here — ``_check_project_access`` coerces
``int(str(project_id))`` for the BIGINT ``projects.id`` column.
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager

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

FAKE_PROJECT_ID = "1"


class _Result:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    """Returns per-table canned tuple-rows keyed by table name in the SQL."""

    _TABLES = ("project_members", "team_members", "projects", "teams")

    def __init__(self, tables: dict[str, list]) -> None:
        self._tables = tables

    async def execute(self, stmt):
        sql = str(stmt)
        for name in self._TABLES:
            if name in sql:
                return _Result(self._tables.get(name, []))
        return _Result([])


@pytest.fixture
def patch_admin(monkeypatch):
    def _install(tables: dict[str, list]) -> None:
        @asynccontextmanager
        async def _fake_read_scope():
            yield _FakeSession(tables)

        import app.db.session as db_session_mod

        monkeypatch.setattr(db_session_mod, "read_scope", _fake_read_scope)

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
            "projects": [("owner", None)],
            "project_members": [("viewer",)],
        }
    )
    project_id = await canvases_router._gate_canvas_read("c1", _auth("u1"))
    assert project_id == FAKE_PROJECT_ID


async def test_viewer_fails_canvas_write_gate(patch_admin):
    patch_admin(
        {
            "projects": [("owner", None)],
            "project_members": [("viewer",)],
        }
    )
    with pytest.raises(HTTPException) as ei:
        await canvases_router._gate_canvas_write("c1", _auth("u1"))
    assert ei.value.status_code == 403


async def test_editor_passes_both_gates(patch_admin):
    patch_admin(
        {
            "projects": [("owner", None)],
            "project_members": [("editor",)],
        }
    )
    await canvases_router._gate_canvas_read("c1", _auth("u1"))
    await canvases_router._gate_canvas_write("c1", _auth("u1"))


async def test_non_member_fails_canvas_read_gate(patch_admin):
    patch_admin(
        {
            "projects": [("owner", None)],
            "project_members": [],
        }
    )
    with pytest.raises(HTTPException) as ei:
        await canvases_router._gate_canvas_read("c1", _auth("u1"))
    assert ei.value.status_code == 403

"""Script editor dispatch routes: wire parity after they gained response models
(P6), plus the two access holes closed alongside.

Each route runs over real HTTP through the real router. The task manager and
the DBOS dispatcher are stubbed (they return the real shapes: ``create`` → a
uuid string); the body must equal what FastAPI sent for the flat dict the
handler builds (``tests/api/wire_parity.py``).

Security (fixed in P6):

- ``expand-chapter`` / ``create-branches`` authorized the body's ``script_id``
  but dispatched with the body's ``chapter_id`` unchecked. The expand workflow
  overwrites that chapter's content, so knowing a chapter id let any user
  rewrite a chapter of someone else's script (and hang branches off it).
  Now the chapter must belong to the authorized script, else 404.
- ``import-screenplay`` created the new script under the body's
  ``project_id`` without checking the caller may write that project.

Every rejection has a same-setup allow case next to it.

Also pinned: the four routes with no caller left are gone.
"""

from __future__ import annotations

import importlib
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

import app.core.scope_guards as guards
from app.core.deps import AuthContext, get_auth
from app.main import app
from tests.api.wire_parity import SAMPLE_BIGINT, assert_wire_unchanged

pytestmark = pytest.mark.unit

ai_mod = importlib.import_module("app.api.script_ai_router")
import_mod = importlib.import_module("app.api.script_import_scenes_router")
script_service_mod = importlib.import_module(
    "app.services.storyboard.script.script_service"
)

USER = "00000000-0000-0000-0000-000000000042"
TASK = "5f0c2a4e-8a53-4c4e-9a7e-7f5d8f0f2b11"
SID = SAMPLE_BIGINT + 11
OTHER_SID = SAMPLE_BIGINT + 12
OWN_CHAPTER = SAMPLE_BIGINT + 21
FOREIGN_CHAPTER = SAMPLE_BIGINT + 22
OWN_PROJECT = 42
FOREIGN_PROJECT = 99
NEW_SCRIPT = SAMPLE_BIGINT + 31

# chapter id → owning script id (the repo reads script_id back as a native int)
CHAPTERS = {str(OWN_CHAPTER): SID, str(FOREIGN_CHAPTER): OTHER_SID}


class FakeChapterRepo:
    async def get_by_id(self, chapter_id):
        owner = CHAPTERS.get(str(chapter_id))
        return None if owner is None else {"id": int(chapter_id), "script_id": owner}


class FakeScriptService:
    chapter_repo = FakeChapterRepo()
    created: list = []

    async def create_project(self, **kwargs):
        FakeScriptService.created.append(kwargs)
        return {"id": NEW_SCRIPT, **kwargs}


class Calls:
    dispatch: AsyncMock
    create: AsyncMock


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=USER, auth_type="jwt")


@pytest.fixture(autouse=True)
def _wiring(monkeypatch):
    app.dependency_overrides[get_auth] = _fake_auth

    async def _script_ok(script_id, auth):
        return None

    async def _project_access(project_id, user_id, *, write):
        if str(project_id) != str(OWN_PROJECT):
            raise HTTPException(status_code=403, detail="Access denied")

    monkeypatch.setattr(ai_mod, "verify_script_access", _script_ok)
    monkeypatch.setattr(guards, "_check_project_access", _project_access)
    monkeypatch.setattr(script_service_mod, "ScriptService", FakeScriptService)
    FakeScriptService.created = []

    async def _team(_user_id):
        return "7"

    monkeypatch.setattr(import_mod, "require_team_id", _team)

    Calls.create = AsyncMock(return_value=TASK)
    mgr = type("Mgr", (), {"create": Calls.create})()
    for mod in (ai_mod, import_mod):
        monkeypatch.setattr(mod, "get_task_manager", lambda: mgr)
    Calls.dispatch = AsyncMock(return_value={"mode": "dbos"})
    monkeypatch.setattr(
        "app.services.infra.dbos_orchestrator.start_workflow_routed", Calls.dispatch
    )
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _chapter_body(chapter_id: int, **extra: Any) -> dict:
    return {
        "script_id": str(SID),
        "chapter_id": str(chapter_id),
        "title": "Chapter 1",
        "summary": "The detective arrives.",
        **extra,
    }


def _import_body(project_id: int) -> dict:
    return {
        "name": "Imported",
        "project_id": project_id,
        "mode": "fountain",
        "content": "INT. KITCHEN - DAY\n\nHello.",
    }


def _assert_nothing_dispatched() -> None:
    Calls.create.assert_not_awaited()
    Calls.dispatch.assert_not_awaited()


# --------------------------------------------------------------------------- #
# Wire parity
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path, extra",
    [("expand-chapter", {}), ("create-branches", {"branch_count": 3})],
)
async def test_chapter_dispatch_wire_unchanged(client, path, extra) -> None:
    resp = await client.post(
        f"/api/v1/scripts/{path}", json=_chapter_body(OWN_CHAPTER, **extra)
    )
    assert_wire_unchanged(resp, {"success": True, "task_id": TASK})
    Calls.dispatch.assert_awaited_once()


@pytest.mark.asyncio
async def test_convert_to_scenes_wire_unchanged(client) -> None:
    resp = await client.post(
        f"/api/v1/scripts/{SID}/chapters/{OWN_CHAPTER}/convert-to-scenes"
    )
    assert_wire_unchanged(resp, {"success": True, "task_id": TASK})


@pytest.mark.asyncio
async def test_import_screenplay_wire_unchanged(client) -> None:
    resp = await client.post(
        "/api/v1/scripts/import-screenplay", json=_import_body(OWN_PROJECT)
    )
    assert_wire_unchanged(
        resp, {"success": True, "script_id": str(NEW_SCRIPT), "task_id": TASK}
    )
    assert FakeScriptService.created[0]["project_id"] == OWN_PROJECT


# --------------------------------------------------------------------------- #
# Security: a chapter of another script cannot ride in under your script
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path, extra",
    [("expand-chapter", {}), ("create-branches", {"branch_count": 2})],
)
@pytest.mark.parametrize("chapter", [FOREIGN_CHAPTER, SAMPLE_BIGINT + 404])
async def test_chapter_outside_script_is_404(client, path, extra, chapter) -> None:
    resp = await client.post(
        f"/api/v1/scripts/{path}", json=_chapter_body(chapter, **extra)
    )
    assert resp.status_code == 404, resp.text
    _assert_nothing_dispatched()


@pytest.mark.asyncio
async def test_convert_to_scenes_chapter_outside_script_is_404(client) -> None:
    resp = await client.post(
        f"/api/v1/scripts/{SID}/chapters/{FOREIGN_CHAPTER}/convert-to-scenes"
    )
    assert resp.status_code == 404
    _assert_nothing_dispatched()


@pytest.mark.asyncio
async def test_script_guard_denial_is_not_a_500(client, monkeypatch) -> None:
    async def _deny(script_id, auth):
        raise HTTPException(status_code=403, detail="Access denied")

    monkeypatch.setattr(ai_mod, "verify_script_access", _deny)
    resp = await client.post(
        "/api/v1/scripts/expand-chapter", json=_chapter_body(OWN_CHAPTER)
    )
    assert resp.status_code == 403
    _assert_nothing_dispatched()


# --------------------------------------------------------------------------- #
# Security: import-screenplay needs write access to the target project
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_import_into_foreign_project_is_403(client) -> None:
    resp = await client.post(
        "/api/v1/scripts/import-screenplay", json=_import_body(FOREIGN_PROJECT)
    )
    assert resp.status_code == 403, resp.text
    assert FakeScriptService.created == []
    _assert_nothing_dispatched()


# --------------------------------------------------------------------------- #
# Removed routes (no caller left since #1153 deleted the legacy editor)
# --------------------------------------------------------------------------- #


def test_dead_routes_are_gone() -> None:
    ops = {
        (method, route.path)
        for route in app.routes
        for method in getattr(route, "methods", ()) or ()
    }
    for gone in [
        ("POST", "/api/v1/scripts/generate-outline"),
        ("POST", "/api/v1/scripts/convert-to-storyboard"),
        ("POST", "/api/v1/scripts/import"),
        ("GET", "/api/v1/scripts/{script_id}/export"),
    ]:
        assert gone not in ops, gone
    assert ("POST", "/api/v1/scripts/import-screenplay") in ops

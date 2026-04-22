"""HTTP tests for the AI Library router (agents + skills + skill files).

Matches the style used by ``tests/test_storyboard.py``: overrides the ``get_auth``
dependency so every request passes through as a fake JWT user, and patches the
repository layer with AsyncMock. This keeps the tests hermetic — no real DB,
no real Supabase, no network — while still exercising the full FastAPI routing
and Pydantic response-model serialization path.

Test cases (6 total):

    1. GET /agents/script_ai returns 200 + system preset + non-empty skill_ids
    2. GET /agents/fake-slug returns 404
    3. PATCH /agents/script_ai (system preset) returns 403
    4. GET /skills lists system presets including 'script-outline'
    5. GET /skills/script-outline includes references/examples.md in files
    6. PATCH /skills/script-outline (system preset) returns 403
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app

BASE = "/api/v1/ai-library"
FAKE_USER_ID = str(uuid4())


# ---------------------------------------------------------------------------
# Auth override — bypass real Supabase JWT validation
# ---------------------------------------------------------------------------


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=FAKE_USER_ID, auth_type="jwt")


@pytest.fixture(autouse=True)
def _override_auth():
    app.dependency_overrides[get_auth] = _fake_auth
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Fixture payloads — mirror the shape of the seed data loaded by Task 10.
# ---------------------------------------------------------------------------


_NOW_ISO = datetime.now(timezone.utc).isoformat()
_SCRIPT_AI_ID = str(uuid4())


def _agent_row(
    *,
    slug: str = "script_ai",
    name: str = "Script AI",
    is_system_preset: bool = True,
) -> Dict[str, Any]:
    return {
        "id": _SCRIPT_AI_ID,
        "slug": slug,
        "name": name,
        "description": "Scriptwriting assistant",
        "model": "qwen-max",
        "temperature": 0.7,
        "max_tokens": 4096,
        "identity_md": "# Script AI\nScriptwriting assistant.",
        "soul_md": None,
        "agent_md": None,
        "is_system_preset": is_system_preset,
        "team_id": None,
        "project_id": None,
        "user_id": None,
        "enabled": True,
        "created_at": _NOW_ISO,
        "updated_at": _NOW_ISO,
    }


def _skill_row(
    *,
    slug: str = "script-outline",
    name: str = "Script Outline",
    skill_id: int = 101,
    is_public: bool = True,
    project_id: int | None = None,
) -> Dict[str, Any]:
    return {
        "id": skill_id,
        "slug": slug,
        "name": name,
        "description": f"{name} skill",
        "body_md": f"# {name}\n",
        "category": "script",
        "icon": None,
        "is_public": is_public,
        "team_id": None,
        "project_id": project_id,
        "output_format": None,
        "frontmatter_json": {},
        "status": "active",
        "updated_at": _NOW_ISO,
    }


def _file_row(
    *,
    path: str = "references/examples.md",
    skill_id: int = 101,
    file_type: str = "markdown",
    content: str = "# Examples\n",
) -> Dict[str, Any]:
    return {
        "id": str(uuid4()),
        "skill_id": skill_id,
        "path": path,
        "content": content,
        "file_type": file_type,
        "binary_url": None,
        "updated_at": _NOW_ISO,
    }


# ---------------------------------------------------------------------------
# Repo-patching helpers
# ---------------------------------------------------------------------------


def _patch_agent_repo(**attrs: Any):
    """Patch every instance attribute we care about on AgentRepository."""
    defaults = {
        "get_by_slug": AsyncMock(return_value=None),
        "get_skill_ids": AsyncMock(return_value=[]),
        "list_accessible": AsyncMock(return_value=[]),
        "update_fields": AsyncMock(return_value={}),
        "update_fields_versioned": AsyncMock(return_value={}),
        "update_skill_bindings": AsyncMock(return_value=None),
    }
    defaults.update(attrs)
    patches = [
        patch.object(
            __import__(
                "app.repositories.agent_repository",
                fromlist=["AgentRepository"],
            ).AgentRepository,
            name,
            mock,
        )
        for name, mock in defaults.items()
    ]
    return patches


def _patch_skill_repo(**attrs: Any):
    defaults = {
        "get_by_slug": AsyncMock(return_value=None),
        "list_accessible": AsyncMock(return_value=[]),
        "list_files": AsyncMock(return_value=[]),
        "update_fields": AsyncMock(return_value={}),
        "update_fields_versioned": AsyncMock(return_value={}),
        "upsert_file": AsyncMock(return_value={}),
        "upsert_file_versioned": AsyncMock(return_value={}),
        "delete_file": AsyncMock(return_value=None),
    }
    defaults.update(attrs)
    patches = [
        patch.object(
            __import__(
                "app.repositories.skill_repository",
                fromlist=["SkillRepository"],
            ).SkillRepository,
            name,
            mock,
        )
        for name, mock in defaults.items()
    ]
    return patches


def _apply(patches):
    """Start a list of patch() context managers and return the active mocks."""
    return [p.__enter__() for p in patches]


def _cleanup(patches):
    for p in patches:
        p.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_script_ai_returns_preset(client: AsyncClient) -> None:
    """GET /agents/script_ai → 200, is_system_preset=true, skill_ids non-empty."""
    agent_patches = _patch_agent_repo(
        get_by_slug=AsyncMock(return_value=_agent_row()),
        get_skill_ids=AsyncMock(return_value=[101, 102, 103]),
    )
    skill_patches = _patch_skill_repo()
    _apply(agent_patches)
    _apply(skill_patches)
    try:
        resp = await client.get(f"{BASE}/agents/script_ai")
    finally:
        _cleanup(agent_patches)
        _cleanup(skill_patches)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["slug"] == "script_ai"
    assert body["is_system_preset"] is True
    assert body["skill_ids"] == [101, 102, 103]


@pytest.mark.asyncio
async def test_get_agent_not_found(client: AsyncClient) -> None:
    """GET /agents/fake-slug → 404 when the slug has no row."""
    agent_patches = _patch_agent_repo(get_by_slug=AsyncMock(return_value=None))
    skill_patches = _patch_skill_repo()
    _apply(agent_patches)
    _apply(skill_patches)
    try:
        resp = await client.get(f"{BASE}/agents/fake-slug")
    finally:
        _cleanup(agent_patches)
        _cleanup(skill_patches)

    assert resp.status_code == 404
    # Global exception handler wraps HTTPException.detail as ErrorResponse.error
    assert resp.json()["error"] == "agent not found"


@pytest.mark.asyncio
async def test_patch_system_preset_rejected(client: AsyncClient) -> None:
    """PATCH /agents/script_ai → 403 because system presets are read-only."""
    agent_patches = _patch_agent_repo(
        get_by_slug=AsyncMock(return_value=_agent_row(is_system_preset=True)),
    )
    skill_patches = _patch_skill_repo()
    _apply(agent_patches)
    _apply(skill_patches)
    try:
        resp = await client.patch(
            f"{BASE}/agents/script_ai",
            json={"name": "hacked"},
        )
    finally:
        _cleanup(agent_patches)
        _cleanup(skill_patches)

    assert resp.status_code == 403
    assert "read-only" in resp.json()["error"].lower()


@pytest.mark.asyncio
async def test_list_skills_includes_script_outline(client: AsyncClient) -> None:
    """GET /skills → response contains a 'script-outline' slug."""
    skills: List[Dict[str, Any]] = [
        _skill_row(slug="script-outline", name="Script Outline", skill_id=101),
        _skill_row(slug="script-beats", name="Script Beats", skill_id=102),
        _skill_row(slug="script-dialogue", name="Script Dialogue", skill_id=103),
    ]
    skill_patches = _patch_skill_repo(
        list_accessible=AsyncMock(return_value=skills),
        list_files=AsyncMock(return_value=[]),
    )
    agent_patches = _patch_agent_repo()

    # list_skills → _fetch_user_team_ids / _fetch_user_project_ids (scope
    # filter) + _enrich_* → _fetch_team_names / _fetch_project_names.
    # All four touch Supabase; stub them so CI doesn't need a live DB.
    enrich_patches = [
        patch(
            "app.api.ai_library_router._fetch_user_team_ids",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.api.ai_library_router._fetch_user_project_ids",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.api.ai_library_router._fetch_team_names",
            AsyncMock(return_value={}),
        ),
        patch(
            "app.api.ai_library_router._fetch_project_names",
            AsyncMock(return_value={}),
        ),
    ]
    _apply(agent_patches)
    _apply(skill_patches)
    for p in enrich_patches:
        p.start()
    try:
        resp = await client.get(f"{BASE}/skills")
    finally:
        for p in enrich_patches:
            p.stop()
        _cleanup(agent_patches)
        _cleanup(skill_patches)

    assert resp.status_code == 200, resp.text
    slugs = [s["slug"] for s in resp.json()]
    assert "script-outline" in slugs
    assert len(slugs) == 3


@pytest.mark.asyncio
async def test_get_script_outline_includes_files(client: AsyncClient) -> None:
    """GET /skills/script-outline → body.files contains references/examples.md."""
    skill_patches = _patch_skill_repo(
        get_by_slug=AsyncMock(return_value=_skill_row(skill_id=101)),
        list_files=AsyncMock(
            return_value=[
                _file_row(path="references/examples.md", skill_id=101),
                _file_row(path="references/format.md", skill_id=101),
            ]
        ),
    )
    agent_patches = _patch_agent_repo()
    _apply(agent_patches)
    _apply(skill_patches)
    try:
        resp = await client.get(f"{BASE}/skills/script-outline")
    finally:
        _cleanup(agent_patches)
        _cleanup(skill_patches)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["slug"] == "script-outline"
    file_paths = [f["path"] for f in body["files"]]
    assert "references/examples.md" in file_paths


@pytest.mark.asyncio
async def test_patch_system_preset_skill_rejected(client: AsyncClient) -> None:
    """PATCH /skills/script-outline → 403 (public + no project = system preset)."""
    skill_patches = _patch_skill_repo(
        get_by_slug=AsyncMock(return_value=_skill_row(is_public=True, project_id=None)),
    )
    agent_patches = _patch_agent_repo()
    _apply(agent_patches)
    _apply(skill_patches)
    try:
        resp = await client.patch(
            f"{BASE}/skills/script-outline",
            json={"name": "x"},
        )
    finally:
        _cleanup(agent_patches)
        _cleanup(skill_patches)

    assert resp.status_code == 403
    assert "read-only" in resp.json()["error"].lower()


# Sanity check: confirms UUID field is returned as a UUID-parseable string.
@pytest.mark.asyncio
async def test_get_script_ai_id_is_uuid(client: AsyncClient) -> None:
    agent_patches = _patch_agent_repo(
        get_by_slug=AsyncMock(return_value=_agent_row()),
        get_skill_ids=AsyncMock(return_value=[101]),
    )
    skill_patches = _patch_skill_repo()
    _apply(agent_patches)
    _apply(skill_patches)
    try:
        resp = await client.get(f"{BASE}/agents/script_ai")
    finally:
        _cleanup(agent_patches)
        _cleanup(skill_patches)

    assert resp.status_code == 200
    # Round-trip through UUID() to confirm it is a valid UUID string
    UUID(resp.json()["id"])

"""GET /skills must say which agents bind each skill (spec 2026-08-02 §B3).

The list endpoint used to leave ``agents`` empty "for performance" — the
reverse index only got populated on the detail page. The skill gallery needs
it per card: a skill no agent uses is the thing worth surfacing, and you
can't see that by opening skills one at a time.

Performance is handled by batching, not by omitting the data: one JOIN for
the whole page. These tests pin both halves — the data and the batching.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import textwrap
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app

BASE = "/api/v1/ai-library"
FAKE_USER_ID = str(uuid4())


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


def _skill(skill_id: int, slug: str) -> dict:
    return {
        "id": skill_id,
        "slug": slug,
        "name": slug,
        "is_public": True,
        "updated_at": "2026-08-03T00:00:00+00:00",
        "frontmatter_json": {},
    }


def _patches(skill_repo):
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(
        patch("app.api.ai_library_router.get_skill_repository", return_value=skill_repo)
    )
    stack.enter_context(
        patch(
            "app.api.ai_library_router._fetch_user_team_ids", AsyncMock(return_value=[])
        )
    )
    stack.enter_context(
        patch(
            "app.api.ai_library_router._fetch_user_project_ids",
            AsyncMock(return_value=[]),
        )
    )
    return stack


def _repo(*, skills, binding_map):
    repo = AsyncMock()
    repo.list_accessible.return_value = skills
    repo.list_files.return_value = []
    repo.map_binding_agents.return_value = binding_map
    return repo


@pytest.mark.asyncio
async def test_list_reports_binding_agents_per_skill(client: AsyncClient) -> None:
    repo = _repo(
        skills=[_skill(1, "script-outline"), _skill(2, "orphan")],
        binding_map={1: [{"slug": "script_ai", "name": "Script AI"}]},
    )
    with _patches(repo):
        resp = await client.get(f"{BASE}/skills")

    assert resp.status_code == 200
    by_slug = {s["slug"]: s for s in resp.json()}
    assert by_slug["script-outline"]["agents"] == [
        {"slug": "script_ai", "name": "Script AI"}
    ]
    # An unbound skill must come back as [] — the gallery's orphan warning
    # keys off that, so "absent" and "empty" cannot be allowed to differ.
    assert by_slug["orphan"]["agents"] == []


@pytest.mark.asyncio
async def test_binding_lookup_is_one_batched_call(client: AsyncClient) -> None:
    repo = _repo(
        skills=[_skill(i, f"skill-{i}") for i in range(1, 10)],
        binding_map={},
    )
    with _patches(repo):
        resp = await client.get(f"{BASE}/skills")

    assert resp.status_code == 200
    assert repo.map_binding_agents.await_count == 1
    # The per-skill helper must not be used here at all.
    assert repo.list_binding_agents.await_count == 0


def test_router_does_not_look_up_bindings_inside_a_loop() -> None:
    """Source guard — the N+1 shape is exactly what made the old code skip
    this field entirely; it must not come back."""
    module = importlib.import_module("app.api.ai_library_router")
    tree = ast.parse(textwrap.dedent(inspect.getsource(module.list_skills)))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            continue
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Attribute)
                and inner.func.attr in {"map_binding_agents", "list_binding_agents"}
            ):
                pytest.fail(f"{inner.func.attr} is called inside a loop (N+1)")

"""Row-scope guard on the by-slug ``/ai-library`` agent and skill routes.

Before OpenAPI P5 these routes only asked "is it a system preset". Any
logged-in user could PATCH, pause, resume or roll back someone else's
private agent, and could edit, roll back or rewrite the files of someone
else's skill, just by naming its slug. They could also read its prompts
and version history the same way.

The guard's real composition runs here. Only the three DB leaves are
stubbed with the answer the database would give for that identity:
``_user_is_team_member``, ``_user_can_write_project`` and ``_user_is_admin``.
Each denial has a positive control on the same setup, so a 403 or 404 has
to come from the identity and not from something else failing.
"""

from __future__ import annotations

import sys
from contextlib import ExitStack
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import get_auth
from app.main import app
from tests.api.ai_library_wire_helpers import USER, ScriptedScope, fake_auth

r = sys.modules["app.api.ai_library_router"]

pytestmark = pytest.mark.unit

BASE = "/api/v1/ai-library"
AGENT_ID = "00000000-0000-0000-0000-0000000000b7"
STRANGER = "99999999-9999-9999-9999-999999999999"
TEAM_ID = 7300000000000000042
PROJECT_ID = 7300000000000000077
NOW = "2026-09-24T00:00:00+00:00"


@pytest.fixture(autouse=True)
def _auth():
    app.dependency_overrides[get_auth] = fake_auth
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _agent(**over: Any) -> Dict[str, Any]:
    row = {
        "id": AGENT_ID,
        "slug": "writer",
        "name": "Writer",
        "is_system_preset": False,
        "user_id": STRANGER,
        "team_id": None,
        "project_id": None,
        "paused_reason": None,
        "current_version": 3,
        "model": "qwen-max",
        "created_at": NOW,
        "updated_at": NOW,
    }
    row.update(over)
    return row


def _skill(**over: Any) -> Dict[str, Any]:
    row = {
        "id": 7300000000000000999,
        "slug": "notes",
        "name": "Notes",
        "is_public": False,
        "created_by": STRANGER,
        "team_id": None,
        "project_id": None,
        "current_version": 2,
        "created_at": NOW,
        "updated_at": NOW,
    }
    row.update(over)
    return row


class _Identity:
    """What the database says about the caller."""

    def __init__(self, *, member=False, project=False, admin=False) -> None:
        self.member, self.project, self.admin = member, project, admin

    def patches(self) -> list:
        return [
            patch.object(
                r, "_user_is_team_member", AsyncMock(return_value=self.member)
            ),
            patch.object(
                r, "_user_can_write_project", AsyncMock(return_value=self.project)
            ),
            patch.object(r, "_user_is_admin", AsyncMock(return_value=self.admin)),
        ]


STRANGER_ID = _Identity()


def _repos(agent: Dict[str, Any] | None = None, skill: Dict[str, Any] | None = None):
    agent_repo = MagicMock()
    agent_repo.get_by_slug = AsyncMock(return_value=agent)
    agent_repo.get_skill_ids = AsyncMock(return_value=[])
    agent_repo.update_fields = AsyncMock()
    agent_repo.update_fields_versioned = AsyncMock()
    agent_repo.update_skill_bindings = AsyncMock()
    skill_repo = MagicMock()
    skill_repo.get_by_slug = AsyncMock(return_value=skill)
    skill_repo.list_files = AsyncMock(return_value=[])
    skill_repo.list_binding_agents = AsyncMock(return_value=[])
    skill_repo.update_fields_versioned = AsyncMock()
    skill_repo.upsert_file_versioned = AsyncMock(
        return_value={
            "id": "00000000-0000-0000-0000-00000000f11e",
            "skill_id": 7300000000000000999,
            "path": "a.md",
            "content": "x",
            "file_type": "markdown",
            "binary_url": None,
        }
    )
    skill_repo.delete_file = AsyncMock()
    return agent_repo, skill_repo


async def _call(
    client: AsyncClient,
    method: str,
    url: str,
    identity: _Identity,
    repos,
    *,
    json: Any = None,
    scope_results: list | None = None,
):
    with ExitStack() as stack:
        stack.enter_context(patch.object(r, "_repos", return_value=repos))
        stack.enter_context(
            patch.object(
                r,
                "_enrich_rows_with_scope_names",
                AsyncMock(side_effect=lambda rows: rows),
            )
        )
        stack.enter_context(
            patch.object(
                r,
                "_enrich_agents_with_scope_names",
                AsyncMock(side_effect=lambda rows: rows),
            )
        )
        stack.enter_context(
            patch.object(
                r,
                "_enrich_skills_with_scope_names",
                AsyncMock(side_effect=lambda rows: rows),
            )
        )
        stack.enter_context(
            patch("app.db.session.read_scope", ScriptedScope(scope_results or []))
        )
        for p in identity.patches():
            stack.enter_context(p)
        return await client.request(method, f"{BASE}{url}", json=json)


# ── Agent writes: 403 for a stranger, nothing written ─────────────────────

AGENT_WRITES = [
    ("PATCH", "/agents/writer", {"agent_md": "hijacked"}),
    ("POST", "/agents/writer/pause", None),
    ("POST", "/agents/writer/rollback/1", None),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("method,url,body", AGENT_WRITES)
async def test_stranger_cannot_write_agent(client, method, url, body) -> None:
    repos = _repos(agent=_agent())
    resp = await _call(client, method, url, STRANGER_ID, repos, json=body)
    assert resp.status_code == 403, resp.text
    agent_repo = repos[0]
    agent_repo.update_fields.assert_not_awaited()
    agent_repo.update_fields_versioned.assert_not_awaited()


@pytest.mark.asyncio
async def test_stranger_cannot_resume_agent(client) -> None:
    repos = _repos(agent=_agent(paused_reason="manual"))
    resp = await _call(client, "POST", "/agents/writer/resume", STRANGER_ID, repos)
    assert resp.status_code == 403, resp.text
    repos[0].update_fields.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "row,identity",
    [
        (_agent(user_id=USER), STRANGER_ID),
        (_agent(team_id=TEAM_ID), _Identity(member=True)),
        (_agent(project_id=PROJECT_ID), _Identity(project=True)),
        (_agent(), _Identity(admin=True)),
    ],
    ids=["creator", "team-member", "project-member", "platform-admin"],
)
async def test_in_scope_caller_can_patch_agent(client, row, identity) -> None:
    """Positive control for the PATCH denial: same request, caller in scope."""
    repos = _repos(agent=row)
    resp = await _call(
        client, "PATCH", "/agents/writer", identity, repos, json={"agent_md": "v2"}
    )
    assert resp.status_code == 200, resp.text
    repos[0].update_fields_versioned.assert_awaited_once()


@pytest.mark.asyncio
async def test_team_agent_denies_non_member(client) -> None:
    """A team-scoped agent is not editable by someone outside that team."""
    repos = _repos(agent=_agent(team_id=TEAM_ID))
    resp = await _call(
        client, "PATCH", "/agents/writer", STRANGER_ID, repos, json={"agent_md": "x"}
    )
    assert resp.status_code == 403
    repos[0].update_fields_versioned.assert_not_awaited()


@pytest.mark.asyncio
async def test_owner_can_pause_and_roll_back(client) -> None:
    repos = _repos(agent=_agent(user_id=USER))
    resp = await _call(client, "POST", "/agents/writer/pause", STRANGER_ID, repos)
    assert resp.status_code == 200, resp.text

    snapshot = [
        {
            "identity_md": "old",
            "soul_md": None,
            "agent_md": None,
            "model": "qwen-max",
            "temperature": 0.7,
            "max_tokens": 4096,
        }
    ]
    repos = _repos(agent=_agent(user_id=USER))
    resp = await _call(
        client,
        "POST",
        "/agents/writer/rollback/1",
        STRANGER_ID,
        repos,
        scope_results=[snapshot],
    )
    assert resp.status_code == 200, resp.text
    repos[0].update_fields_versioned.assert_awaited_once()


@pytest.mark.asyncio
async def test_system_preset_rollback_stays_403_even_for_admin(client) -> None:
    repos = _repos(agent=_agent(is_system_preset=True, user_id=None))
    resp = await _call(
        client, "POST", "/agents/writer/rollback/1", _Identity(admin=True), repos
    )
    assert resp.status_code == 403
    assert "system preset" in resp.json()["error"]


# ── Agent reads: out of scope looks like a missing slug ──────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url", ["/agents/writer", "/agents/writer/versions", "/agents/writer/versions/1"]
)
async def test_stranger_cannot_read_private_agent(client, url) -> None:
    resp = await _call(client, "GET", url, STRANGER_ID, _repos(agent=_agent()))
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_preset_and_own_agent_stay_readable(client) -> None:
    for row in (_agent(is_system_preset=True, user_id=None), _agent(user_id=USER)):
        resp = await _call(
            client, "GET", "/agents/writer", STRANGER_ID, _repos(agent=row)
        )
        assert resp.status_code == 200, resp.text


# ── Skills ────────────────────────────────────────────────────────────────

SKILL_WRITES = [
    ("PATCH", "/skills/notes", {"body_md": "hijacked"}),
    (
        "PUT",
        "/skills/notes/files/a.md",
        {"path": "a.md", "content": "x", "file_type": "markdown"},
    ),
    ("DELETE", "/skills/notes/files/a.md", None),
    ("POST", "/skills/notes/rollback/1", None),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("method,url,body", SKILL_WRITES)
async def test_stranger_cannot_write_skill(client, method, url, body) -> None:
    repos = _repos(skill=_skill())
    resp = await _call(client, method, url, STRANGER_ID, repos, json=body)
    assert resp.status_code == 403, resp.text
    skill_repo = repos[1]
    skill_repo.update_fields_versioned.assert_not_awaited()
    skill_repo.upsert_file_versioned.assert_not_awaited()
    skill_repo.delete_file.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "row,identity",
    [
        (_skill(created_by=USER), STRANGER_ID),
        (_skill(team_id=TEAM_ID), _Identity(member=True)),
        (_skill(project_id=PROJECT_ID), _Identity(project=True)),
        (_skill(), _Identity(admin=True)),
    ],
    ids=["creator", "team-member", "project-member", "platform-admin"],
)
async def test_in_scope_caller_can_write_skill(client, row, identity) -> None:
    repos = _repos(skill=row)
    resp = await _call(
        client, "PATCH", "/skills/notes", identity, repos, json={"body_md": "v2"}
    )
    assert resp.status_code == 200, resp.text
    repos[1].update_fields_versioned.assert_awaited_once()

    repos = _repos(skill=row)
    resp = await _call(client, "DELETE", "/skills/notes/files/a.md", identity, repos)
    assert resp.status_code == 204, resp.text
    repos[1].delete_file.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url", ["/skills/notes", "/skills/notes/files", "/skills/notes/versions"]
)
async def test_stranger_cannot_read_private_skill(client, url) -> None:
    resp = await _call(client, "GET", url, STRANGER_ID, _repos(skill=_skill()))
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_public_and_own_skill_stay_readable(client) -> None:
    for row in (_skill(is_public=True, created_by=None), _skill(created_by=USER)):
        resp = await _call(
            client, "GET", "/skills/notes/files", STRANGER_ID, _repos(skill=row)
        )
        assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_admin_lookup_error_fails_closed() -> None:
    with (
        patch.object(r, "_user_is_team_member", AsyncMock(return_value=False)),
        patch.object(r, "_user_is_admin", AsyncMock(side_effect=RuntimeError("db"))),
    ):
        allowed = await r._in_row_scope(_agent(), UUID(USER), owner_key="user_id")
    assert allowed is False

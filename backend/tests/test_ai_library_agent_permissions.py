"""Tests for agent chat-permission schema + PATCH carve-out (CHAT-PERM-15/16)."""

from __future__ import annotations

from typing import Any, Dict
from unittest.mock import AsyncMock
from unittest.mock import patch as _patch
from uuid import UUID as _UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.ai_library_router import _can_edit_chat_permissions, router
from app.schemas.ai_library import AgentUpdate, ChatPermissionsIn


def test_chat_permissions_all_optional():
    p = ChatPermissionsIn()
    assert p.model_dump(exclude_none=True) == {}


def test_chat_permissions_partial():
    p = ChatPermissionsIn(enabled=True)
    assert p.model_dump(exclude_none=True) == {"enabled": True}


def test_agent_update_accepts_chat_permissions():
    u = AgentUpdate(
        chat_permissions=ChatPermissionsIn(enabled=True, allowed_team_ids=[1, 2])
    )
    assert u.chat_permissions is not None
    assert u.chat_permissions.enabled is True
    assert u.chat_permissions.allowed_team_ids == [1, 2]


def test_chat_permissions_out_from_caps():
    from app.schemas.ai_library import ChatPermissionsOut
    from app.services.ai.permissions.agent_chat_caps import agent_chat_caps

    caps = agent_chat_caps({"capability_profile": {"chat": {"enabled": True}}})
    out = ChatPermissionsOut.from_caps(caps)
    assert out.enabled is True
    assert out.read_team_resources is False
    assert out.allowed_team_ids == []


# ---------------------------------------------------------------------------
# Router-level tests: role gate + preset carve-out + deep-merge (CHAT-PERM-08-prep/15)
# ---------------------------------------------------------------------------


def _client_with_repo(existing: Dict[str, Any], captured: dict):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    from app.core.deps import get_auth

    class _Auth:
        user_id = "11111111-1111-1111-1111-111111111111"
        email = "u@example.com"

    async def _grant():
        return _Auth()

    app.dependency_overrides[get_auth] = _grant

    repo = AsyncMock()
    repo.get_by_slug.return_value = existing

    async def _update_fields(agent_uuid, updates, created_by=None):
        captured["updates"] = updates

    repo.update_fields_versioned.side_effect = _update_fields
    repo.get_skill_ids.return_value = []
    repo.update_skill_bindings.return_value = None
    return app, repo


def _preset_agent() -> Dict[str, Any]:
    return {
        "id": "22222222-2222-2222-2222-222222222222",
        "slug": "script_ai",
        "name": "Script AI",
        "is_system_preset": True,
        "capability_profile": {"allowed_skills": ["script-outline"]},
        "model": "qwen-max",
        "temperature": 0.7,
        "max_tokens": 4096,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }


def test_patch_chat_permissions_on_preset_merges_and_returns_perms():
    captured: dict = {}
    app, repo = _client_with_repo(_preset_agent(), captured)
    with (
        _patch("app.api.ai_library_router._repos", return_value=(repo, None)),
        _patch(
            "app.api.ai_library_router._enrich_agents_with_scope_names",
            new=AsyncMock(side_effect=lambda rows: rows),
        ),
        _patch(
            # role gate: allow (platform admin). Tested separately below.
            "app.api.ai_library_router._can_edit_chat_permissions",
            new=AsyncMock(return_value=True),
        ),
    ):
        # refresh get_by_slug returns the merged row so the response reflects it
        merged = {
            **_preset_agent(),
            "capability_profile": {
                "allowed_skills": ["script-outline"],
                "chat": {"enabled": True, "read_team_resources": True},
            },
        }
        repo.get_by_slug.side_effect = [_preset_agent(), merged]
        client = TestClient(app)
        resp = client.patch(
            "/api/v1/ai-library/agents/script_ai",
            json={"chat_permissions": {"enabled": True, "read_team_resources": True}},
        )
    assert resp.status_code == 200, resp.text
    # storage merged, did NOT clobber the existing Phase 4.5 key
    prof = captured["updates"]["capability_profile"]
    assert prof["allowed_skills"] == ["script-outline"]
    assert prof["chat"]["enabled"] is True
    # review C1: the response actually carries the resolved perms
    body = resp.json()
    assert body["chat_permissions"]["enabled"] is True
    assert body["chat_permissions"]["read_team_resources"] is True


def test_patch_chat_permissions_denied_without_role():
    captured: dict = {}
    app, repo = _client_with_repo(_preset_agent(), captured)
    with (
        _patch("app.api.ai_library_router._repos", return_value=(repo, None)),
        _patch(
            "app.api.ai_library_router._can_edit_chat_permissions",
            new=AsyncMock(return_value=False),
        ),
    ):
        client = TestClient(app)
        resp = client.patch(
            "/api/v1/ai-library/agents/script_ai",
            json={"chat_permissions": {"enabled": True}},
        )
    assert resp.status_code == 403  # review H1: role gate


def test_patch_content_field_on_preset_still_403():
    captured: dict = {}
    app, repo = _client_with_repo(_preset_agent(), captured)
    with _patch("app.api.ai_library_router._repos", return_value=(repo, None)):
        client = TestClient(app)
        resp = client.patch(
            "/api/v1/ai-library/agents/script_ai", json={"name": "Hacked"}
        )
    assert resp.status_code == 403


def test_patch_mixed_content_and_perms_on_preset_403():
    captured: dict = {}
    app, repo = _client_with_repo(_preset_agent(), captured)
    with _patch("app.api.ai_library_router._repos", return_value=(repo, None)):
        client = TestClient(app)
        resp = client.patch(
            "/api/v1/ai-library/agents/script_ai",
            json={"name": "X", "chat_permissions": {"enabled": True}},
        )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Unit tests for _can_edit_chat_permissions composition logic (Fix #2)
# ---------------------------------------------------------------------------

_CALLER_ID = _UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_OTHER_ID = _UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
_TEAM_ID = 42


def _user_agent(owner_id=_CALLER_ID) -> Dict[str, Any]:
    """Agent owned by a specific user (no team)."""
    return {
        "id": "22222222-2222-2222-2222-222222222222",
        "slug": "my-agent",
        "user_id": str(owner_id),
        "team_id": None,
        "is_system_preset": False,
    }


def _team_agent(owner_id=_OTHER_ID, team_id=_TEAM_ID) -> Dict[str, Any]:
    """Agent scoped to a team, owned by someone else."""
    return {
        "id": "33333333-3333-3333-3333-333333333333",
        "slug": "team-agent",
        "user_id": str(owner_id),
        "team_id": team_id,
        "is_system_preset": False,
    }


def _preset_agent_dict() -> Dict[str, Any]:
    """System-preset agent (no user_id, no team)."""
    return {
        "id": "44444444-4444-4444-4444-444444444444",
        "slug": "preset-agent",
        "user_id": None,
        "team_id": None,
        "is_system_preset": True,
    }


@pytest.mark.asyncio
async def test_can_edit_agent_owner_returns_true():
    """(a) caller IS the agent owner → True (no team/admin lookup needed)."""
    agent = _user_agent(owner_id=_CALLER_ID)
    with (
        _patch(
            "app.api.ai_library_router._is_team_owner",
            new=AsyncMock(return_value=False),
        ) as mock_team,
        _patch(
            "app.api.ai_library_router._user_is_admin",
            new=AsyncMock(return_value=False),
        ) as mock_admin,
    ):
        result = await _can_edit_chat_permissions(None, agent, _CALLER_ID)
    assert result is True
    # Short-circuit: neither dependency should have been called
    mock_team.assert_not_called()
    mock_admin.assert_not_called()


@pytest.mark.asyncio
async def test_can_edit_team_owner_returns_true():
    """(b) caller is team owner/admin (owner check fails, _is_team_owner → True) → True."""
    agent = _team_agent()
    with (
        _patch(
            "app.api.ai_library_router._is_team_owner",
            new=AsyncMock(return_value=True),
        ) as mock_team,
        _patch(
            "app.api.ai_library_router._user_is_admin",
            new=AsyncMock(return_value=False),
        ) as mock_admin,
    ):
        result = await _can_edit_chat_permissions(None, agent, _CALLER_ID)
    assert result is True
    mock_team.assert_called_once_with(_CALLER_ID, _TEAM_ID)
    mock_admin.assert_not_called()


@pytest.mark.asyncio
async def test_can_edit_stranger_returns_false():
    """(c) stranger: owner check fails, _is_team_owner → False, _user_is_admin → False → False."""
    agent = _team_agent()
    with (
        _patch(
            "app.api.ai_library_router._is_team_owner",
            new=AsyncMock(return_value=False),
        ),
        _patch(
            "app.api.ai_library_router._user_is_admin",
            new=AsyncMock(return_value=False),
        ),
    ):
        result = await _can_edit_chat_permissions(None, agent, _CALLER_ID)
    assert result is False


@pytest.mark.asyncio
async def test_can_edit_platform_admin_returns_true():
    """(d) platform admin: owner+team False, _user_is_admin → True → True."""
    agent = _preset_agent_dict()
    with (
        _patch(
            "app.api.ai_library_router._is_team_owner",
            new=AsyncMock(return_value=False),
        ),
        _patch(
            "app.api.ai_library_router._user_is_admin",
            new=AsyncMock(return_value=True),
        ),
    ):
        result = await _can_edit_chat_permissions(None, agent, _CALLER_ID)
    assert result is True


@pytest.mark.asyncio
async def test_can_edit_admin_db_error_returns_false():
    """(e) _user_is_admin raises → function returns False (fail-closed wrap from fix #1)."""
    agent = _preset_agent_dict()
    with (
        _patch(
            "app.api.ai_library_router._is_team_owner",
            new=AsyncMock(return_value=False),
        ),
        _patch(
            "app.api.ai_library_router._user_is_admin",
            new=AsyncMock(side_effect=RuntimeError("DB connection timeout")),
        ),
    ):
        result = await _can_edit_chat_permissions(None, agent, _CALLER_ID)
    assert result is False

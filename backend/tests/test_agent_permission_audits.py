"""Tests for permission-change audit writes + read endpoint (agent permissions
overhaul Task 2).

Style mirrors ``test_ai_library_agent_permissions.py``: direct-call /
router-level tests against a mocked ``AgentRepository`` (AsyncMock), never a
real DB session.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock
from unittest.mock import patch as _patch
from uuid import UUID as _UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.ai_library_router import router
from app.schemas.ai_library import AgentUpdate

_CALLER_ID = "11111111-1111-1111-1111-111111111111"


def _client_with_repo(existing: Dict[str, Any], captured: dict):
    """Same harness as test_ai_library_agent_permissions.py, extended to
    capture the ``permission_audit`` kwarg passed to update_fields_versioned."""
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    from app.core.deps import get_auth

    class _Auth:
        user_id = _CALLER_ID
        email = "u@example.com"

    async def _grant():
        return _Auth()

    app.dependency_overrides[get_auth] = _grant

    repo = AsyncMock()
    repo.get_by_slug.return_value = existing

    async def _update_fields(
        agent_uuid, updates, created_by=None, notes=None, permission_audit=None
    ):
        captured["updates"] = updates
        captured["created_by"] = created_by
        captured["permission_audit"] = permission_audit

    repo.update_fields_versioned.side_effect = _update_fields
    repo.get_skill_ids.return_value = []
    repo.update_skill_bindings.return_value = None
    return app, repo


def _user_agent() -> Dict[str, Any]:
    """Non-preset, user-owned agent with a pre-existing chat grant so the
    "before" snapshot isn't all-defaults."""
    return {
        "id": "22222222-2222-2222-2222-222222222222",
        "slug": "my-agent",
        "name": "My Agent",
        "is_system_preset": False,
        "user_id": _CALLER_ID,
        "team_id": None,
        "capability_profile": {"chat": {"enabled": True}},
        "model": "qwen-max",
        "temperature": 0.7,
        "max_tokens": 4096,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }


# ---------------------------------------------------------------------------
# 1) PATCH capabilities -> permission_audit carries the resolved snapshot
# ---------------------------------------------------------------------------


def test_patch_capabilities_produces_resolved_permission_audit():
    captured: dict = {}
    existing = _user_agent()
    app, repo = _client_with_repo(existing, captured)
    merged = {
        **existing,
        "capability_profile": {
            "chat": {"enabled": True},
            "capabilities": {"write_level": "write"},
        },
    }
    repo.get_by_slug.side_effect = [existing, merged]
    with (
        _patch("app.api.ai_library_router._repos", return_value=(repo, None)),
        _patch(
            "app.api.ai_library_router._enrich_agents_with_scope_names",
            new=AsyncMock(side_effect=lambda rows: rows),
        ),
        _patch(
            "app.api.ai_library_router._can_edit_chat_permissions",
            new=AsyncMock(return_value=True),
        ),
    ):
        client = TestClient(app)
        resp = client.patch(
            "/api/v1/ai-library/agents/my-agent",
            json={
                "capabilities": {"write_level": "write"},
                "permission_change_reason": "grant write for episode X",
            },
        )
    assert resp.status_code == 200, resp.text

    audit = captured["permission_audit"]
    assert audit is not None
    assert audit["agent_id"] == _UUID(existing["id"])
    assert audit["changed_by"] == _UUID(_CALLER_ID)
    assert audit["reason"] == "grant write for episode X"

    before = audit["before_json"]
    after = audit["after_json"]
    # resolved shape: both chat + capabilities subtrees present
    assert set(before.keys()) == {"chat", "capabilities"}
    assert set(after.keys()) == {"chat", "capabilities"}
    # write_level key exists in the resolved (fail-closed) capabilities shape
    assert "write_level" in before["capabilities"]
    assert "write_level" in after["capabilities"]
    assert before["capabilities"]["write_level"] == "none"  # nothing granted yet
    assert after["capabilities"]["write_level"] == "write"


# ---------------------------------------------------------------------------
# 1b) PATCH whose RESOLVED before == after -> no permission_audit row, even
#     though the raw capability_profile changes (final-review Important 2).
# ---------------------------------------------------------------------------


def test_patch_capabilities_resolved_noop_skips_audit_row():
    captured: dict = {}
    existing = _user_agent()
    # Raw profile has no "capabilities" key at all — the resolved default for
    # write_level is already "none".
    existing["capability_profile"] = {"chat": {"enabled": True}}
    app, repo = _client_with_repo(existing, captured)
    # Payload explicitly sets write_level="none", which the raw-comparison
    # path would see as {} -> {"write_level": "none"} (a "change"), but the
    # RESOLVED value is "none" both before and after.
    merged = {
        **existing,
        "capability_profile": {
            "chat": {"enabled": True},
            "capabilities": {"write_level": "none"},
        },
    }
    repo.get_by_slug.side_effect = [existing, merged]
    with (
        _patch("app.api.ai_library_router._repos", return_value=(repo, None)),
        _patch(
            "app.api.ai_library_router._enrich_agents_with_scope_names",
            new=AsyncMock(side_effect=lambda rows: rows),
        ),
        _patch(
            "app.api.ai_library_router._can_edit_chat_permissions",
            new=AsyncMock(return_value=True),
        ),
    ):
        client = TestClient(app)
        resp = client.patch(
            "/api/v1/ai-library/agents/my-agent",
            json={"capabilities": {"write_level": "none"}},
        )
    assert resp.status_code == 200, resp.text

    # No audit row was written...
    assert captured.get("permission_audit") is None
    # ...but the raw capability_profile was still passed through to the
    # update — the profile itself is not gated on the audit-row decision.
    assert captured["updates"]["capability_profile"] == {
        "chat": {"enabled": True},
        "capabilities": {"write_level": "none"},
    }


# ---------------------------------------------------------------------------
# 2) Content-only PATCH -> no permission_audit
# ---------------------------------------------------------------------------


def test_patch_content_only_no_permission_audit():
    captured: dict = {}
    existing = _user_agent()
    app, repo = _client_with_repo(existing, captured)
    repo.get_by_slug.side_effect = [existing, existing]
    with (
        _patch("app.api.ai_library_router._repos", return_value=(repo, None)),
        _patch(
            "app.api.ai_library_router._enrich_agents_with_scope_names",
            new=AsyncMock(side_effect=lambda rows: rows),
        ),
    ):
        client = TestClient(app)
        resp = client.patch(
            "/api/v1/ai-library/agents/my-agent", json={"name": "Renamed"}
        )
    assert resp.status_code == 200, resp.text
    assert captured.get("permission_audit") is None


# ---------------------------------------------------------------------------
# 3) GET permission-audits: 403 / 404 / 200
# ---------------------------------------------------------------------------


def _client_for_get(agent: Optional[Dict[str, Any]], audits: List[Dict[str, Any]]):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    from app.core.deps import get_auth

    class _Auth:
        user_id = _CALLER_ID
        email = "u@example.com"

    async def _grant():
        return _Auth()

    app.dependency_overrides[get_auth] = _grant

    repo = AsyncMock()
    repo.get_by_slug.return_value = agent
    repo.list_permission_audits.return_value = audits
    return app, repo


def test_get_permission_audits_forbidden_without_role():
    app, repo = _client_for_get(_user_agent(), [])
    with (
        _patch("app.api.ai_library_router._repos", return_value=(repo, None)),
        _patch(
            "app.api.ai_library_router._can_edit_chat_permissions",
            new=AsyncMock(return_value=False),
        ),
    ):
        client = TestClient(app)
        resp = client.get("/api/v1/ai-library/agents/my-agent/permission-audits")
    assert resp.status_code == 403


def test_get_permission_audits_404_when_agent_missing():
    app, repo = _client_for_get(None, [])
    with _patch("app.api.ai_library_router._repos", return_value=(repo, None)):
        client = TestClient(app)
        resp = client.get("/api/v1/ai-library/agents/missing/permission-audits")
    assert resp.status_code == 404


def test_get_permission_audits_repo_error_propagates():
    """A DB error out of list_permission_audits must NOT be disguised as
    "200 + empty history" (final-review Important 1). The repo no longer
    swallows exceptions, so the endpoint should surface the failure instead
    of silently returning []."""
    agent = _user_agent()
    app, repo = _client_for_get(agent, [])
    repo.list_permission_audits.side_effect = RuntimeError("db unreachable")
    with (
        _patch("app.api.ai_library_router._repos", return_value=(repo, None)),
        _patch(
            "app.api.ai_library_router._can_edit_chat_permissions",
            new=AsyncMock(return_value=True),
        ),
    ):
        client = TestClient(app)
        with pytest.raises(RuntimeError, match="db unreachable"):
            client.get("/api/v1/ai-library/agents/my-agent/permission-audits")


def test_get_permission_audits_rejects_invalid_limit():
    """``limit`` is now bounded via Query(ge=1, le=100) -- an out-of-range
    value 422s at the FastAPI validation layer before the endpoint (and thus
    the no-longer-exception-swallowing repo call) ever runs."""
    agent = _user_agent()
    app, repo = _client_for_get(agent, [])
    with (
        _patch("app.api.ai_library_router._repos", return_value=(repo, None)),
        _patch(
            "app.api.ai_library_router._can_edit_chat_permissions",
            new=AsyncMock(return_value=True),
        ),
    ):
        client = TestClient(app)
        resp = client.get(
            "/api/v1/ai-library/agents/my-agent/permission-audits?limit=-1"
        )
    assert resp.status_code == 422
    repo.list_permission_audits.assert_not_called()


def test_get_permission_audits_returns_items():
    agent = _user_agent()
    rows = [
        {
            "id": "123456789012345",
            "changed_by": _CALLER_ID,
            "before": {"chat": {"enabled": False}, "capabilities": {}},
            "after": {"chat": {"enabled": True}, "capabilities": {}},
            "reason": "enable chat",
            "created_at": "2026-08-10T00:00:00+00:00",
        }
    ]
    app, repo = _client_for_get(agent, rows)
    with (
        _patch("app.api.ai_library_router._repos", return_value=(repo, None)),
        _patch(
            "app.api.ai_library_router._can_edit_chat_permissions",
            new=AsyncMock(return_value=True),
        ),
    ):
        client = TestClient(app)
        resp = client.get("/api/v1/ai-library/agents/my-agent/permission-audits")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert isinstance(item["id"], str)
    assert isinstance(item["changed_by"], str)
    assert item["reason"] == "enable chat"
    assert item["before"] == rows[0]["before"]
    assert item["after"] == rows[0]["after"]


# ---------------------------------------------------------------------------
# 4) permission_change_reason accepted on AgentUpdate, excluded from content
#    updates handed to update_fields_versioned.
# ---------------------------------------------------------------------------


def test_agent_update_accepts_permission_change_reason():
    u = AgentUpdate(permission_change_reason="testing reason carve-out")
    assert u.permission_change_reason == "testing reason carve-out"


def test_permission_change_reason_not_in_content_updates():
    captured: dict = {}
    existing = _user_agent()
    app, repo = _client_with_repo(existing, captured)
    merged = {
        **existing,
        "capability_profile": {"chat": {"enabled": True, "auto_broadcast": True}},
    }
    repo.get_by_slug.side_effect = [existing, merged]
    with (
        _patch("app.api.ai_library_router._repos", return_value=(repo, None)),
        _patch(
            "app.api.ai_library_router._enrich_agents_with_scope_names",
            new=AsyncMock(side_effect=lambda rows: rows),
        ),
        _patch(
            "app.api.ai_library_router._can_edit_chat_permissions",
            new=AsyncMock(return_value=True),
        ),
    ):
        client = TestClient(app)
        resp = client.patch(
            "/api/v1/ai-library/agents/my-agent",
            json={
                "chat_permissions": {"auto_broadcast": True},
                "permission_change_reason": "testing reason carve-out",
            },
        )
    assert resp.status_code == 200, resp.text
    assert "permission_change_reason" not in captured["updates"]
    assert captured["permission_audit"]["reason"] == "testing reason carve-out"

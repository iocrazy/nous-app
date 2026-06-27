"""Admin memory promotion endpoint tests (Phase C1).

Tests for:
  GET  /admin/settings/memory/promotions?status=pending
  POST /admin/settings/memory/promotions/{proposal_id}/approve
  POST /admin/settings/memory/promotions/{proposal_id}/reject
  POST /admin/settings/memory/{memory_id}/demote

in ``app.api.admin.settings_router``.

Pattern follows test_admin_ai_governance_settings.py: patch the repo
functions at the import site in the router module, pass a MagicMock auth
with ``.user_id``, call each handler directly.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_TEAM_ROW = {
    "id": 1001,
    "memory_id": 42,
    "proposed_scope": "team",
    "target_team_id": 7,
    "target_project_id": None,
    "classification_kind": "project_context",
    "confidence": 0.91,
    "justification": "Team-wide context.",
    "scrubbed_body_md": "## Scrubbed\nSafe content.",
    "status": "pending",
    "created_at": "2026-06-01T12:00:00",
    # JOINed from agent_memory
    "title": "Q2 Context",
    "owner_user_id": "user-uuid-1",
    "body_md": "## Original\nRaw content.",
    "scope": "team",
}

_PROJECT_ROW = {
    "id": 1002,
    "memory_id": 55,
    "proposed_scope": "project",
    "target_team_id": 7,
    "target_project_id": 99,
    "classification_kind": "workflow_pattern",
    "confidence": 0.75,
    "justification": "Project-scoped pattern.",
    "scrubbed_body_md": "## Scrubbed\nProject content.",
    "status": "pending",
    "created_at": "2026-06-02T09:00:00",
    # JOINed from agent_memory
    "title": "Sprint Retrospective",
    "owner_user_id": "user-uuid-2",
    "body_md": "## Original\nProject raw.",
    "scope": "project",
}


def _make_auth(user_id: str = "admin-uuid-1") -> MagicMock:
    auth = MagicMock()
    auth.user_id = user_id
    return auth


# ---------------------------------------------------------------------------
# T1: list_promotions — maps rows to PromotionItem list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_promotions_maps_rows_to_items():
    """GET /memory/promotions returns PromotionListResponse with correct items."""
    from app.api.admin.settings_router import list_memory_promotions

    auth = _make_auth()
    mock_list = AsyncMock(return_value=[_TEAM_ROW, _PROJECT_ROW])

    with patch(
        "app.api.admin.settings_router.list_proposals",
        mock_list,
    ):
        result = await list_memory_promotions(status="pending", auth=auth)

    # Called with the right status
    mock_list.assert_called_once_with(status="pending", limit=100)

    assert len(result.items) == 2

    # Check team-scope row
    team_item = result.items[0]
    assert team_item.id == 1001
    assert team_item.memory_id == 42
    assert team_item.proposed_scope == "team"
    assert team_item.target_team_id == 7
    assert team_item.target_project_id is None
    assert team_item.title == "Q2 Context"
    assert team_item.owner_user_id == "user-uuid-1"
    assert team_item.original_body_md == "## Original\nRaw content."
    assert team_item.scrubbed_body_md == "## Scrubbed\nSafe content."
    assert team_item.classification_kind == "project_context"
    assert team_item.confidence == 0.91
    assert team_item.justification == "Team-wide context."
    assert team_item.status == "pending"
    assert team_item.created_at == "2026-06-01T12:00:00"

    # Check project-scope row — target_project_id must be populated
    project_item = result.items[1]
    assert project_item.id == 1002
    assert project_item.memory_id == 55
    assert project_item.proposed_scope == "project"
    assert project_item.target_team_id == 7
    assert project_item.target_project_id == 99
    assert project_item.title == "Sprint Retrospective"
    assert project_item.owner_user_id == "user-uuid-2"


@pytest.mark.asyncio
async def test_list_promotions_empty():
    """GET /memory/promotions returns empty list when no proposals."""
    from app.api.admin.settings_router import list_memory_promotions

    auth = _make_auth()
    mock_list = AsyncMock(return_value=[])

    with patch("app.api.admin.settings_router.list_proposals", mock_list):
        result = await list_memory_promotions(status="pending", auth=auth)

    assert result.items == []


@pytest.mark.asyncio
async def test_list_promotions_status_param_forwarded():
    """The status query param is forwarded to list_proposals unchanged."""
    from app.api.admin.settings_router import list_memory_promotions

    auth = _make_auth()
    mock_list = AsyncMock(return_value=[])

    with patch("app.api.admin.settings_router.list_proposals", mock_list):
        await list_memory_promotions(status="approved", auth=auth)

    mock_list.assert_called_once_with(status="approved", limit=100)


# ---------------------------------------------------------------------------
# T2: approve_memory_promotion — forwards reviewer_id=auth.user_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_forwards_reviewer_id():
    """POST /memory/promotions/{id}/approve calls approve_proposal with reviewer_id=auth.user_id."""
    from app.api.admin.settings_router import approve_memory_promotion

    auth = _make_auth("admin-uuid-approve")
    mock_approve = AsyncMock(return_value=True)

    with patch("app.api.admin.settings_router.approve_proposal", mock_approve):
        result = await approve_memory_promotion(proposal_id=1001, auth=auth)

    mock_approve.assert_called_once_with(
        proposal_id=1001, reviewer_id="admin-uuid-approve"
    )
    assert result == {"approved": True}


@pytest.mark.asyncio
async def test_approve_returns_false_when_not_found():
    """approve_proposal returns False → response is {"approved": False}."""
    from app.api.admin.settings_router import approve_memory_promotion

    auth = _make_auth()
    mock_approve = AsyncMock(return_value=False)

    with patch("app.api.admin.settings_router.approve_proposal", mock_approve):
        result = await approve_memory_promotion(proposal_id=9999, auth=auth)

    assert result == {"approved": False}


# ---------------------------------------------------------------------------
# T3: reject_memory_promotion — forwards reviewer_id=auth.user_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reject_forwards_reviewer_id():
    """POST /memory/promotions/{id}/reject calls reject_proposal with reviewer_id=auth.user_id."""
    from app.api.admin.settings_router import reject_memory_promotion

    auth = _make_auth("admin-uuid-reject")
    mock_reject = AsyncMock(return_value=True)

    with patch("app.api.admin.settings_router.reject_proposal", mock_reject):
        result = await reject_memory_promotion(proposal_id=1001, auth=auth)

    mock_reject.assert_called_once_with(
        proposal_id=1001, reviewer_id="admin-uuid-reject"
    )
    assert result == {"rejected": True}


@pytest.mark.asyncio
async def test_reject_returns_false_when_not_found():
    """reject_proposal returns False → response is {"rejected": False}."""
    from app.api.admin.settings_router import reject_memory_promotion

    auth = _make_auth()
    mock_reject = AsyncMock(return_value=False)

    with patch("app.api.admin.settings_router.reject_proposal", mock_reject):
        result = await reject_memory_promotion(proposal_id=9999, auth=auth)

    assert result == {"rejected": False}


# ---------------------------------------------------------------------------
# T4: demote_agent_memory — calls demote_memory with memory_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_demote_calls_demote_memory():
    """POST /memory/{memory_id}/demote calls demote_memory and returns {"demoted": True}."""
    from app.api.admin.settings_router import demote_agent_memory

    auth = _make_auth("admin-uuid-demote")
    mock_demote = AsyncMock(return_value=True)

    with patch("app.api.admin.settings_router.demote_memory", mock_demote):
        result = await demote_agent_memory(memory_id=42, auth=auth)

    mock_demote.assert_called_once_with(memory_id=42)
    assert result == {"demoted": True}


@pytest.mark.asyncio
async def test_demote_returns_false_on_failure():
    """demote_memory returns False → response is {"demoted": False}."""
    from app.api.admin.settings_router import demote_agent_memory

    auth = _make_auth()
    mock_demote = AsyncMock(return_value=False)

    with patch("app.api.admin.settings_router.demote_memory", mock_demote):
        result = await demote_agent_memory(memory_id=999, auth=auth)

    assert result == {"demoted": False}

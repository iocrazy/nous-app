"""Unit tests for PR-8 Task A — GET /projects/suggestions batch queue
data source (final spec G7).

M2 PR-G1.5: the legacy SOP stage cursor is retired end-to-end, so every
project now degrades to the existing "no stage" suggestion (kind="" — the
frontend renders nothing). There is no more storyboard-stage branch, no
per-project shot-progress fan-out, and no dependency on
``ProjectStagesRepository.stages_for_projects``/``list_catalog`` (both
retired). ``latest_activity``/``stalled`` still come from the existing
``_get_card_enrichment`` batch — workflow badge / file activity are
unaffected by this retirement.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.library.projects_service import ProjectsService

_PID_A = 9101
_PID_B = 9102
_PID_STALLED = 9103


def _stages_repo_mock(activity=None):
    repo = MagicMock()
    repo.latest_activity_for_projects = AsyncMock(return_value=activity or {})
    repo.latest_file_activity_for_projects = AsyncMock(return_value={})
    return repo


def _nodes_repo_mock(badges=None):
    repo = MagicMock()
    repo.workflow_badges_for_projects = AsyncMock(return_value=badges or {})
    return repo


def _svc():
    svc = ProjectsService.__new__(ProjectsService)  # bypass __init__ deps
    svc.repo = MagicMock()
    svc.repo.get_project_members_preview = AsyncMock(return_value={})
    return svc


def _patched(activity=None, badges=None):
    return (
        patch(
            "app.repositories.project_stages_repository.get_project_stages_repository",
            return_value=_stages_repo_mock(activity=activity),
        ),
        patch(
            "app.repositories.project_stage_nodes_repository."
            "get_project_stage_nodes_repository",
            return_value=_nodes_repo_mock(badges=badges),
        ),
    )


@pytest.mark.asyncio
async def test_empty_project_list_returns_empty_items():
    svc = _svc()
    svc.repo.get_user_projects = AsyncMock(return_value=[])
    p1, p2 = _patched()
    with p1, p2:
        items = await svc.get_project_suggestions("user-1")
    assert items == []


@pytest.mark.asyncio
async def test_workflow_project_suggests_its_active_stage():
    """2026-07-28 queue revival: a project whose workflow badge carries an
    active node suggests continuing that stage (kind="workflow_stage")."""
    svc = _svc()
    projects = [{"id": _PID_A, "name": "Workflow Project"}]
    svc.repo.get_user_projects = AsyncMock(return_value=projects)

    badges = {
        str(_PID_A): {
            "current_node_name": "Storyboard",
            "workflow_position": 2,
            "workflow_total": 6,
            "agents_active": 0,
        }
    }
    p1, p2 = _patched(badges=badges)
    with p1, p2:
        items = await svc.get_project_suggestions("user-1")

    assert len(items) == 1
    assert items[0]["kind"] == "workflow_stage"
    assert items[0]["stage_slug"] is None
    assert items[0]["action"] is None
    assert items[0]["progress"] is None


@pytest.mark.asyncio
async def test_every_project_without_workflow_gets_open_row():
    """A project with no workflow badge still gets one queue row
    (kind="open_project") — the queue keeps its one-row-per-active-project
    contract instead of going silently empty (2026-07-27 regression)."""
    svc = _svc()
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(hours=1)).isoformat()
    ancient = (now - timedelta(days=30)).isoformat()  # past the 7-day default SLA

    projects = [
        {"id": _PID_A, "name": "Project A"},
        {"id": _PID_B, "name": "Project B"},
        {"id": _PID_STALLED, "name": "Stalled Project"},
    ]
    svc.repo.get_user_projects = AsyncMock(return_value=projects)

    activity = {
        str(_PID_A): {
            "stage_name": "Storyboard",
            "actor": "heygo",
            "entered_at": recent,
        },
        str(_PID_B): {
            "stage_name": "Planning",
            "actor": "heygo",
            "entered_at": recent,
        },
        str(_PID_STALLED): {
            "stage_name": "Review",
            "actor": "heygo",
            "entered_at": ancient,
        },
    }

    p1, p2 = _patched(activity=activity)
    with p1, p2:
        items = await svc.get_project_suggestions("user-1")

    by_id = {item["project_id"]: item for item in items}
    assert len(items) == 3

    for item in items:
        assert item["kind"] == "open_project"
        assert item["stage_slug"] is None
        assert item["action"] is None
        assert item["progress"] is None

    # latest_activity / stalled still come through the card enrichment —
    # there is no more per-stage SLA (no stage_slug to look up), so every
    # row falls back to the default 7-day dwell threshold.
    assert by_id[str(_PID_A)]["stalled"] is False
    assert by_id[str(_PID_B)]["stalled"] is False
    assert by_id[str(_PID_STALLED)]["stalled"] is True

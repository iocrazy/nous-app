"""Unit tests for PR-8 Task A — GET /projects/suggestions batch queue
data source (final spec G7).

The batch service method (``ProjectsService.get_project_suggestions``)
reuses the exact scope call the list endpoint uses (``repo.get_user_projects``,
non-archived) and the existing 5-way card enrichment (``_get_card_enrichment``)
for stage/activity/stalled — it does NOT reinvent permissions or re-derive
stage state. Only storyboard-stage projects need an extra per-project
progress query, fanned out via ``asyncio.gather`` (capped at 8 concurrent),
each best-effort so a single failing project degrades to a plain navigate
suggestion instead of 500ing the whole batch.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.library.projects_service import ProjectsService

_PID_STORYBOARD = 9101
_PID_PLANNING = 9102
_PID_REVIEW_STALLED = 9103

_CATALOG = [
    {"slug": "planning", "name": "Planning", "sort_order": 10},
    {"slug": "script", "name": "Script", "sort_order": 20},
    {"slug": "storyboard", "name": "Storyboard", "sort_order": 30},
    {"slug": "generation", "name": "Generation", "sort_order": 40},
    {"slug": "review", "name": "Review", "sort_order": 50},
    {"slug": "delivery", "name": "Delivery", "sort_order": 60},
]


def _stages_repo_mock(stage_map, activity=None):
    repo = MagicMock()
    repo.stages_for_projects = AsyncMock(return_value=stage_map)
    repo.latest_activity_for_projects = AsyncMock(return_value=activity or {})
    repo.latest_file_activity_for_projects = AsyncMock(return_value={})
    repo.list_catalog = AsyncMock(return_value=_CATALOG)
    return repo


def _svc():
    svc = ProjectsService.__new__(ProjectsService)  # bypass __init__ deps
    svc.repo = MagicMock()
    svc.repo.get_project_members_preview = AsyncMock(return_value={})
    return svc


@pytest.mark.asyncio
async def test_empty_project_list_returns_empty_items():
    svc = _svc()
    svc.repo.get_user_projects = AsyncMock(return_value=[])
    stages_repo = _stages_repo_mock({})
    with patch(
        "app.repositories.project_stages_repository.get_project_stages_repository",
        return_value=stages_repo,
    ):
        items = await svc.get_project_suggestions("user-1")
    assert items == []


@pytest.mark.asyncio
async def test_batch_covers_storyboard_generate_planning_and_stalled_review():
    svc = _svc()
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(hours=1)).isoformat()
    ancient = (now - timedelta(days=30)).isoformat()  # older than review's 3-day SLA

    projects = [
        {"id": _PID_STORYBOARD, "name": "Storyboard Project"},
        {"id": _PID_PLANNING, "name": "Planning Project"},
        {"id": _PID_REVIEW_STALLED, "name": "Stalled Review Project"},
    ]
    svc.repo.get_user_projects = AsyncMock(return_value=projects)

    stages_repo = _stages_repo_mock(
        stage_map={
            str(_PID_STORYBOARD): {
                "slug": "storyboard",
                "name": "Storyboard",
                "sort_order": 30,
            },
            str(_PID_PLANNING): {
                "slug": "planning",
                "name": "Planning",
                "sort_order": 10,
            },
            str(_PID_REVIEW_STALLED): {
                "slug": "review",
                "name": "Review",
                "sort_order": 50,
            },
        },
        activity={
            str(_PID_STORYBOARD): {
                "stage_name": "Storyboard",
                "actor": "heygo",
                "entered_at": recent,
            },
            str(_PID_PLANNING): {
                "stage_name": "Planning",
                "actor": "heygo",
                "entered_at": recent,
            },
            str(_PID_REVIEW_STALLED): {
                "stage_name": "Review",
                "actor": "heygo",
                "entered_at": ancient,
            },
        },
    )

    shots_repo = AsyncMock()
    shots_repo.storyboard_progress_for_project = AsyncMock(
        return_value={
            "total": 12,
            "done": 9,
            "empty": 3,
            "generating": 0,
            "failed": 0,
            "script_count": 2,
            "scene_count": 5,
        }
    )
    svc._shots_repo_override = shots_repo

    with patch(
        "app.repositories.project_stages_repository.get_project_stages_repository",
        return_value=stages_repo,
    ):
        items = await svc.get_project_suggestions("user-1")

    by_id = {item["project_id"]: item for item in items}
    assert len(items) == 3

    sb = by_id[str(_PID_STORYBOARD)]
    assert sb["kind"] == "storyboard_generate"
    assert sb["action"]["type"] == "generate_missing_frames"
    assert sb["action"]["count"] == 3
    assert sb["stalled"] is False

    pl = by_id[str(_PID_PLANNING)]
    assert pl["kind"] == "planning_nav"
    assert pl["action"]["type"] == "navigate" and pl["action"]["tab"] == "scripts"
    assert pl["stalled"] is False

    rv = by_id[str(_PID_REVIEW_STALLED)]
    assert rv["kind"] == "review_nav"
    assert rv["action"]["type"] == "navigate" and rv["action"]["tab"] == "files"
    assert rv["stalled"] is True

    # Progress is only fetched for the storyboard-stage project, never for
    # planning/review (no batch N+1 across non-storyboard stages).
    shots_repo.storyboard_progress_for_project.assert_awaited_once_with(
        str(_PID_STORYBOARD)
    )


@pytest.mark.asyncio
async def test_storyboard_progress_failure_degrades_to_navigate_not_500():
    svc = _svc()
    projects = [{"id": _PID_STORYBOARD, "name": "Storyboard Project"}]
    svc.repo.get_user_projects = AsyncMock(return_value=projects)

    stages_repo = _stages_repo_mock(
        stage_map={
            str(_PID_STORYBOARD): {
                "slug": "storyboard",
                "name": "Storyboard",
                "sort_order": 30,
            }
        }
    )

    shots_repo = AsyncMock()
    shots_repo.storyboard_progress_for_project = AsyncMock(
        side_effect=RuntimeError("db down")
    )
    svc._shots_repo_override = shots_repo

    with patch(
        "app.repositories.project_stages_repository.get_project_stages_repository",
        return_value=stages_repo,
    ):
        items = await svc.get_project_suggestions("user-1")  # must not raise

    assert len(items) == 1
    assert items[0]["kind"] == "storyboard_nav"
    assert items[0]["action"]["type"] == "navigate"
    assert items[0]["progress"] is None

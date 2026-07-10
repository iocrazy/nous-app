"""Unit tests for PR-8 Task B — every new project is born with an Episode 1
+ an empty script attached (final spec G3), so Episodes/Canvas always have
a target from creation.

Runs AFTER the existing PR-6 default-stage block in
``ProjectsService.create_project`` and follows the same best-effort
discipline: episode/script creation failures must never fail project
creation (see ``test_create_project_default_stage.py`` for the sibling
block this pattern was copied from).
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.services.library.projects_service import ProjectsService


class _FakeRepo:
    """No team_id on the created project row (personal project) — the
    default team_id must come from get_team_id_for_user()."""

    async def create_project(self, data):
        return {"id": 9001, "name": data.get("name", "Test Project"), "team_id": None}


class _TeamRepo:
    """Project already belongs to a team — no user-team lookup needed."""

    async def create_project(self, data):
        return {"id": 9002, "name": data.get("name", "Team Project"), "team_id": "555"}


class _NoStages:
    """Empty catalog short-circuits the PR-6 default-stage block so these
    tests stay focused on the Task B episode/script block."""

    async def list_catalog(self):
        return []


def _svc(repo):
    svc = ProjectsService.__new__(ProjectsService)  # bypass __init__ deps
    svc.repo = repo
    svc._stages_repo_override = _NoStages()
    return svc


@pytest.mark.asyncio
async def test_create_project_creates_default_episode_and_script():
    svc = _svc(_FakeRepo())
    episode_repo = AsyncMock()
    episode_repo.create = AsyncMock(
        return_value={"id": 5001, "project_id": 9001, "title": "Episode 1"}
    )
    script_repo = AsyncMock()
    script_repo.create = AsyncMock(return_value={"id": 6001})

    with (
        patch(
            "app.core.deps.get_team_id_for_user",
            new=AsyncMock(return_value="7001"),
        ) as team_lookup,
        patch(
            "app.repositories.episode_repository.get_episode_repository",
            return_value=episode_repo,
        ),
        patch(
            "app.repositories.script_repository.get_script_project_repository",
            return_value=script_repo,
        ),
    ):
        out = await svc.create_project("user-1", {"name": "Test Project"})

    assert out["id"] == 9001  # create_project's own return value is untouched
    team_lookup.assert_awaited_once_with("user-1")
    episode_repo.create.assert_awaited_once_with(
        {"project_id": 9001, "title": "Episode 1", "sort_order": 1}
    )
    script_repo.create.assert_awaited_once_with(
        {
            "project_id": 9001,
            "team_id": "7001",
            "episode_id": 5001,
            "name": "Episode 1",
            "status": "active",
            "created_by": "user-1",
        }
    )


@pytest.mark.asyncio
async def test_create_project_uses_project_team_id_when_present():
    svc = _svc(_TeamRepo())
    episode_repo = AsyncMock()
    episode_repo.create = AsyncMock(return_value={"id": 5002})
    script_repo = AsyncMock()
    script_repo.create = AsyncMock(return_value={"id": 6002})

    with (
        patch("app.core.deps.get_team_id_for_user", new=AsyncMock()) as team_lookup,
        patch(
            "app.repositories.episode_repository.get_episode_repository",
            return_value=episode_repo,
        ),
        patch(
            "app.repositories.script_repository.get_script_project_repository",
            return_value=script_repo,
        ),
    ):
        await svc.create_project("user-1", {"name": "Team Project"})

    # Project already has a team_id — no fallback lookup needed.
    team_lookup.assert_not_awaited()
    assert script_repo.create.await_args.args[0]["team_id"] == "555"


@pytest.mark.asyncio
async def test_create_survives_episode_creation_failure():
    svc = _svc(_FakeRepo())

    with (
        patch(
            "app.core.deps.get_team_id_for_user",
            new=AsyncMock(return_value="7001"),
        ),
        patch(
            "app.repositories.episode_repository.get_episode_repository",
            side_effect=RuntimeError("episodes down"),
        ),
    ):
        out = await svc.create_project("user-1", {"name": "Test Project"})

    assert out["id"] == 9001  # project creation still succeeds


@pytest.mark.asyncio
async def test_create_survives_script_creation_failure():
    svc = _svc(_FakeRepo())
    episode_repo = AsyncMock()
    episode_repo.create = AsyncMock(return_value={"id": 5001})

    with (
        patch(
            "app.core.deps.get_team_id_for_user",
            new=AsyncMock(return_value="7001"),
        ),
        patch(
            "app.repositories.episode_repository.get_episode_repository",
            return_value=episode_repo,
        ),
        patch(
            "app.repositories.script_repository.get_script_project_repository",
            side_effect=RuntimeError("scripts down"),
        ),
    ):
        out = await svc.create_project("user-1", {"name": "Test Project"})

    assert out["id"] == 9001  # project creation still succeeds


@pytest.mark.asyncio
async def test_create_skips_episode_when_no_team_resolvable():
    svc = _svc(_FakeRepo())
    episode_repo = AsyncMock()

    with (
        patch(
            "app.core.deps.get_team_id_for_user",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.repositories.episode_repository.get_episode_repository",
            return_value=episode_repo,
        ),
    ):
        out = await svc.create_project("user-1", {"name": "Test Project"})

    assert out["id"] == 9001
    episode_repo.create.assert_not_called()

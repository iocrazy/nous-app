import pytest

from app.services.library.projects_service import ProjectsService


class _FakeStages:
    def __init__(self, stage):
        self._stage = stage

    async def get_current(self, pid):
        return self._stage


class _FakeShots:
    def __init__(self, progress):
        self._p = progress

    async def storyboard_progress_for_project(self, pid):
        return self._p


def _svc(stage, progress):
    s = ProjectsService.__new__(ProjectsService)  # bypass __init__ deps
    s._stages_repo_override = _FakeStages(stage)
    s._shots_repo_override = _FakeShots(progress)
    return s


@pytest.mark.asyncio
async def test_no_current_stage_renders_nothing():
    out = await _svc(None, {}).build_stage_suggestion(1)
    assert out["kind"] == "" and out["stage_slug"] is None


@pytest.mark.asyncio
async def test_storyboard_with_empty_shots_is_generate():
    stage = {"slug": "storyboard", "name": "Storyboarding"}
    progress = {
        "total": 12,
        "done": 9,
        "empty": 3,
        "generating": 0,
        "failed": 0,
        "script_count": 2,
        "scene_count": 5,
    }
    out = await _svc(stage, progress).build_stage_suggestion(1)
    assert out["kind"] == "storyboard_generate"
    assert out["action"]["type"] == "generate_missing_frames"
    assert out["action"]["count"] == 3
    assert out["progress"]["done"] == 9


@pytest.mark.asyncio
async def test_storyboard_no_script_navigates_to_scripts():
    stage = {"slug": "storyboard", "name": "Storyboarding"}
    progress = {
        "total": 0,
        "done": 0,
        "empty": 0,
        "generating": 0,
        "failed": 0,
        "script_count": 0,
        "scene_count": 0,
    }
    out = await _svc(stage, progress).build_stage_suggestion(1)
    assert out["kind"] == "storyboard_no_script"
    assert out["action"]["type"] == "navigate" and out["action"]["tab"] == "scripts"


@pytest.mark.asyncio
async def test_storyboard_all_done_is_ready():
    stage = {"slug": "storyboard", "name": "Storyboarding"}
    progress = {
        "total": 12,
        "done": 12,
        "empty": 0,
        "generating": 0,
        "failed": 0,
        "script_count": 1,
        "scene_count": 4,
    }
    out = await _svc(stage, progress).build_stage_suggestion(1)
    assert out["kind"] == "storyboard_ready"
    assert out["action"]["type"] == "navigate"


@pytest.mark.asyncio
async def test_non_storyboard_stage_is_nav():
    stage = {"slug": "planning", "name": "Planning"}
    out = await _svc(stage, {}).build_stage_suggestion(1)
    assert out["kind"] == "planning_nav"
    assert out["action"]["type"] == "navigate"
    assert out["progress"] is None

"""M2 PR-G1.5 retired the legacy SOP stage cursor (``current_stage_id`` /
``ProjectStagesRepository.get_current``) end-to-end, so there is nothing left
to read a stage from and the single-project entry point
(``build_stage_suggestion``) — which could then only return the degraded "no
stage" dict, and had no callers — has been deleted. The rich kind-decision
table (storyboard progress branches, generic navigate) still lives in the pure
``_suggestion_from`` helper, the reference for the ``StageSuggestionResponse``
kind values the frontend renders, and stays covered here directly.
"""

from app.services.library.projects_service import _suggestion_from


def test_suggestion_from_no_stage_renders_nothing():
    out = _suggestion_from(None)
    assert out["kind"] == "" and out["stage_slug"] is None


def test_suggestion_from_storyboard_with_empty_shots_is_generate():
    progress = {
        "total": 12,
        "done": 9,
        "empty": 3,
        "generating": 0,
        "failed": 0,
        "script_count": 2,
        "scene_count": 5,
    }
    out = _suggestion_from("storyboard", progress)
    assert out["kind"] == "storyboard_generate"
    assert out["action"]["type"] == "generate_missing_frames"
    assert out["action"]["count"] == 3
    assert out["progress"]["done"] == 9


def test_suggestion_from_storyboard_no_script_navigates_to_scripts():
    progress = {
        "total": 0,
        "done": 0,
        "empty": 0,
        "generating": 0,
        "failed": 0,
        "script_count": 0,
        "scene_count": 0,
    }
    out = _suggestion_from("storyboard", progress)
    assert out["kind"] == "storyboard_no_script"
    assert out["action"]["type"] == "navigate" and out["action"]["tab"] == "scripts"


def test_suggestion_from_storyboard_all_done_is_ready():
    progress = {
        "total": 12,
        "done": 12,
        "empty": 0,
        "generating": 0,
        "failed": 0,
        "script_count": 1,
        "scene_count": 4,
    }
    out = _suggestion_from("storyboard", progress)
    assert out["kind"] == "storyboard_ready"
    assert out["action"]["type"] == "navigate"


def test_suggestion_from_storyboard_with_script_but_no_shots_navigates_to_breakdown():
    progress = {
        "total": 0,
        "done": 0,
        "empty": 0,
        "generating": 0,
        "failed": 0,
        "script_count": 1,
        "scene_count": 2,
    }
    out = _suggestion_from("storyboard", progress)
    assert out["kind"] == "storyboard_no_shots"
    assert out["action"]["type"] == "navigate" and out["action"]["tab"] == "scripts"


def test_suggestion_from_non_storyboard_stage_is_nav():
    out = _suggestion_from("planning", {})
    assert out["kind"] == "planning_nav"
    assert out["action"]["type"] == "navigate"
    assert out["progress"] is None

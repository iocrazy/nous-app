import pytest

pytestmark = pytest.mark.unit


def test_scene_models_importable_and_shaped():
    from app.models import Episodes, ScriptOps, ScriptScenes
    from app.models.scripts import ScriptProjects

    assert ScriptScenes.__tablename__ == "script_scenes"
    assert Episodes.__tablename__ == "episodes"
    assert ScriptOps.__tablename__ == "script_ops"
    cols = {c.key for c in ScriptScenes.__mapper__.column_attrs}
    assert {
        "content_json",
        "content",
        "content_version",
        "chapter_id",
        "sort_order",
    } <= cols
    assert "episode_id" in {c.key for c in ScriptProjects.__mapper__.column_attrs}


def test_shot_model_importable_and_shaped():
    from app.models import ScriptShots

    assert ScriptShots.__tablename__ == "script_shots"
    cols = {c.key for c in ScriptShots.__mapper__.column_attrs}
    assert {
        "scene_id",
        "shot_number",
        "shot_type",
        "status",
        "sort_order",
    } <= cols

"""Regression lock for the storyboard AI-gen phantom-column prod bug (BUG 6).

Two write paths fed columns that don't exist on the storyboard models, so the
nodes/frames inserts raised in production (PostgREST 400 / SQLAlchemy compile
error on the ORM path):

  - app/api/script_ai_router.py built node_data with top-level scene_number /
    camera_notes and was missing node_type (NOT NULL).
  - app/workflows/storyboard.py (persist_split_scenes_step /
    persist_video_scenes_step) built frame_data with top-level order_index /
    prompt / notes / status / source_image_path and was missing project_id /
    node_id (NOT NULL).

The DECIDED fix nests the non-column keys into the jsonb columns (data_json /
annotations_json) and renames the rest to real columns. These tests assert the
dicts handed to the repos contain ONLY real StoryboardNodes / StoryboardFrames
column attributes (plus a valid jsonb dict), so the regression can't silently
return.

Unit-level (not integration): the integration path needs a real project/node FK
chain + a configured engine. A focused key-validity assertion on the built dicts
is enough to lock the schema-drift regression and runs in the default unit suite.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.workflows.storyboard as sb
from app.models import StoryboardFrames, StoryboardNodes

_NODE_ATTRS = {p.key for p in StoryboardNodes.__mapper__.column_attrs}
_FRAME_ATTRS = {p.key for p in StoryboardFrames.__mapper__.column_attrs}


def _capture_persist(step, project_id, scenes):
    """Run a persist_*_scenes_step with mocked repos, return the node + frame
    dicts it handed to the repos."""
    captured: dict[str, list[dict]] = {"nodes": [], "frames": []}

    node_repo = MagicMock()
    node_repo.bulk_upsert = AsyncMock(
        side_effect=lambda pid, rows: (captured["nodes"].extend(rows) or [{"id": 123}])
    )
    frame_repo = MagicMock()
    frame_repo.bulk_upsert = AsyncMock(
        side_effect=lambda nid, rows: (captured["frames"].extend(rows) or [{"id": 1}])
    )

    with (
        patch(
            "app.repositories.storyboard_repository.get_storyboard_node_repository",
            return_value=node_repo,
        ),
        patch(
            "app.repositories.storyboard_repository.get_storyboard_frame_repository",
            return_value=frame_repo,
        ),
    ):
        step(project_id, scenes)

    return captured


def _assert_real_columns(row: dict, attrs: set[str]) -> None:
    phantom = set(row) - attrs
    assert not phantom, f"phantom (non-column) keys leaked into the write: {phantom}"


def test_persist_split_scenes_dicts_are_real_columns() -> None:
    captured = _capture_persist(
        sb.persist_split_scenes_step,
        "999",
        [
            {
                "title": "Scene",
                "description": "a description",
                "image_prompt": "an image prompt",
                "notes": "some notes",
            }
        ],
    )

    node = captured["nodes"][0]
    _assert_real_columns(node, _NODE_ATTRS)
    assert node["node_type"] == "storyboard_split"
    assert isinstance(node["data_json"], dict)

    frame = captured["frames"][0]
    _assert_real_columns(frame, _FRAME_ATTRS)
    # required NOT NULL columns supplied
    assert frame["project_id"] == "999"
    assert frame["node_id"] == 123
    assert frame["frame_index"] == 0
    # prompt + status nested in annotations_json jsonb (native dict)
    assert isinstance(frame["annotations_json"], dict)
    assert frame["annotations_json"]["prompt"] == "an image prompt"
    assert frame["annotations_json"]["status"] == "pending"
    assert frame["note"] == "some notes"


def test_persist_video_scenes_dicts_are_real_columns() -> None:
    captured = _capture_persist(
        sb.persist_video_scenes_step,
        "888",
        [{"description": "a scene", "image_path": "frames/x.png", "time": 3}],
    )

    node = captured["nodes"][0]
    _assert_real_columns(node, _NODE_ATTRS)
    assert node["node_type"] == "storyboard_split"
    assert isinstance(node["data_json"], dict)

    frame = captured["frames"][0]
    _assert_real_columns(frame, _FRAME_ATTRS)
    assert frame["project_id"] == "888"
    assert frame["node_id"] == 123
    assert frame["frame_index"] == 0
    assert frame["image_url"] == "frames/x.png"
    assert isinstance(frame["annotations_json"], dict)
    # image_path present → status completed
    assert frame["annotations_json"]["status"] == "completed"
    assert frame["annotations_json"]["prompt"] == "a scene"


def test_script_ai_router_node_dict_is_real_columns() -> None:
    """The script_ai_router node_data build (inlined here to mirror the source)
    must use only StoryboardNodes columns + a data_json dict."""
    NODE_Y_SPACING = 300
    scene = {
        "scene_number": 2,
        "description": "a scene description",
        "camera_notes": "pan left",
    }
    node_data = {
        "project_id": "777",
        "node_type": "storyboard_split",
        "position_x": 100,
        "position_y": scene["scene_number"] * NODE_Y_SPACING,
        "data_json": {
            "source": "script_conversion",
            "scene_number": scene["scene_number"],
            "description": scene["description"],
            "camera_notes": scene.get("camera_notes", ""),
        },
    }
    _assert_real_columns(node_data, _NODE_ATTRS)
    assert node_data["node_type"] == "storyboard_split"
    assert node_data["data_json"]["scene_number"] == 2
    assert node_data["data_json"]["camera_notes"] == "pan left"

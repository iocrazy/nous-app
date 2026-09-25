"""Response shapes of the storyboard shot routes (``script_shots_router``).

These declare what the router already sent; nothing here changes the wire.
Rows come out of ``script_shot_repository._row`` (strategy-C parity) and then
the router's ``_to_response``, which stringifies ``id`` / ``scene_id`` /
``created_by_agent_run_id`` (#1809). Timestamps are already ISO strings.

``status`` is declared ``str`` rather than the four-value union the frontend
narrows it to, so a row with an unexpected value still reads.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class StoryboardShotResponse(BaseModel):
    """One ``script_shots`` row with its bigint ids as strings."""

    id: str
    scene_id: str
    shot_number: Optional[int]
    shot_type: Optional[str]
    camera_angle: Optional[str]
    camera_movement: Optional[str]
    focal_length: Optional[str]
    lighting: Optional[str]
    description: Optional[str]
    image_url: Optional[str]
    thumbnail_url: Optional[str]
    video_url: Optional[str]
    status: str
    sort_order: int
    created_at: str
    updated_at: str
    created_by_agent_run_id: Optional[str]


class StoryboardTaskDispatch(BaseModel):
    """The flat ``{"success", "task_id"}`` body of an async dispatch."""

    success: bool = True
    task_id: str

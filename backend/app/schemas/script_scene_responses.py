"""Response shapes of the scene routes (``script_scenes_router``).

These declare what the router already sent; nothing here changes the wire.
Every row comes out of ``script_scene_repository._row`` (strategy-C parity)
and then the router's ``_to_response``:

- ``id`` / ``script_id`` / ``chapter_id`` / ``location_id`` are JSON
  **strings** — the router stringifies them at the response boundary (#1809,
  the 2026-08 storyboard-canvas P0). This differs on purpose from
  ``/scripts/projects`` (``script_project_responses.ScriptSceneRow``), which
  keeps the same columns as numbers;
- every ``timestamptz`` is already an ISO string, so it is declared ``str``;
- ``content_json`` is the element list (JSONB, default ``[]``), passed through
  as-is and typed as any JSON value so a historical odd row still reads.

``tests/api/test_script_scenes_wire.py`` pins the row model's field set to the
ORM columns, so a column added to the table fails until it is declared here.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class ScriptSceneResponse(BaseModel):
    """One ``script_scenes`` row with its bigint ids as strings."""

    id: str
    script_id: str
    chapter_id: Optional[str]
    heading_int_ext: Optional[str]
    location_text: Optional[str]
    location_id: Optional[str]
    time_of_day: Optional[str]
    content_json: Any
    content: str
    content_version: int
    position_x: Optional[float]
    position_y: Optional[float]
    width: Optional[float]
    height: Optional[float]
    sort_order: int
    created_at: str
    updated_at: str
    scene_number: Optional[str]
    omitted_at: Optional[str]


class ScriptSceneNumbered(ScriptSceneResponse):
    """A scene row plus the derived ``scene_no_in_episode`` (list / get): the
    locked ``scene_number``, else the number derived from canonical order,
    else null."""

    scene_no_in_episode: Optional[str]


class ScriptScenePatched(ScriptSceneResponse):
    """``PATCH /scenes/{id}``. An update that writes a field returns the bare
    row; an update with nothing to write re-reads the scene through
    ``get_by_id``, which also carries ``scene_no_in_episode``. The route uses
    ``response_model_exclude_unset`` so the key is only sent when present."""

    scene_no_in_episode: Optional[str] = None


class ScriptSceneDeleteResult(BaseModel):
    """``DELETE /scenes/{id}``: hard delete pre-lock (``deleted``), or the
    OMITTED row once numbering is locked (``omitted`` + ``scene``). A missing
    scene is a quiet no-op (both false, ``scene`` null)."""

    deleted: bool
    omitted: bool
    scene: Optional[ScriptSceneResponse]


class ScriptSceneOpsResult(BaseModel):
    """``POST /scenes/{id}/elements/ops`` success: the new version and the
    element list after the batch."""

    content_version: int
    elements: List[Any]


class ScriptSceneVersionConflict(BaseModel):
    """409 body of ``/elements/ops``: the editor rebases onto these."""

    success: bool = False
    code: str
    current_version: int
    elements: List[Any]


class ScriptSceneCopilotOps(BaseModel):
    """``POST /scenes/{id}/copilot-ops``: dry-run-validated ops (never
    written). ``proposal`` is only sent (as true) when the scene moved past
    the caller's ``read_version``; the route uses
    ``response_model_exclude_unset``."""

    ops: List[Dict[str, Any]]
    base_version: int
    summary: str
    proposal: Optional[bool] = None

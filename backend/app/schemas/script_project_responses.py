"""Response shapes of ``/api/v1/scripts/projects`` (script projects, their
chapters and assets).

These declare what the routers already sent; nothing here changes the wire.
Every row comes out of ``script_repository._to_dict`` (or, for scenes,
``script_scene_repository._row``), which applies "strategy-C parity":

- bigint ids and FKs stay JSON **numbers** (never stringified on this surface);
- ``created_by`` (uuid) is a string;
- every ``timestamptz`` is already an ISO string (``.isoformat()``), so those
  fields are declared ``str``, not :data:`app.schemas.wire.WireDatetime`;
- JSONB columns pass through as-is.

``tests/api/test_script_projects_wire.py`` pins each row model's field set to
its ORM model's columns, so a column added to the table fails until it is
declared here.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class ScriptProjectRow(BaseModel):
    """One ``script_projects`` row (``SELECT *`` shape)."""

    id: int
    project_id: int
    team_id: int
    name: str
    created_by: str
    display_code: Optional[str]
    description: Optional[str]
    settings_json: Optional[Dict[str, Any]]
    viewport_json: Optional[Dict[str, Any]]
    status: Optional[str]
    created_at: Optional[str]
    updated_at: Optional[str]
    genre: Optional[str]
    episode_id: Optional[int]
    target_duration_sec: Optional[int]
    # NULL = writing phase (scene numbers derived, not stored); once set every
    # scene_number under this script is frozen (mig 403).
    numbering_locked_at: Optional[str]


class ScriptProjectPage(BaseModel):
    """``GET /scripts/projects``: one page of a project's live scripts."""

    items: List[ScriptProjectRow]
    total: int
    page: int
    limit: int


class ScriptChapterRow(BaseModel):
    """One ``script_chapters`` row (``SELECT *`` shape)."""

    id: int
    script_id: int
    parent_chapter_id: Optional[int]
    chapter_number: Optional[int]
    title: Optional[str]
    summary: Optional[str]
    content: Optional[str]
    branch_label: Optional[str]
    branch_type: Optional[str]
    position_x: Optional[float]
    position_y: Optional[float]
    width: Optional[float]
    height: Optional[float]
    data_json: Optional[Dict[str, Any]]
    sort_order: Optional[int]
    created_at: Optional[str]
    updated_at: Optional[str]
    content_json: Optional[Dict[str, Any]]


class ScriptProjectFull(BaseModel):
    """``GET /scripts/projects/{id}``: the project row plus its chapters in
    ``sort_order``."""

    project: ScriptProjectRow
    chapters: List[ScriptChapterRow]


class ScriptCanvasSyncResult(BaseModel):
    """``POST /{id}/canvas/sync``: every chapter of the script after the sync."""

    chapters: List[ScriptChapterRow]


class ScriptAssetRow(BaseModel):
    """One ``script_assets`` row (``SELECT *`` shape)."""

    id: int
    script_id: int
    asset_type: str
    name: str
    content: Optional[str]
    data_json: Optional[Dict[str, Any]]
    sort_order: Optional[int]
    created_at: Optional[str]
    updated_at: Optional[str]


class ScriptSceneRow(BaseModel):
    """One ``script_scenes`` row plus the derived ``scene_no_in_episode``.

    ``content_json`` is the scene's element list (JSONB, default ``[]``), so it
    is typed as any JSON value rather than an object.
    """

    id: int
    script_id: int
    chapter_id: Optional[int]
    heading_int_ext: Optional[str]
    location_text: Optional[str]
    location_id: Optional[int]
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
    # The locked scene_number, else the number derived from canonical order.
    scene_no_in_episode: Optional[str]


class ScriptNumberingLock(BaseModel):
    """``POST /{id}/lock-numbering``. ``already_locked`` is true when the call
    was a no-op; ``scenes`` is then the current (unchanged) scene list."""

    already_locked: bool
    scenes: List[ScriptSceneRow]


class ScriptAck(BaseModel):
    """``{"success": true}`` with no ``data``: deletes and the viewport save."""

    success: bool = True

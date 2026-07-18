"""Script Editor request/response Pydantic schemas."""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, constr

# ---------------------------------------------------------------------------
# Script Project schemas
# ---------------------------------------------------------------------------


class ScriptProjectCreate(BaseModel):
    """Request body for creating a new script project."""

    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    project_id: int
    # Auto-provision (episode → empty script). When set, create is idempotent:
    # the endpoint returns the episode's existing active script instead of
    # inserting a duplicate (#1432 double-fire kill). Snowflake id as str; the
    # repo bigint-coerces it. Omitted by the normal "New Script" button.
    episode_id: Optional[str] = None


class ScriptImportRequest(BaseModel):
    """Request body for creating a script from imported screenplay/prose text.

    ``mode`` selects the parse path: ``fountain`` runs the deterministic
    parser (zero LLM), ``prose`` routes the raw text through the existing
    convert-to-scenes LLM pipeline. ``content`` size is additionally guarded
    at the endpoint (422) so the message is explicit."""

    name: str = Field(..., min_length=1, max_length=200)
    project_id: int
    mode: Literal["fountain", "prose"]
    content: str = Field(..., min_length=1)


class ScriptProjectUpdate(BaseModel):
    """Request body for updating an existing script project."""

    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    status: Optional[str] = Field(None, pattern="^(active|archived)$")
    settings_json: Optional[Dict[str, Any]] = None
    # Reassign the script to another episode (Phase B P2). Snowflake id as str;
    # the repo bigint-coerces it. FK is ON DELETE RESTRICT.
    episode_id: Optional[str] = None


class ScriptProjectResponse(BaseModel):
    """API response for a single script project."""

    id: str
    project_id: str
    team_id: str
    created_by: str
    name: str
    description: Optional[str] = None
    display_code: Optional[str] = None
    episode_id: Optional[str] = None
    status: str
    settings_json: Optional[Dict[str, Any]] = None
    viewport_json: Optional[Dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Script Chapter schemas
# ---------------------------------------------------------------------------


class ScriptChapterCreate(BaseModel):
    """Request body for creating a chapter node."""

    title: Optional[str] = Field(None, max_length=200)
    summary: Optional[str] = Field(None, max_length=5000)
    content: Optional[str] = Field(None, max_length=100000)
    chapter_number: Optional[int] = None
    parent_chapter_id: Optional[str] = None
    branch_label: Optional[str] = Field(None, max_length=100)
    branch_type: Optional[str] = Field(None, pattern="^(condition|choice)$")
    position_x: float = Field(default=0.0)
    position_y: float = Field(default=0.0)
    width: Optional[float] = None
    height: Optional[float] = None
    data_json: Dict[str, Any] = Field(default_factory=dict)
    sort_order: int = Field(default=0)


class ScriptChapterUpdate(BaseModel):
    """Request body for updating a chapter node."""

    title: Optional[str] = Field(None, max_length=200)
    summary: Optional[str] = Field(None, max_length=5000)
    content: Optional[str] = Field(None, max_length=100000)
    chapter_number: Optional[int] = None
    parent_chapter_id: Optional[str] = None
    branch_label: Optional[str] = Field(None, max_length=100)
    branch_type: Optional[str] = Field(None, pattern="^(condition|choice)$")
    position_x: Optional[float] = None
    position_y: Optional[float] = None
    width: Optional[float] = None
    height: Optional[float] = None
    data_json: Optional[Dict[str, Any]] = None
    sort_order: Optional[int] = None


# ---------------------------------------------------------------------------
# Episode schemas (Phase B P2)
# ---------------------------------------------------------------------------


class EpisodeCreate(BaseModel):
    """Request body for creating an episode under a project."""

    title: Optional[str] = Field(None, max_length=200)
    sort_order: Optional[int] = None


class EpisodeUpdate(BaseModel):
    """Request body for updating an episode (title / sort_order)."""

    title: Optional[str] = Field(None, max_length=200)
    sort_order: Optional[int] = None


# ---------------------------------------------------------------------------
# Scene schemas (Phase B P2)
# ---------------------------------------------------------------------------


class SceneCreate(BaseModel):
    """Request body for creating a scene under a script. ``script_id`` comes
    from the path; ``content`` flows exclusively through the ops endpoint."""

    chapter_id: Optional[str] = None
    heading_int_ext: Optional[str] = Field(None, max_length=10)
    location_text: Optional[str] = None
    location_id: Optional[str] = None
    time_of_day: Optional[str] = Field(None, max_length=20)
    position_x: Optional[float] = None
    position_y: Optional[float] = None
    width: Optional[float] = None
    height: Optional[float] = None
    sort_order: Optional[int] = None


class SceneMetaUpdate(BaseModel):
    """Request body for updating scene header fields / canvas coords. NEVER
    touches content_version/content — those move through the ops endpoint."""

    heading_int_ext: Optional[str] = Field(None, max_length=10)
    location_text: Optional[str] = None
    location_id: Optional[str] = None
    time_of_day: Optional[str] = Field(None, max_length=20)
    position_x: Optional[float] = None
    position_y: Optional[float] = None
    width: Optional[float] = None
    height: Optional[float] = None


class SceneOpsRequest(BaseModel):
    """Request body for `POST /scenes/{scene_id}/elements/ops`."""

    ops: List[Dict[str, Any]] = Field(default_factory=list)


class CopilotOpsRequest(BaseModel):
    """Request body for `POST /scenes/{scene_id}/copilot-ops`.

    ``instruction`` is untrusted user free-text (the reconciler wraps it in
    delimiters before handing it to the LLM). ``read_version`` is the
    ``content_version`` the editor last read: when it lags the scene's current
    version the endpoint returns a ``proposal`` (ops regenerated against the
    CURRENT elements) for the editor to review before applying."""

    instruction: constr(min_length=1, max_length=2000)
    read_version: int


class SceneMoveRequest(BaseModel):
    """Request body for `POST /scenes/{scene_id}/move`.

    ``chapter_id`` uses field-set semantics: omit to keep the current chapter,
    supply (incl. null) to reparent. The router reads ``model_fields_set`` to
    honour that distinction against the repository's UNSET sentinel."""

    chapter_id: Optional[str] = None
    before_scene_id: Optional[str] = None
    after_scene_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Shot schemas (Phase B P3 — storyboard shots hang off a scene)
# ---------------------------------------------------------------------------


class ShotCreate(BaseModel):
    """Request body for creating a shot under a scene. ``scene_id`` comes from
    the path; ``status`` / image URLs are never client-set on create (a fresh
    shot lands ``status='empty'`` — those move through generate/update_status)."""

    shot_number: Optional[int] = None
    shot_type: Optional[str] = Field(None, max_length=20)
    camera_angle: Optional[str] = Field(None, max_length=20)
    camera_movement: Optional[str] = Field(None, max_length=20)
    focal_length: Optional[str] = Field(None, max_length=20)
    lighting: Optional[str] = None
    description: Optional[str] = None
    sort_order: Optional[int] = None


class ShotUpdate(BaseModel):
    """Request body for updating a shot's parameter tags / description. NEVER
    touches ``status`` or the image/thumbnail/video URLs — those flow through
    the generate workflow's ``update_status`` write lane."""

    shot_number: Optional[int] = None
    shot_type: Optional[str] = Field(None, max_length=20)
    camera_angle: Optional[str] = Field(None, max_length=20)
    camera_movement: Optional[str] = Field(None, max_length=20)
    focal_length: Optional[str] = Field(None, max_length=20)
    lighting: Optional[str] = None
    description: Optional[str] = None


class ShotMoveRequest(BaseModel):
    """Request body for `POST /shots/{shot_id}/move`.

    Shots are reordered WITHIN their scene only (no cross-scene move in P3), so
    there is no reparent field — just the sparse-insertion anchors."""

    before_shot_id: Optional[str] = None
    after_shot_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Beat schemas (Beats view — classic beat sheet)
# ---------------------------------------------------------------------------


class BeatCreate(BaseModel):
    """Request body for creating a beat under a script. ``script_id`` comes from
    the path; ``scene_ids`` is an ordered list of linked scene id strings."""

    title: str = Field(..., min_length=1, max_length=200)
    summary: Optional[str] = None
    scene_ids: Optional[List[str]] = None


class BeatUpdate(BaseModel):
    """Request body for updating a beat's title / summary / linked scenes."""

    title: Optional[str] = Field(None, min_length=1, max_length=200)
    summary: Optional[str] = None
    scene_ids: Optional[List[str]] = None


class BeatMoveRequest(BaseModel):
    """Request body for `POST /beats/{beat_id}/move`.

    ``after_beat_id`` names the beat this one lands just after; ``null`` moves it
    to the front. Beats are reordered within their script only."""

    after_beat_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Version / commit schemas (Phase B P4 — manual tags over the op ledger)
# ---------------------------------------------------------------------------


class CommitCreate(BaseModel):
    """Request body for `POST /scripts/{script_id}/commits`.

    ``message`` is the tag label. It is capped at 200 chars to match the
    ``script_commits.message`` column (VARCHAR(200)) — an over-long message is
    rejected 422 at the boundary rather than truncated/erroring at the DB."""

    message: constr(min_length=1, max_length=200)


# ---------------------------------------------------------------------------
# Canvas sync schema
# ---------------------------------------------------------------------------


class ScriptCanvasSyncRequest(BaseModel):
    """Request body for a full script canvas sync operation."""

    added_chapters: List[ScriptChapterCreate] = Field(default_factory=list)
    updated_chapters: List[Dict[str, Any]] = Field(default_factory=list)
    deleted_chapter_ids: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# AI outline generation
# ---------------------------------------------------------------------------


class ViewportUpdate(BaseModel):
    """Viewport position and zoom for canvas."""

    x: float = 0
    y: float = 0
    zoom: float = Field(default=1, ge=0.1, le=10)


class GenerateOutlineRequest(BaseModel):
    """Request body for AI-generated story outline."""

    script_id: str
    premise: str = Field(..., min_length=1, max_length=10000)
    chapter_count: int = Field(default=5, ge=2, le=20)
    style_guide: Optional[str] = Field(None, max_length=2000)


class ExpandChapterRequest(BaseModel):
    """Request body for AI chapter expansion."""

    script_id: str
    chapter_id: str
    title: str = Field(..., max_length=200)
    summary: str = Field(..., min_length=1, max_length=5000)
    context: Optional[str] = Field(None, max_length=10000)


class CreateBranchesRequest(BaseModel):
    """Request body for AI story branching."""

    script_id: str
    chapter_id: str
    title: str = Field(..., max_length=200)
    summary: str = Field(..., min_length=1, max_length=5000)
    branch_count: int = Field(default=2, ge=2, le=4)
    branch_type: str = Field(default="choice", pattern="^(choice|condition)$")
    context: Optional[str] = Field(None, max_length=10000)


# ---------------------------------------------------------------------------
# Script Asset schemas
# ---------------------------------------------------------------------------


class ScriptAssetCreate(BaseModel):
    """Request body for creating a script asset."""

    script_id: str
    asset_type: str = Field(
        ..., pattern="^(worldview|character|location|prop|plot_point)$"
    )
    name: str = Field(..., min_length=1, max_length=200)
    content: Optional[str] = Field(None, max_length=100000)
    data_json: Dict[str, Any] = Field(default_factory=dict)
    sort_order: int = Field(default=0)


class ScriptAssetUpdate(BaseModel):
    """Request body for updating a script asset."""

    name: Optional[str] = Field(None, min_length=1, max_length=200)
    content: Optional[str] = Field(None, max_length=100000)
    data_json: Optional[Dict[str, Any]] = None
    sort_order: Optional[int] = None


# ---------------------------------------------------------------------------
# Script-to-Storyboard conversion
# ---------------------------------------------------------------------------


class ConvertToStoryboardRequest(BaseModel):
    """Request body for converting a chapter to storyboard scenes."""

    script_id: str
    chapter_id: str
    storyboard_project_id: Optional[str] = None

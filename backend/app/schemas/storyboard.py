# backend/app/schemas/storyboard.py

"""
Storyboard Workbench request/response Pydantic schemas.

Covers projects, canvas nodes, frames, characters, and AI generation
requests for the storyboard module.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Project schemas
# ---------------------------------------------------------------------------


class StoryboardProjectCreate(BaseModel):
    """Request body for creating a new storyboard project."""

    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    settings_json: Optional[Dict[str, Any]] = None
    project_id: Optional[int] = None


class StoryboardProjectUpdate(BaseModel):
    """Request body for updating an existing storyboard project."""

    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    status: Optional[str] = Field(None, pattern="^(active|archived)$")
    settings_json: Optional[Dict[str, Any]] = None


class StoryboardProjectResponse(BaseModel):
    """API response for a single storyboard project."""

    id: str
    created_by: str
    name: str
    description: Optional[str] = None
    status: str
    settings_json: Optional[Dict[str, Any]] = None
    project_id: Optional[int] = None
    display_code: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Node schemas
# ---------------------------------------------------------------------------

_NODE_TYPE_PATTERN = (
    "^(upload|image_edit|storyboard_split|storyboard_gen"
    "|text_annotation|group|export|image_to_video)$"
)


class StoryboardNodeCreate(BaseModel):
    """Request body for creating a canvas node."""

    node_type: str = Field(..., pattern=_NODE_TYPE_PATTERN)
    position_x: float = Field(default=0.0)
    position_y: float = Field(default=0.0)
    width: Optional[float] = None
    height: Optional[float] = None
    data_json: Dict[str, Any] = Field(default_factory=dict)


class StoryboardNodeUpdate(BaseModel):
    """Request body for updating an existing canvas node."""

    position_x: Optional[float] = None
    position_y: Optional[float] = None
    width: Optional[float] = None
    height: Optional[float] = None
    data_json: Optional[Dict[str, Any]] = None
    locked: Optional[bool] = None


# ---------------------------------------------------------------------------
# Canvas sync schema
# ---------------------------------------------------------------------------


class CanvasSyncRequest(BaseModel):
    """Request body for a full canvas state sync operation."""

    added_nodes: List[StoryboardNodeCreate] = Field(default_factory=list)
    updated_nodes: List[Dict[str, Any]] = Field(default_factory=list)
    deleted_node_ids: List[str] = Field(default_factory=list)
    added_edges: List[Dict[str, Any]] = Field(default_factory=list)
    deleted_edge_ids: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Frame schemas
# ---------------------------------------------------------------------------

_SHOT_TYPE_PATTERN = (
    "^(extreme_close_up|close_up|medium_close_up|medium|medium_wide|wide|extreme_wide)$"
)
_CAMERA_ANGLE_PATTERN = "^(eye_level|low_angle|high_angle|bird_eye|worm_eye)$"
_CAMERA_MOVEMENT_PATTERN = "^(static|push|pull|pan|tilt|dolly|crane|tracking)$"
_FOCAL_LENGTH_PATTERN = "^(24mm|35mm|50mm|85mm|135mm)$"
_TRANSITION_TYPE_PATTERN = "^(cut|fade|dissolve)$"


class StoryboardFrameUpdate(BaseModel):
    """Request body for updating a storyboard frame's metadata."""

    note: Optional[str] = Field(None, max_length=2000)
    shot_type: Optional[str] = Field(None, pattern=_SHOT_TYPE_PATTERN)
    camera_angle: Optional[str] = Field(None, pattern=_CAMERA_ANGLE_PATTERN)
    camera_movement: Optional[str] = Field(None, pattern=_CAMERA_MOVEMENT_PATTERN)
    focal_length: Optional[str] = Field(None, pattern=_FOCAL_LENGTH_PATTERN)
    lighting: Optional[str] = Field(None, max_length=500)
    duration_seconds: Optional[float] = Field(None, ge=0.5, le=30.0)
    transition_type: Optional[str] = Field(None, pattern=_TRANSITION_TYPE_PATTERN)
    annotations_json: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Character schemas
# ---------------------------------------------------------------------------


class CharacterCreate(BaseModel):
    """Request body for creating a character."""

    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=2000)
    visual_traits: Optional[Dict[str, Any]] = None


class CharacterUpdate(BaseModel):
    """Request body for updating an existing character."""

    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=2000)
    visual_traits: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# AI request schemas
# ---------------------------------------------------------------------------


class GenerateImageRequest(BaseModel):
    """Request body for AI image generation on a canvas node."""

    project_id: str
    node_id: str
    prompt: str = Field(..., min_length=1, max_length=4000)
    model: str
    provider: str
    aspect_ratio: Optional[str] = Field(default="16:9")
    character_ids: List[str] = Field(default_factory=list)
    reference_image_url: Optional[str] = None


_MOTION_INTENSITY_PATTERN = "^(low|medium|high)$"


class GenerateVideoRequest(BaseModel):
    """Request body for AI video generation from a source image."""

    project_id: str
    node_id: str
    source_image_url: str
    prompt: Optional[str] = Field(None, max_length=4000)
    provider: str
    duration_seconds: float = Field(default=5.0, ge=3.0, le=25.0)
    motion_intensity: Optional[str] = Field(
        default="medium", pattern=_MOTION_INTENSITY_PATTERN
    )


class SplitScriptRequest(BaseModel):
    """Request body for splitting a script into storyboard scenes via AI."""

    project_id: str
    script_text: str = Field(..., min_length=10, max_length=50000)
    style_guide: Optional[str] = Field(None, max_length=2000)

"""Pydantic schemas for the canvas core (Phase 1 of the canvas + AI plan).

Mirrors the columns of the ``canvases`` table from migration 280. Treats the
JSONB fields (viewport / nodes / connections / ops) as opaque pass-through
payloads so the frontend can iterate on the shape without backend churn —
the contract is enforced by the React Flow + Zustand layer, not here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

# One definition of "a snowflake id on the wire" for the whole app boundary —
# re-declaring the pattern here is how the two copies drift apart.
from app.schemas.assets import SnowflakeId

# 'character' (mig 357) reuses the smart pipeline with the CharacterNode +
# preset agent workflow on top (character canvas epic 2026-07-13).
# 'classic' (canvas 1.0 engine) is RETIRED (mig 361 soft-deleted every
# remaining row): it stays in CanvasKind so trashed rows still serialize,
# but new canvases may only use CreatableCanvasKind.
# 'storyboard' (mig 421) is the system per-episode shot canvas
# (shot-nodes-on-canvas spec 2026-08-11 §2) — NOT in CreatableCanvasKind:
# it is never user-created via POST /projects/{project_id}/canvases, only
# via the dedicated get-or-create GET /canvases/storyboard endpoint.
CanvasKind = Literal[
    "smart", "lite", "classic", "character", "location", "prop", "storyboard"
]
CreatableCanvasKind = Literal["smart", "lite", "character", "location", "prop"]


class CanvasViewport(BaseModel):
    x: float = 0
    y: float = 0
    zoom: float = 1


class CanvasResponse(BaseModel):
    """Full canvas payload returned by GET / on successful PUT."""

    id: str = Field(..., description="Snowflake bigint, serialized as string")
    project_id: str = Field(..., description="Snowflake bigint, serialized as string")
    episode_id: Optional[str] = Field(
        None,
        description="Snowflake bigint, serialized as string; set only for kind='storyboard'",
    )
    name: str
    kind: CanvasKind
    asset_id: Optional[str] = Field(
        None,
        description=(
            "Snowflake bigint, serialized as string; set when the canvas belongs "
            "to an asset (canvases.asset_id, mig 446). Declared because "
            "``_to_response`` emits it — the 409 conflict body builds a "
            "CanvasResponse, and an undeclared key would be dropped there."
        ),
    )
    viewport_json: Dict[str, Any]
    nodes_json: List[Dict[str, Any]]
    connections_json: List[Dict[str, Any]]
    node_ops_json: List[Dict[str, Any]]
    connection_ops_json: List[Dict[str, Any]]
    base_updated_at: datetime
    created_at: datetime
    updated_at: datetime
    created_by: Optional[str] = None
    can_edit: Optional[bool] = Field(
        None,
        description=(
            "Whether this caller may write the canvas — the same verdict the "
            "PUT's write guard reaches (scope_guards."
            "resolve_project_read_access → ProjectAccess.can_write). Set "
            "on the load responses (GET /canvases/{id}, GET "
            "/canvases/storyboard) so the client can render read-only up "
            "front instead of discovering it from a 403. None = not supplied "
            "on this payload (e.g. the 409 conflict body, which is only "
            "reachable after the write guard already passed)."
        ),
    )


class CanvasCreate(BaseModel):
    name: str = "Untitled"
    kind: CreatableCanvasKind = "smart"
    viewport_json: Optional[Dict[str, Any]] = None
    # The asset this canvas belongs to (``canvases.asset_id``, mig 446). Optional:
    # most canvases belong to a project only. ``SnowflakeId`` (not a bare str) so
    # a non-numeric or past-BIGINT value is a 422 here instead of a ValueError in
    # the repo's ``int()`` — the same boundary every other asset id is pinned at.
    # The route checks the asset is in the project's scope before persisting it.
    asset_id: Optional[SnowflakeId] = None


class CanvasUpdate(BaseModel):
    """PUT payload. ``base_updated_at`` is the optimistic-lock token the
    client read; the server rejects with 409 if it doesn't match the
    current row.

    Only fields the client wants to change need to be set — None means
    "leave unchanged". The ops arrays are append-only at the client; the
    server replaces them wholesale because trusting per-field append
    semantics from an unauthenticated payload is a footgun.
    """

    base_updated_at: datetime
    name: Optional[str] = None
    kind: Optional[CanvasKind] = None
    viewport_json: Optional[Dict[str, Any]] = None
    nodes_json: Optional[List[Dict[str, Any]]] = None
    connections_json: Optional[List[Dict[str, Any]]] = None
    node_ops_json: Optional[List[Dict[str, Any]]] = None
    connection_ops_json: Optional[List[Dict[str, Any]]] = None


class CanvasConflictResponse(BaseModel):
    """409 payload — gives the client the up-to-date server state so it
    can show a merge/discard prompt without a second round trip."""

    error: Literal["canvas_conflict"] = "canvas_conflict"
    current: CanvasResponse


class TimelineSegment(BaseModel):
    """One block on the timeline director (G8)."""

    prompt: str = Field(..., min_length=1)
    seconds: int = Field(default=5, ge=1, le=10)

    @field_validator("prompt")
    @classmethod
    def _seg_prompt_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("segment prompt must not be blank")
        return v


class CanvasTimelineRequest(BaseModel):
    """POST /canvases/{id}/timeline-runs — one multi-segment film (G8)."""

    node_id: str = Field(..., min_length=1)
    segments: List[TimelineSegment] = Field(..., min_length=1, max_length=12)
    model: str = ""
    aspect: str = ""


class CanvasGenerationRequest(BaseModel):
    """POST /canvases/{id}/generations — dispatch image/video generation
    tasks for a smart-canvas node (G4-B1). ``count`` fans out to N
    independent DBOS tasks (clamped 1..8, video always 1)."""

    node_id: str = Field(..., min_length=1)
    kind: Literal["image", "video"]
    prompt: str = Field(..., min_length=1)
    model: str = ""
    count: int = Field(default=1, ge=1)
    params: Dict[str, Any] = Field(default_factory=dict)
    source_url: Optional[str] = None

    @field_validator("prompt")
    @classmethod
    def _prompt_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("prompt must not be blank")
        return v


class CanvasZipItem(BaseModel):
    """One entry in a Download-All zip: a whitelisted generated-media serve
    URL plus the desired archive filename."""

    url: str = Field(..., min_length=1)
    name: str = ""


class CanvasZipRequest(BaseModel):
    """POST /canvases/assets/zip — bundle several generated-media results
    into one archive (P2-7). Only generated-media serve URLs are accepted;
    each id is scope-checked before its bytes are read (no remote fetch)."""

    filename: str = "canvas-assets.zip"
    items: List[CanvasZipItem] = Field(..., min_length=1, max_length=64)

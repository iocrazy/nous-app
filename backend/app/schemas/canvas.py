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

CanvasKind = Literal["smart", "classic"]


class CanvasViewport(BaseModel):
    x: float = 0
    y: float = 0
    zoom: float = 1


class CanvasResponse(BaseModel):
    """Full canvas payload returned by GET / on successful PUT."""

    id: str = Field(..., description="Snowflake bigint, serialized as string")
    project_id: str = Field(..., description="Snowflake bigint, serialized as string")
    name: str
    kind: CanvasKind
    viewport_json: Dict[str, Any]
    nodes_json: List[Dict[str, Any]]
    connections_json: List[Dict[str, Any]]
    node_ops_json: List[Dict[str, Any]]
    connection_ops_json: List[Dict[str, Any]]
    base_updated_at: datetime
    created_at: datetime
    updated_at: datetime
    created_by: Optional[str] = None


class CanvasCreate(BaseModel):
    name: str = "Untitled"
    kind: CanvasKind = "smart"
    viewport_json: Optional[Dict[str, Any]] = None


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

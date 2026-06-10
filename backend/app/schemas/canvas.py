"""Pydantic schemas for the canvas core (Phase 1 of the canvas + AI plan).

Mirrors the columns of the ``canvases`` table from migration 280. Treats the
JSONB fields (viewport / nodes / connections / ops) as opaque pass-through
payloads so the frontend can iterate on the shape without backend churn —
the contract is enforced by the React Flow + Zustand layer, not here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

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

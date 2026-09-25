"""Response shapes of the canvas routes (``canvases_router`` and
``project_assets_router``).

These declare what the routers already sent; nothing here changes the wire
(the one addition, ``CanvasSummary.node_count``, is called out on the field).

Snowflake ids are **strings** on this surface: ``canvases_router._to_response``
stringifies ``id`` / ``project_id`` / ``episode_id`` / ``asset_id`` and the
summary builders and ref queries ``str()`` / ``CAST(... AS TEXT)`` the rest.
That differs from scenes/shots (JSON numbers) on purpose — do not "unify" it.

Timestamps: the canvas repository's ``_serialize`` already ran
``isoformat()``, so those fields are ``str``.

``CanvasResponse`` in :mod:`app.schemas.canvas` is NOT the success body: it
types its timestamps as ``datetime`` (so it re-serializes them) and has no
``deleted_at``. It stays the shape of the 409 conflict body only.

Spec: docs/superpowers/specs/2026-09-24-openapi-typed-frontend-design.md
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from app.schemas.canvas import CanvasKind
from app.schemas.envelope import Envelope

# ``canvas_resource_refs_role_check`` (mig 290).
CanvasRefRole = Literal["reference", "output"]
# ``nous_models_last_test_status_check`` (mig 503); the code-side twin is
# ``nous_model_health.PROBE_STATUSES``, pinned equal by the canvas wire test.
ProbeStatus = Literal["ok", "fail", "idle", "not_probed"]


class CanvasRow(BaseModel):
    """A full canvas document: every ``canvases`` column, as the router emits it.

    ``tests/api/test_canvases_wire.py`` pins the field set (minus
    ``can_edit``) to the ORM columns.
    """

    id: str
    project_id: str
    episode_id: Optional[str]
    name: str
    kind: CanvasKind
    asset_id: Optional[str]
    viewport_json: Dict[str, Any]
    nodes_json: List[Dict[str, Any]]
    connections_json: List[Dict[str, Any]]
    node_ops_json: List[Dict[str, Any]]
    connection_ops_json: List[Dict[str, Any]]
    base_updated_at: str = Field(
        ..., description="Optimistic-lock token: echo it back on the next PUT."
    )
    created_at: str
    updated_at: str
    created_by: Optional[str]
    deleted_at: Optional[str]
    can_edit: Optional[bool] = Field(
        None,
        description=(
            "Whether this caller may write the canvas. Present only on the two "
            "load responses (GET /canvases/{id}, GET /canvases/storyboard); "
            "absent (not null) on create and save."
        ),
    )


class CanvasSummary(BaseModel):
    """A project canvas list row: summary columns, no node graph."""

    id: str
    project_id: str
    name: str
    kind: CanvasKind
    created_at: str
    updated_at: str
    node_count: int = Field(
        ...,
        description=(
            "Top-level nodes in the canvas (``jsonb_array_length(nodes_json)``). "
            "Added in the P3 typing pass: the workspace card showed a node count "
            "read from ``nodes_json``, which this summary never carries."
        ),
    )


class TeamCanvasSummary(BaseModel):
    id: str
    name: str
    kind: CanvasKind
    updated_at: str


class TeamCanvasProject(BaseModel):
    """A team project with its live canvases (empty list when it has none)."""

    project_id: str
    project_name: str
    canvases: List[TeamCanvasSummary]


class ProjectTrashedCanvas(BaseModel):
    """A trashed canvas in a project's trash list."""

    id: str
    name: str
    kind: CanvasKind
    updated_at: Optional[str]
    deleted_at: Optional[str]
    project_id: str


class TeamTrashedCanvas(ProjectTrashedCanvas):
    """A trashed canvas in a team's trash list, named with its project."""

    project_name: str


class CanvasAck(BaseModel):
    """Delete / restore / purge answer ``{"success": true}`` and nothing else."""

    success: bool = True


class CanvasAssetRef(BaseModel):
    """An asset-library ref held by a canvas (``canvas_asset_refs``)."""

    asset_id: str
    node_id: str
    loadout_id: Optional[str]
    asset_name: str
    asset_type: str
    loadout_name: Optional[str]


class CanvasAssetRefsEnvelope(Envelope[List[CanvasAssetRef]]):
    count: int


class ResourceCanvasRef(BaseModel):
    """A live canvas that references a resource, and in which role."""

    canvas_id: str
    canvas_name: str
    kind: CanvasKind
    project_id: str
    role: CanvasRefRole


class ResourceCanvasRefsEnvelope(Envelope[List[ResourceCanvasRef]]):
    count: int


class CanvasModelOption(BaseModel):
    """A catalog model a canvas picker may offer (public columns only).

    The router projects ``_GENERATION_MODEL_PUBLIC_FIELDS`` off each row;
    ``tests/api/test_canvases_wire.py`` pins this field set to that tuple.
    No credential, host or ``actual_provider`` ever appears here.
    """

    name: str
    display_name: str
    actual_model: str
    type: str
    is_local: bool
    sort_order: int
    last_test_status: Optional[ProbeStatus]


class CanvasGenerationCapability(BaseModel):
    """Which generation knobs one catalog model honours.

    ``ratios`` keeps the aspect declaration order and ``quality_tiers`` runs
    low → max; ``video_modes`` is sorted.
    """

    ratios: List[str]
    quality: bool
    quality_tiers: List[str]
    resolution: bool
    max_refs: int
    negative: bool
    video_modes: List[str]

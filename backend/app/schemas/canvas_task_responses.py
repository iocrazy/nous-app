"""Response shapes of the canvas task routes: generation dispatch/poll/cancel,
timeline dispatch, smart-mode prompt runs, and the three derive endpoints.

These declare what the routers already sent (P4 typing pass); nothing here
changes the wire. ``tests/api/test_canvas_tasks_wire.py`` pins each one with
a real HTTP call against ``jsonable_encoder`` of the bare dict.

Ids: a task id is the ``task_tracking.dbos_workflow_id`` (a UUID string); a
derived image id is a ``generated_media`` Snowflake the derive service already
``str()``-ed. ``flow_id`` is the ``task_flows`` UUID string, or null.

Spec: docs/superpowers/specs/2026-09-24-openapi-typed-frontend-design.md
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class CanvasGenerationTask(BaseModel):
    """One ``task_tracking`` row as ``GET /canvases/generations/{task_id}``
    projects it. ``phase`` / ``status`` / ``error_msg`` are trigger-owned
    (route C); ``metadata`` is the workflow's open jsonb decoration, where
    ``result_url`` / ``generated_media_id`` / ``dropped_knobs`` /
    ``dropped_refs`` / ``failure`` land."""

    dbos_workflow_id: str
    phase: Optional[str]
    status: str
    error_msg: Optional[str]
    metadata: Optional[Dict[str, Any]]


class CanvasGenerationDispatch(BaseModel):
    """``POST /canvases/{id}/generations``: the ids sit NEXT TO ``success``,
    not under ``data`` (that is how the route has always answered)."""

    success: bool = True
    task_ids: List[str]
    flow_id: Optional[str] = Field(
        ...,
        description=(
            "The Task Center group (``task_flows.id``) when count > 1 and the "
            "flow row was created; null for a single task or when the "
            "best-effort flow insert failed."
        ),
    )


class CanvasTimelineDispatch(BaseModel):
    task_id: str


class CanvasDerivedImage(BaseModel):
    """A durable generated-media item a canvas derive produced."""

    id: str
    url: str = Field(..., description="Always ``/api/v1/generated-media/{id}/cover``.")
    kind: Literal["image"]
    row: Optional[int] = Field(
        ..., description="0-based tile row for a grid derive; null otherwise."
    )
    col: Optional[int] = Field(
        ..., description="0-based tile column for a grid derive; null otherwise."
    )


class CanvasDeriveResult(BaseModel):
    images: List[CanvasDerivedImage]

"""Pydantic models for content relay pipelines (W2b).

A pipeline is a fixed, ordered relay of agent steps run against a parent issue.
See ``app/services/issues/pipeline_relay.py`` for the fan-out engine and
``app/api/pipelines_router.py`` for the REST surface.

Snowflake bigint ids are exposed as STRINGS (never JSON numbers) so the JS
client never loses precision above 2**53 — the repository coerces them at the
read boundary. UUID columns are likewise str on the wire.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class PipelineRunStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    HALTED = "halted"
    CANCELLED = "cancelled"


class PipelineStepInput(BaseModel):
    """One relay step in a create/update payload."""

    step_order: int = Field(ge=1)
    agent_id: UUID
    title_template: str = Field(min_length=1, max_length=500)
    prompt_template: str = Field(min_length=1, max_length=50000)


class PipelineStep(BaseModel):
    """One relay step in a response."""

    id: str
    pipeline_id: str
    step_order: int
    agent_id: str
    title_template: str
    prompt_template: str


class PipelineCreate(BaseModel):
    """POST /pipelines payload. team_id is a body field (validated server-side
    against membership); created_by_user_id comes from auth, never the client."""

    team_id: int
    name: str = Field(min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=2000)
    enabled: bool = True
    steps: list[PipelineStepInput] = Field(min_length=1)


class PipelineUpdate(BaseModel):
    """PATCH /pipelines/{id} payload — every field optional. When ``steps`` is
    provided it REPLACES the whole ordered set atomically; omit it to leave the
    steps untouched."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=2000)
    enabled: Optional[bool] = None
    steps: Optional[list[PipelineStepInput]] = Field(default=None, min_length=1)


class Pipeline(BaseModel):
    """Read response — the pipeline plus its ordered steps."""

    id: str
    team_id: str
    name: str
    description: Optional[str] = None
    enabled: bool
    created_by_user_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    steps: list[PipelineStep] = []


class PipelineRunCreate(BaseModel):
    """POST /pipelines/{id}/run payload."""

    parent_issue_id: int


class PipelineRun(BaseModel):
    """Read response for a run. The decorated fields (pipeline_name /
    total_steps / current_agent_id) are joined in for the active-run strip; they
    are None on the bare row."""

    id: str
    pipeline_id: str
    parent_issue_id: str
    current_step: int
    status: PipelineRunStatus
    halted_reason: Optional[str] = None
    started_by_user_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None
    # Decoration for the UI strip (joined by the runs-for-parent endpoint).
    pipeline_name: Optional[str] = None
    total_steps: Optional[int] = None
    current_agent_id: Optional[str] = None


class PipelineRunListResponse(BaseModel):
    items: list[PipelineRun]

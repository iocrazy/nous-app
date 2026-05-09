"""Pydantic models for agent_runs read endpoints.

Write paths (INSERT/UPDATE) go through RunRecorder only — no schema here
for them. These shapes serve the GET endpoints that power the Agent Detail
Runs tab, cancel trigger, and Settings → AI Usage dashboard.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field

RunStatus = Literal["running", "completed", "failed", "cancelled", "heartbeat_lost"]


class RunListItem(BaseModel):
    """Slim row for the runs list view — no metadata_json / full_output."""

    id: UUID
    agent_id: UUID
    status: RunStatus
    trigger: str
    model: Optional[str] = None
    provider: Optional[str] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_cents: Optional[float] = None
    started_at: datetime
    ended_at: Optional[datetime] = None
    error_code: Optional[str] = None
    skill_slugs_used: list[str] = Field(default_factory=list)
    # Phase 3a/3b of #199. Top-level runs leave this NULL; runs spawned
    # via the SubAgentTask tool point to the parent. Frontend uses this
    # to show "this is a sub-run" badges and to render Runs lists as a
    # tree.
    parent_run_id: Optional[UUID] = None


class RunDetail(RunListItem):
    """Full detail view — includes summaries + metadata_json + cancel_requested."""

    session_id: Optional[UUID] = None
    team_id: Optional[int] = None
    project_id: Optional[int] = None
    heartbeat_at: datetime
    cancel_requested: bool = False
    prompt_cents_per_1k_snapshot: Optional[float] = None
    completion_cents_per_1k_snapshot: Optional[float] = None
    input_summary: Optional[str] = None
    output_summary: Optional[str] = None
    error_message: Optional[str] = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class RunListResponse(BaseModel):
    """Pagination envelope for the runs list endpoint."""

    items: list[RunListItem]
    total: int
    limit: int
    offset: int


class UsagePerAgent(BaseModel):
    """One row in the AI Usage dashboard, per agent."""

    agent_id: UUID
    agent_slug: Optional[str] = None
    agent_name: Optional[str] = None
    run_count: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_cents: float
    failed_count: int = 0


class UsageAggregate(BaseModel):
    """Settings → AI Usage response shape."""

    scope: Literal["user", "team", "project"]
    month: str  # YYYY-MM
    total_runs: int
    total_tokens: int
    total_cost_cents: float
    per_agent: list[UsagePerAgent]

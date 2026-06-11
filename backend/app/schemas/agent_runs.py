"""Pydantic models for agent_runs read endpoints.

Write paths (INSERT/UPDATE) go through RunRecorder only — no schema here
for them. These shapes serve the GET endpoints that power the Agent Detail
Runs tab, cancel trigger, and Settings → AI Usage dashboard.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

RunStatus = Literal["running", "completed", "failed", "cancelled", "heartbeat_lost"]


class RunListItem(BaseModel):
    """Slim row for the runs list view — no metadata_json / full_output."""

    # BIGINT Snowflake ids (id / parent_run_id / session_id) arrive from the
    # DB as ints but are modelled as str; Pydantic v2 won't coerce int→str
    # without this opt-in. Inherited by RunDetail.
    model_config = ConfigDict(coerce_numbers_to_str=True)

    # agent_runs.id is BIGINT Snowflake (mig 232) → numeric string, not UUID.
    id: str
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
    parent_run_id: Optional[str] = None
    # mig 282: task_tracking PK when the run executed inside a tracked
    # workflow. Lets the list show a "task-linked" hint without a join.
    task_id: Optional[str] = None
    # Display-only 500-char summary (RunRecorder.set_summaries) — used as
    # the list snippet (paperclip-style split pane shows it in the left
    # column). Full content stays in metadata_json on the detail view.
    output_summary: Optional[str] = None


class RunTaskRef(BaseModel):
    """Slim task_tracking reference attached to a run detail (mig 282
    task ↔ run linkage) — powers the "Tasks Touched" block."""

    id: str
    title: Optional[str] = None
    phase: Optional[str] = None
    task_type: Optional[str] = None


class RunDetail(RunListItem):
    """Full detail view — includes summaries + metadata_json + cancel_requested."""

    # ai_sessions.id is BIGINT Snowflake (mig 231) → numeric string.
    session_id: Optional[str] = None
    team_id: Optional[int] = None
    project_id: Optional[int] = None
    heartbeat_at: datetime
    cancel_requested: bool = False
    prompt_cents_per_1k_snapshot: Optional[float] = None
    completion_cents_per_1k_snapshot: Optional[float] = None
    cached_input_tokens: int = 0
    input_summary: Optional[str] = None
    error_message: Optional[str] = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    # Resolved from task_id by the get_run endpoint (None when the run
    # wasn't part of a tracked workflow).
    task: Optional[RunTaskRef] = None


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

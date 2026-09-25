"""Response models for ``GET /issues/{id}/progress`` and ``GET /issues/{id}/schedules``.

``/progress`` returns ``issue_rollup.compute_rollup``'s dict. Its timestamps
come from ORM rows as native ``datetime`` (serialized through ``isoformat()``
by :data:`IssueRollupInstant`, as ``jsonable_encoder`` did), but a caller that
already holds an ISO string passes it through untouched — the union keeps
whichever it got. ``view`` / ``cost`` / ``ended`` / ``step`` / ``origin`` /
``execution_state`` are jsonb the runner and the origin resolvers own; they are
passed through as-is.

Spec: docs/superpowers/specs/2026-09-24-openapi-typed-frontend-design.md
"""

from __future__ import annotations

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict

from app.schemas.wire import WireDatetime

IssueRollupInstant = Union[WireDatetime, str]


class IssueRollupCurrentRun(BaseModel):
    id: str
    status: Optional[str]
    started_at: Optional[IssueRollupInstant]
    model: Optional[str]
    view: dict[str, Any]
    cost: dict[str, Any]
    last_seq: Optional[int]


class IssueRollupRun(BaseModel):
    """``cost_cents`` is the run TREE's total (Σ own_cost_cents)."""

    id: str
    status: Optional[str]
    started_at: Optional[IssueRollupInstant]
    ended_at: Optional[IssueRollupInstant]
    model: Optional[str]
    error_code: Optional[str]
    cost_cents: float
    ended: Any
    step: Any
    charged_points: Optional[float]


class IssueRollupSubIssue(BaseModel):
    id: str
    identifier: Optional[str]
    title: Optional[str]
    status: Optional[str]


class IssueRollupSubIssues(BaseModel):
    total: int
    done: int
    items: list[IssueRollupSubIssue]


class IssueRollupEfficiency(BaseModel):
    runs: int
    steps: int
    tool_calls: int
    tool_errors: int
    deliverables: int
    avg_run_ms: Optional[int]
    turn_end_reasons: dict[str, int]
    cost_per_deliverable_cents: Optional[float]


class IssueRollupBudget(BaseModel):
    """``budget_cents`` null = unlimited; ``pct`` null then too."""

    budget_cents: Optional[int]
    spent_cents: float
    pct: Optional[int]
    state: Literal["ok", "warn", "over"]


class IssueRollupOrigin(BaseModel):
    """``origin_resolvers.resolve_origin``: ``kind`` + ``origin_id`` always,
    plus whatever the kind's resolver adds (passed through)."""

    model_config = ConfigDict(extra="allow")

    kind: str
    origin_id: Optional[Union[str, int]]


class IssueRollup(BaseModel):
    """``phase`` is derived from the runs: paused > waiting_input > running >
    blocked > done > idle."""

    issue_id: str
    status: Optional[str]
    phase: Literal["paused", "waiting_input", "running", "blocked", "done", "idle"]
    paused_at: Optional[IssueRollupInstant]
    current_run: Optional[IssueRollupCurrentRun]
    runs: list[IssueRollupRun]
    sub_issues: IssueRollupSubIssues
    inbox_pending: int
    efficiency: IssueRollupEfficiency
    budget: IssueRollupBudget
    origin: IssueRollupOrigin
    execution_state: dict[str, Any]
    computed_at: WireDatetime


class IssueScheduleItem(BaseModel):
    """One wake-up pointing at the issue, or the routine that created it."""

    id: str
    task_type: str
    fire_at: Optional[str]
    cron_expr: Optional[str]
    text: str
    created_by: str
    enabled: bool
    pause_reason: Optional[str]


class IssueScheduleList(BaseModel):
    items: list[IssueScheduleItem]

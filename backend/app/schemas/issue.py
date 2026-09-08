"""Issue API schemas — top-level user-visible "thing" entity (PR-D1).

Mirrors public.issues table from migration 166. Status / origin / priority
enums match CHECK constraints in the schema verbatim.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


class IssueStatus(str, Enum):
    BACKLOG = "backlog"
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    IN_REVIEW = "in_review"
    BLOCKED = "blocked"
    # Spec-2 slice 2b: agent stalled, needs a human decision/info (a deliberate
    # hand-off — distinct from BLOCKED, which means execution errored).
    NEEDS_FOLLOWUP = "needs_followup"
    DONE = "done"
    CANCELLED = "cancelled"


class IssuePriority(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class IssueOriginKind(str, Enum):
    """MUST stay in lock-step with the DB CHECK (migration 367 /
    models/reviews.py) and the TS union in issuesService.ts — the enum has
    FOUR mirrors, and missing one here made GET /issues/ 500 for any team
    holding a stage-mirror issue (2026-07-18)."""

    MANUAL = "manual"
    CHAT_DELEGATE = "chat_delegate"
    CELERY_PIPELINE = "celery_pipeline"
    AGENT_DISPATCH = "agent_dispatch"
    ROUTINE = "routine"
    ESCALATION = "escalation"
    PROJECT_STAGE = "project_stage"
    PUBLISH = "publish"
    PIPELINE = "pipeline"


class IssueBase(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    description: Optional[str] = Field(default=None, max_length=50000)
    status: IssueStatus = IssueStatus.BACKLOG
    priority: IssuePriority = IssuePriority.MEDIUM
    team_id: Optional[int] = None
    project_id: Optional[int] = None
    parent_id: Optional[int] = None
    # UUID type (not str) so empty-string and malformed values fail at parse,
    # not at the DB. Empty string would silently bypass the XOR check below
    # because "" is falsy.
    assignee_user_id: Optional[UUID] = None
    assignee_agent_id: Optional[UUID] = None
    origin_kind: IssueOriginKind = IssueOriginKind.MANUAL
    origin_id: Optional[str] = None
    origin_fingerprint: str = "default"
    billing_code: Optional[str] = None

    @model_validator(mode="after")
    def assignee_xor(self) -> "IssueBase":
        if self.assignee_user_id is not None and self.assignee_agent_id is not None:
            raise ValueError(
                "assignee_user_id and assignee_agent_id are mutually exclusive"
            )
        return self


class IssueCreate(IssueBase):
    """Caller payload for POST /issues. created_by_* is set by the router from auth context."""


class IssueUpdate(BaseModel):
    """PATCH payload — every field optional. Status changes go through dedicated endpoint."""

    title: Optional[str] = Field(default=None, min_length=1, max_length=500)
    description: Optional[str] = Field(default=None, max_length=50000)
    priority: Optional[IssuePriority] = None
    assignee_user_id: Optional[UUID] = None
    assignee_agent_id: Optional[UUID] = None
    project_id: Optional[int] = None
    team_id: Optional[int] = None
    billing_code: Optional[str] = None
    hidden_at: Optional[datetime] = None  # for soft-delete
    # harness p4 §1-⑤: NULL = unlimited; 0 is allowed (spend nothing more).
    # Optional-with-None cannot express "clear the budget" through
    # exclude_none — clearing goes through ``clear_budget``.
    budget_cents: Optional[int] = Field(default=None, ge=0)
    clear_budget: Optional[bool] = None

    @model_validator(mode="after")
    def assignee_xor(self) -> "IssueUpdate":
        if self.assignee_user_id is not None and self.assignee_agent_id is not None:
            raise ValueError(
                "assignee_user_id and assignee_agent_id are mutually exclusive"
            )
        return self


class IssueStatusTransition(BaseModel):
    status: IssueStatus
    reason: Optional[str] = (
        None  # for transitions that need an audit hint (cancel, block)
    )


class Issue(IssueBase):
    """Read response — all server-set fields included."""

    id: int
    issue_number: int
    identifier: str
    created_by_user_id: Optional[UUID] = None
    created_by_agent_id: Optional[UUID] = None
    dbos_workflow_id: Optional[str] = None
    # BIGINT Snowflake, but str-serialized (unlike id / team_id / project_id
    # above, which stay native int for back-compat). It exists only to be
    # pasted into a URL — a JSON number past 2^53 rounds in the browser and
    # deep-links to a session that does not exist.
    ai_session_id: Optional[str] = None
    execution_locked_at: Optional[datetime] = None
    execution_state: Optional[dict[str, Any]] = None
    paused_at: Optional[datetime] = None
    budget_cents: Optional[int] = None
    request_depth: int = 0
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    hidden_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    @field_validator("ai_session_id", mode="before")
    @classmethod
    def _bigint_to_str(cls, v: Any) -> Any:
        """The repository hands this column back as a native int (the 5.3
        parity rule keeps BIGINTs unconverted). Pydantic v2 will not coerce
        int → str on its own, so do it here rather than at every call site."""
        return str(v) if isinstance(v, int) else v


class IssueListResponse(BaseModel):
    items: list[Issue]
    total: int
    limit: int
    offset: int


class DispatchBlockedReason(str, Enum):
    """Why a dispatch would NOT start anything."""

    NO_ASSIGNEE = "no_assignee"
    DBOS_DISABLED = "dbos_disabled"
    TERMINAL_STATUS = "terminal_status"
    ALREADY_RUNNING = "already_running"


class DispatchPreview(BaseModel):
    """What POST /{id}/dispatch would actually do.

    The rule lives here (server-side) so the confirm dialog reports what will
    happen instead of the client re-deriving it and drifting: the preview and
    the dispatch read the same predicate.
    """

    will_start: bool
    agent_id: Optional[str] = None
    blocked_reason: Optional[DispatchBlockedReason] = None


class IssuePauseResponse(BaseModel):
    """``POST /issues/{id}/pause`` (phase 2a target-level pause). ``run_id``
    is the ROOT run that was asked to stop at its next step boundary, or
    None when nothing was running. Ids str-serialized (Snowflake)."""

    issue_id: str
    paused_at: datetime
    run_id: Optional[str] = None


class IssueResumeResponse(BaseModel):
    """``POST /issues/{id}/resume``. ``dispatched`` is True when a fresh
    ``execute_issue`` was started (queued comments to run, or the last run
    ended paused) and ``workflow_id`` is its id; False when resuming was
    only clearing the flag — including the case where the paused run had
    not observed the pause yet and simply keeps going (``run_id``)."""

    issue_id: str
    dispatched: bool
    # Why the resume did what it did — the UI (Task 8) shows it verbatim:
    #   dispatched — a fresh execute_issue was started (workflow_id is its id)
    #   withdrawn  — the pause had not been observed; the run keeps going (run_id)
    #   running    — not paused, a run is live: queued comments are claimed at
    #                its next step boundary, nothing to dispatch
    #   parked     — the workflow holds the lock waiting on a question; the
    #                answer wakes it (workflow_id is the parked one)
    #   cleared    — nothing to run; only the flag was cleared
    reason: str
    workflow_id: Optional[str] = None
    run_id: Optional[str] = None


class PausedIssueItem(BaseModel):
    """One issue a person paused (``issues.paused_at``), for GET /issues/paused.
    Ids str-serialized (Snowflake), same convention as NeedsInputItem."""

    issue_id: str
    identifier: Optional[str] = None
    title: str
    paused_at: datetime
    team_id: Optional[str] = None
    project_id: Optional[str] = None
    assignee_agent_id: Optional[str] = None


class PausedListResponse(BaseModel):
    items: list[PausedIssueItem]
    # True when more issues are paused than the page holds (the UI says "50+").
    has_more: bool = False


class NeedsInputItem(BaseModel):
    """One issue parked at ``needs_followup`` with the agent waiting on a
    human answer (Spec-4 needs_input first-class). Feeds the Task Center
    'needs your answer' section (Task 3).

    ``issue_id`` / ``project_id`` / ``team_id`` are BIGINT — str-serialized
    per the Snowflake-precision convention, NOT the native int the plain
    ``Issue`` schema uses for these same columns.
    """

    issue_id: str
    title: str
    question: Optional[str] = None
    project_id: Optional[str] = None
    team_id: Optional[str] = None
    asked_at: datetime
    # Which agent is parked on this question. Without it the feed has no agent
    # dimension, and a per-agent surface (the AI Library workbench) can only
    # show a count someone else computed — never the question itself.
    assignee_agent_id: Optional[str] = None
    # Human identifier ("MH-7"). The issue detail route is keyed by it, not by
    # the numeric id, so a "go answer" deep link cannot be built without it.
    identifier: Optional[str] = None
    # Phase 2a: the typed question the issue was parked with (from
    # execution_state.awaiting_input). Absent on plain needs_input parks.
    question_id: Optional[str] = None
    kind: Optional[str] = None
    options: list[dict] = Field(default_factory=list)
    allow_free_text: bool = True


class NeedsInputListResponse(BaseModel):
    items: list[NeedsInputItem]

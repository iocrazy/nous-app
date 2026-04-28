"""Issue API schemas — top-level user-visible "thing" entity (PR-D1).

Mirrors public.issues table from migration 166. Status / origin / priority
enums match CHECK constraints in the schema verbatim.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class IssueStatus(str, Enum):
    BACKLOG = "backlog"
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    IN_REVIEW = "in_review"
    BLOCKED = "blocked"
    DONE = "done"
    CANCELLED = "cancelled"


class IssuePriority(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class IssueOriginKind(str, Enum):
    MANUAL = "manual"
    CHAT_DELEGATE = "chat_delegate"
    CELERY_PIPELINE = "celery_pipeline"
    AGENT_DISPATCH = "agent_dispatch"
    ROUTINE = "routine"
    ESCALATION = "escalation"


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
            raise ValueError("assignee_user_id and assignee_agent_id are mutually exclusive")
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

    @model_validator(mode="after")
    def assignee_xor(self) -> "IssueUpdate":
        if self.assignee_user_id is not None and self.assignee_agent_id is not None:
            raise ValueError("assignee_user_id and assignee_agent_id are mutually exclusive")
        return self


class IssueStatusTransition(BaseModel):
    status: IssueStatus
    reason: Optional[str] = None  # for transitions that need an audit hint (cancel, block)


class Issue(IssueBase):
    """Read response — all server-set fields included."""
    id: int
    issue_number: int
    identifier: str
    created_by_user_id: Optional[UUID] = None
    created_by_agent_id: Optional[UUID] = None
    dbos_workflow_id: Optional[str] = None
    execution_locked_at: Optional[datetime] = None
    execution_state: Optional[dict[str, Any]] = None
    request_depth: int = 0
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    hidden_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class IssueListResponse(BaseModel):
    items: list[Issue]
    total: int
    limit: int
    offset: int

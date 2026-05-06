"""IssueMessage API schemas (A8 paperclip-style chat thread).

Mirrors public.issue_messages from migration 205. Three message kinds in
one timeline; per-kind required fields enforced at the DB CHECK level.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class IssueMessageKind(str, Enum):
    COMMENT = "comment"
    AGENT_RUN = "agent_run"
    SYSTEM_STATUS = "system_status"


class IssueMessage(BaseModel):
    id: UUID
    issue_id: int
    kind: IssueMessageKind
    author_user_id: Optional[UUID] = None
    author_agent_id: Optional[UUID] = None
    body: Optional[str] = None
    meta: dict[str, Any] = Field(default_factory=dict)
    duration_seconds: Optional[int] = None
    agent_run_id: Optional[UUID] = None
    from_status: Optional[str] = None
    to_status: Optional[str] = None
    created_at: datetime


class IssueMessageList(BaseModel):
    messages: list[IssueMessage]
    total: int


class IssueMessagePost(BaseModel):
    """User-facing reply payload. UI sends body + optional agent_id; if
    agent_id is set the backend also dispatches an agent run linked to
    the same issue (recorded as kind='agent_run' in the same thread)."""

    body: str = Field(min_length=1, max_length=50000)
    agent_id: Optional[UUID] = None

    @model_validator(mode="after")
    def _strip_body(self) -> "IssueMessagePost":
        self.body = self.body.strip()
        if not self.body:
            raise ValueError("body cannot be empty after strip")
        return self


class IssueMessagePostResponse(BaseModel):
    """Response includes the user's comment row + the dispatched agent_run
    placeholder row (when agent_id was set)."""

    comment: IssueMessage
    agent_run: Optional[IssueMessage] = None

"""Reviews, notifications, issues, and issue messages."""

from __future__ import annotations

import datetime
import uuid
from typing import Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Double,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as Uuid
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import text

from app.db.orm_base import Base


class IssueSequence(Base):
    __tablename__ = "issue_sequence"
    __table_args__ = (
        PrimaryKeyConstraint("scope", name="issue_sequence_pkey"),
        {
            "comment": (
                "Atomic counter for issues.identifier (MH-N). Single-row table; "
                "UPDATE...RETURNING is the contract."
            ),
            "schema": "public",
        },
    )

    scope: Mapped[str] = mapped_column(
        Text, primary_key=True, server_default=text("'global'::text")
    )
    prefix: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'MH'::text")
    )
    counter: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )


class ReviewAnnotations(Base):
    __tablename__ = "review_annotations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["comment_id"],
            ["public.review_comments.id"],
            ondelete="CASCADE",
            name="review_annotations_comment_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="review_annotations_pkey"),
        Index("idx_review_annotations_comment_id", "comment_id"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    comment_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tool_type: Mapped[str] = mapped_column(String(20), nullable=False)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True),
        nullable=False,
        server_default=text("now()"),
    )


class ReviewComments(Base):
    __tablename__ = "review_comments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["parent_id"],
            ["public.review_comments.id"],
            ondelete="CASCADE",
            name="review_comments_parent_id_fkey",
        ),
        ForeignKeyConstraint(
            ["resource_id"],
            ["public.resources.id"],
            ondelete="CASCADE",
            name="review_comments_resource_id_fkey",
        ),
        ForeignKeyConstraint(
            ["version_id"],
            ["public.resource_versions.id"],
            ondelete="SET NULL",
            name="review_comments_version_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="review_comments_pkey"),
        Index("idx_review_comments_author_id", "author_id"),
        Index("idx_review_comments_parent", "parent_id"),
        Index("idx_review_comments_resource", "resource_id"),
        Index("idx_review_comments_version_id", "version_id"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    resource_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    author_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'open'::character varying"),
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True),
        nullable=False,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True),
        nullable=False,
        server_default=text("now()"),
    )
    timecode: Mapped[Optional[float]] = mapped_column(Double(53))
    frame_number: Mapped[Optional[int]] = mapped_column(Integer)
    parent_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    version_id: Mapped[Optional[int]] = mapped_column(BigInteger)


class ReviewStatus(Base):
    __tablename__ = "review_status"
    __table_args__ = (
        ForeignKeyConstraint(
            ["resource_id"],
            ["public.resources.id"],
            ondelete="CASCADE",
            name="review_status_resource_id_fkey",
        ),
        ForeignKeyConstraint(
            ["version_id"],
            ["public.resource_versions.id"],
            ondelete="CASCADE",
            name="review_status_version_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="review_status_pkey"),
        Index("idx_review_status_resource", "resource_id"),
        Index("idx_review_status_reviewer_id", "reviewer_id"),
        Index("idx_review_status_version_id", "version_id"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    resource_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reviewer_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'pending'::character varying"),
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True),
        nullable=False,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True),
        nullable=False,
        server_default=text("now()"),
    )
    comment: Mapped[Optional[str]] = mapped_column(Text)
    version_id: Mapped[Optional[int]] = mapped_column(BigInteger)


class Notifications(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint(
            "type::text = ANY (ARRAY['system'::character varying::text, 'team'::character varying::text])",
            name="notifications_type_check",
        ),
        ForeignKeyConstraint(
            ["team_id"],
            ["public.teams.id"],
            ondelete="CASCADE",
            name="notifications_team_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="notifications_pkey"),
        Index("idx_notifications_created_by", "created_by"),
        Index("idx_notifications_team", "team_id"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[Optional[str]] = mapped_column(Text)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True),
        server_default=text("now()"),
    )
    team_id: Mapped[Optional[int]] = mapped_column(BigInteger)


class UserNotifications(Base):
    __tablename__ = "user_notifications"
    __table_args__ = (
        ForeignKeyConstraint(
            ["notification_id"],
            ["public.notifications.id"],
            ondelete="CASCADE",
            name="user_notifications_notification_id_fkey",
        ),
        PrimaryKeyConstraint(
            "user_id", "notification_id", name="user_notifications_pkey"
        ),
        Index("idx_user_notifications_notification_id", "notification_id"),
        {"schema": "public"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    notification_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    read_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))


class Issues(Base):
    __tablename__ = "issues"
    __table_args__ = (
        CheckConstraint(
            "NOT (assignee_agent_id IS NOT NULL AND assignee_user_id IS NOT NULL)",
            name="issues_assignee_xor",
        ),
        CheckConstraint(
            "created_by_agent_id IS NOT NULL OR created_by_user_id IS NOT NULL",
            name="issues_creator_required",
        ),
        CheckConstraint(
            "description IS NULL OR length(description) <= 50000",
            name="issues_description_check",
        ),
        CheckConstraint(
            "length(title) >= 1 AND length(title) <= 500",
            name="issues_title_check",
        ),
        CheckConstraint(
            "origin_kind = ANY (ARRAY['manual'::text, 'chat_delegate'::text, "
            "'celery_pipeline'::text, 'agent_dispatch'::text, 'routine'::text, 'escalation'::text])",
            name="issues_origin_kind_check",
        ),
        CheckConstraint(
            "priority = ANY (ARRAY['critical'::text, 'high'::text, 'medium'::text, 'low'::text])",
            name="issues_priority_check",
        ),
        CheckConstraint(
            "request_depth >= 0 AND request_depth < 100",
            name="issues_request_depth_check",
        ),
        CheckConstraint(
            "status = ANY (ARRAY['backlog'::text, 'todo'::text, 'in_progress'::text, "
            "'in_review'::text, 'blocked'::text, 'done'::text, 'cancelled'::text])",
            name="issues_status_check",
        ),
        ForeignKeyConstraint(
            ["ai_session_id"],
            ["public.ai_sessions.id"],
            ondelete="SET NULL",
            name="issues_ai_session_id_fkey",
        ),
        ForeignKeyConstraint(
            ["assignee_agent_id"],
            ["public.ai_agents.id"],
            ondelete="SET NULL",
            name="issues_assignee_agent_id_fkey",
        ),
        ForeignKeyConstraint(
            ["created_by_agent_id"],
            ["public.ai_agents.id"],
            ondelete="SET NULL",
            name="issues_created_by_agent_id_fkey",
        ),
        ForeignKeyConstraint(
            ["parent_id"],
            ["public.issues.id"],
            ondelete="SET NULL",
            name="issues_parent_id_fkey",
        ),
        ForeignKeyConstraint(
            ["project_id"],
            ["public.projects.id"],
            ondelete="SET NULL",
            name="issues_project_id_fkey",
        ),
        ForeignKeyConstraint(
            ["team_id"],
            ["public.teams.id"],
            ondelete="SET NULL",
            name="issues_team_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="issues_pkey"),
        Index(
            "idx_issues_ai_session",
            "ai_session_id",
            postgresql_where="(ai_session_id IS NOT NULL)",
        ),
        Index(
            "issues_assignee_agent_status_idx",
            "assignee_agent_id",
            "status",
            postgresql_where="(assignee_agent_id IS NOT NULL)",
        ),
        Index(
            "issues_assignee_user_status_idx",
            "assignee_user_id",
            "status",
            postgresql_where="(assignee_user_id IS NOT NULL)",
        ),
        Index("issues_created_at_idx", "created_at"),
        Index(
            "issues_created_by_user_idx",
            "created_by_user_id",
            postgresql_where="(created_by_user_id IS NOT NULL)",
        ),
        Index(
            "issues_dbos_workflow_idx",
            "dbos_workflow_id",
            postgresql_where="(dbos_workflow_id IS NOT NULL)",
        ),
        Index(
            "issues_description_trgm_idx",
            "description",
            postgresql_ops={"description": "gin_trgm_ops"},
            postgresql_using="gin",
            postgresql_where="(description IS NOT NULL)",
        ),
        Index("issues_identifier_idx", "identifier", unique=True),
        Index(
            "issues_identifier_trgm_idx",
            "identifier",
            postgresql_ops={"identifier": "gin_trgm_ops"},
            postgresql_using="gin",
        ),
        Index(
            "issues_open_routine_execution_uq",
            "origin_kind",
            "origin_id",
            "origin_fingerprint",
            postgresql_where=(
                "((origin_kind = 'routine'::text) AND "
                "(status <> ALL (ARRAY['done'::text, 'cancelled'::text])) AND "
                "(hidden_at IS NULL))"
            ),
            unique=True,
        ),
        Index("issues_origin_idx", "origin_kind", "origin_id"),
        Index("issues_parent_idx", "parent_id"),
        Index("issues_project_status_idx", "project_id", "status"),
        Index("issues_status_idx", "status"),
        Index("issues_team_status_idx", "team_id", "status"),
        Index(
            "issues_title_trgm_idx",
            "title",
            postgresql_ops={"title": "gin_trgm_ops"},
            postgresql_using="gin",
        ),
        {
            "comment": (
                'Top-level user-visible "thing". DBOS workflows reference '
                "issues.id via dbos_workflow_id and issues.dbos_workflow_id "
                "reciprocally. Schema ported from Paperclip (MIT) with mediahub "
                "adaptations. Realtime publication excludes execution_state / "
                "execution_locked_at / dbos_workflow_id (internal plumbing or "
                "sensitive); see migration 172."
            ),
            "schema": "public",
        },
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    issue_number: Mapped[int] = mapped_column(Integer, nullable=False)
    identifier: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'backlog'::text")
    )
    priority: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'medium'::text")
    )
    origin_kind: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'manual'::text")
    )
    origin_fingerprint: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'default'::text")
    )
    request_depth: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
        comment=(
            "Delegation chain depth — incremented when an agent dispatches a child issue. "
            "Capped at 100 to break cycles."
        ),
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True),
        nullable=False,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True),
        nullable=False,
        server_default=text("now()"),
    )
    team_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    project_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    parent_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    goal_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    description: Mapped[Optional[str]] = mapped_column(Text)
    assignee_agent_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    assignee_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    created_by_agent_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    dbos_workflow_id: Mapped[Optional[str]] = mapped_column(
        Text,
        comment="DBOS workflow handle (string id from DBOS.start_workflow). NULL until execution starts.",
    )
    execution_locked_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True)
    )
    execution_state: Mapped[Optional[dict]] = mapped_column(JSONB)
    origin_id: Mapped[Optional[str]] = mapped_column(Text)
    billing_code: Mapped[Optional[str]] = mapped_column(Text)
    started_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    completed_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    cancelled_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    hidden_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    ai_session_id: Mapped[Optional[int]] = mapped_column(BigInteger)


class IssueMessages(Base):
    __tablename__ = "issue_messages"
    __table_args__ = (
        CheckConstraint(
            "\nCASE kind"
            "\n    WHEN 'comment'::text THEN author_user_id IS NOT NULL OR author_agent_id IS NOT NULL"
            "\n    WHEN 'agent_run'::text THEN author_agent_id IS NOT NULL"
            "\n    WHEN 'system_status'::text THEN author_agent_id IS NULL"
            "\n    ELSE false"
            "\nEND",
            name="issue_messages_author_chk",
        ),
        CheckConstraint(
            "kind = 'system_status'::text) = (from_status IS NOT NULL OR to_status IS NOT NULL",
            name="issue_messages_status_chk",
        ),
        CheckConstraint(
            "kind = ANY (ARRAY['comment'::text, 'agent_run'::text, 'system_status'::text])",
            name="issue_messages_kind_check",
        ),
        ForeignKeyConstraint(
            ["agent_run_id"],
            ["public.agent_runs.id"],
            ondelete="SET NULL",
            name="issue_messages_agent_run_id_fkey",
        ),
        ForeignKeyConstraint(
            ["author_agent_id"],
            ["public.ai_agents.id"],
            ondelete="SET NULL",
            name="issue_messages_author_agent_id_fkey",
        ),
        ForeignKeyConstraint(
            ["issue_id"],
            ["public.issues.id"],
            ondelete="CASCADE",
            name="issue_messages_issue_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="issue_messages_pkey"),
        Index("idx_issue_messages_issue_created", "issue_id", "created_at"),
        Index(
            "idx_issue_messages_run",
            "agent_run_id",
            postgresql_where="(agent_run_id IS NOT NULL)",
        ),
        {
            "comment": (
                "Paperclip-style chat thread per issue (A8). Three kinds in one "
                "timeline:\n"
                "   comment / agent_run / system_status. RLS cascades through "
                "issues:\n"
                "   anyone who can SELECT the issue can SELECT its messages. "
                "Trigger\n"
                "   trg_issue_status_change_message auto-emits a system_status row "
                "on\n"
                "   any issues.status update."
            ),
            "schema": "public",
        },
    )

    issue_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    meta: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True),
        nullable=False,
        server_default=text("now()"),
    )
    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    author_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    author_agent_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    body: Mapped[Optional[str]] = mapped_column(Text)
    duration_seconds: Mapped[Optional[int]] = mapped_column(Integer)
    from_status: Mapped[Optional[str]] = mapped_column(Text)
    to_status: Mapped[Optional[str]] = mapped_column(Text)
    agent_run_id: Mapped[Optional[int]] = mapped_column(BigInteger)


# Unused import suppressor — UniqueConstraint imported for completeness
__all__ = [
    "IssueSequence",
    "ReviewAnnotations",
    "ReviewComments",
    "ReviewStatus",
    "Notifications",
    "UserNotifications",
    "Issues",
    "IssueMessages",
]

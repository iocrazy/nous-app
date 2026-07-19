"""Content relay pipelines (W2b) — fixed-order agent handoff on sub-issues.

Three tables:
  * ``issue_pipelines``       — the pipeline definition, scoped to a team.
  * ``issue_pipeline_steps``  — the ordered relay steps (agent + templates).
  * ``issue_pipeline_runs``   — one active relay per parent issue.

Reference metadata only — the schema is owned by
``supabase/migrations/371_issue_pipelines.sql``. Every FK below is declared with
its live ondelete so the schema-drift FK gate (tests/db/test_schema_drift.py)
matches prod both directions.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as Uuid
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import text

from app.db.orm_base import Base


class IssuePipelines(Base):
    __tablename__ = "issue_pipelines"
    __table_args__ = (
        ForeignKeyConstraint(
            ["team_id"],
            ["public.teams.id"],
            ondelete="CASCADE",
            name="issue_pipelines_team_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="issue_pipelines_pkey"),
        Index("issue_pipelines_team_idx", "team_id"),
        {
            "comment": (
                "Content relay pipeline definition (W2b) — a fixed, ordered "
                "relay of agent steps, scoped to a team."
            ),
            "schema": "public",
        },
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    team_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class IssuePipelineSteps(Base):
    __tablename__ = "issue_pipeline_steps"
    __table_args__ = (
        ForeignKeyConstraint(
            ["pipeline_id"],
            ["public.issue_pipelines.id"],
            ondelete="CASCADE",
            name="issue_pipeline_steps_pipeline_id_fkey",
        ),
        ForeignKeyConstraint(
            ["agent_id"],
            ["public.ai_agents.id"],
            ondelete="CASCADE",
            name="issue_pipeline_steps_agent_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="issue_pipeline_steps_pkey"),
        UniqueConstraint(
            "pipeline_id",
            "step_order",
            name="issue_pipeline_steps_pipeline_id_step_order_key",
        ),
        Index("issue_pipeline_steps_pipeline_idx", "pipeline_id"),
        {
            "comment": "Ordered relay steps for a content pipeline (W2b).",
            "schema": "public",
        },
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    pipeline_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    agent_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    title_template: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_template: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class IssuePipelineRuns(Base):
    __tablename__ = "issue_pipeline_runs"
    __table_args__ = (
        CheckConstraint(
            "status = ANY (ARRAY['running'::text, 'completed'::text, "
            "'halted'::text, 'cancelled'::text])",
            name="issue_pipeline_runs_status_check",
        ),
        ForeignKeyConstraint(
            ["pipeline_id"],
            ["public.issue_pipelines.id"],
            ondelete="CASCADE",
            name="issue_pipeline_runs_pipeline_id_fkey",
        ),
        ForeignKeyConstraint(
            ["parent_issue_id"],
            ["public.issues.id"],
            ondelete="CASCADE",
            name="issue_pipeline_runs_parent_issue_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="issue_pipeline_runs_pkey"),
        Index("issue_pipeline_runs_parent_idx", "parent_issue_id"),
        Index("issue_pipeline_runs_pipeline_idx", "pipeline_id"),
        Index(
            "issue_pipeline_runs_one_running_per_parent",
            "parent_issue_id",
            unique=True,
            postgresql_where="(status = 'running'::text)",
        ),
        {
            "comment": "One active content-relay run per parent issue (W2b).",
            "schema": "public",
        },
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    pipeline_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    parent_issue_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    current_step: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'running'::text")
    )
    halted_reason: Mapped[Optional[str]] = mapped_column(Text)
    started_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    completed_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))

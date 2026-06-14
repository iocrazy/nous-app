"""Agent runtime: runs, run events, skills, state history, tasks, and workers."""

from __future__ import annotations

import datetime
import decimal
import uuid
from typing import Optional

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    SmallInteger,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.orm_base import Base


class AgentStateHistory(Base):
    __tablename__ = "agent_state_history"
    __table_args__ = (
        ForeignKeyConstraint(
            ["agent_id"],
            ["public.ai_agents.id"],
            ondelete="CASCADE",
            name="agent_state_history_agent_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="agent_state_history_pkey"),
        Index("idx_state_history_agent", "agent_id", "changed_at"),
        {
            "comment": "M2 Persistent Workforce: audit trail for state machine transitions.",
            "schema": "public",
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    to_state: Mapped[str] = mapped_column(Text, nullable=False)
    trigger: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    changed_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True),
        nullable=False,
        server_default=text("now()"),
    )
    from_state: Mapped[Optional[str]] = mapped_column(Text)
    task_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid,
        comment="UUID 形式的 task id，对应 task_tracking.dbos_workflow_id (TEXT) — 应用层 cast 后查询。FK 在 A4 (migration 200) 移除。",
    )


class AgentWorkers(Base):
    __tablename__ = "agent_workers"
    __table_args__ = (
        CheckConstraint(
            "state = ANY (ARRAY['idle'::text, 'working'::text, 'waiting_for_other'::text,"
            " 'blocked'::text, 'paused'::text, 'terminated'::text])",
            name="agent_workers_state_check",
        ),
        ForeignKeyConstraint(
            ["agent_id"],
            ["public.ai_agents.id"],
            ondelete="CASCADE",
            name="agent_workers_agent_id_fkey",
        ),
        PrimaryKeyConstraint("agent_id", name="agent_workers_pkey"),
        Index(
            "idx_agent_workers_heartbeat",
            "heartbeat_at",
            postgresql_where="(state = ANY (ARRAY['idle'::text, 'working'::text, 'waiting_for_other'::text]))",
        ),
        {
            "comment": "M2 Persistent Workforce: one row per agent runtime state.",
            "schema": "public",
        },
    )

    agent_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    heartbeat_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True),
        nullable=False,
        server_default=text("now()"),
    )
    started_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True),
        nullable=False,
        server_default=text("now()"),
    )
    state_changed_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True),
        nullable=False,
        server_default=text("now()"),
    )
    current_task_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    worker_pid: Mapped[Optional[int]] = mapped_column(Integer)
    worker_hostname: Mapped[Optional[str]] = mapped_column(Text)


class AgentRuns(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        CheckConstraint(
            "liveness_state = ANY (ARRAY['running'::text, 'silent'::text, 'stuck'::text,"
            " 'dead'::text, 'cancelled'::text])",
            name="agent_runs_liveness_state_check",
        ),
        CheckConstraint(
            "status = ANY (ARRAY['running'::text, 'completed'::text, 'failed'::text,"
            " 'cancelled'::text, 'heartbeat_lost'::text])",
            name="agent_runs_status_check",
        ),
        ForeignKeyConstraint(
            ["agent_id"],
            ["public.ai_agents.id"],
            ondelete="CASCADE",
            name="agent_runs_agent_id_fkey",
        ),
        ForeignKeyConstraint(
            ["issue_id"],
            ["public.issues.id"],
            ondelete="SET NULL",
            name="agent_runs_issue_id_fkey",
        ),
        ForeignKeyConstraint(
            ["parent_run_id"],
            ["public.agent_runs.id"],
            ondelete="CASCADE",
            name="agent_runs_parent_run_id_fkey",
        ),
        ForeignKeyConstraint(
            ["root_run_id"],
            ["public.agent_runs.id"],
            name="agent_runs_root_run_id_fkey",
        ),
        ForeignKeyConstraint(
            ["session_id"],
            ["public.ai_sessions.id"],
            ondelete="SET NULL",
            name="agent_runs_session_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="agent_runs_pkey"),
        Index("idx_agent_runs_agent_started", "agent_id", "started_at"),
        Index(
            "idx_agent_runs_billing",
            "team_id",
            "created_at",
            postgresql_where="(team_id IS NOT NULL)",
        ),
        Index(
            "idx_agent_runs_heartbeat",
            "heartbeat_at",
            postgresql_where="(status = 'running'::text)",
        ),
        Index(
            "idx_agent_runs_issue",
            "issue_id",
            postgresql_where="(issue_id IS NOT NULL)",
        ),
        Index(
            "idx_agent_runs_liveness_scan",
            "liveness_state",
            "heartbeat_at",
            postgresql_where="(status = 'running'::text)",
        ),
        Index(
            "idx_agent_runs_parent",
            "parent_run_id",
            postgresql_where="(parent_run_id IS NOT NULL)",
        ),
        Index(
            "idx_agent_runs_root_tree",
            "root_run_id",
            "started_at",
            postgresql_where="(root_run_id IS NOT NULL)",
        ),
        Index(
            "idx_agent_runs_running_agent",
            "agent_id",
            postgresql_where="(status = 'running'::text)",
        ),
        Index(
            "idx_agent_runs_running_user",
            "user_id",
            postgresql_where="(status = 'running'::text)",
        ),
        Index(
            "idx_agent_runs_session",
            "session_id",
            postgresql_where="(session_id IS NOT NULL)",
        ),
        Index(
            "idx_agent_runs_useful_action_scan",
            "last_useful_action_at",
            postgresql_where="(status = 'running'::text)",
        ),
        {"schema": "public"},
    )

    agent_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    started_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True),
        nullable=False,
        server_default=text("now()"),
    )
    heartbeat_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True),
        nullable=False,
        server_default=text("now()"),
    )
    prompt_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    completion_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    trigger: Mapped[str] = mapped_column(Text, nullable=False)
    skill_slugs_used: Mapped[list[str]] = mapped_column(
        ARRAY(Text()),
        nullable=False,
        server_default=text("'{}'::text[]"),
    )
    metadata_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True),
        nullable=False,
        server_default=text("now()"),
    )
    agent_depth: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0")
    )
    liveness_state: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'running'::text"),
        comment=(
            "paperclip-style 5-state liveness orthogonal to status.\n"
            "   running→silent→stuck→dead transitions are driven by the scanner\n"
            "   in app/workflows/liveness_scanner.py based on heartbeat_at +\n"
            "   last_useful_action_at thresholds."
        ),
    )
    continuation_attempt: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
        comment=(
            "Number of times the scanner has tried to revive this run from\n"
            "   stuck. Bumped by the scanner; capped to prevent infinite loops."
        ),
    )
    output_silence_bytes: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text("0"),
        comment=(
            "RESERVED — not currently used. Was intended as an output-frozen\n"
            "   signal but the scanner never wrote or read it; liveness keys off\n"
            "   heartbeat_at + last_useful_action_at only. Kept (always 0) to\n"
            "   avoid a column-drop migration; safe to drop in a later cleanup."
        ),
    )
    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    cached_input_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    team_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    project_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    ended_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    model: Mapped[Optional[str]] = mapped_column(Text)
    provider: Mapped[Optional[str]] = mapped_column(Text)
    total_tokens: Mapped[Optional[int]] = mapped_column(
        Integer,
        Computed("(prompt_tokens + completion_tokens)", persisted=True),
    )
    prompt_cents_per_1k_snapshot: Mapped[Optional[decimal.Decimal]] = mapped_column(
        Numeric(12, 6)
    )
    completion_cents_per_1k_snapshot: Mapped[Optional[decimal.Decimal]] = mapped_column(
        Numeric(12, 6)
    )
    cost_cents: Mapped[Optional[decimal.Decimal]] = mapped_column(Numeric(12, 6))
    input_summary: Mapped[Optional[str]] = mapped_column(Text)
    output_summary: Mapped[Optional[str]] = mapped_column(Text)
    error_code: Mapped[Optional[str]] = mapped_column(Text)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    delegation_reason: Mapped[Optional[str]] = mapped_column(Text)
    issue_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        comment=(
            "Optional FK back to issues. Set when an agent run was dispatched\n"
            '   from an issue (paperclip-style "reply triggers agent"). Surfaced\n'
            "   in issue_messages.kind=agent_run for the chat thread."
        ),
    )
    last_useful_action_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True),
        comment=(
            "Last time the agent made observable progress (new output bytes,\n"
            "   completed step, written file, etc). Distinct from heartbeat_at —\n"
            '   heartbeat says "process alive," last_useful_action_at says\n'
            '   "actually doing work." Set by the runtime, not the scanner.'
        ),
    )
    liveness_changed_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True)
    )
    session_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    parent_run_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    root_run_id: Mapped[Optional[int]] = mapped_column(BigInteger)


class AgentSkills(Base):
    __tablename__ = "agent_skills"
    __table_args__ = (
        ForeignKeyConstraint(
            ["agent_id"],
            ["public.ai_agents.id"],
            ondelete="CASCADE",
            name="agent_skills_agent_id_fkey",
        ),
        ForeignKeyConstraint(
            ["skill_id"],
            ["public.skills.id"],
            ondelete="CASCADE",
            name="agent_skills_skill_id_fkey",
        ),
        PrimaryKeyConstraint("agent_id", "skill_id", name="agent_skills_pkey"),
        Index("idx_agent_skills_agent", "agent_id", "sort_order"),
        Index("idx_agent_skills_skill", "skill_id"),
        {
            "comment": "Binds agents to their available skills (lazy-readable skill injection)",
            "schema": "public",
        },
    )

    agent_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    skill_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    sort_order: Mapped[Optional[int]] = mapped_column(Integer, server_default=text("0"))
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True),
        server_default=text("now()"),
    )
    enabled: Mapped[Optional[bool]] = mapped_column(
        Boolean,
        server_default=text("true"),
        comment="Per-binding toggle to disable a skill on an agent without removing the row",
    )


class AgentRunEvents(Base):
    __tablename__ = "agent_run_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id"],
            ["public.agent_runs.id"],
            ondelete="CASCADE",
            name="agent_run_events_run_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="agent_run_events_pkey"),
        Index("idx_agent_run_events_created", "created_at"),
        Index("idx_agent_run_events_run", "run_id", "iteration"),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    iteration: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    prompt_tokens_delta: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    completion_tokens_delta: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    cost_cents_delta: Mapped[decimal.Decimal] = mapped_column(
        Numeric(10, 6),
        nullable=False,
        server_default=text("0"),
    )
    hook_decisions: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    metadata_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True),
        nullable=False,
        server_default=text("now()"),
    )
    run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tool_name: Mapped[Optional[str]] = mapped_column(Text)
    tool_args_summary: Mapped[Optional[str]] = mapped_column(Text)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    model: Mapped[Optional[str]] = mapped_column(Text)
    provider: Mapped[Optional[str]] = mapped_column(Text)
    error_code: Mapped[Optional[str]] = mapped_column(Text)
    error_message: Mapped[Optional[str]] = mapped_column(Text)


class AgentTasks(Base):
    __tablename__ = "agent_tasks"
    __table_args__ = (
        CheckConstraint(
            "lifecycle_status = ANY (ARRAY['queued'::text, 'assigned'::text, 'in_progress'::text,"
            " 'waiting_for_other'::text, 'blocked'::text, 'done'::text, 'failed'::text, 'cancelled'::text])",
            name="agent_tasks_lifecycle_status_check",
        ),
        ForeignKeyConstraint(
            ["agent_id"],
            ["public.ai_agents.id"],
            ondelete="CASCADE",
            name="agent_tasks_agent_id_fkey",
        ),
        ForeignKeyConstraint(
            ["current_run_id"],
            ["public.agent_runs.id"],
            ondelete="SET NULL",
            name="agent_tasks_current_run_id_fkey",
        ),
        ForeignKeyConstraint(
            ["inbox_message_id"],
            ["public.agent_inbox.id"],
            ondelete="SET NULL",
            name="agent_tasks_inbox_message_id_fkey",
        ),
        ForeignKeyConstraint(
            ["parent_task_id"],
            ["public.agent_tasks.id"],
            ondelete="SET NULL",
            name="agent_tasks_parent_task_id_fkey",
        ),
        ForeignKeyConstraint(
            ["root_task_id"],
            ["public.agent_tasks.id"],
            ondelete="SET NULL",
            name="agent_tasks_root_task_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="agent_tasks_pkey"),
        Index(
            "agent_tasks_issue_id_idx",
            "issue_id",
            postgresql_where="(issue_id IS NOT NULL)",
        ),
        Index(
            "idx_tasks_active",
            "agent_id",
            "started_at",
            postgresql_where="(lifecycle_status = ANY (ARRAY['in_progress'::text, 'waiting_for_other'::text]))",
        ),
        Index(
            "idx_tasks_queue",
            "agent_id",
            "created_at",
            postgresql_where="(lifecycle_status = ANY (ARRAY['queued'::text, 'assigned'::text]))",
        ),
        Index("idx_tasks_user_recent", "user_id", "created_at"),
        {
            "comment": "M2 Persistent Workforce: per-agent task queue with lifecycle state machine.",
            "schema": "public",
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    lifecycle_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'queued'::text")
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
    parent_task_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    root_task_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    inbox_message_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    title: Mapped[Optional[str]] = mapped_column(Text)
    result: Mapped[Optional[dict]] = mapped_column(JSONB)
    error_code: Mapped[Optional[str]] = mapped_column(Text)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    assigned_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    started_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    ended_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    issue_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        comment=(
            "Back-reference to issues.id. agent_tasks rows that originate from a "
            "chat_delegate or agent_dispatch issue link back here."
        ),
    )
    current_run_id: Mapped[Optional[int]] = mapped_column(BigInteger)

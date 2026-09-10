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
    Identity,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    SmallInteger,
    Text,
    UniqueConstraint,
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
            " 'dead'::text, 'cancelled'::text, 'finished'::text])",
            name="agent_runs_liveness_state_check",
        ),
        CheckConstraint(
            "status = ANY (ARRAY['running'::text, 'completed'::text, 'failed'::text,"
            " 'cancelled'::text, 'heartbeat_lost'::text])",
            name="agent_runs_status_check",
        ),
        CheckConstraint(
            "outcome IS NULL OR outcome = ANY (ARRAY['pending'::text,"
            " 'user_accepted'::text, 'user_rejected'::text, 'timeout'::text])",
            name="agent_runs_outcome_check",
        ),
        ForeignKeyConstraint(
            ["agent_id"],
            ["public.ai_agents.id"],
            ondelete="CASCADE",
            name="agent_runs_agent_id_fkey",
        ),
        ForeignKeyConstraint(
            ["episode_id"],
            ["public.episodes.id"],
            ondelete="SET NULL",
            name="agent_runs_episode_id_fkey",
        ),
        ForeignKeyConstraint(
            ["fork_of_run_id"],
            ["public.agent_runs.id"],
            ondelete="SET NULL",
            name="agent_runs_fork_of_run_id_fkey",
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
            ["conversation_id"],
            ["public.conversations.id"],
            ondelete="SET NULL",
            name="agent_runs_conversation_id_fkey",
        ),
        # task_id is text because task_tracking's PK is dbos_workflow_id, not a
        # bigint surrogate.
        ForeignKeyConstraint(
            ["task_id"],
            ["public.task_tracking.dbos_workflow_id"],
            ondelete="SET NULL",
            name="agent_runs_task_id_fkey",
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
    pause_requested: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
        comment="453: pause signal for the live run; paused-ness lives on the target",
    )
    fork_of_run_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, comment="453: forked from that run's events[:fork_at_seq]"
    )
    fork_at_seq: Mapped[Optional[int]] = mapped_column(Integer)
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
            "paperclip-style liveness, orthogonal to status — describes HOW\n"
            "   the run ended, never WHETHER it succeeded.\n"
            "   running→silent→stuck→dead is the degradation ladder driven by\n"
            "   the scanner in app/workflows/liveness_scanner.py based on\n"
            "   heartbeat_at + last_useful_action_at thresholds.\n"
            "   finished = wound up in an orderly way (RunRecorder, mig 406),\n"
            "   written for status=completed AND status=failed alike;\n"
            "   cancelled mirrors status=cancelled. dead is reserved for 'the\n"
            "   process actually died' and is always written together with\n"
            "   status=failed — never a generic terminal value."
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
    # mig 404 (A4). Third and last member of the server-bound scope triple —
    # see app/services/ai/scope/agent_run_scope.py. Stamped once at insert,
    # never updated; NULL = project-wide (no episode restriction).
    episode_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    ended_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    undone_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True),
        comment="mig 415: run 级一次性撤销标记；NULL = 未撤销",
    )
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
    outcome: Mapped[Optional[str]] = mapped_column(
        Text,
        comment=(
            "How the run was resolved once it finished. NULL until classified; "
            "'pending' means awaiting user verdict."
        ),
    )
    task_id: Mapped[Optional[str]] = mapped_column(
        Text,
        comment="Free-form external task correlation id (text, not the bigint issue_id).",
    )
    conversation_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    attribution: Mapped[Optional[str]] = mapped_column(
        Text,
        comment=(
            "Two-level cost attribution (W3c): 'direct_human' (a human "
            "initiated this turn) vs 'rule_owner' (a scheduled routine or "
            "pipeline advance fired it on the owner's behalf). Derived from the "
            "issue origin_kind at dispatch. NULL = legacy / unclassified."
        ),
    )


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
    cost_snapshot: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        comment="Pricing inputs frozen at event time, so later price changes cannot rewrite history.",
    )
    byok_key_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        comment="provider_byok_keys row this event billed against. NULL = platform key.",
    )
    parent_run_id: Mapped[Optional[int]] = mapped_column(BigInteger)


class AgentRunTranscriptEvents(Base):
    """Per-run transcript stream (mig 397).

    NOT the same thing as ``AgentRunEvents`` above: that table is the
    mig-155 cost-audit log (iteration / token deltas, written by
    cost_auditor). Mig 285 tried to create THIS shape under that name and
    silently no-oped on IF NOT EXISTS — the transcript stream lives here
    instead. Writer: RunRecorder.record_event; reader:
    GET /ai-library/runs/{id}/events.
    """

    __tablename__ = "agent_run_transcript_events"
    __table_args__ = (
        CheckConstraint(
            "event_type = ANY (ARRAY['user'::text, 'assistant'::text,"
            " 'tool_call'::text, 'error'::text, 'system'::text,"
            " 'llm_retry'::text, 'todo_write'::text,"
            " 'compaction_start'::text, 'compaction_summary'::text,"
            " 'compaction_end'::text, 'turn_end'::text,"
            " 'step_start'::text, 'step_end'::text, 'inbox_claimed'::text,"
            " 'deliverable'::text, 'budget_check'::text,"
            " 'question_asked'::text, 'question_answered'::text,"
            " 'capability_denied'::text, 'fork'::text,"
            " 'subagent_spawned'::text, 'subagent_done'::text,"
            " 'schedule_set'::text])",
            name="agent_run_transcript_events_event_type_check",
        ),
        ForeignKeyConstraint(
            ["run_id"],
            ["public.agent_runs.id"],
            ondelete="CASCADE",
            name="agent_run_transcript_events_run_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="agent_run_transcript_events_pkey"),
        UniqueConstraint(
            "run_id", "seq", name="agent_run_transcript_events_run_id_seq_key"
        ),
        Index("idx_agent_run_transcript_events_run_id", "run_id"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    turn: Mapped[Optional[int]] = mapped_column(
        Integer, comment="453: turn coordinate; NULL on pre-453 rows"
    )
    step: Mapped[Optional[int]] = mapped_column(
        Integer, comment="453: step = one LLM call + its tool executions"
    )


class AgentPermissionAudits(Base):
    __tablename__ = "agent_permission_audits"
    __table_args__ = (
        ForeignKeyConstraint(
            ["agent_id"],
            ["public.ai_agents.id"],
            ondelete="CASCADE",
            name="agent_permission_audits_agent_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="agent_permission_audits_pkey"),
        Index("idx_agent_permission_audits_agent", "agent_id", "created_at"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    changed_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    before_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    after_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class AgentRunInbox(Base):
    """453: the one queue into a running agent (harness p4 §1-③).

    Keyed by the durable target (issue / conversation), not by run — a run
    is one turn, the target outlives it. ``claimed_at IS NULL`` is the
    queue; the root run claims at each step boundary (SKIP LOCKED) and
    stamps ``claimed_run_id / claimed_turn / claimed_step``.
    """

    __tablename__ = "agent_run_inbox"
    __table_args__ = (
        CheckConstraint(
            "target_kind = ANY (ARRAY['conversation'::text, 'issue'::text])",
            name="agent_run_inbox_target_kind_check",
        ),
        CheckConstraint(
            "kind = ANY (ARRAY['steer'::text, 'answer'::text, 'pause'::text,"
            " 'resume'::text, 'budget_reply'::text,"
            " 'subagent_result'::text])",
            name="agent_run_inbox_kind_check",
        ),
        ForeignKeyConstraint(
            ["claimed_run_id"],
            ["public.agent_runs.id"],
            ondelete="SET NULL",
            name="agent_run_inbox_claimed_run_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="agent_run_inbox_pkey"),
        Index("idx_agent_run_inbox_run", "claimed_run_id"),
        Index(
            "idx_agent_run_inbox_pending",
            "target_kind",
            "target_id",
            "created_at",
            postgresql_where=text("claimed_at IS NULL AND expired_at IS NULL"),
        ),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    target_kind: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    claimed_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    claimed_run_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    claimed_turn: Mapped[Optional[int]] = mapped_column(Integer)
    claimed_step: Mapped[Optional[int]] = mapped_column(Integer)
    expired_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))


class RunDeliverables(Base):
    """453: every output an agent produces, registered through the single
    choke point ``register_deliverable()`` (harness p4 §1-④; wired in
    phase 3). Not registered = does not exist."""

    __tablename__ = "run_deliverables"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id"],
            ["public.agent_runs.id"],
            ondelete="CASCADE",
            name="run_deliverables_run_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="run_deliverables_pkey"),
        Index("idx_run_deliverables_run", "run_id"),
        Index("idx_run_deliverables_ref", "kind", "ref_id"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    seq: Mapped[Optional[int]] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    ref_id: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    parent_version: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )

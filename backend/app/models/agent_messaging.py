"""Agent messaging: inbox, outbox, commitments, approval requests."""

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


class AgentInbox(Base):
    __tablename__ = "agent_inbox"
    __table_args__ = (
        CheckConstraint(
            "message_type = ANY (ARRAY['task'::text, 'question'::text, 'notification'::text,"
            " 'approval_request'::text, 'cancel'::text, 'status_query'::text])",
            name="agent_inbox_message_type_check",
        ),
        CheckConstraint(
            "sender_kind = ANY (ARRAY['user'::text, 'agent'::text, 'system'::text, 'schedule'::text])",
            name="agent_inbox_sender_kind_check",
        ),
        CheckConstraint(
            "status = ANY (ARRAY['unread'::text, 'reading'::text, 'processed'::text,"
            " 'dismissed'::text, 'expired'::text])",
            name="agent_inbox_status_check",
        ),
        ForeignKeyConstraint(
            ["recipient_agent_id"],
            ["public.ai_agents.id"],
            ondelete="CASCADE",
            name="agent_inbox_recipient_agent_id_fkey",
        ),
        ForeignKeyConstraint(
            ["reply_to_message_id"],
            ["public.agent_inbox.id"],
            ondelete="SET NULL",
            name="agent_inbox_reply_to_message_id_fkey",
        ),
        ForeignKeyConstraint(
            ["sender_agent_id"],
            ["public.ai_agents.id"],
            ondelete="SET NULL",
            name="agent_inbox_sender_agent_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="agent_inbox_pkey"),
        Index(
            "idx_inbox_dedup",
            "recipient_agent_id",
            "dedup_key",
            postgresql_where="(dedup_key IS NOT NULL)",
            unique=True,
        ),
        Index("idx_inbox_recipient_active", "recipient_agent_id", "created_at"),
        Index(
            "idx_inbox_recipient_unread",
            "recipient_agent_id",
            "priority",
            "created_at",
            postgresql_where="(status = 'unread'::text)",
        ),
        {
            "comment": "M2 Persistent Workforce: incoming messages (user/agent/system/schedule).",
            "schema": "public",
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    recipient_agent_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    sender_kind: Mapped[str] = mapped_column(Text, nullable=False)
    message_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'unread'::text")
    )
    priority: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("5")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True),
        nullable=False,
        server_default=text("now()"),
    )
    sender_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    sender_agent_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    reading_claimed_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True)
    )
    reading_claimed_by: Mapped[Optional[str]] = mapped_column(Text)
    task_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid,
        comment="UUID 形式的 task id，对应 task_tracking.dbos_workflow_id (TEXT) — 应用层 cast 后查询。FK 在 A4 (migration 200) 移除。",
    )
    reply_to_message_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    dedup_key: Mapped[Optional[str]] = mapped_column(Text)
    processed_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    expires_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))


class AgentOutbox(Base):
    __tablename__ = "agent_outbox"
    __table_args__ = (
        CheckConstraint(
            "recipient_kind = ANY (ARRAY['user'::text, 'agent'::text, 'broadcast'::text])",
            name="agent_outbox_recipient_kind_check",
        ),
        ForeignKeyConstraint(
            ["recipient_agent_id"],
            ["public.ai_agents.id"],
            ondelete="SET NULL",
            name="agent_outbox_recipient_agent_id_fkey",
        ),
        ForeignKeyConstraint(
            ["sender_agent_id"],
            ["public.ai_agents.id"],
            ondelete="CASCADE",
            name="agent_outbox_sender_agent_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="agent_outbox_pkey"),
        Index(
            "idx_outbox_recipient_agent",
            "recipient_agent_id",
            "created_at",
            postgresql_where="(recipient_agent_id IS NOT NULL)",
        ),
        Index(
            "idx_outbox_recipient_user",
            "recipient_user_id",
            "created_at",
            postgresql_where="(recipient_user_id IS NOT NULL)",
        ),
        Index("idx_outbox_sender", "sender_agent_id", "created_at"),
        {
            "comment": "M2 Persistent Workforce: outgoing messages, Realtime delivery channel.",
            "schema": "public",
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    sender_agent_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    recipient_kind: Mapped[str] = mapped_column(Text, nullable=False)
    message_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    delivered: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True),
        nullable=False,
        server_default=text("now()"),
    )
    recipient_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    recipient_agent_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    task_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid,
        comment="UUID 形式的 task id，对应 task_tracking.dbos_workflow_id (TEXT) — 应用层 cast 后查询。FK 在 A4 (migration 200) 移除。",
    )
    delivered_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))


class AgentCommitments(Base):
    __tablename__ = "agent_commitments"
    __table_args__ = (
        CheckConstraint(
            "status = ANY (ARRAY['pending'::text, 'fulfilled'::text, 'cancelled'::text,"
            " 'expired'::text, 'failed'::text])",
            name="commitments_status_check",
        ),
        CheckConstraint(
            "trigger_type = 'time'::text AND trigger_at IS NOT NULL"
            " OR trigger_type = 'event'::text AND trigger_event IS NOT NULL"
            " OR trigger_type = 'next_session'::text",
            name="commitments_trigger_data_check",
        ),
        CheckConstraint(
            "trigger_type = ANY (ARRAY['time'::text, 'event'::text, 'next_session'::text])",
            name="commitments_trigger_type_check",
        ),
        ForeignKeyConstraint(
            ["agent_id"],
            ["public.ai_agents.id"],
            ondelete="CASCADE",
            name="agent_commitments_agent_id_fkey",
        ),
        ForeignKeyConstraint(
            ["fulfillment_run_id"],
            ["public.agent_runs.id"],
            ondelete="SET NULL",
            name="agent_commitments_fulfillment_run_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="agent_commitments_pkey"),
        Index(
            "idx_commitments_due_time",
            "trigger_at",
            postgresql_where="((status = 'pending'::text) AND (trigger_type = 'time'::text))",
        ),
        Index(
            "idx_commitments_next_session",
            "user_id",
            "agent_id",
            postgresql_where="((status = 'pending'::text) AND (trigger_type = 'next_session'::text))",
        ),
        Index(
            "idx_commitments_pending_event",
            "trigger_event",
            postgresql_where="((status = 'pending'::text) AND (trigger_type = 'event'::text))",
        ),
        Index(
            "idx_commitments_user_status",
            "user_id",
            "status",
            "created_at",
            postgresql_where="(user_id IS NOT NULL)",
        ),
        {
            "comment": (
                "Cross-session followups (Sprint 4). Distinct from agent_memories "
                "(facts) — these are promises owed."
            ),
            "schema": "public",
        },
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    trigger_type: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment=(
            "time = fire at trigger_at; event = fire on trigger_event publish;"
            " next_session = fire on next session open."
        ),
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'pending'::text")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True),
        nullable=False,
        server_default=text("now()"),
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    trigger_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    trigger_event: Mapped[Optional[str]] = mapped_column(Text)
    expires_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True),
        comment="Auto-cancel after this. NULL = no auto-expire.",
    )
    fulfilled_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    fulfillment_notes: Mapped[Optional[str]] = mapped_column(Text)
    fulfillment_run_id: Mapped[Optional[int]] = mapped_column(BigInteger)


class AgentApprovalRequests(Base):
    __tablename__ = "agent_approval_requests"
    __table_args__ = (
        CheckConstraint(
            "status = ANY (ARRAY['pending'::text, 'approved'::text, 'rejected'::text,"
            " 'expired'::text, 'cancelled'::text])",
            name="agent_approval_requests_status_check",
        ),
        PrimaryKeyConstraint("id", name="agent_approval_requests_pkey"),
        UniqueConstraint("run_id", "status", name="one_open_per_run"),
        Index(
            "idx_approval_requests_expires",
            "expires_at",
            postgresql_where="(status = 'pending'::text)",
        ),
        Index(
            "idx_approval_requests_run",
            "run_id",
            postgresql_where="(run_id IS NOT NULL)",
        ),
        Index(
            "idx_approval_requests_user_pending",
            "user_id",
            "created_at",
            postgresql_where="(status = 'pending'::text)",
        ),
        {
            "comment": (
                "G1: gating rows for hook decisions of type await_approval. The "
                "agent run pauses; the frontend renders pending rows; the user "
                "approves/rejects; a wake-up path resumes the workflow with the "
                "decision. Sweeper marks rows expired after expires_at."
            ),
            "schema": "public",
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    agent_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    hook_name: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'pending'::text")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True),
        nullable=False,
        server_default=text("now()"),
    )
    expires_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True),
        nullable=False,
        server_default=text("(now() + '24:00:00'::interval)"),
    )
    session_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    run_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    decided_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    decided_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    decision_note: Mapped[Optional[str]] = mapped_column(Text)

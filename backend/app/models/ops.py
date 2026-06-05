"""Operational models: logs, API keys, system settings, task tracking, etc."""

from __future__ import annotations

import datetime
import uuid
from typing import Any, Optional

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.orm_base import Base
from app.models._enums import ApiKeyStatus


class ApplicationLogs(Base):
    __tablename__ = "application_logs"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="application_logs_pkey"),
        Index("idx_application_logs_level", "level"),
        Index("idx_application_logs_logged_at", "logged_at"),
        Index("idx_application_logs_module", "module"),
        Index("idx_application_logs_module_logged_at", "module", "logged_at"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    level: Mapped[str] = mapped_column(String(10), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    logged_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    module: Mapped[Optional[str]] = mapped_column(String(255))
    function: Mapped[Optional[str]] = mapped_column(String(255))
    line: Mapped[Optional[int]] = mapped_column(Integer)
    file_path: Mapped[Optional[str]] = mapped_column(String(500))
    exception: Mapped[Optional[str]] = mapped_column(Text)
    extra: Mapped[Optional[dict]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )


class ApiRequestLogs(Base):
    __tablename__ = "api_request_logs"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="api_request_logs_pkey"),
        Index("idx_api_request_logs_method", "method"),
        Index("idx_api_request_logs_timestamp", "timestamp"),
        Index("idx_api_request_logs_user_id", "user_id"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    request_id: Mapped[str] = mapped_column(String(36), nullable=False)
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    timestamp: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    auth_type: Mapped[Optional[str]] = mapped_column(
        String(20), server_default=text("'anonymous'::character varying")
    )
    query_params: Mapped[Optional[dict]] = mapped_column(JSONB)
    request_body: Mapped[Optional[dict]] = mapped_column(JSONB)
    status_code: Mapped[Optional[int]] = mapped_column(Integer)
    response_time_ms: Mapped[Optional[int]] = mapped_column(Integer)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45))
    user_agent: Mapped[Optional[str]] = mapped_column(Text)
    error_detail: Mapped[Optional[str]] = mapped_column(Text)


class ApiKeys(Base):
    __tablename__ = "api_keys"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="api_keys_pkey"),
        UniqueConstraint("key_id", name="api_keys_key_id_key"),
        Index("idx_api_keys_key_hash", "key_hash"),
        Index("idx_api_keys_key_id", "key_id"),
        Index("idx_api_keys_user_id", "user_id"),
        {"comment": "API 密钥管理表", "schema": "public"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    key_id: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="密钥公开标识符"
    )
    key_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="密钥 SHA-256 哈希值"
    )
    key_prefix: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="密钥前缀，用于用户识别"
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    scopes: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
        comment="权限范围 JSON 数组",
    )
    status: Mapped[ApiKeyStatus] = mapped_column(
        Enum(
            ApiKeyStatus,
            values_callable=lambda cls: [member.value for member in cls],
            name="api_key_status",
        ),
        nullable=False,
        server_default=text("'active'::api_key_status"),
    )
    description: Mapped[Optional[str]] = mapped_column(Text)
    expires_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    last_used_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    usage_count: Mapped[Optional[int]] = mapped_column(
        Integer, server_default=text("0")
    )
    rate_limit: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=text("now()")
    )
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=text("now()")
    )
    key_value: Mapped[Optional[str]] = mapped_column(String)


class ApiKeyLogs(Base):
    __tablename__ = "api_key_logs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["api_key_id"],
            ["public.api_keys.id"],
            ondelete="CASCADE",
            name="api_key_logs_api_key_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="api_key_logs_pkey"),
        Index("idx_api_key_logs_api_key_id", "api_key_id"),
        {"comment": "API 密钥使用日志表", "schema": "public"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    api_key_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    endpoint: Mapped[str] = mapped_column(String(255), nullable=False)
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    ip_address: Mapped[Optional[Any]] = mapped_column(INET)
    user_agent: Mapped[Optional[str]] = mapped_column(Text)
    status_code: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=text("now()")
    )


class AuditLogs(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="audit_logs_pkey"),
        Index("idx_audit_logs_action", "action"),
        Index("idx_audit_logs_admin_id", "admin_id"),
        Index("idx_audit_logs_created_at", "created_at"),
        Index("idx_audit_logs_target_type", "target_type"),
        {"comment": "Audit trail for admin actions", "schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    admin_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, nullable=False, comment="Admin who performed the action"
    )
    action: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="Action performed (e.g., ban_user, adjust_credits)",
    )
    target_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="Type of target entity (e.g., user, video, setting)",
    )
    target_id: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="ID of the target entity"
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    details: Mapped[Optional[dict]] = mapped_column(
        JSONB, comment="Additional details about the action"
    )
    ip_address: Mapped[Optional[str]] = mapped_column(
        String(45), comment="IP address of the admin"
    )


class FrontendErrorLogs(Base):
    __tablename__ = "frontend_error_logs"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="frontend_error_logs_pkey"),
        Index("idx_frontend_error_logs_created_at", "created_at"),
        Index("idx_frontend_error_logs_user_id", "user_id"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    error_type: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    session_id: Mapped[Optional[str]] = mapped_column(String(100))
    message: Mapped[Optional[str]] = mapped_column(Text)
    stack: Mapped[Optional[str]] = mapped_column(Text)
    url: Mapped[Optional[str]] = mapped_column(String(1000))
    component: Mapped[Optional[str]] = mapped_column(String(255))
    user_agent: Mapped[Optional[str]] = mapped_column(Text)
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSONB)


class DeploymentLogs(Base):
    __tablename__ = "deployment_logs"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="deployment_logs_pkey"),
        Index("idx_deployment_logs_service_deployed_at", "service", "deployed_at"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text(
            "(((EXTRACT(epoch FROM now()) * (1000000)::numeric))::bigint"
            " + ((random() * (1000)::double precision))::bigint)"
        ),
    )
    service: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[Optional[str]] = mapped_column(Text)
    commit_sha: Mapped[Optional[str]] = mapped_column(Text)
    commit_count: Mapped[Optional[int]] = mapped_column(
        Integer, server_default=text("0")
    )
    commits: Mapped[Optional[dict]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    summary: Mapped[Optional[str]] = mapped_column(Text)
    deployed_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=text("now()")
    )
    deployed_by: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[Optional[str]] = mapped_column(
        Text, server_default=text("'success'::text")
    )
    metadata_: Mapped[Optional[dict]] = mapped_column(
        "metadata", JSONB, server_default=text("'{}'::jsonb")
    )
    release_notes: Mapped[Optional[str]] = mapped_column(Text)
    published_by: Mapped[Optional[str]] = mapped_column(
        Text, server_default=text("'auto'::text")
    )


class SystemSettings(Base):
    __tablename__ = "system_settings"
    __table_args__ = (
        PrimaryKeyConstraint("key", name="system_settings_pkey"),
        Index("idx_system_settings_updated_by", "updated_by"),
        {"comment": "Global system configuration settings", "schema": "public"},
    )

    key: Mapped[str] = mapped_column(
        String(100), primary_key=True, comment="Setting key identifier"
    )
    value: Mapped[dict] = mapped_column(
        JSONB, nullable=False, comment="Setting value in JSON format"
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    description: Mapped[Optional[str]] = mapped_column(Text)
    updated_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, comment="Admin who last updated this setting"
    )
    category: Mapped[Optional[str]] = mapped_column(
        String(50), server_default=text("'general'::character varying")
    )
    input_type: Mapped[Optional[str]] = mapped_column(
        String(30), server_default=text("'text'::character varying")
    )
    options: Mapped[Optional[dict]] = mapped_column(JSONB)


class SystemStatus(Base):
    __tablename__ = "system_status"
    __table_args__ = (
        CheckConstraint(
            "id = '00000000-0000-0000-0000-000000000001'::uuid",
            name="system_status_id_check",
        ),
        PrimaryKeyConstraint("id", name="system_status_pkey"),
        {
            "comment": (
                "DEPRECATED 2026-05-04 (A-route A2). Snapshot moved to Redis HASH\n"
                "     mediahub:system:status (see app/services/system_status_redis.py).\n"
                "     Table no longer receives writes. Will be dropped in a follow-up\n"
                "     migration after the 30-day deprecation window. Read from /api/v1/\n"
                "     system/status (Redis-backed) instead."
            ),
            "schema": "public",
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        server_default=text("'00000000-0000-0000-0000-000000000001'::uuid"),
    )
    queue: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    workers: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    storage: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    network: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    active_tasks: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=text("now()")
    )


class TaskTracking(Base):
    __tablename__ = "task_tracking"
    __table_args__ = (
        CheckConstraint(
            "progress >= 0 AND progress <= 100", name="unified_tasks_progress_check"
        ),
        CheckConstraint(
            "task_kind = ANY (ARRAY['workflow'::text, 'agent_task'::text])",
            name="task_tracking_task_kind_check",
        ),
        ForeignKeyConstraint(
            ["agent_id"],
            ["public.ai_agents.id"],
            ondelete="SET NULL",
            name="task_tracking_agent_id_fkey",
        ),
        ForeignKeyConstraint(
            ["flow_id"],
            ["public.task_flows.id"],
            ondelete="SET NULL",
            name="task_tracking_flow_id_fkey",
        ),
        ForeignKeyConstraint(
            ["inbox_message_id"],
            ["public.agent_inbox.id"],
            ondelete="SET NULL",
            name="task_tracking_inbox_fk",
        ),
        ForeignKeyConstraint(
            ["parent_task_id"],
            ["public.task_tracking.dbos_workflow_id"],
            ondelete="SET NULL",
            deferrable=True,
            initially="DEFERRED",
            name="task_tracking_parent_fk",
        ),
        ForeignKeyConstraint(
            ["root_task_id"],
            ["public.task_tracking.dbos_workflow_id"],
            ondelete="SET NULL",
            deferrable=True,
            initially="DEFERRED",
            name="task_tracking_root_fk",
        ),
        PrimaryKeyConstraint("dbos_workflow_id", name="task_tracking_pkey"),
        Index(
            "idx_task_tracking_active_heartbeat",
            "heartbeat_at",
            postgresql_where=(
                "(phase = ANY (ARRAY['queued'::text, 'in_progress'::text]))"
            ),
        ),
        Index(
            "idx_task_tracking_active_per_resource_type",
            "resource_id",
            "task_type",
            postgresql_where=(
                "((status)::text = ANY (ARRAY[('pending'::character varying)::text,"
                " ('queued'::character varying)::text,"
                " ('processing'::character varying)::text,"
                " ('running'::character varying)::text]))"
            ),
            unique=True,
        ),
        Index(
            "idx_task_tracking_agent",
            "agent_id",
            "created_at",
            postgresql_where="(agent_id IS NOT NULL)",
        ),
        Index(
            "idx_task_tracking_agent_active",
            "agent_id",
            "created_at",
            postgresql_where=(
                "((task_kind = 'agent_task'::text) AND ((status)::text = ANY"
                " (ARRAY[('pending'::character varying)::text,"
                " ('processing'::character varying)::text])))"
            ),
        ),
        Index(
            "idx_task_tracking_dedup_key",
            "dedup_key",
            postgresql_where="(dedup_key IS NOT NULL)",
        ),
        Index(
            "idx_task_tracking_flow_id",
            "flow_id",
            postgresql_where="(flow_id IS NOT NULL)",
        ),
        Index(
            "idx_task_tracking_parent",
            "parent_task_id",
            postgresql_where="(parent_task_id IS NOT NULL)",
        ),
        Index(
            "idx_task_tracking_phase",
            "phase",
            postgresql_where=(
                "(phase = ANY (ARRAY['queued'::text, 'dedup_check'::text,"
                " 'processing'::text]))"
            ),
        ),
        Index(
            "idx_task_tracking_root",
            "root_task_id",
            postgresql_where="(root_task_id IS NOT NULL)",
        ),
        Index("idx_task_tracking_task_kind", "task_kind"),
        Index("idx_task_tracking_type", "task_type"),
        Index(
            "idx_task_tracking_user_active",
            "user_id",
            "created_at",
            postgresql_where=(
                "((status)::text = ANY (ARRAY[('pending'::character varying)::text,"
                " ('processing'::character varying)::text]))"
            ),
        ),
        Index("idx_task_tracking_user_status", "user_id", "status"),
        Index(
            "task_tracking_issue_id_idx",
            "issue_id",
            postgresql_where="(issue_id IS NOT NULL)",
        ),
        {
            "comment": (
                "通用任务追踪表（multi-task_kind）。\n"
                "   task_kind='workflow'   → DBOS workflow，1:1 mirror dbos.workflow_status，\n"
                "                              status 由 mirror_dbos_lifecycle_to_tracking trigger 写\n"
                "   task_kind='agent_task' → 应用层（agent_workforce）维护 lifecycle，\n"
                "                              不依赖 dbos.workflow_status；\n"
                "                              phase 列保留 8 状态精度 (queued / assigned /\n"
                "                              in_progress / waiting_for_other / blocked /\n"
                "                              done / failed / cancelled)\n"
                "   原 1:1 sidecar FK (task_tracking_dbos_fk) 在 A4 (migration 200) 移除，\n"
                "   cascade GC 失效 — 由应用层负责清理过期任务。"
            ),
            "schema": "public",
        },
    )

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    task_type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'pending'::character varying"),
        comment=(
            "Mirror of DBOS lifecycle (5-state). Updated by trigger ONLY — never\n"
            "   write directly. Source of truth: dbos.workflow_status.status."
        ),
    )
    dbos_workflow_id: Mapped[str] = mapped_column(
        Text,
        primary_key=True,
        comment="PK; equals dbos.workflow_status.workflow_uuid (FK CASCADE).",
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    cost_cents: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
        comment=(
            "Aggregated AI cost in cents for tasks that triggered AI workflows. Updated\n"
            "   by application code reading agent_runs at task completion time."
        ),
    )
    do_not_auto_cancel: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
        comment=(
            "Opt out of any sweeper-driven action. Even ORPHAN_PENDING and LOST\n"
            "     classifications only log; never flip status. User must cancel manually."
        ),
    )
    task_kind: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'workflow'::text"),
        comment="workflow | agent_task — 决定 status 列的写入权（trigger vs 应用层）",
    )
    resource_id: Mapped[Optional[str]] = mapped_column(Text)
    media_id: Mapped[Optional[str]] = mapped_column(Text)
    progress: Mapped[Optional[int]] = mapped_column(
        SmallInteger, server_default=text("0")
    )
    speed: Mapped[Optional[int]] = mapped_column(BigInteger)
    total_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    subtitle: Mapped[Optional[str]] = mapped_column(Text)
    error_msg: Mapped[Optional[str]] = mapped_column(Text)
    metadata_: Mapped[Optional[dict]] = mapped_column(
        "metadata", JSONB, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=text("now()")
    )
    started_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    completed_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=text("now()")
    )
    group_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, comment="Groups related sub-tasks (e.g. 3 AI steps for same media)"
    )
    phase: Mapped[Optional[str]] = mapped_column(
        Text,
        server_default=text("'queued'::text"),
        comment=(
            "Free-form business phase (e.g. parsing/downloading/waiting_for_human_review).\n"
            "   Application code is the truth source for this column. DBOS lifecycle stays\n"
            "   in `status` column; phase is finer-grained business detail."
        ),
    )
    dedup_key: Mapped[Optional[str]] = mapped_column(Text)
    subscribers: Mapped[Optional[dict]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    error_code: Mapped[Optional[str]] = mapped_column(Text)
    issue_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        comment=(
            "Back-reference to issues.id. NULL for legacy rows (pre-PR-D1) and "
            "system-only tasks (scheduled cleanup, etc.). User-facing tasks set this on creation."
        ),
    )
    heartbeat_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True),
        comment=(
            "Set by the workflow body itself every ~30s. NULL = workflow has not\n"
            "     started writing heartbeats yet. Stale = worker died (LOST)."
        ),
    )
    health_status: Mapped[Optional[str]] = mapped_column(
        Text,
        comment=(
            "Last classification by workflow_health_sweeper.\n"
            "     One of: HEALTHY / SLOW / STUCK_IN_STEP / STALLED / LOST /"
            " ORPHAN_PENDING / NULL."
        ),
    )
    health_notified_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True),
        comment=(
            'Last time we surfaced a "running long" notification to the user.\n'
            "     Used to dedupe — we notify once per classification flip."
        ),
    )
    max_duration_minutes: Mapped[Optional[int]] = mapped_column(
        Integer,
        comment=(
            "User-set hard cap. NULL = use workflow_timeout_policy.hard_ceiling.\n"
            "     The sweeper RESPECTS this even when do_not_auto_cancel=false:\n"
            "     surpassing max_duration_minutes flips status=timed_out (which IS\n"
            "     auto-action; user opted into it by setting the field)."
        ),
    )
    expected_duration_minutes: Mapped[Optional[int]] = mapped_column(
        Integer,
        comment=(
            'User-declared "this is supposed to be slow". Above this we DO NOT\n'
            '     emit "running long" notifications. Below max_duration_minutes still\n'
            "     auto-times-out."
        ),
    )
    flow_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid,
        comment=(
            "Parent flow this task belongs to. NULL for unflow'd tasks (legacy\n"
            "     ad-hoc dispatches still allowed). FK ON DELETE SET NULL so deleting\n"
            "     a flow doesn't cascade-delete child tasks (keep them for history)."
        ),
    )
    agent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid,
        comment="task_kind=agent_task 时填，引用 ai_agents.id；workflow 时为 NULL",
    )
    parent_task_id: Mapped[Optional[str]] = mapped_column(
        Text,
        comment=(
            "父任务的 dbos_workflow_id (TEXT)，跨 task_kind 通用；DBOS 自身 parent_workflow_id"
            " 仍存在 dbos.workflow_status，本列是应用层快查路径"
        ),
    )
    root_task_id: Mapped[Optional[str]] = mapped_column(
        Text,
        comment="根任务的 dbos_workflow_id (TEXT)，跨 task_kind 通用",
    )
    inbox_message_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid,
        comment="task_kind=agent_task 时填，引用 agent_inbox.id（触发该 task 的入站消息）",
    )


class AccessOverrides(Base):
    __tablename__ = "access_overrides"
    __table_args__ = (
        CheckConstraint(
            "object_type::text = ANY (ARRAY['library'::character varying::text,"
            " 'folder'::character varying::text,"
            " 'project'::character varying::text])",
            name="access_overrides_object_type_check",
        ),
        CheckConstraint(
            "role::text = ANY (ARRAY['admin'::character varying::text,"
            " 'editor'::character varying::text,"
            " 'viewer'::character varying::text,"
            " 'none'::character varying::text])",
            name="access_overrides_role_check",
        ),
        PrimaryKeyConstraint("id", name="access_overrides_pkey"),
        UniqueConstraint(
            "object_type",
            "object_id",
            "user_id",
            name="access_overrides_object_type_object_id_user_id_key",
        ),
        Index("idx_access_overrides_granted_by", "granted_by"),
        Index("idx_access_overrides_object", "object_type", "object_id"),
        Index("idx_access_overrides_user_id", "user_id"),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    object_type: Mapped[str] = mapped_column(String(20), nullable=False)
    object_id: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    granted_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)


class AdminTablePreferences(Base):
    __tablename__ = "admin_table_preferences"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="admin_table_preferences_pkey"),
        UniqueConstraint(
            "user_id",
            "table_key",
            name="admin_table_preferences_user_id_table_key_key",
        ),
        Index("idx_admin_table_prefs_user_table", "user_id", "table_key"),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    table_key: Mapped[str] = mapped_column(Text, nullable=False)
    filters: Mapped[Optional[dict]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    sorts: Mapped[Optional[dict]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    visible_columns: Mapped[Optional[list[str]]] = mapped_column(ARRAY(Text()))
    column_order: Mapped[Optional[list[str]]] = mapped_column(ARRAY(Text()))
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=text("now()")
    )
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=text("now()")
    )


class BoundaryAudit(Base):
    __tablename__ = "boundary_audit"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="boundary_audit_pkey"),
        Index("boundary_audit_blocked_at_idx", "blocked_at"),
        Index("boundary_audit_layer_reason_idx", "layer", "reason"),
        Index(
            "boundary_audit_user_id_idx",
            "user_id",
            postgresql_where="(user_id IS NOT NULL)",
        ),
        {
            "comment": (
                "Boundary layer block audit log. Server-side only; raw_url never echoed "
                "to client. Sweeper auto-prunes >90d (B9-G follow-up)."
            ),
            "schema": "public",
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    blocked_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    layer: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    raw_url: Mapped[Optional[str]] = mapped_column(Text)
    resolved_ip: Mapped[Optional[str]] = mapped_column(Text)
    user_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    request_id: Mapped[Optional[str]] = mapped_column(Text)
    metadata_json: Mapped[Optional[dict]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )

"""Script & workflow models: script projects/assets/chapters, workflows, task flows."""

from __future__ import annotations

import datetime
import uuid
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Double,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.orm_base import Base


class TaskFlows(Base):
    __tablename__ = "task_flows"
    __table_args__ = (
        CheckConstraint(
            "state = ANY (ARRAY['running'::text, 'completed'::text, 'failed'::text,"
            " 'cancelled'::text, 'partial'::text])",
            name="task_flows_state_check",
        ),
        PrimaryKeyConstraint("id", name="task_flows_pkey"),
        Index("idx_task_flows_created", "created_at"),
        Index("idx_task_flows_user_state", "user_id", "state"),
        {
            "comment": (
                "Parent grouping for related tasks. One flow = one user submission\n"
                '     (e.g., "Process URL X"). Child tasks reference via task_tracking.flow_id.\n'
                "     Aggregate counters maintained by trigger trg_task_tracking_flow_aggregate."
            ),
            "schema": "public",
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'running'::text"),
        comment=(
            "running / completed (all children done OK) / failed (any child failed\n"
            "     and cascade_cancel=true) / cancelled (user cancelled) / partial\n"
            "     (mixed: some completed, some cancelled — terminal but not pure success)"
        ),
    )
    cascade_cancel: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
        comment=(
            "When true, cancelling the flow cancels every non-terminal child task\n"
            "     via the lifecycle bus. When false, cancel marks the flow alone."
        ),
    )
    total_tasks: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    completed_tasks: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    failed_tasks: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    cancelled_tasks: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
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
    metadata_: Mapped[Optional[dict]] = mapped_column(
        "metadata", JSONB, server_default=text("'{}'::jsonb")
    )
    completed_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))


class WorkflowTimeoutPolicy(Base):
    __tablename__ = "workflow_timeout_policy"
    __table_args__ = (
        CheckConstraint(
            "hard_ceiling_seconds >= expected_duration_seconds",
            name="workflow_timeout_policy_check",
        ),
        PrimaryKeyConstraint("task_type", name="workflow_timeout_policy_pkey"),
        {
            "comment": (
                "Per-task-type duration policy used by the workflow health classifier.\n"
                '     expected_duration_seconds = when we tell the user "running long".\n'
                "     hard_ceiling_seconds = upper bound for ORPHAN_PENDING detection.\n"
                "     heartbeat_stale_seconds = how long without heartbeat = LOST."
            ),
            "schema": "public",
        },
    )

    task_type: Mapped[str] = mapped_column(Text, primary_key=True)
    expected_duration_seconds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment='Beyond this, user gets a "running long" notification (no auto-action).',
    )
    hard_ceiling_seconds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment=(
            "A workflow stuck PENDING (never picked up by executor) for hard_ceiling × 3 = "
            "ORPHAN_PENDING. Safe to auto-cancel because the body never ran."
        ),
    )
    heartbeat_stale_seconds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("300"),
        comment="Heartbeat older than this with status=RUNNING ⇒ LOST (worker died).",
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
    description: Mapped[Optional[str]] = mapped_column(Text)


class ProjectWorkflows(Base):
    __tablename__ = "project_workflows"
    __table_args__ = (
        ForeignKeyConstraint(
            ["team_id"],
            ["public.teams.id"],
            ondelete="CASCADE",
            name="project_workflows_team_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="project_workflows_pkey"),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    team_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True),
        nullable=False,
        server_default=text("now()"),
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )


class WorkflowNodes(Base):
    __tablename__ = "workflow_nodes"
    __table_args__ = (
        CheckConstraint(
            "node_type::text = ANY (ARRAY['status'::character varying::text,"
            " 'milestone'::character varying::text, 'gate'::character varying::text])",
            name="workflow_nodes_node_type_check",
        ),
        CheckConstraint(
            "status_type::text = ANY (ARRAY['not_started'::character varying::text,"
            " 'in_progress'::character varying::text, 'completed'::character varying::text])",
            name="workflow_nodes_status_type_check",
        ),
        ForeignKeyConstraint(
            ["workflow_id"],
            ["public.project_workflows.id"],
            ondelete="CASCADE",
            name="workflow_nodes_workflow_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="workflow_nodes_pkey"),
        Index("idx_workflow_nodes_workflow_id", "workflow_id"),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    status_type: Mapped[str] = mapped_column(String(20), nullable=False)
    node_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        server_default=text("'status'::character varying"),
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True),
        nullable=False,
        server_default=text("now()"),
    )
    color: Mapped[Optional[str]] = mapped_column(String(20))


class ScriptProjects(Base):
    __tablename__ = "script_projects"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id"],
            ["public.projects.id"],
            name="script_projects_project_id_fkey",
        ),
        ForeignKeyConstraint(
            ["team_id"],
            ["public.teams.id"],
            name="script_projects_team_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="script_projects_pkey"),
        Index("idx_script_projects_created_by", "created_by"),
        Index("idx_script_projects_project_id", "project_id"),
        Index("idx_script_projects_team_id", "team_id"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    team_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    display_code: Mapped[Optional[str]] = mapped_column(Text)
    description: Mapped[Optional[str]] = mapped_column(Text)
    settings_json: Mapped[Optional[dict]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    viewport_json: Mapped[Optional[dict]] = mapped_column(JSONB)
    status: Mapped[Optional[str]] = mapped_column(
        Text, server_default=text("'active'::text")
    )
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True),
        server_default=text("now()"),
    )
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True),
        server_default=text("now()"),
    )
    genre: Mapped[Optional[str]] = mapped_column(
        String(50), comment="Story genre/style"
    )


class ScriptAssets(Base):
    __tablename__ = "script_assets"
    __table_args__ = (
        ForeignKeyConstraint(
            ["script_id"],
            ["public.script_projects.id"],
            ondelete="CASCADE",
            name="script_assets_script_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="script_assets_pkey"),
        Index("idx_script_assets_script_id", "script_id"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    script_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    asset_type: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[Optional[str]] = mapped_column(Text)
    data_json: Mapped[Optional[dict]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    sort_order: Mapped[Optional[int]] = mapped_column(Integer, server_default=text("0"))
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True),
        server_default=text("now()"),
    )
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True),
        server_default=text("now()"),
    )


class ScriptChapters(Base):
    __tablename__ = "script_chapters"
    __table_args__ = (
        ForeignKeyConstraint(
            ["parent_chapter_id"],
            ["public.script_chapters.id"],
            name="script_chapters_parent_chapter_id_fkey",
        ),
        ForeignKeyConstraint(
            ["script_id"],
            ["public.script_projects.id"],
            ondelete="CASCADE",
            name="script_chapters_script_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="script_chapters_pkey"),
        Index("idx_script_chapters_parent_chapter_id", "parent_chapter_id"),
        Index("idx_script_chapters_script_id", "script_id"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    script_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    parent_chapter_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    chapter_number: Mapped[Optional[int]] = mapped_column(Integer)
    title: Mapped[Optional[str]] = mapped_column(Text)
    summary: Mapped[Optional[str]] = mapped_column(Text)
    content: Mapped[Optional[str]] = mapped_column(
        Text,
        comment="Plain text derived from content_json (for search and AI)",
    )
    branch_label: Mapped[Optional[str]] = mapped_column(Text)
    branch_type: Mapped[Optional[str]] = mapped_column(Text)
    position_x: Mapped[Optional[float]] = mapped_column(
        Double(53), server_default=text("0")
    )
    position_y: Mapped[Optional[float]] = mapped_column(
        Double(53), server_default=text("0")
    )
    width: Mapped[Optional[float]] = mapped_column(Double(53))
    height: Mapped[Optional[float]] = mapped_column(Double(53))
    data_json: Mapped[Optional[dict]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    sort_order: Mapped[Optional[int]] = mapped_column(Integer, server_default=text("0"))
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True),
        server_default=text("now()"),
    )
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True),
        server_default=text("now()"),
    )
    content_json: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        comment="TipTap ProseMirror JSON document (source of truth)",
    )


class ScriptStoryboardLinks(Base):
    __tablename__ = "script_storyboard_links"
    __table_args__ = (
        ForeignKeyConstraint(
            ["chapter_id"],
            ["public.script_chapters.id"],
            ondelete="CASCADE",
            name="script_storyboard_links_chapter_id_fkey",
        ),
        ForeignKeyConstraint(
            ["storyboard_node_id"],
            ["public.storyboard_nodes.id"],
            ondelete="SET NULL",
            name="script_storyboard_links_storyboard_node_id_fkey",
        ),
        ForeignKeyConstraint(
            ["storyboard_project_id"],
            ["public.storyboard_projects.id"],
            ondelete="CASCADE",
            name="script_storyboard_links_storyboard_project_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="script_storyboard_links_pkey"),
        Index("idx_script_storyboard_links_chapter_id", "chapter_id"),
        Index("idx_script_storyboard_links_storyboard_node_id", "storyboard_node_id"),
        Index(
            "idx_script_storyboard_links_storyboard_project_id", "storyboard_project_id"
        ),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    chapter_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storyboard_project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storyboard_node_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True),
        server_default=text("now()"),
    )

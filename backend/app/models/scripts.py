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
            ["episode_id"],
            ["public.episodes.id"],
            ondelete="RESTRICT",
            name="script_projects_episode_id_fkey",
        ),
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
    episode_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    target_duration_sec: Mapped[Optional[int]] = mapped_column(
        Integer, comment="Beats timeline target total runtime (sec); NULL=unset"
    )
    numbering_locked_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True),
        comment="NULL=writing phase (scene numbers derived, not stored). Set "
        "once at lock time to freeze every scene_number under this script — "
        "a one-way transition (mig 403).",
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


class Episodes(Base):
    """(mig 338) ``current_node_id`` (mig 402, B1) is the per-episode
    workflow cursor -- the design doc's recommended home, replacing the
    project-level ``projects.current_node_id`` (mig 380). The episode-level
    column has ``EpisodeRepository.set_current_node_id`` as its write accessor;
    the project-level column's write entry point was deleted in Task 7 (B6)
    and is now read-only (preserved for historical data and union guards)."""

    __tablename__ = "episodes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id"],
            ["public.projects.id"],
            ondelete="CASCADE",
            name="episodes_project_id_fkey",
        ),
        ForeignKeyConstraint(
            ["current_node_id"],
            ["public.project_stage_nodes.id"],
            ondelete="SET NULL",
            name="episodes_current_node_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="episodes_pkey"),
        Index("idx_episodes_project", "project_id", "sort_order"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    title: Mapped[str] = mapped_column(
        String(200), nullable=False, server_default=text("'Ep 1'::character varying")
    )
    sort_order: Mapped[int] = mapped_column(
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
    # Per-episode workflow cursor (mig 402, B1) -- FK fixes the gap mig 380
    # left on ``projects.current_node_id`` (no FK there at all -- a deleted
    # node left a dangling cursor id that ``advance_service._active_index``
    # silently read as "nothing found -> group 0"), rather than repeating it
    # on this new column.
    current_node_id: Mapped[int | None] = mapped_column(BigInteger)


class ScriptScenes(Base):
    __tablename__ = "script_scenes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["chapter_id"],
            ["public.script_chapters.id"],
            ondelete="SET NULL",
            name="script_scenes_chapter_id_fkey",
        ),
        ForeignKeyConstraint(
            ["script_id"],
            ["public.script_projects.id"],
            ondelete="CASCADE",
            name="script_scenes_script_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="script_scenes_pkey"),
        Index("idx_script_scenes_script", "script_id", "chapter_id", "sort_order"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    script_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    chapter_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    heading_int_ext: Mapped[Optional[str]] = mapped_column(String(10))
    location_text: Mapped[Optional[str]] = mapped_column(Text)
    location_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, comment="Entity soft reference, no FK (spec §2.4)"
    )
    time_of_day: Mapped[Optional[str]] = mapped_column(String(20))
    content_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    content: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''::text")
    )
    content_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    position_x: Mapped[Optional[float]] = mapped_column(Double(53))
    position_y: Mapped[Optional[float]] = mapped_column(Double(53))
    width: Mapped[Optional[float]] = mapped_column(Double(53))
    height: Mapped[Optional[float]] = mapped_column(Double(53))
    sort_order: Mapped[int] = mapped_column(
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
    scene_number: Mapped[Optional[str]] = mapped_column(
        Text,
        comment="NULL pre-lock (derived, not stored). Post-lock: authoritative, "
        "never-renumbered display number — plain int string or int+letter "
        "suffix for a post-lock insert (mig 403).",
    )
    omitted_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True),
        comment="Set instead of hard-delete once numbering is locked (printed-"
        "script 'OMITTED' convention) — row + scene_number kept (mig 403).",
    )


class ScriptShots(Base):
    __tablename__ = "script_shots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["created_by_agent_run_id"],
            ["public.agent_runs.id"],
            ondelete="SET NULL",
            name="script_shots_created_by_agent_run_id_fkey",
        ),
        ForeignKeyConstraint(
            ["scene_id"],
            ["public.script_scenes.id"],
            ondelete="CASCADE",
            name="script_shots_scene_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="script_shots_pkey"),
        Index("idx_script_shots_scene", "scene_id", "sort_order"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    scene_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    shot_number: Mapped[Optional[int]] = mapped_column(Integer)
    shot_type: Mapped[Optional[str]] = mapped_column(String(20))
    camera_angle: Mapped[Optional[str]] = mapped_column(String(20))
    camera_movement: Mapped[Optional[str]] = mapped_column(String(20))
    focal_length: Mapped[Optional[str]] = mapped_column(String(20))
    lighting: Mapped[Optional[str]] = mapped_column(Text)
    description: Mapped[Optional[str]] = mapped_column(Text)
    image_url: Mapped[Optional[str]] = mapped_column(Text)
    thumbnail_url: Mapped[Optional[str]] = mapped_column(Text)
    video_url: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'empty'::character varying")
    )
    sort_order: Mapped[int] = mapped_column(
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
    created_by_agent_run_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        comment="mig 413: agent CreateShot 归属；人写 / Auto-Storyboard 为 NULL",
    )


class ScriptBeats(Base):
    __tablename__ = "script_beats"
    __table_args__ = (
        ForeignKeyConstraint(
            ["script_id"],
            ["public.script_projects.id"],
            ondelete="CASCADE",
            name="script_beats_script_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="script_beats_pkey"),
        Index("idx_script_beats_script", "script_id", "sort_order"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    script_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(Text)
    scene_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    # Arrangement columns (mig 372). All NULLable — NULL start_sec = "not yet
    # arranged" (classic list-mode beat), NULL beat_role = free-form beat.
    start_sec: Mapped[Optional[int]] = mapped_column(Integer)
    duration_sec: Mapped[Optional[int]] = mapped_column(Integer)
    beat_role: Mapped[Optional[str]] = mapped_column(String(40))
    color: Mapped[Optional[str]] = mapped_column(String(20))
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


class BeatTemplates(Base):
    __tablename__ = "beat_templates"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="beat_templates_pkey"),
        Index("idx_beat_templates_user_created", "user_id", "created_at"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    # Owner (auth.users.id). No DB FK — auth.users lives outside the app schema
    # (same convention as inbox_notifications; the FK gate exempts auth.users).
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # Ordered percentage anchors: [{title, summary, pctStart, pctEnd, color}].
    anchors: Mapped[list] = mapped_column(JSONB, nullable=False)
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


class BeatMemos(Base):
    __tablename__ = "beat_memos"
    __table_args__ = (
        ForeignKeyConstraint(
            ["script_id"],
            ["public.script_projects.id"],
            ondelete="CASCADE",
            name="beat_memos_script_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="beat_memos_pkey"),
        Index("idx_beat_memos_script", "script_id", "anchor_sec"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    script_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Whole-second offset on the script's Beats arrangement timeline.
    anchor_sec: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''::text")
    )
    # ≤4 object-store path strings (beats/memos/ prefix); the image serve route
    # bounds reads by memo id + index, so bare paths are safe to store.
    images: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
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


class ScriptOps(Base):
    __tablename__ = "script_ops"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scene_id"],
            ["public.script_scenes.id"],
            ondelete="CASCADE",
            name="script_ops_scene_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="script_ops_pkey"),
        Index("idx_script_ops_scene", "scene_id", "op_seq"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    scene_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    op_seq: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="= content_version after apply"
    )
    op_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, comment='{"ops":[...], "inverse":[...]}'
    )
    actor: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="user uuid or 'copilot'"
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True),
        nullable=False,
        server_default=text("now()"),
    )


class ScriptShotOps(Base):
    __tablename__ = "script_shot_ops"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id"],
            ["public.agent_runs.id"],
            ondelete="CASCADE",
            name="script_shot_ops_run_id_fkey",
        ),
        ForeignKeyConstraint(
            ["shot_id"],
            ["public.script_shots.id"],
            ondelete="CASCADE",
            name="script_shot_ops_shot_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="script_shot_ops_pkey"),
        Index("idx_script_shot_ops_run", "run_id"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    shot_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    scene_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    before_json: Mapped[Optional[dict]] = mapped_column(JSONB)
    after_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class ScriptCommits(Base):
    __tablename__ = "script_commits"
    __table_args__ = (
        ForeignKeyConstraint(
            ["script_id"],
            ["public.script_projects.id"],
            ondelete="CASCADE",
            name="script_commits_script_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="script_commits_pkey"),
        Index("idx_script_commits_script", "script_id", "created_at"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    script_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message: Mapped[str] = mapped_column(String(200), nullable=False)
    watermarks: Mapped[dict] = mapped_column(
        JSONB, nullable=False, comment="{scene_id: op_seq} per-scene ledger high-water"
    )
    scene_ids: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        comment="ordered [{id, sort_order, heading...}] scene-set snapshot",
    )
    created_by: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="user uuid"
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True),
        nullable=False,
        server_default=text("now()"),
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
        # storyboard_project_id / storyboard_node_id referenced the retired
        # zzz_deprecated_storyboard_* tombstone tables. Migration 366 dropped
        # those tables (and these two FK constraints); the columns stay as plain
        # bigints (data preserved) with no FK — same convention as auth.users
        # references (see app/models/__init__.py).
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

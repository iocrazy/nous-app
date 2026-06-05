"""Teams, membership, projects, and project-related models."""

from __future__ import annotations

import datetime
import decimal
import uuid
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.orm_base import Base


class Teams(Base):
    __tablename__ = "teams"
    __table_args__ = (
        CheckConstraint(
            "kind = ANY (ARRAY['personal'::text, 'collaborative'::text])",
            name="teams_kind_check",
        ),
        PrimaryKeyConstraint("id", name="teams_pkey"),
        UniqueConstraint("invite_code", name="teams_invite_code_key"),
        Index("idx_teams_owner_id", "owner_id"),
        Index(
            "uq_teams_owner_personal",
            "owner_id",
            postgresql_where="(kind = 'personal'::text)",
            unique=True,
        ),
        {"schema": "public"},
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    invite_code: Mapped[str] = mapped_column(String(20), nullable=False)
    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    settings_json: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        comment=(
            "Per-team settings as JSON. Known keys: chat_temp_ttl_days "
            "(int days, -1 = never expire)."
        ),
    )
    kind: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'collaborative'::text"),
        comment=(
            "personal = auto-created single-member team; collaborative = user-created "
            "multi-member team. See ID-unification design 2026-05-28."
        ),
    )
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=text("now()")
    )
    enabled_modules: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        server_default=text(
            '\'["parser", "resources", "library", "projects", "ai_analysis",'
            ' "dashboard", "cleanup"]\'::jsonb'
        ),
    )


class TeamInvites(Base):
    __tablename__ = "team_invites"
    __table_args__ = (
        ForeignKeyConstraint(
            ["team_id"],
            ["public.teams.id"],
            ondelete="CASCADE",
            name="team_invites_team_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="team_invites_pkey"),
        UniqueConstraint("code", name="team_invites_code_key"),
        Index("idx_team_invites_created_by", "created_by"),
        Index("idx_team_invites_team", "team_id"),
        {"schema": "public"},
    )

    code: Mapped[str] = mapped_column(String(20), nullable=False)
    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    team_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    expires_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    max_uses: Mapped[Optional[int]] = mapped_column(Integer)
    use_count: Mapped[Optional[int]] = mapped_column(Integer, server_default=text("0"))
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=text("now()")
    )


class TeamMembers(Base):
    __tablename__ = "team_members"
    __table_args__ = (
        CheckConstraint(
            "role::text = ANY (ARRAY['owner'::character varying::text,"
            " 'admin'::character varying::text, 'member'::character varying::text])",
            name="team_members_role_check",
        ),
        ForeignKeyConstraint(
            ["team_id"],
            ["public.teams.id"],
            ondelete="CASCADE",
            name="team_members_team_id_fkey",
        ),
        PrimaryKeyConstraint("team_id", "user_id", name="team_members_pkey"),
        Index("idx_team_members_user", "user_id"),
        {"schema": "public"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    team_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    role: Mapped[Optional[str]] = mapped_column(
        String(20), server_default=text("'member'::character varying")
    )
    joined_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=text("now()")
    )


class TeamPlans(Base):
    __tablename__ = "team_plans"
    __table_args__ = (
        CheckConstraint(
            "plan_tier::text = ANY (ARRAY['free'::character varying::text,"
            " 'studio_standard'::character varying::text,"
            " 'studio_pro'::character varying::text,"
            " 'enterprise_standard'::character varying::text,"
            " 'enterprise_pro'::character varying::text,"
            " 'enterprise_flagship'::character varying::text])",
            name="team_plans_plan_tier_check",
        ),
        ForeignKeyConstraint(
            ["team_id"],
            ["public.teams.id"],
            ondelete="CASCADE",
            name="team_plans_team_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="team_plans_pkey"),
        UniqueConstraint("team_id", name="team_plans_team_id_key"),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    plan_tier: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default=text("'free'::character varying")
    )
    max_seats: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    max_storage_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("'5368709120'::bigint")
    )
    max_project_members: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("5")
    )
    custom_permissions: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    custom_workflows: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    team_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expires_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))


class TeamQuotas(Base):
    __tablename__ = "team_quotas"
    __table_args__ = (
        ForeignKeyConstraint(
            ["team_id"],
            ["public.teams.id"],
            ondelete="CASCADE",
            name="team_quotas_team_id_fkey",
        ),
        PrimaryKeyConstraint("team_id", name="team_quotas_pkey"),
        {"schema": "public"},
    )

    points_balance: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    storage_limit_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("'5368709120'::bigint")
    )
    storage_used_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    free_points_granted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    team_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)


class MemberQuotas(Base):
    __tablename__ = "member_quotas"
    __table_args__ = (
        ForeignKeyConstraint(
            ["team_id"],
            ["public.teams.id"],
            ondelete="CASCADE",
            name="member_quotas_team_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="member_quotas_pkey"),
        UniqueConstraint(
            "team_id", "user_id", name="member_quotas_team_id_user_id_key"
        ),
        Index("idx_member_quotas_user_id", "user_id"),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    points_used_this_month: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    reset_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True),
        nullable=False,
        server_default=text("(date_trunc('month'::text, now()) + '1 mon'::interval)"),
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    team_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    monthly_points_limit: Mapped[Optional[int]] = mapped_column(Integer)


class Projects(Base):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint(
            "project_type::text = ANY (ARRAY['internal'::character varying::text,"
            " 'external'::character varying::text,"
            " 'personal'::character varying::text])",
            name="projects_project_type_check",
        ),
        CheckConstraint(
            "visibility::text = ANY (ARRAY['inherited'::character varying::text,"
            " 'restricted'::character varying::text])",
            name="projects_visibility_check",
        ),
        ForeignKeyConstraint(
            ["team_id"],
            ["public.teams.id"],
            ondelete="SET NULL",
            name="projects_team_id_fkey",
        ),
        ForeignKeyConstraint(
            ["workflow_id"],
            ["public.project_workflows.id"],
            name="projects_workflow_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="projects_pkey"),
        Index("idx_projects_owner", "owner_id"),
        Index("idx_projects_team", "team_id"),
        Index("idx_projects_workflow_id", "workflow_id"),
        {"schema": "public"},
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    project_type: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'personal'::character varying")
    )
    is_starred: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    visibility: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'inherited'::character varying"),
    )
    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    description: Mapped[Optional[str]] = mapped_column(Text)
    project_group: Mapped[Optional[str]] = mapped_column(String(100))
    workflow_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    team_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    announcement: Mapped[Optional[str]] = mapped_column(
        Text, comment="Project announcement (max 100 chars)"
    )
    color_label: Mapped[Optional[str]] = mapped_column(String(20))


class ProjectFiles(Base):
    __tablename__ = "project_files"
    __table_args__ = (
        CheckConstraint(
            "review_status::text = ANY (ARRAY['pending_review'::character varying::text,"
            " 'in_review'::character varying::text,"
            " 'feedback_collected'::character varying::text,"
            " 'approved'::character varying::text])",
            name="project_files_review_status_check",
        ),
        ForeignKeyConstraint(
            ["folder_id"],
            ["public.project_folders.id"],
            ondelete="SET NULL",
            name="project_files_folder_id_fkey",
        ),
        ForeignKeyConstraint(
            ["media_id"],
            ["public.parsed_media.id"],
            ondelete="SET NULL",
            name="project_files_video_id_fkey",
        ),
        ForeignKeyConstraint(
            ["project_id"],
            ["public.projects.id"],
            ondelete="CASCADE",
            name="project_files_project_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="project_files_pkey"),
        Index("idx_project_files_folder", "project_id", "folder_id"),
        Index("idx_project_files_folder_id", "folder_id"),
        Index(
            "idx_project_files_media",
            "media_id",
            postgresql_where="(media_id IS NOT NULL)",
        ),
        Index("idx_project_files_uploaded_by", "uploaded_by"),
        {"schema": "public"},
    )

    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    is_trashed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    current_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    file_type: Mapped[Optional[str]] = mapped_column(String(50))
    mime_type: Mapped[Optional[str]] = mapped_column(String(100))
    file_path: Mapped[Optional[str]] = mapped_column(Text)
    file_size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    duration_seconds: Mapped[Optional[int]] = mapped_column(Integer)
    resolution: Mapped[Optional[str]] = mapped_column(String(20))
    fps: Mapped[Optional[decimal.Decimal]] = mapped_column(Numeric(6, 2))
    video_codec: Mapped[Optional[str]] = mapped_column(String(50))
    audio_codec: Mapped[Optional[str]] = mapped_column(String(50))
    video_bitrate_kbps: Mapped[Optional[int]] = mapped_column(Integer)
    audio_bitrate_kbps: Mapped[Optional[int]] = mapped_column(Integer)
    audio_channels: Mapped[Optional[int]] = mapped_column(Integer)
    audio_sample_rate: Mapped[Optional[int]] = mapped_column(Integer)
    thumbnail_path: Mapped[Optional[str]] = mapped_column(Text)
    cover_image_path: Mapped[Optional[str]] = mapped_column(Text)
    uploaded_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    trashed_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    review_status: Mapped[Optional[str]] = mapped_column(String(30))
    media_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    folder_id: Mapped[Optional[int]] = mapped_column(BigInteger)


class ProjectFolders(Base):
    __tablename__ = "project_folders"
    __table_args__ = (
        ForeignKeyConstraint(
            ["parent_id"],
            ["public.project_folders.id"],
            ondelete="CASCADE",
            name="project_folders_parent_id_fkey",
        ),
        ForeignKeyConstraint(
            ["project_id"],
            ["public.projects.id"],
            ondelete="CASCADE",
            name="project_folders_project_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="project_folders_pkey"),
        Index("idx_project_folders_created_by", "created_by"),
        Index("idx_project_folders_parent_id", "parent_id"),
        Index("idx_project_folders_project", "project_id"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        server_default=text("'New Folder'::character varying"),
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    parent_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)


class ProjectMembers(Base):
    __tablename__ = "project_members"
    __table_args__ = (
        CheckConstraint(
            "role::text = ANY (ARRAY['manager'::character varying::text,"
            " 'editor'::character varying::text,"
            " 'viewer'::character varying::text,"
            " 'external'::character varying::text])",
            name="project_members_role_check",
        ),
        ForeignKeyConstraint(
            ["project_id"],
            ["public.projects.id"],
            ondelete="CASCADE",
            name="project_members_project_id_fkey",
        ),
        PrimaryKeyConstraint("project_id", "user_id", name="project_members_pkey"),
        Index("idx_project_members_invited_by", "invited_by"),
        Index("idx_project_members_user_id", "user_id"),
        {"schema": "public"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'viewer'::character varying")
    )
    joined_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    project_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    invited_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)


class ProjectTasks(Base):
    __tablename__ = "project_tasks"
    __table_args__ = (
        CheckConstraint(
            "status::text = ANY (ARRAY['todo'::character varying::text,"
            " 'in_progress'::character varying::text,"
            " 'done'::character varying::text,"
            " 'cancelled'::character varying::text,"
            " 'on_hold'::character varying::text])",
            name="project_tasks_status_check",
        ),
        CheckConstraint(
            "task_type::text = ANY (ARRAY['general'::character varying::text,"
            " 'storyboard'::character varying::text,"
            " 'script'::character varying::text,"
            " 'filming'::character varying::text,"
            " 'editing'::character varying::text,"
            " 'review'::character varying::text])",
            name="project_tasks_task_type_check",
        ),
        ForeignKeyConstraint(
            ["project_id"],
            ["public.projects.id"],
            ondelete="CASCADE",
            name="project_tasks_project_id_fkey",
        ),
        ForeignKeyConstraint(
            ["workflow_node_id"],
            ["public.workflow_nodes.id"],
            ondelete="SET NULL",
            name="project_tasks_workflow_node_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="project_tasks_pkey"),
        Index("idx_project_tasks_assignee_id", "assignee_id"),
        Index("idx_project_tasks_created_by", "created_by"),
        Index("idx_project_tasks_status", "project_id", "status"),
        Index("idx_project_tasks_workflow_node_id", "workflow_node_id"),
        Index(
            "project_tasks_issue_id_idx",
            "issue_id",
            postgresql_where="(issue_id IS NOT NULL)",
        ),
        {"schema": "public"},
    )

    title: Mapped[str] = mapped_column(String(500), nullable=False)
    task_type: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default=text("'general'::character varying")
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'todo'::character varying")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    workflow_node_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    description: Mapped[Optional[str]] = mapped_column(Text)
    assignee_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    due_date: Mapped[Optional[datetime.date]] = mapped_column(Date)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    issue_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        comment=(
            "Back-reference to issues.id. Set during PR-D6 dual-write window; NULL for "
            "tasks that pre-date the migration. Frontend may read either side."
        ),
    )


class FileVersions(Base):
    __tablename__ = "file_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["file_id"],
            ["public.project_files.id"],
            ondelete="CASCADE",
            name="file_versions_file_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="file_versions_pkey"),
        UniqueConstraint(
            "file_id", "version_number", name="file_versions_file_id_version_number_key"
        ),
        Index("idx_file_versions_uploaded_by", "uploaded_by"),
        {"schema": "public"},
    )

    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    file_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    filename: Mapped[Optional[str]] = mapped_column(String(500))
    file_path: Mapped[Optional[str]] = mapped_column(Text)
    file_size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    mime_type: Mapped[Optional[str]] = mapped_column(String(100))
    duration_seconds: Mapped[Optional[int]] = mapped_column(Integer)
    resolution: Mapped[Optional[str]] = mapped_column(String(50))
    fps: Mapped[Optional[decimal.Decimal]] = mapped_column(Numeric(6, 2))
    video_codec: Mapped[Optional[str]] = mapped_column(String(50))
    audio_codec: Mapped[Optional[str]] = mapped_column(String(50))
    video_bitrate_kbps: Mapped[Optional[int]] = mapped_column(Integer)
    audio_bitrate_kbps: Mapped[Optional[int]] = mapped_column(Integer)
    audio_channels: Mapped[Optional[int]] = mapped_column(Integer)
    audio_sample_rate: Mapped[Optional[int]] = mapped_column(Integer)
    thumbnail_path: Mapped[Optional[str]] = mapped_column(Text)
    cover_image_path: Mapped[Optional[str]] = mapped_column(Text)
    uploaded_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    notes: Mapped[Optional[str]] = mapped_column(Text)

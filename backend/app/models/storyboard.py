"""Storyboard models: projects, assets, characters, nodes, edges, frames, video assets,
plus user schedules."""

from __future__ import annotations

import datetime
import uuid
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Double,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.orm_base import Base


class UserSchedules(Base):
    __tablename__ = "user_schedules"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="user_schedules_pkey"),
        Index(
            "idx_user_schedules_due",
            "next_fire_at",
            postgresql_where="(enabled = true)",
        ),
        Index("idx_user_schedules_user", "user_id", "enabled"),
        {
            "comment": (
                "User- or system-configured cron schedules. Master scheduler workflow\n"
                "     (app/workflows/scheduled_master.py) scans this table every minute and\n"
                "     dispatches due rows. Replaces hard-coded @DBOS.scheduled decorators\n"
                "     for user-facing recurring tasks; internal DBOS sweepers still use\n"
                "     the decorator pattern because they're system primitives."
            ),
            "schema": "public",
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    cron_expr: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment=(
            "5-field cron expression in UTC (m h dom mon dow). Validated by\n"
            "     croniter on insert/update at the service layer (DB doesn't parse\n"
            '     cron). Examples: "0 9 * * *" daily 9am UTC, "*/15 * * * *" every\n'
            '     15 min, "0 0 * * 0" weekly Sunday midnight.'
        ),
    )
    task_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    lane: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'scheduled'::text")
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    next_fire_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True),
        nullable=False,
        comment=(
            "Pre-computed next fire time, kept in sync by the master scheduler\n"
            "     after each fire (or by service layer on insert/update). The\n"
            "     idx_user_schedules_due index makes finding due rows O(log n)."
        ),
    )
    fire_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    fail_count: Mapped[int] = mapped_column(
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
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid,
        comment=(
            "Owner of the schedule. NULL means system-owned (operator-managed\n"
            "     via DB / admin tools, not user-facing UI)."
        ),
    )
    last_fired_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    last_error: Mapped[Optional[str]] = mapped_column(Text)


class StoryboardProjects(Base):
    __tablename__ = "storyboard_projects"
    __table_args__ = (
        CheckConstraint(
            "status::text = ANY (ARRAY['active'::character varying::text,"
            " 'archived'::character varying::text, 'deleted'::character varying::text])",
            name="storyboard_projects_status_check",
        ),
        ForeignKeyConstraint(
            ["project_id"],
            ["public.projects.id"],
            ondelete="SET NULL",
            name="storyboard_projects_project_id_fkey",
        ),
        ForeignKeyConstraint(
            ["team_id"],
            ["public.teams.id"],
            ondelete="CASCADE",
            name="storyboard_projects_team_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="storyboard_projects_pkey"),
        Index("idx_sb_projects_parent", "project_id"),
        Index("idx_storyboard_projects_created_by", "created_by"),
        Index("idx_storyboard_projects_team_id", "team_id"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    team_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'active'::character varying"),
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
    cover_image_url: Mapped[Optional[str]] = mapped_column(Text)
    viewport_json: Mapped[Optional[dict]] = mapped_column(JSONB)
    settings_json: Mapped[Optional[dict]] = mapped_column(JSONB)
    project_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    display_code: Mapped[Optional[str]] = mapped_column(String(20))


class StoryboardAssets(Base):
    __tablename__ = "storyboard_assets"
    __table_args__ = (
        CheckConstraint(
            "source_type::text = ANY (ARRAY['uploaded'::character varying::text,"
            " 'generated'::character varying::text, 'split'::character varying::text,"
            " 'imported'::character varying::text])",
            name="storyboard_assets_source_type_check",
        ),
        ForeignKeyConstraint(
            ["project_id"],
            ["public.storyboard_projects.id"],
            ondelete="CASCADE",
            name="storyboard_assets_project_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="storyboard_assets_pkey"),
        Index("idx_storyboard_assets_project_id", "project_id"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'generated'::character varying"),
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True),
        nullable=False,
        server_default=text("now()"),
    )
    file_hash: Mapped[Optional[str]] = mapped_column(String(64))
    file_size: Mapped[Optional[int]] = mapped_column(BigInteger)
    mime_type: Mapped[Optional[str]] = mapped_column(String(50))
    width: Mapped[Optional[int]] = mapped_column(Integer)
    height: Mapped[Optional[int]] = mapped_column(Integer)
    preview_path: Mapped[Optional[str]] = mapped_column(Text)
    metadata_json: Mapped[Optional[dict]] = mapped_column(JSONB)


class StoryboardCharacters(Base):
    __tablename__ = "storyboard_characters"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id"],
            ["public.storyboard_projects.id"],
            ondelete="CASCADE",
            name="storyboard_characters_project_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="storyboard_characters_pkey"),
        Index("idx_sb_characters_project", "project_id"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
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
    description: Mapped[Optional[str]] = mapped_column(Text)
    reference_image_url: Mapped[Optional[str]] = mapped_column(Text)
    thumbnail_url: Mapped[Optional[str]] = mapped_column(Text)
    visual_traits: Mapped[Optional[dict]] = mapped_column(JSONB)


class StoryboardNodes(Base):
    __tablename__ = "storyboard_nodes"
    __table_args__ = (
        CheckConstraint(
            "node_type::text = ANY (ARRAY['upload'::character varying::text, 'image_edit'::character varying::text, "
            "'storyboard_split'::character varying::text, 'storyboard_gen'::character varying::text, "
            "'text_annotation'::character varying::text, 'group'::character varying::text, "
            "'export'::character varying::text, 'image_to_video'::character varying::text])",
            name="storyboard_nodes_node_type_check",
        ),
        ForeignKeyConstraint(
            ["project_id"],
            ["public.storyboard_projects.id"],
            ondelete="CASCADE",
            name="storyboard_nodes_project_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="storyboard_nodes_pkey"),
        Index("idx_sb_nodes_project", "project_id"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    node_type: Mapped[str] = mapped_column(String(30), nullable=False)
    position_x: Mapped[float] = mapped_column(
        Double(53), nullable=False, server_default=text("0")
    )
    position_y: Mapped[float] = mapped_column(
        Double(53), nullable=False, server_default=text("0")
    )
    data_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    locked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
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
    width: Mapped[Optional[float]] = mapped_column(Double(53))
    height: Mapped[Optional[float]] = mapped_column(Double(53))
    sort_order: Mapped[Optional[int]] = mapped_column(Integer)


class StoryboardEdges(Base):
    __tablename__ = "storyboard_edges"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id"],
            ["public.storyboard_projects.id"],
            ondelete="CASCADE",
            name="storyboard_edges_project_id_fkey",
        ),
        ForeignKeyConstraint(
            ["source_node_id"],
            ["public.storyboard_nodes.id"],
            ondelete="CASCADE",
            name="storyboard_edges_source_node_id_fkey",
        ),
        ForeignKeyConstraint(
            ["target_node_id"],
            ["public.storyboard_nodes.id"],
            ondelete="CASCADE",
            name="storyboard_edges_target_node_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="storyboard_edges_pkey"),
        Index("idx_sb_edges_project", "project_id"),
        Index("idx_storyboard_edges_source_node_id", "source_node_id"),
        Index("idx_storyboard_edges_target_node_id", "target_node_id"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_node_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    target_node_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    edge_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'default'::character varying"),
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True),
        nullable=False,
        server_default=text("now()"),
    )
    source_handle: Mapped[Optional[str]] = mapped_column(String(50))
    target_handle: Mapped[Optional[str]] = mapped_column(String(50))


class StoryboardFrames(Base):
    __tablename__ = "storyboard_frames"
    __table_args__ = (
        CheckConstraint(
            "transition_type::text = ANY (ARRAY['cut'::character varying::text,"
            " 'fade'::character varying::text, 'dissolve'::character varying::text])",
            name="storyboard_frames_transition_type_check",
        ),
        ForeignKeyConstraint(
            ["node_id"],
            ["public.storyboard_nodes.id"],
            ondelete="CASCADE",
            name="storyboard_frames_node_id_fkey",
        ),
        ForeignKeyConstraint(
            ["project_id"],
            ["public.storyboard_projects.id"],
            ondelete="CASCADE",
            name="storyboard_frames_project_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="storyboard_frames_pkey"),
        UniqueConstraint(
            "node_id", "frame_index", name="storyboard_frames_node_id_frame_index_key"
        ),
        Index("idx_sb_frames_project", "project_id"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    node_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    frame_index: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_seconds: Mapped[float] = mapped_column(
        Double(53),
        nullable=False,
        server_default=text("3.0"),
    )
    transition_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'cut'::character varying"),
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
    image_url: Mapped[Optional[str]] = mapped_column(Text)
    thumbnail_url: Mapped[Optional[str]] = mapped_column(Text)
    note: Mapped[Optional[str]] = mapped_column(Text)
    shot_type: Mapped[Optional[str]] = mapped_column(String(30))
    camera_angle: Mapped[Optional[str]] = mapped_column(String(30))
    camera_movement: Mapped[Optional[str]] = mapped_column(String(30))
    focal_length: Mapped[Optional[str]] = mapped_column(String(20))
    lighting: Mapped[Optional[str]] = mapped_column(Text)
    annotations_json: Mapped[Optional[dict]] = mapped_column(JSONB)


class StoryboardVideoAssets(Base):
    __tablename__ = "storyboard_video_assets"
    __table_args__ = (
        CheckConstraint(
            "status::text = ANY (ARRAY['pending'::character varying::text,"
            " 'processing'::character varying::text, 'completed'::character varying::text,"
            " 'failed'::character varying::text])",
            name="storyboard_video_assets_status_check",
        ),
        ForeignKeyConstraint(
            ["project_id"],
            ["public.storyboard_projects.id"],
            ondelete="CASCADE",
            name="storyboard_video_assets_project_id_fkey",
        ),
        ForeignKeyConstraint(
            ["source_frame_id"],
            ["public.storyboard_frames.id"],
            ondelete="SET NULL",
            name="storyboard_video_assets_source_frame_id_fkey",
        ),
        ForeignKeyConstraint(
            ["source_node_id"],
            ["public.storyboard_nodes.id"],
            ondelete="SET NULL",
            name="storyboard_video_assets_source_node_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="storyboard_video_assets_pkey"),
        Index("idx_storyboard_video_assets_project_id", "project_id"),
        Index("idx_storyboard_video_assets_source_frame_id", "source_frame_id"),
        Index("idx_storyboard_video_assets_source_node_id", "source_node_id"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
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
    source_frame_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    source_node_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    thumbnail_path: Mapped[Optional[str]] = mapped_column(Text)
    duration_seconds: Mapped[Optional[float]] = mapped_column(Double(53))
    width: Mapped[Optional[int]] = mapped_column(Integer)
    height: Mapped[Optional[int]] = mapped_column(Integer)
    file_size: Mapped[Optional[int]] = mapped_column(BigInteger)
    provider: Mapped[Optional[str]] = mapped_column(String(30))
    generation_params: Mapped[Optional[dict]] = mapped_column(JSONB)


# Junction table (no ORM class — uses Table() directly with Base.metadata)


t_storyboard_frame_characters = Table(
    "storyboard_frame_characters",
    Base.metadata,
    Column("frame_id", BigInteger, primary_key=True),
    Column("character_id", BigInteger, primary_key=True),
    ForeignKeyConstraint(
        ["character_id"],
        ["public.storyboard_characters.id"],
        ondelete="CASCADE",
        name="storyboard_frame_characters_character_id_fkey",
    ),
    ForeignKeyConstraint(
        ["frame_id"],
        ["public.storyboard_frames.id"],
        ondelete="CASCADE",
        name="storyboard_frame_characters_frame_id_fkey",
    ),
    PrimaryKeyConstraint(
        "frame_id", "character_id", name="storyboard_frame_characters_pkey"
    ),
    Index("idx_storyboard_frame_characters_character_id", "character_id"),
    schema="public",
)

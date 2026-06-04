from __future__ import annotations

import datetime
import uuid
from typing import Optional

from sqlalchemy import (
    ARRAY,
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
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.orm_base import Base


class Authors(Base):
    __tablename__ = "authors"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="authors_pkey"),
        UniqueConstraint("author_id", name="authors_author_id_key"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    nickname: Mapped[str] = mapped_column(String(255), nullable=False)
    author_id: Mapped[Optional[str]] = mapped_column(String(64))
    signature: Mapped[Optional[str]] = mapped_column(Text)
    avatar_url: Mapped[Optional[str]] = mapped_column(Text)
    follower_count: Mapped[Optional[int]] = mapped_column(
        Integer, server_default=text("0")
    )
    following_count: Mapped[Optional[int]] = mapped_column(
        Integer, server_default=text("0")
    )
    total_favorited: Mapped[Optional[int]] = mapped_column(
        Integer, server_default=text("0")
    )
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=text("now()")
    )
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=text("now()")
    )


class TagGroups(Base):
    __tablename__ = "tag_groups"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="tag_groups_pkey"),
        UniqueConstraint("name", name="tag_groups_name_key"),
        Index("idx_tag_groups_sort", "sort_order"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    sort_order: Mapped[Optional[int]] = mapped_column(Integer, server_default=text("0"))
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=text("now()")
    )


class Tags(Base):
    __tablename__ = "tags"
    __table_args__ = (
        CheckConstraint(
            "type::text = ANY (ARRAY['system'::character varying::text,"
            " 'user'::character varying::text, 'time'::character varying::text])",
            name="tags_type_check",
        ),
        ForeignKeyConstraint(
            ["group_id"],
            ["public.tag_groups.id"],
            ondelete="SET NULL",
            name="tags_group_id_fkey",
        ),
        ForeignKeyConstraint(
            ["scope_id"],
            ["public.teams.id"],
            ondelete="CASCADE",
            name="tags_scope_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="tags_pkey"),
        UniqueConstraint("name", "type", "user_id", name="unique_tag_per_scope"),
        Index("idx_tags_group_id", "group_id"),
        Index("idx_tags_name_zh", "name_zh"),
        Index("idx_tags_user_id", "user_id"),
        {"schema": "public"},
    )

    name: Mapped[str] = mapped_column(String(50), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
        comment="Whether this tag is visible in the frontend API.",
    )
    color: Mapped[Optional[str]] = mapped_column(
        String(20), server_default=text("'#6366f1'::character varying")
    )
    icon: Mapped[Optional[str]] = mapped_column(String(50))
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=text("now()")
    )
    name_zh: Mapped[Optional[str]] = mapped_column(String(50))
    scope_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    group_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    sort_order: Mapped[Optional[int]] = mapped_column(Integer, server_default=text("0"))


class Libraries(Base):
    __tablename__ = "libraries"
    __table_args__ = (
        CheckConstraint("scope_type = 'team'::text", name="libraries_scope_type_check"),
        CheckConstraint(
            "visibility = ANY (ARRAY['inherited'::text, 'restricted'::text])",
            name="libraries_visibility_check",
        ),
        PrimaryKeyConstraint("id", name="libraries_pkey"),
        Index("idx_libraries_created_by", "created_by"),
        Index("idx_libraries_scope", "scope_type", "scope_id"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    scope_type: Mapped[str] = mapped_column(Text, nullable=False)
    scope_id: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    visibility: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'inherited'::text")
    )
    icon: Mapped[Optional[str]] = mapped_column(Text)
    color: Mapped[Optional[str]] = mapped_column(Text)
    sort_order: Mapped[Optional[int]] = mapped_column(Integer, server_default=text("0"))
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=text("now()")
    )
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=text("now()")
    )


class Collections(Base):
    __tablename__ = "collections"
    __table_args__ = (
        ForeignKeyConstraint(
            ["team_id"],
            ["public.teams.id"],
            ondelete="SET NULL",
            name="collections_team_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="collections_pkey"),
        Index("idx_collections_owner_id", "owner_id"),
        Index("idx_collections_team_id", "team_id"),
        {
            "comment": "User/team video collections. PK migrated to Snowflake BIGINT in 060.",
            "schema": "public",
        },
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    owner_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=text("now()")
    )
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=text("now()")
    )
    team_id: Mapped[Optional[int]] = mapped_column(BigInteger)


class SmartCollections(Base):
    __tablename__ = "smart_collections"
    __table_args__ = (
        CheckConstraint(
            "sort_order::text = ANY (ARRAY['asc'::character varying::text, 'desc'::character varying::text])",
            name="smart_collections_sort_order_check",
        ),
        ForeignKeyConstraint(
            ["scope_id"],
            ["public.teams.id"],
            ondelete="CASCADE",
            name="smart_collections_scope_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="smart_collections_pkey"),
        Index("idx_smart_collections_scope_id", "scope_id"),
        Index("idx_smart_collections_user", "user_id"),
        {
            "comment": "Smart folders with dynamic filter rules. PK migrated from UUID to Snowflake BIGINT in 060.",
            "schema": "public",
        },
    )

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    rules: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text('\'{"match": "all", "conditions": []}\'::jsonb'),
    )
    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    icon: Mapped[Optional[str]] = mapped_column(
        String(50), server_default=text("'📁'::character varying")
    )
    description: Mapped[Optional[str]] = mapped_column(Text)
    cached_count: Mapped[Optional[int]] = mapped_column(
        Integer, server_default=text("0")
    )
    cached_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    is_preset: Mapped[Optional[bool]] = mapped_column(
        Boolean, server_default=text("false")
    )
    sort_by: Mapped[Optional[str]] = mapped_column(
        String(50), server_default=text("'created_at'::character varying")
    )
    sort_order: Mapped[Optional[str]] = mapped_column(
        String(10), server_default=text("'desc'::character varying")
    )
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=text("now()")
    )
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=text("now()")
    )
    is_active: Mapped[Optional[bool]] = mapped_column(
        Boolean,
        server_default=text("true"),
        comment="Whether the collection is currently active",
    )
    color: Mapped[Optional[str]] = mapped_column(
        String(20),
        server_default=text("'#3b82f6'::character varying"),
        comment="Optional color for collection display (e.g., #6366f1)",
    )
    scope_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    cached_video_ids: Mapped[Optional[list[int]]] = mapped_column(
        ARRAY(BigInteger()), server_default=text("'{}'::bigint[]")
    )


class StyleTemplates(Base):
    __tablename__ = "style_templates"
    __table_args__ = (
        ForeignKeyConstraint(
            ["team_id"], ["public.teams.id"], name="style_templates_team_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="style_templates_pkey"),
        Index("idx_style_templates_created_by", "created_by"),
        Index("idx_style_templates_team_id", "team_id"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_content: Mapped[str] = mapped_column(Text, nullable=False)
    team_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    description: Mapped[Optional[str]] = mapped_column(Text)
    category: Mapped[Optional[str]] = mapped_column(Text)
    is_public: Mapped[Optional[bool]] = mapped_column(
        Boolean, server_default=text("false")
    )
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=text("now()")
    )
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=text("now()")
    )


class SearchLogs(Base):
    __tablename__ = "search_logs"
    __table_args__ = (
        CheckConstraint(
            "search_type::text = ANY (ARRAY['semantic'::character varying::text,"
            " 'hybrid'::character varying::text, 'similar'::character varying::text,"
            " 'quick'::character varying::text])",
            name="search_logs_search_type_check",
        ),
        PrimaryKeyConstraint("id", name="search_logs_pkey"),
        Index("idx_search_logs_user_id", "user_id"),
        {
            "comment": "Tracks search queries for analytics and suggestions",
            "schema": "public",
        },
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    query: Mapped[str] = mapped_column(Text, nullable=False)
    search_type: Mapped[str] = mapped_column(String(20), nullable=False)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    result_count: Mapped[Optional[int]] = mapped_column(
        Integer, server_default=text("0")
    )
    top_result_similarity: Mapped[Optional[float]] = mapped_column(Double(53))
    filters_used: Mapped[Optional[dict]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=text("now()")
    )
    top_result_video_id: Mapped[Optional[int]] = mapped_column(BigInteger)


class TempTokens(Base):
    __tablename__ = "temp_tokens"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="temp_tokens_pkey"),
        UniqueConstraint("token", name="temp_tokens_token_key"),
        Index("idx_temp_tokens_token", "token"),
        Index("idx_temp_tokens_user_id", "user_id"),
        {
            "comment": "Short-lived tokens for secure web page access (e.g. Shortcuts tag picker)",
            "schema": "public",
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    token: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    scopes: Mapped[list[str]] = mapped_column(
        ARRAY(Text()), nullable=False, server_default=text("'{}'::text[]")
    )
    expires_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    used_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    selection: Mapped[Optional[list[str]]] = mapped_column(
        ARRAY(Text()), server_default=text("'{}'::text[]")
    )


class Shares(Base):
    __tablename__ = "shares"
    __table_args__ = (
        CheckConstraint(
            "share_type::text = ANY (ARRAY['link'::character varying::text,"
            " 'review'::character varying::text, 'presentation'::character varying::text,"
            " 'delivery'::character varying::text])",
            name="shares_share_type_check",
        ),
        CheckConstraint(
            "status::text = ANY (ARRAY['active'::character varying::text,"
            " 'inactive'::character varying::text, 'expired'::character varying::text,"
            " 'cancelled'::character varying::text])",
            name="shares_status_check",
        ),
        ForeignKeyConstraint(
            ["folder_id"],
            ["public.folders.id"],
            ondelete="CASCADE",
            name="shares_folder_id_fkey",
        ),
        ForeignKeyConstraint(
            ["library_id"],
            ["public.libraries.id"],
            ondelete="CASCADE",
            name="shares_library_id_fkey",
        ),
        ForeignKeyConstraint(
            ["project_file_id"],
            ["public.project_files.id"],
            ondelete="CASCADE",
            name="shares_project_file_id_fkey",
        ),
        ForeignKeyConstraint(
            ["resource_id"],
            ["public.resources.id"],
            ondelete="CASCADE",
            name="shares_resource_id_fkey",
        ),
        ForeignKeyConstraint(
            ["team_id"], ["public.teams.id"], name="shares_team_id_fkey"
        ),
        ForeignKeyConstraint(
            ["version_id"],
            ["public.file_versions.id"],
            ondelete="SET NULL",
            name="shares_version_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="shares_pkey"),
        UniqueConstraint("share_code", name="shares_share_code_key"),
        Index("idx_shares_code", "share_code"),
        Index(
            "idx_shares_folder", "folder_id", postgresql_where="(folder_id IS NOT NULL)"
        ),
        Index("idx_shares_library_id", "library_id"),
        Index("idx_shares_project_file_id", "project_file_id"),
        Index(
            "idx_shares_resource",
            "resource_id",
            postgresql_where="(resource_id IS NOT NULL)",
        ),
        Index("idx_shares_shared_by", "shared_by"),
        Index(
            "idx_shares_status",
            "status",
            postgresql_where="((status)::text = 'active'::text)",
        ),
        Index("idx_shares_team_id", "team_id"),
        Index("idx_shares_version_id", "version_id"),
        {"schema": "public"},
    )

    share_type: Mapped[str] = mapped_column(String(20), nullable=False)
    shared_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    share_name: Mapped[str] = mapped_column(String(200), nullable=False)
    share_code: Mapped[str] = mapped_column(String(20), nullable=False)
    allow_download: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    view_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    watermark: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'active'::character varying")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    password: Mapped[Optional[str]] = mapped_column(Text)
    expires_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    max_views: Mapped[Optional[int]] = mapped_column(Integer)
    project_file_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    version_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    resource_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    folder_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    library_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    team_id: Mapped[Optional[int]] = mapped_column(BigInteger)


class ShareViews(Base):
    __tablename__ = "share_views"
    __table_args__ = (
        ForeignKeyConstraint(
            ["share_id"],
            ["public.shares.id"],
            ondelete="CASCADE",
            name="share_views_share_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="share_views_pkey"),
        UniqueConstraint(
            "share_id", "viewer_id", name="share_views_share_id_viewer_id_key"
        ),
        Index("idx_share_views_share", "share_id"),
        Index("idx_share_views_viewer_id", "viewer_id"),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    is_favorited: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    last_viewed_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    view_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    share_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    viewer_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)


class ProjectCollections(Base):
    __tablename__ = "project_collections"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id"],
            ["public.projects.id"],
            ondelete="CASCADE",
            name="project_collections_project_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="project_collections_pkey"),
        UniqueConstraint(
            "collection_code", name="project_collections_collection_code_key"
        ),
        Index("idx_project_collections_created_by", "created_by"),
        Index("idx_project_collections_project", "project_id"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    collection_code: Mapped[str] = mapped_column(String(20), nullable=False)
    collection_name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    allowed_types: Mapped[Optional[list[str]]] = mapped_column(ARRAY(Text()))
    max_file_size_mb: Mapped[Optional[int]] = mapped_column(
        Integer, server_default=text("500")
    )
    deadline: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    is_active: Mapped[Optional[bool]] = mapped_column(
        Boolean, server_default=text("true")
    )
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)

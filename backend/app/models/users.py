"""User-related ORM models: UserProfiles, UserCredits, UserTagPreferences,
UserSettings, UserCookies, UserLogs.

Note: auth.users (GoTrue) is permanently EXCLUDED from these models; there is no
public.users table. FK constraints that targeted users.id are intentionally omitted —
referential integrity lives in Postgres, not in this reference metadata.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
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
from app.models._enums import UserRole


class UserProfiles(Base):
    __tablename__ = "user_profiles"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="user_profiles_pkey"),
        UniqueConstraint("display_id", name="user_profiles_display_id_key"),
        UniqueConstraint("username", name="user_profiles_username_key"),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    is_banned: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
        comment="Whether the user is banned from the platform",
    )
    display_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("generate_snowflake_id()")
    )
    username: Mapped[str | None] = mapped_column(String(255))
    avatar_url: Mapped[str | None] = mapped_column(Text)
    role: Mapped[UserRole | None] = mapped_column(
        Enum(
            UserRole,
            values_callable=lambda cls: [member.value for member in cls],
            name="user_role",
        ),
        server_default=text("'user'::user_role"),
    )
    created_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(True), server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(True), server_default=text("now()")
    )


class UserCredits(Base):
    __tablename__ = "user_credits"
    __table_args__ = (
        PrimaryKeyConstraint("user_id", name="user_credits_pkey"),
        {
            "comment": "User credit accounts for tracking balance and usage",
            "schema": "public",
        },
    )

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    balance: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
        comment="Current available credits",
    )
    total_earned: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
        comment="Total credits earned (recharges, gifts, etc.)",
    )
    total_spent: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
        comment="Total credits spent on actions",
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class UserTagPreferences(Base):
    __tablename__ = "user_tag_preferences"
    __table_args__ = (
        PrimaryKeyConstraint("user_id", name="user_tag_preferences_pkey"),
        {"schema": "public"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    starred_tag_ids: Mapped[list[str] | None] = mapped_column(
        ARRAY(Text()), server_default=text("'{}'::text[]")
    )
    picker_settings: Mapped[dict | None] = mapped_column(
        JSONB,
        server_default=text(
            '\'{"layout": "list", "showCount": true, "columnWidth": "medium",'
            ' "showStarred": true, "showRecently": true, "showRecommended": false}\'::jsonb'
        ),
    )
    panel_size: Mapped[dict | None] = mapped_column(
        JSONB,
        server_default=text('\'{"width": 480, "height": 400}\'::jsonb'),
    )
    created_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(True), server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(True), server_default=text("now()")
    )


class UserSettings(Base):
    __tablename__ = "user_settings"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="user_settings_pkey"),
        UniqueConstraint("user_id", name="user_settings_user_id_key"),
        Index("idx_user_settings_user_id", "user_id"),
        {"comment": "用户个人设置表", "schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, nullable=False, comment="用户ID，关联 auth.users"
    )
    download_path: Mapped[str | None] = mapped_column(
        Text,
        server_default=text("'/home/user/downloads/douyin'::text"),
        comment="默认下载路径",
    )
    settings_json: Mapped[dict | None] = mapped_column(
        JSONB,
        server_default=text("'{}'::jsonb"),
        comment="Per-user settings as JSON. Known keys: chat_temp_ttl_days (int days, -1 = never expire).",
    )
    created_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(True), server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(True), server_default=text("now()")
    )


class UserCookies(Base):
    __tablename__ = "user_cookies"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="user_cookies_pkey"),
        UniqueConstraint(
            "user_id", "platform", name="user_cookies_user_id_platform_key"
        ),
        Index("idx_user_cookies_user_platform", "user_id", "platform"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_snowflake_id()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    is_valid: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    cookie_text: Mapped[str | None] = mapped_column(Text)
    cookie_file: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    custom_headers: Mapped[str | None] = mapped_column(Text)


class UserLogs(Base):
    __tablename__ = "user_logs"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="user_logs_pkey"),
        Index("idx_user_logs_created_at", "created_at"),
        Index("idx_user_logs_user_id", "user_id"),
        {"comment": "用户操作日志表", "schema": "public"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    action: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="操作类型: fetch, download, delete, retry, update, login, logout",
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_snowflake_id()"),
    )
    status: Mapped[str | None] = mapped_column(
        String(20),
        server_default=text("'info'::character varying"),
        comment="状态: success, error, warning, info, pending",
    )
    aweme_id: Mapped[str | None] = mapped_column(String(50))
    details: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(True), server_default=text("now()")
    )

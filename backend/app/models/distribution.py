"""Distribution (multi-platform publish) domain ORM models.

  * ``SocialAccounts``       — social_accounts (mig 351; platform accounts bound
    by OAuth or, since mig 401, by a browser session;
    ``access_token``/``refresh_token``/``session_state`` hold Fernet ciphertext
    — the encrypt/decrypt boundary lives in social_accounts_repository)
  * ``AccountEnvironments``  — account_environments (mig 402; the pinned browser
    environment of a session-bound account; ``proxy_url`` is ciphertext too)
  * ``PublishTasks``         — publish_tasks (mig 356)
  * ``PublishTaskAccounts``  — publish_task_accounts (mig 356; per-account
    business status, layered apart from the DBOS phase per route C)

No scope mixin — scope checks live in the routers/services; the tables are
RLS-locked to service_role (engine role bypasses via BYPASSRLS).
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Double,
    ForeignKeyConstraint,
    Index,
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


class SocialAccounts(Base):
    """A bound platform account — by OAuth (mig 351) or by a browser session
    (mig 401, ``auth_type='session'``). Token and session columns store Fernet
    ciphertext; the repo strips them on the public read path."""

    __tablename__ = "social_accounts"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="social_accounts_pkey"),
        UniqueConstraint(
            "scope_type",
            "scope_id",
            "platform",
            "platform_user_id",
            name="social_accounts_scope_type_scope_id_platform_platform_user_key",
        ),
        CheckConstraint(
            "scope_type IN ('user', 'team')", name="social_accounts_scope_type_check"
        ),
        CheckConstraint(
            "status IN ('active', 'expired', 'needs_relogin')",
            name="social_accounts_status_check",
        ),
        CheckConstraint(
            "auth_type IN ('oauth', 'session')", name="social_accounts_auth_type_check"
        ),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_snowflake_id()"),
    )
    scope_type: Mapped[str] = mapped_column(String(10), nullable=False)
    scope_id: Mapped[str] = mapped_column(Text, nullable=False)
    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    platform_user_id: Mapped[str] = mapped_column(Text, nullable=False)
    username: Mapped[str] = mapped_column(Text, nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(Text)
    access_token: Mapped[str | None] = mapped_column(Text)
    refresh_token: Mapped[str | None] = mapped_column(Text)
    token_expires_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'active'::character varying")
    )
    auth_type: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default=text("'oauth'::character varying")
    )
    # Fernet ciphertext of Playwright's storage_state JSON — plaintext exists
    # only inside the nous-browser process (spec §7.6).
    session_state: Mapped[str | None] = mapped_column(Text)
    session_checked_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class AccountEnvironments(Base):
    """The pinned browser environment of a session-bound account (mig 402).

    1:1 with social_accounts (UNIQUE account_id) and meant to stay put — a
    fingerprint that changes every run is itself the risk signal. ``proxy_url``
    holds Fernet ciphertext (it embeds proxy credentials).
    """

    __tablename__ = "account_environments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["account_id"],
            ["public.social_accounts.id"],
            ondelete="CASCADE",
            name="account_environments_account_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="account_environments_pkey"),
        UniqueConstraint("account_id", name="account_environments_account_id_key"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_snowflake_id()"),
    )
    account_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    proxy_url: Mapped[str | None] = mapped_column(Text)
    user_agent: Mapped[str | None] = mapped_column(Text)
    locale: Mapped[str | None] = mapped_column(
        String(20), server_default=text("'zh-CN'::character varying")
    )
    timezone_id: Mapped[str | None] = mapped_column(
        String(50), server_default=text("'Asia/Shanghai'::character varying")
    )
    geo_lat: Mapped[float | None] = mapped_column(Double(53))
    geo_lng: Mapped[float | None] = mapped_column(Double(53))
    fingerprint_profile_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class PublishTasks(Base):
    """A publish batch (mig 356)."""

    __tablename__ = "publish_tasks"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="publish_tasks_pkey"),
        Index("idx_publish_tasks_user", "user_id"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_snowflake_id()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    team_id: Mapped[int | None] = mapped_column(BigInteger)
    content_type: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default=text("'video'::character varying")
    )
    resource_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    topics: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    cover_vertical_resource_id: Mapped[int | None] = mapped_column(BigInteger)
    cover_horizontal_resource_id: Mapped[int | None] = mapped_column(BigInteger)
    visibility: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default=text("'public'::character varying")
    )
    ai_content: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    allow_download: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    distribution_mode: Mapped[str] = mapped_column(
        String(12),
        nullable=False,
        server_default=text("'broadcast'::character varying"),
    )
    scheduled_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    # mig 407 — 抖音发布页的两个平台原生字段。self_declaration 存的是页面上的
    # 中文原文（六个之一，库里有 CHECK），NULL = 不碰那个控件；与 ai_content 的
    # 关系见 services/distribution/publish_options.py::resolve_self_declaration。
    self_declaration: Mapped[str | None] = mapped_column(Text)
    collection_name: Mapped[str | None] = mapped_column(Text)
    dbos_workflow_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class PublishTaskAccounts(Base):
    """Per-account publish state within a batch (mig 356). ``status`` is the
    BUSINESS state (incl. the H5 'pending_share'), layered apart from the DBOS
    phase (route C)."""

    __tablename__ = "publish_task_accounts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["task_id"],
            ["public.publish_tasks.id"],
            ondelete="CASCADE",
            name="publish_task_accounts_task_id_fkey",
        ),
        ForeignKeyConstraint(
            ["account_id"],
            ["public.social_accounts.id"],
            ondelete="CASCADE",
            name="publish_task_accounts_account_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="publish_task_accounts_pkey"),
        Index("idx_publish_task_accounts_task", "task_id"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_snowflake_id()"),
    )
    task_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    account_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    resource_id: Mapped[int | None] = mapped_column(BigInteger)
    channel: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default=text("'h5'::character varying")
    )
    title: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    topics: Mapped[list | None] = mapped_column(JSONB)
    share_id: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'pending'::character varying")
    )
    error_message: Mapped[str | None] = mapped_column(Text)
    published_url: Mapped[str | None] = mapped_column(Text)
    platform_item_id: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class DistributionOauthStates(Base):
    """Short-lived CSRF state for the platform OAuth handshake (mig 351).

    PK is the opaque ``state`` value echoed back by the provider; the row is
    consumed on callback. ``scope_id`` is text so it can carry either a team or
    user snowflake without a second column.
    """

    __tablename__ = "distribution_oauth_states"
    __table_args__ = (
        PrimaryKeyConstraint("state", name="distribution_oauth_states_pkey"),
        {"schema": "public"},
    )

    state: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(10), nullable=False)
    scope_id: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )

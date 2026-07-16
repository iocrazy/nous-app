"""Distribution (multi-platform publish) domain ORM models.

  * ``SocialAccounts``       — social_accounts (mig 351; OAuth-bound platform
    accounts; ``access_token``/``refresh_token`` hold Fernet ciphertext — the
    encrypt/decrypt boundary lives in social_accounts_repository)
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
    """An OAuth-bound platform account (mig 351). Token columns store Fernet
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
            "status IN ('active', 'expired')", name="social_accounts_status_check"
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
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
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

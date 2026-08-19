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
  * ``MusicCharts`` / ``MusicChartTracks`` — music_charts, music_chart_tracks
    (mig 428; the 「选择音乐」 panel's chart tabs, cached per account because
    reading them costs a browser run and leaves a draft)

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
    # mig 414 — the platform's human-facing account name (抖音号 / 小红书号).
    # DISPLAY ONLY, and the distinction is load-bearing: it used to be fed into
    # platform_user_id whenever its DOM selector happened to hit, which bound one
    # Douyin account into two rows on 2026-08-09. Users rename it at will, so it
    # can never take part in the unique key.
    platform_handle: Mapped[str | None] = mapped_column(Text)
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
    # mig 416 — unbind timestamp. NULL means "in use"; every business read path
    # filters on it. The row is kept because publish_task_accounts CASCADEs off
    # this table, so a hard DELETE would take the account's publish history with
    # it — and that history is the audit record, not a detail of the binding.
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
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
    # mig 424 — the one fingerprint axis that can differ per account today (a
    # window size cannot contradict any other surface). NULL/NULL = Playwright's
    # default 1280x720, which is what every account bound before mig 424 uses.
    viewport_width: Mapped[int | None] = mapped_column(Integer)
    viewport_height: Mapped[int | None] = mapped_column(Integer)
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
    # mig 426 — 话题名 → 平台话题实体 id（抖音 challenge cid）的绑定记录，
    # [{"name","topic_id","view_count"}]。与 topics **平行**：发布链读的仍是
    # topics，这一列是"用户从建议下拉里选中当刻"的凭据，事后补不回来。
    topic_refs: Mapped[list] = mapped_column(
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
    # mig 425 — 「选择音乐」。存曲名原文，浏览器侧拿它去发布页的音乐弹窗里搜。
    # NULL = 不碰那个控件 = 平台默认（原声），也就是这一列存在之前的行为。
    music_name: Mapped[str | None] = mapped_column(Text)
    # mig 429 — 从平台曲库里选中的那一首的**结构化身份**（名+作者+时长+id）。
    # NULL = 手打曲名的老路径。曲名不是身份：实测一次搜索里 5 条标题完全相同、
    # id 各异，按名匹配会满怀信心地发出另一首歌。详见迁移文件的说明。
    music_ref: Mapped[dict | None] = mapped_column(JSONB)
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
    # Read-back verification (mig 418, P1-3). These are what let a scheduled
    # batch close on a CHECK instead of on a clock: nothing writes
    # published_url on the session channel at publish time (the platform's
    # post-publish redirect carries no id), so confirmation has to come from a
    # later visit to the creator centre. Vocabulary and transitions live in
    # ``app/workflows/publish_readback.py``.
    verify_state: Mapped[str | None] = mapped_column(Text)
    verify_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    verify_detail: Mapped[str | None] = mapped_column(Text)
    verify_checked_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
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


class MusicCharts(Base):
    """One tab of the 「选择音乐」 panel, cached for one account (mig 428).

    Per ACCOUNT, not global: 「收藏」 is plainly account-private and 「推荐」 is
    personalised too — showing one account's favourites under another is a
    leak, not a cache optimisation.

    ⚠️ ``(category_kind, category_id)`` together are the identity. 推荐 and
    收藏 both answer ``category_id='1'`` on the live panel and differ only by
    ``type``; a unique key without the kind lets the two overwrite each other
    while everything still looks healthy.

    ⚠️ ``ok`` is load-bearing. ``ok=True`` with zero tracks is a genuinely empty
    chart (measured: an account with no favourites gets a 137-byte body with no
    ``songs`` key at all); ``ok=False`` is a chart we failed to read. Merging
    them makes an empty favourites list look broken forever, or a failed
    harvest look like a platform with nothing on it.
    """

    __tablename__ = "music_charts"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="music_charts_pkey"),
        UniqueConstraint(
            "account_id",
            "category_kind",
            "category_id",
            name="music_charts_identity_key",
        ),
        ForeignKeyConstraint(
            ["account_id"],
            ["public.social_accounts.id"],
            ondelete="CASCADE",
            name="music_charts_account_id_fkey",
        ),
        Index("idx_music_charts_account", "account_id", "position"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    account_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    category_id: Mapped[str] = mapped_column(Text, nullable=False)
    category_kind: Mapped[str] = mapped_column(Text, nullable=False)
    category_name: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    ok: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    error: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    cursor: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    has_more: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    #: When the tracks currently stored were read. Advances on SUCCESS only.
    fetched_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    #: When we last tried. Advances every attempt, including failures. Kept
    #: apart from ``fetched_at`` so a failed run cannot stamp stale tracks as
    #: fresh — "updated 1 minute ago" would otherwise stay true through a
    #: three-day outage.
    checked_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class MusicChartTracks(Base):
    """One track on one chart, at one position (mig 428).

    ⚠️ ``music_id`` is TEXT. It is Douyin's 19-digit catalogue id and the list
    endpoint ships it as a JSON string; putting it through a number loses
    precision above 2^53 in every JS consumer — and the picker keys its
    selection on exactly this value.

    ⚠️ ``user_count`` is nullable and NULL is not zero. Zero is a real
    catalogue value (a track with exactly that produced the 2026-08-17
    production refusal); NULL means the payload did not say.
    """

    __tablename__ = "music_chart_tracks"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="music_chart_tracks_pkey"),
        UniqueConstraint(
            "chart_id", "position", name="music_chart_tracks_position_key"
        ),
        ForeignKeyConstraint(
            ["chart_id"],
            ["public.music_charts.id"],
            ondelete="CASCADE",
            name="music_chart_tracks_chart_id_fkey",
        ),
        Index("idx_music_chart_tracks_chart", "chart_id", "position"),
        Index("idx_music_chart_tracks_music", "music_id"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    chart_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: The chart IS an ordered list; losing the order loses its whole meaning.
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    music_id: Mapped[str] = mapped_column(Text, nullable=False)
    music_name: Mapped[str] = mapped_column(Text, nullable=False)
    music_author: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''")
    )
    duration_s: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    user_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cover_url: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''")
    )
    #: A time-limited CDN link. Refreshed with every harvest; no long-term
    #: promise is made about it.
    play_url: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''")
    )

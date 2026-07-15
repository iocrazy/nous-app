"""Topic-inspiration / signal-feed ORM models.

The tables behind the topic-inspiration feature (signal sources, hotspots and
the per-user state around them). Historically these repos went direct to
PostgREST via the service-role client; they are being migrated to the ORM
transport (read_scope/write_scope). Models are added here table-by-table as
each repo migrates:

  * ``UserHiddenSources``  — user_hidden_sources (mig 316)
  * ``HotspotUserState``   — hotspot_user_state  (mig 306)

None carry a scope mixin: these repos run under the service-role/RLS-bypass
model with ownership enforced in the service layer, so the choke point stays
inert for them (byte-for-byte legacy behaviour) until a later scope-enforcement
epic opts a table in.
"""

from __future__ import annotations

import datetime
import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    SmallInteger,
    Text,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.orm_base import Base


class TopicGroups(Base):
    """A cluster of related hotspots (mig 304 + 323 source_labels). Referenced
    by hotspots.topic_group_id; the feed embeds ``source_count`` /
    ``source_labels`` from here."""

    __tablename__ = "topic_groups"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="topic_groups_pkey"),
        Index("idx_topic_groups_user", "user_id"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_snowflake_id()"),
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    source_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    heat: Mapped[float] = mapped_column(
        Numeric, nullable=False, server_default=text("0")
    )
    first_seen: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    last_seen: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    source_labels: Mapped[list] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )


class Hotspots(Base):
    """A single topic-inspiration hotspot (mig 304 + 308 heat + 313 embedding +
    318 score_dims). ``user_id`` NULL = global (Phase 1)."""

    __tablename__ = "hotspots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_id"],
            ["public.signal_sources.id"],
            ondelete="SET NULL",
            name="hotspots_source_id_fkey",
        ),
        ForeignKeyConstraint(
            ["topic_group_id"],
            ["public.topic_groups.id"],
            ondelete="SET NULL",
            name="hotspots_topic_group_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="hotspots_pkey"),
        Index("idx_hotspots_dedup", "dedup_key", unique=True),
        Index("idx_hotspots_captured", "captured_at"),
        Index("idx_hotspots_heat", "heat"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_snowflake_id()"),
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    source_id: Mapped[int | None] = mapped_column(BigInteger)
    source_label: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str | None] = mapped_column(Text)
    origin_url: Mapped[str | None] = mapped_column(Text)
    content_original: Mapped[str | None] = mapped_column(Text)
    content_translated: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    ai_summary: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    score: Mapped[float | None] = mapped_column(Numeric)
    tags: Mapped[list] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    category: Mapped[str | None] = mapped_column(Text)
    topic_group_id: Mapped[int | None] = mapped_column(BigInteger)
    media_url: Mapped[str | None] = mapped_column(Text)
    cover_url: Mapped[str | None] = mapped_column(Text)
    captured_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    rank_timeline: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    dedup_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    heat: Mapped[float | None] = mapped_column(Numeric)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(2048))
    score_dims: Mapped[dict | None] = mapped_column(JSONB)


class SignalSources(Base):
    """A topic-inspiration signal source (mig 304 + 318 tier). ``user_id`` NULL
    means a global/system source visible to everyone."""

    __tablename__ = "signal_sources"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="signal_sources_pkey"),
        CheckConstraint(
            "kind IN ('newsnow', 'rss', 'http_api', 'custom')",
            name="signal_sources_kind_check",
        ),
        CheckConstraint(
            "health IN ('ok', 'degraded', 'dead')",
            name="signal_sources_health_check",
        ),
        CheckConstraint("tier IN (1, 2, 3)", name="signal_sources_tier_check"),
        Index("idx_signal_sources_user", "user_id"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_snowflake_id()"),
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    config: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    category: Mapped[str | None] = mapped_column(Text)
    health: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'ok'::text")
    )
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    last_fetched_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    last_ok_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    tier: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("2")
    )


class UserHiddenSources(Base):
    """Per-user "close/hide" of a signal source (composite PK, mig 316).

    A hidden source keeps collecting globally; it is just excluded from this
    user's feed.
    """

    __tablename__ = "user_hidden_sources"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_id"],
            ["public.signal_sources.id"],
            ondelete="CASCADE",
            name="user_hidden_sources_source_id_fkey",
        ),
        PrimaryKeyConstraint("user_id", "source_id", name="user_hidden_sources_pkey"),
        Index("idx_user_hidden_sources_user", "user_id"),
        {"schema": "public"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    source_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class HotspotUserState(Base):
    """Per-user read/saved/hidden flags for a global hotspot (composite PK,
    mig 306)."""

    __tablename__ = "hotspot_user_state"
    __table_args__ = (
        ForeignKeyConstraint(
            ["hotspot_id"],
            ["public.hotspots.id"],
            ondelete="CASCADE",
            name="hotspot_user_state_hotspot_id_fkey",
        ),
        PrimaryKeyConstraint("user_id", "hotspot_id", name="hotspot_user_state_pkey"),
        Index("idx_hotspot_user_state_user", "user_id"),
        {"schema": "public"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    hotspot_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    is_read: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    is_saved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    is_hidden: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )

"""GeneratedMedia ORM model — Tier-1 store for AI-generated media (sub-plan 5)."""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Numeric,
    PrimaryKeyConstraint,
    Text,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.orm_base import Base


class GeneratedMedia(Base):
    __tablename__ = "generated_media"
    __table_args__ = (
        ForeignKeyConstraint(
            ["conversation_id"],
            ["public.conversations.id"],
            ondelete="SET NULL",
            name="generated_media_conversation_id_fkey",
        ),
        ForeignKeyConstraint(
            ["source_asset_id"],
            ["public.assets.id"],
            ondelete="SET NULL",
            name="generated_media_source_asset_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="generated_media_pkey"),
        CheckConstraint(
            "review_state IN ('unreviewed','saved','in_assets','deleted')",
            name="generated_media_review_state_check",
        ),
        Index("idx_genmedia_scope_created", "scope_id", "created_at"),
        Index("idx_genmedia_origin_run", "origin_run_id"),
        Index("idx_genmedia_canvas", "canvas_id"),
        Index(
            "idx_genmedia_scope_state_created",
            "scope_id",
            "review_state",
            text("created_at DESC"),
        ),
        Index(
            "idx_genmedia_source_asset",
            "source_asset_id",
            postgresql_where=text("source_asset_id IS NOT NULL"),
        ),
        # mig 456: one inbox row per promoted resource. Partial so the many
        # never-promoted rows (promoted_resource_id IS NULL) do not collide
        # with each other. Subsumes the plain idx_genmedia_promoted (mig 307),
        # which that migration drops.
        Index(
            "uq_genmedia_promoted_resource",
            "promoted_resource_id",
            unique=True,
            postgresql_where=text("promoted_resource_id IS NOT NULL"),
        ),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    scope_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    creator_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    media_kind: Mapped[str] = mapped_column(Text, nullable=False)
    mime: Mapped[str | None] = mapped_column(Text)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    # sha256 of the bytes; populated only for object-store writes (dedup +
    # integrity). NULL for every filesystem row. Migration 337.
    content_sha256: Mapped[str | None] = mapped_column(Text)
    origin_kind: Mapped[str] = mapped_column(Text, nullable=False)
    origin_run_id: Mapped[str | None] = mapped_column(Text)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    canvas_id: Mapped[int | None] = mapped_column(BigInteger)
    node_id: Mapped[str | None] = mapped_column(Text)
    prompt: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    provider: Mapped[str | None] = mapped_column(Text)
    params: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    cost_cents: Mapped[float | None] = mapped_column(Numeric)
    parent_resource_id: Mapped[int | None] = mapped_column(BigInteger)
    derivation_kind: Mapped[str | None] = mapped_column(Text)
    promoted_resource_id: Mapped[int | None] = mapped_column(BigInteger)
    # mig 446: Generated inbox state — 'unreviewed'|'saved'|'in_assets'|'deleted'.
    review_state: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'unreviewed'")
    )
    # mig 446: the asset this generation was dispatched from (pre-fills Save-as-Asset).
    source_asset_id: Mapped[int | None] = mapped_column(BigInteger)
    # Chat provenance (mig 327 renamed channel_id → conversation_id). Was
    # missing from this model (drift) — added when the repo moved to select().
    conversation_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )

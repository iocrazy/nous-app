"""GeneratedMedia ORM model — Tier-1 store for AI-generated media (sub-plan 5)."""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    BigInteger,
    DateTime,
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
        PrimaryKeyConstraint("id", name="generated_media_pkey"),
        Index("idx_genmedia_scope_created", "scope_id", "created_at"),
        Index("idx_genmedia_origin_run", "origin_run_id"),
        Index("idx_genmedia_canvas", "canvas_id"),
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
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )

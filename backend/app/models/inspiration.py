"""Inspiration-notes domain ORM models (migration 349).

  * ``InspirationAttachments`` — inspiration_attachments
  * ``InspirationApiTokens``   — inspiration_api_tokens (personal access tokens;
    only the SHA-256 hex ``token_hash`` is stored, never the plaintext PAT)

Added table-by-table as each repo migrates off the service-role PostgREST
client; ``InspirationNotes`` follows with the notes repo. No scope mixin —
ownership is scoped by the explicit ``user_id`` predicate in the repos
(service-role/RLS-bypass model), so the choke point stays inert.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    PrimaryKeyConstraint,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.orm_base import Base


class InspirationNotes(Base):
    """An inspiration note (mig 349). Soft-deleted via ``deleted_at``."""

    __tablename__ = "inspiration_notes"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="inspiration_notes_pkey"),
        Index("idx_inspiration_notes_user_date", "user_id", "note_date"),
        Index("idx_inspiration_notes_user_pinned", "user_id", "pinned"),
        Index("idx_inspiration_notes_tags", "tags", postgresql_using="gin"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_snowflake_id()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    content_md: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''::text")
    )
    tags: Mapped[list] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    ref_hotspot: Mapped[dict | None] = mapped_column(JSONB)
    pinned: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    note_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))


class InspirationAttachments(Base):
    """File attachments on an inspiration note (mig 349)."""

    __tablename__ = "inspiration_attachments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["note_id"],
            ["public.inspiration_notes.id"],
            ondelete="CASCADE",
            name="inspiration_attachments_note_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="inspiration_attachments_pkey"),
        Index("idx_inspiration_attachments_note", "note_id"),
        Index("idx_inspiration_attachments_user", "user_id"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_snowflake_id()"),
    )
    note_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    storage_backend: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'supabase'::character varying")
    )
    bucket: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        server_default=text("'inspiration'::character varying"),
    )
    path: Mapped[str] = mapped_column(Text, nullable=False)
    mime: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    original_name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class InspirationApiTokens(Base):
    """Personal access tokens for external inspiration ingestion (mig 349).

    Secret-at-rest: only the SHA-256 hex ``token_hash`` is persisted."""

    __tablename__ = "inspiration_api_tokens"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="inspiration_api_tokens_pkey"),
        Index("idx_inspiration_api_tokens_hash", "token_hash", unique=True),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_snowflake_id()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    last_used_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    revoked_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))

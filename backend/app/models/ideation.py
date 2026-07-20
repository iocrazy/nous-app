"""Ideation topic-pool ORM model (mig 382, Project Workflow M1.5).

``Topics`` is the global "before you make a project" pool: a topic = cover +
title + reference (spec §1 / §9). The reference is a soft pointer to exactly one
of four sources (or none = blank hand-written) — an inspiration note, an
inspiration "# topic" (hotspots feed), a download-library resource, or parsed
media — all BIGINT snowflake ids, intentionally NOT FK-constrained so a trashed
source just goes stale rather than blocking the delete.

Snowflake BIGINT ids ride as strings at the API boundary (bigIntSafeFetch); the
repo ``str()``s ids on the way out. No scope mixin: ownership is the explicit
``team_id`` predicate in every repo method plus the router's
``resolve_effective_role`` guard (service-role / RLS-bypass model).
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    PrimaryKeyConstraint,
    Text,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.orm_base import Base


class Topics(Base):
    """One ideation-pool topic (mig 382). Team-scoped; status flows
    candidate → shortlisted → produced → archived."""

    __tablename__ = "topics"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="topics_pkey"),
        CheckConstraint(
            "status IN ('candidate', 'shortlisted', 'produced', 'archived')",
            name="topics_status_check",
        ),
        Index("idx_topics_team_status", "team_id", "status"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_snowflake_id()"),
    )
    team_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    cover_url: Mapped[str | None] = mapped_column(Text)
    excerpt: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'candidate'::text")
    )
    # Soft reference to one source (or none = blank); not FK-constrained.
    note_id: Mapped[int | None] = mapped_column(BigInteger)
    resource_id: Mapped[int | None] = mapped_column(BigInteger)
    media_id: Mapped[int | None] = mapped_column(BigInteger)
    inspiration_topic_id: Mapped[int | None] = mapped_column(BigInteger)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )

"""Playback-position ORM model (mig 473).

Where each user got to in each video, shared across their devices. The
browser-local store (``frontend/utils/playbackResume.ts``) stays as the
instant, offline-capable layer; this table is what makes a phone and a laptop
agree.

``UserScoped`` names ``user_id`` as the owner column, so the do_orm_execute
choke point injects ``user_id == scope.user_id`` and every read/write must run
inside a scope. That is the whole security story for this table — viewing
history is exactly the kind of data a missing filter leaks.

The DB has ``user_id REFERENCES auth.users(id) ON DELETE CASCADE`` (mig 473);
it is NOT declared here because ``auth.users`` is permanently excluded from the
ORM metadata (see ``app/models/__init__.py``). Declaring it makes SQLAlchemy
raise ``NoReferencedTableError`` on the first flush — which is how this comment
came to exist.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Double,
    Index,
    PrimaryKeyConstraint,
    Text,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.orm_base import Base, UserScoped


class PlaybackPositions(Base, UserScoped):
    __tablename__ = "playback_positions"
    __tenant_user_col__ = "user_id"
    __table_args__ = (
        CheckConstraint(
            "position_seconds >= 0::double precision",
            name="playback_positions_position_nonneg",
        ),
        CheckConstraint(
            "duration_seconds > 0::double precision",
            name="playback_positions_duration_positive",
        ),
        CheckConstraint(
            "char_length(media_key) >= 1 AND char_length(media_key) <= 512",
            name="playback_positions_media_key_len",
        ),
        PrimaryKeyConstraint("id", name="playback_positions_pkey"),
        Index("uq_playback_positions_user_media", "user_id", "media_key", unique=True),
        Index("idx_playback_positions_user_updated", "user_id", "updated_at"),
        {
            "comment": ("Per-user playback position per media, synced across devices."),
            "schema": "public",
        },
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    media_key: Mapped[str] = mapped_column(Text, nullable=False)
    position_seconds: Mapped[float] = mapped_column(Double, nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Double, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    # Server time, never client-supplied: it is the arbiter for which device
    # wrote last, and two devices' wall clocks cannot order anything.
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )

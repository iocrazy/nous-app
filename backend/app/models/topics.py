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

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKeyConstraint,
    Index,
    PrimaryKeyConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.orm_base import Base


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
